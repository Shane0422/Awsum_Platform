"""Quiesced, copy/verify/cutover/cleanup migration to the final program/data roots.

Run as a module. Never reads or edits PostgreSQL cluster files.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from uuid import uuid4

from sqlalchemy import inspect, text

from backend.core.data_paths import PROJECT_ROOT
from backend.services.builtin_assets import BUILTIN_ROOT, CATEGORIES as BUILTIN_CATEGORIES
from backend.services.paths import AwsumPathResolver


BUILTINS = {
    **{f"platform/{name}": f"logo/{name}" for name in
       ("logo_full.png", "icon.png", "icon_01.png", "icon_02.png", "icon_03.png")},
    "platform/hero_platform.jpg": "background/hero_platform.jpg",
    **{f"platform/{name}.jpg": f"ui/{name}.jpg" for name in
       ("dashboard_preview", "feature_dashboard", "feature_store", "feature_users")},
    "tuxedo_banner.jpg": "background/tuxedo_banner.jpg",
    **{f"collection_{name}.jpg": f"ui/collection_{name}.jpg" for name in
       ("black", "navy", "romantic", "white")},
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def is_current_builtin(path: Path, builtin_root: Path) -> bool:
    """Files in the current built-in category folders are program assets; legacy flat files are not."""
    if not path.is_relative_to(builtin_root):
        return False
    parts = path.relative_to(builtin_root).parts
    return len(parts) >= 2 and parts[0] in BUILTIN_CATEGORIES


def write_manifest(path: Path, plan: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(plan, ensure_ascii=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def database_fingerprint(engine) -> dict:
    result = {}
    with engine.connect() as conn:
        for table in sorted(inspect(conn).get_table_names()):
            # Session audit activity is intentionally mutable on application verification.
            if table == "tb_session":
                continue
            quoted = conn.dialect.identifier_preparer.quote(table)
            rows = sorted(json.dumps(list(row), default=str, ensure_ascii=True)
                          for row in conn.execute(text(f"SELECT * FROM {quoted}")))
            result[table] = {"rows": len(rows), "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest()}
    return result


def owner_inventory(engine) -> list[dict]:
    with engine.connect() as conn:
        owners = [{"context": "PLATFORM", "id": None, "code": None, "logical_root": "/platform-admin"}]
        for table, key, code, context in (
            ("tb_agency", "i_agency_id", "c_agency_code", "AGENCY"),
            ("tb_merchant", "i_merchant_id", "c_merchant_code", "MERCHANT"),
        ):
            for identity, value in conn.execute(text(f"SELECT {key},{code} FROM {table} ORDER BY {key}")):
                resolver = AwsumPathResolver.for_owner(context, value)
                owners.append({"context": context, "id": identity, "code": value,
                               "logical_root": resolver.get_context_root()})
        from backend.database.pg_platform import ADMIN_STORE_ID
        for identity, merchant_id, code, merchant in conn.execute(text("""
            SELECT s.i_store_id,s.i_merchant_id,s.c_store_code,c.c_merchant_code
            FROM tb_store s LEFT JOIN tb_merchant c ON c.i_merchant_id=s.i_merchant_id
            WHERE s.i_store_id<>:hq ORDER BY s.i_store_id
        """), {"hq": ADMIN_STORE_ID}):
            resolver = AwsumPathResolver("STORE", merchant_code=merchant, store_code=code)
            owners.append({"context": "STORE", "id": identity, "merchant_id": merchant_id, "code": code,
                           "logical_root": resolver.get_context_root()})
        return owners


def build_plan(engine, destination: Path, *, project: Path = PROJECT_ROOT) -> dict:
    project, destination = project.resolve(), destination.resolve()
    if destination == project or destination.is_relative_to(project) or project.is_relative_to(destination):
        raise ValueError("Program and storage roots must be separate, non-overlapping directories.")
    if destination.name.casefold() == "postgresql":
        raise ValueError("Do not use the PostgreSQL cluster root as application storage.")
    owners = owner_inventory(engine)
    roots = {item["logical_root"]: item for item in owners}
    old_root = project / "data"
    static = project / "static" / "images"
    builtin_root = project / BUILTIN_ROOT.relative_to(PROJECT_ROOT)
    known_builtin_hashes = {digest(static / source) for source in BUILTINS if (static / source).is_file()}
    files, conflicts = [], []
    targets = {}
    for base in (old_root, static, project / "db_backups"):
        if not base.exists():
            continue
        for source in sorted(base.rglob("*")):
            if is_current_builtin(source, builtin_root):
                continue  # Current built-in program assets, not legacy files.
            if source.is_symlink():
                raise ValueError(f"Resolve legacy symlink before migration: {source}")
            if not source.is_file():
                continue
            if source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                for suffix in ("-wal", "-journal"):
                    journal = Path(str(source) + suffix)
                    if journal.exists() and journal.stat().st_size:
                        raise RuntimeError(f"Quiesce/checkpoint SQLite before migrating: {source}")
            relative = source.relative_to(project).as_posix()
            sha = digest(source)
            entry = {"source": str(source), "relative": relative, "bytes": source.stat().st_size,
                     "sha256": sha, "kind": "retained_archive", "destination": None, "owner": None}
            if base == static and source.relative_to(static).as_posix() in BUILTINS:
                entry.update(kind="builtin", destination=str(builtin_root / BUILTINS[source.relative_to(static).as_posix()]))
            elif base == old_root:
                tail = source.relative_to(old_root).parts
                if tail[:1] == ("media",):
                    logical = "/" + "/".join(tail[1:])
                    try:
                        owner, resource = AwsumPathResolver.parse_resource(logical)
                    except ValueError:
                        entry["reason"] = "Legacy standalone Store image or unrecognized historical resource."
                    else:
                        identity = roots.get(owner.get_context_root())
                        if identity is None:
                            entry["reason"] = "No matching current database owner; no code was guessed."
                        elif owner.context_type == "PLATFORM" and resource[1] != "master" and sha in known_builtin_hashes:
                            entry["reason"] = "Historical runtime copy of a bundled program asset."
                        elif owner.context_type == "PLATFORM" and source.name.startswith("legacy-"):
                            entry["reason"] = "Historical Platform asset; retained for recovery, not an active default."
                        elif source.name == ".master-image.lock":
                            entry["reason"] = "Ephemeral operation lock; recreated on next save."
                        else:
                            entry.update(kind="runtime", destination=str(destination.joinpath(*tail[1:])), owner=identity)
                elif len(tail) >= 3 and tail[0] in {"platform-admin", "agency", "merchant"}:
                    logical = "/" + "/".join(tail)
                    owner, _ = AwsumPathResolver.parse_resource(logical)
                    identity = roots.get(owner.get_context_root())
                    if identity is None:
                        raise ValueError(f"Data owner cannot be verified: {source}")
                    entry.update(kind="runtime", destination=str(destination.joinpath(*tail)), owner=identity)
                else:
                    entry["reason"] = "Prior migration map, backup, or obsolete SQLite archive; preserved in audit backup."
            if entry["destination"]:
                target = Path(entry["destination"])
                if not target.resolve().is_relative_to(builtin_root.resolve() if entry["kind"] == "builtin" else destination):
                    raise ValueError(f"Destination escapes expected root: {target}")
                if target.exists() and (not target.is_file() or digest(target) != sha):
                    conflicts.append({"source": str(source), "destination": str(target), "reason": "Destination differs; no overwrite allowed."})
                previous = targets.get(str(target).casefold())
                if previous and previous != sha:
                    conflicts.append({"destination": str(target), "reason": "Different source files map to one destination."})
                targets[str(target).casefold()] = sha
            files.append(entry)
    return {"version": 1, "project_root": str(project), "legacy_root": str(old_root),
            "storage_root": str(destination), "owners": owners, "files": files, "conflicts": conflicts,
            "database_before": database_fingerprint(engine), "status": "PLANNED",
            "postgresql_cluster": "EXCLUDED: database-native pg_dump only; no cluster file operations"}


def copy_checked(source: Path, target: Path, sha: str) -> None:
    if digest(source) != sha:
        raise RuntimeError(f"Source changed; quiesce writers and regenerate the plan: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or digest(target) != sha:
            raise RuntimeError(f"Destination conflict; nothing overwritten: {target}")
        return
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    if digest(target) != sha:
        raise RuntimeError(f"Copy verification failed: {target}")


def backup_postgres(engine, target: Path, executable: Path) -> None:
    url = engine.url
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    subprocess.run([str(executable), "--host", url.host or "localhost", "--port", str(url.port or 5432),
                    "--username", url.username or "", "--dbname", url.database or "", "--format=custom",
                    "--file", str(target)], env=env, check=True, capture_output=True)
    subprocess.run([str(executable.with_name("pg_restore.exe" if os.name == "nt" else "pg_restore")),
                    "--list", str(target)], check=True, capture_output=True)


def verify_sqlite(path: Path) -> None:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise RuntimeError(f"SQLite integrity check failed: {path}")
        db.execute("SELECT name FROM sqlite_master").fetchall()


def apply_plan(plan: dict, engine, *, pg_dump: Path | None = None) -> Path:
    if plan["conflicts"]:
        raise RuntimeError("Resolve preflight collisions before applying; nothing copied.")
    if database_fingerprint(engine) != plan["database_before"]:
        raise RuntimeError("Database changed after planning; stop application writers and retry.")
    root = Path(plan["storage_root"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    audit = root / "platform-admin" / "data" / "audit" / "storage-migrations" / stamp
    audit.mkdir(parents=True, exist_ok=False)
    manifest = audit / "manifest.json"
    plan.update(manifest=str(manifest), created_at=stamp, status="BACKING_UP")
    write_manifest(manifest, plan)
    if engine.dialect.name == "postgresql":
        if pg_dump is None:
            raise ValueError("A pg_dump executable is required before actual PostgreSQL cutover.")
        backup_postgres(engine, audit / "platform.dump", pg_dump)
    for item in plan["files"]:
        backup = audit / "backup" / Path(item["relative"])
        copy_checked(Path(item["source"]), backup, item["sha256"])
        item["backup"] = str(backup)
    plan["status"] = "BACKED_UP"
    write_manifest(manifest, plan)
    for item in plan["files"]:
        if item["destination"]:
            copy_checked(Path(item["source"]), Path(item["destination"]), item["sha256"])
            if Path(item["destination"]).suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                verify_sqlite(Path(item["destination"]))
    for owner in plan["owners"]:
        base = root.joinpath(*owner["logical_root"].lstrip("/").split("/"))
        for kind in ("images", "data", *(("store",) if owner["context"] == "MERCHANT" else ())):
            (base / kind).mkdir(parents=True, exist_ok=True)
    marker = root / "platform-admin" / "data" / "security" / "storage-layout.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    value = {"version": 2, "storage_root": str(root), "manifest": str(manifest)}
    if marker.exists():
        existing = json.loads(marker.read_text(encoding="utf-8"))
        if existing.get("storage_root") != str(root) or existing.get("version") != 2:
            raise RuntimeError("Storage layout marker conflict; no marker overwritten.")
    else:
        write_manifest(marker, value)
    plan["status"] = "COPIED_AWAITING_CONFIGURATION_AND_APP_VERIFICATION"
    write_manifest(manifest, plan)
    return manifest


def verify_plan(plan: dict, engine) -> dict:
    for item in plan["files"]:
        backup = Path(item["backup"])
        if not backup.is_file() or digest(backup) != item["sha256"]:
            raise RuntimeError(f"Recovery backup mismatch: {backup}")
        if item["destination"]:
            target = Path(item["destination"])
            if not target.is_file() or target.stat().st_size != item["bytes"] or digest(target) != item["sha256"]:
                raise RuntimeError(f"Migrated file mismatch: {target}")
            if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                verify_sqlite(target)
    if database_fingerprint(engine) != plan["database_before"]:
        raise RuntimeError("Business/security DB changed; reconcile before legacy cleanup.")
    plan["verification"] = {"files": len(plan["files"]), "database_unchanged": True, "sqlite_integrity": "ok"}
    write_manifest(Path(plan["manifest"]), plan)
    return plan["verification"]


def cleanup_plan(plan: dict, engine, *, app_verified: bool) -> None:
    if not app_verified:
        raise ValueError("Application and uploaded-image verification must precede cleanup.")
    verify_plan(plan, engine)
    project = Path(plan["project_root"]).resolve()
    allowed = [project / "data", project / "static" / "images", project / "db_backups"]
    builtin_root = project / BUILTIN_ROOT.relative_to(PROJECT_ROOT)
    inventory = {Path(item["source"]).resolve() for item in plan["files"]}
    for base in allowed:
        if base.exists() and any(path.resolve() not in inventory for path in base.rglob("*")
                                 if path.is_file() and not is_current_builtin(path, builtin_root)):
            raise RuntimeError(f"New files appeared after inventory; reconcile before cleanup: {base}")
    # Validate the complete deletion set before touching any source.
    for item in plan["files"]:
        source = Path(item["source"])
        if not any(source.resolve().is_relative_to(base.resolve()) for base in allowed):
            raise ValueError(f"Refusing cleanup outside inventory: {source}")
        if source.exists() and digest(source) != item["sha256"]:
            raise RuntimeError(f"Source changed since migration; nothing removed: {source}")
    plan["status"] = "APPLICATION_VERIFIED_CLEANUP_IN_PROGRESS"
    write_manifest(Path(plan["manifest"]), plan)
    for item in plan["files"]:
        Path(item["source"]).unlink(missing_ok=True)
    for base in allowed:
        if base.exists():
            for directory in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                if not any(directory.iterdir()):
                    directory.rmdir()
            if not any(base.iterdir()):
                base.rmdir()
    plan["status"] = "COMPLETED"
    write_manifest(Path(plan["manifest"]), plan)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=PROJECT_ROOT.parent / "Awsum_Data")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--pg-dump", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--app-verified", action="store_true")
    args = parser.parse_args()
    from backend.database.pg_platform import platform_engine
    if args.manifest:
        plan = json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.cleanup:
            cleanup_plan(plan, platform_engine, app_verified=args.app_verified)
        elif args.verify:
            verify_plan(plan, platform_engine)
        else:
            parser.error("--manifest requires --verify or --cleanup")
    else:
        plan = build_plan(platform_engine, args.destination)
        if args.apply:
            apply_plan(plan, platform_engine, pg_dump=args.pg_dump)
    print(json.dumps({"status": plan["status"], "storage_root": plan["storage_root"],
                      "owners": plan["owners"], "file_counts": dict(Counter(i["kind"] for i in plan["files"])),
                      "conflicts": plan["conflicts"], "manifest": plan.get("manifest"),
                      "verification": plan.get("verification")}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
