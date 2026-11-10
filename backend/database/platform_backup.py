"""Verified PostgreSQL backups taken before an approved Platform migration (tools.migrate_platform --apply).

A backup is a pg_dump custom-format file plus ``<dump>.manifest.json`` (target DB, UTC creation time, size,
SHA-256, tool / server versions). verify_backup() accepts it only when the file, the manifest, the target
database, the age (default 24 hours) and ``pg_restore --list`` all agree. Backups live in private Platform
audit storage (Awsum_Data/platform-admin/data/audit/migration-backups); passwords are passed to the
PostgreSQL tools only through the child process environment, never written to the manifest or a command line.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

MANIFEST_VERSION = 1
MAX_BACKUP_AGE = timedelta(hours=24)
CLOCK_SKEW = timedelta(minutes=5)


class BackupInvalid(RuntimeError):
    def __init__(self, problems: list[str]):
        super().__init__("Backup verification failed: " + "; ".join(problems))
        self.problems = problems


def backup_directory() -> Path:
    from backend.services.paths import AwsumPathResolver, resource_storage_path

    return Path(resource_storage_path(AwsumPathResolver("PLATFORM").get_data_path("audit"))) / "migration-backups"


def pg_tool(name: str) -> str:
    """Full path of a PostgreSQL client tool: AWSUM_PG_BIN, PATH, then the newest Windows install."""
    exe = name + (".exe" if os.name == "nt" else "")
    configured = os.getenv("AWSUM_PG_BIN")
    candidates = [Path(configured) / exe] if configured else []
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    installs = sorted(glob.glob(r"C:\Program Files\PostgreSQL\*\bin"),
                      key=lambda p: int(Path(p).parent.name) if Path(p).parent.name.isdigit() else 0, reverse=True)
    candidates += [Path(p) / exe for p in installs]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(f"{name} not found; set AWSUM_PG_BIN to the PostgreSQL bin folder.")


def _target(url) -> dict:
    parsed = make_url(str(url))
    return {"host": parsed.host or "localhost", "port": int(parsed.port or 5432), "database": parsed.database,
            "username": parsed.username}


def _env(url) -> dict:
    return {**os.environ, "PGPASSWORD": make_url(str(url)).password or ""}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path(dump: Path) -> Path:
    return dump.with_name(dump.name + ".manifest.json")


def _tool_version(tool: str) -> str:
    return subprocess.run([tool, "--version"], capture_output=True, text=True, check=True).stdout.strip()


def _restore_listing(dump: Path, url) -> subprocess.CompletedProcess:
    return subprocess.run([pg_tool("pg_restore"), "--list", str(dump)], env=_env(url), capture_output=True, text=True)


def create_backup(url, label: str, *, server_version: str | None = None, out_dir: Path | None = None) -> Path:
    """pg_dump the target database (custom format) and write its manifest. Returns the dump path."""
    target = _target(url)
    folder = out_dir or backup_directory()
    folder.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    dump = folder / f"{target['database']}-{label}-{created:%Y%m%d-%H%M%S}.dump"
    pg_dump = pg_tool("pg_dump")
    subprocess.run([pg_dump, "--host", target["host"], "--port", str(target["port"]), "--username", target["username"],
                    "--dbname", target["database"], "--format", "custom", "--file", str(dump)],
                   env=_env(url), check=True)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_at_utc": created.isoformat(),
        "label": label,
        "target": {key: target[key] for key in ("host", "port", "database")},
        "dump_file": dump.name,
        "format": "custom",
        "size_bytes": dump.stat().st_size,
        "sha256": _sha256(dump),
        "pg_dump_version": _tool_version(pg_dump),
        "server_version": server_version,
    }
    manifest_path(dump).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dump


def verify_backup(dump, url, *, max_age: timedelta = MAX_BACKUP_AGE, now: datetime | None = None) -> dict:
    """Check the backup file, its manifest, the target database, its age and pg_restore --list. Returns the manifest."""
    dump = Path(dump)
    now = now or datetime.now(timezone.utc)
    target = _target(url)
    problems: list[str] = []
    if not dump.is_file() or dump.stat().st_size == 0:
        raise BackupInvalid([f"backup file missing or empty: {dump}"])
    try:
        manifest = json.loads(manifest_path(dump).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BackupInvalid([f"manifest missing or unreadable: {manifest_path(dump).name}"]) from None

    if manifest.get("manifest_version") != MANIFEST_VERSION:
        problems.append("unsupported manifest version")
    if manifest.get("dump_file") != dump.name:
        problems.append("manifest belongs to another file")
    if manifest.get("size_bytes") != dump.stat().st_size:
        problems.append("file size differs from the manifest")
    if manifest.get("sha256") != _sha256(dump):
        problems.append("SHA-256 differs from the manifest")
    recorded = manifest.get("target") or {}
    for key in ("host", "port", "database"):
        if recorded.get(key) != target[key]:
            problems.append(f"backup {key} {recorded.get(key)!r} is not the target {target[key]!r}")
    try:
        created = datetime.fromisoformat(manifest["created_at_utc"])
    except (KeyError, TypeError, ValueError):
        problems.append("manifest creation time missing or invalid")
    else:
        if created > now + CLOCK_SKEW:
            problems.append("manifest creation time is in the future")
        elif now - created > max_age:
            problems.append(f"backup is older than {max_age}")
    modified = datetime.fromtimestamp(dump.stat().st_mtime, timezone.utc)
    if now - modified > max_age + CLOCK_SKEW:
        problems.append(f"backup file is older than {max_age}")

    listing = _restore_listing(dump, url)
    if listing.returncode != 0:
        problems.append(f"pg_restore --list failed: {listing.stderr.strip()[:200]}")
    else:
        header = listing.stdout.lower()
        if f"dbname: {str(target['database']).lower()}" not in header:
            problems.append("pg_restore header names another database")
        if " table data " not in header:
            problems.append("pg_restore listing has no table data")
    if problems:
        raise BackupInvalid(problems)
    return manifest
