"""Safely remove separators from existing Merchant, Store, and Agency codes.

Preview with ``py tools/migrate_business_codes.py`` and apply explicitly with
``py tools/migrate_business_codes.py --apply``. Every apply writes a JSON
mapping manifest that can be supplied to ``--rollback``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine.url import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.data_paths import PROJECT_ROOT, get_data_root
from backend.database.pg_platform import get_platform_db_url


CODE_COLUMNS = {
    "tb_client": ("i_client_id", "c_client_code", ("CLT", "MER")),
    "tb_store": ("i_store_id", "c_store_code", ("CLT", "STR", "CUST")),
    "tb_agency": ("i_agency_id", "c_agency_code", ("AGT",)),
}


def transform_code(value: str, prefixes: tuple[str, ...]) -> str | None:
    """Return the same code without its separator, or None for other values."""
    match = re.fullmatch(r"([A-Za-z]+)_([0-9]+)", value or "")
    if match and match.group(1).upper() in prefixes:
        return match.group(1) + match.group(2)
    return None


def replace_code_tokens(value: str, mappings: list[dict[str, Any]]) -> str:
    updated = value
    for item in mappings:
        old = item["old_code"]
        updated = re.sub(
            rf"(?<![A-Za-z0-9]){re.escape(old)}(?![A-Za-z0-9])",
            item["new_code"],
            updated,
        )
    return updated


def _load_rows(conn, table: str, id_column: str, code_column: str) -> list[tuple[Any, str]]:
    return conn.execute(
        text(f'SELECT "{id_column}", "{code_column}" FROM "{table}" WHERE "{code_column}" IS NOT NULL')
    ).all()


def _find_code_conflicts(
    table: str,
    rows: list[tuple[Any, str]],
    prefixes: tuple[str, ...],
) -> list[str]:
    final_rows = []
    for row_id, old_code in rows:
        new_code = transform_code(old_code, prefixes)
        final_rows.append((row_id, old_code, new_code or old_code, new_code is not None))

    grouped: dict[str, list[tuple[Any, str, str, bool]]] = {}
    for item in final_rows:
        grouped.setdefault(item[2].casefold(), []).append(item)

    conflicts = []
    for items in grouped.values():
        if len(items) > 1 and any(item[3] for item in items):
            details = ", ".join(
                f"id={row_id}: {old_code} -> {final_code}"
                for row_id, old_code, final_code, _ in items
            )
            conflicts.append(f"{table}: code collision after conversion ({details})")
    return conflicts


def _load_database_mappings(conn) -> tuple[list[dict[str, str]], list[str]]:
    rows = conn.execute(
        text(
            """
            SELECT datname
            FROM pg_database
            WHERE datname ~ '^(CUST|STORE)_[0-9]+$'
            ORDER BY datname
            """
        )
    ).scalars().all()
    existing_names = {
        name.casefold()
        for name in conn.execute(text("SELECT datname FROM pg_database")).scalars().all()
    }
    mappings = []
    conflicts = []
    targets = set()
    for old_name in rows:
        new_name = old_name.replace("_", "", 1)
        if new_name.casefold() in existing_names or new_name.casefold() in targets:
            conflicts.append(
                f'PostgreSQL database name collision: "{old_name}" -> "{new_name}" '
                "but the target name already exists."
            )
            continue
        targets.add(new_name.casefold())
        active_sessions = conn.execute(
            text("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = :db_name"),
            {"db_name": old_name},
        ).scalar() or 0
        if active_sessions:
            conflicts.append(
                f'PostgreSQL database "{old_name}" has {active_sessions} active connection(s); '
                "stop those clients before migration."
            )
            continue
        mappings.append({"old_name": old_name, "new_name": new_name})
    return mappings, conflicts


def _path_move(source: Path, destination: Path, probe: Path | None = None) -> dict[str, str] | None:
    probe = probe or source
    if not probe.exists():
        return None
    if destination.exists():
        raise ValueError(f"Path collision; refusing to overwrite: {destination} (source: {probe})")
    return {"from": str(source), "to": str(destination)}


def _build_plan(
    conn, data_root: Path, backup_root: Path
) -> tuple[list[dict], list[dict], list[dict], list[dict], list[str]]:
    inspector = inspect(conn)
    mappings: list[dict] = []
    rows_by_table: dict[str, list[tuple[Any, str]]] = {}
    conflicts: list[str] = []
    database_mappings, database_conflicts = _load_database_mappings(conn)
    conflicts.extend(database_conflicts)

    for table, (id_column, code_column, prefixes) in CODE_COLUMNS.items():
        if not inspector.has_table(table):
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if id_column not in columns or code_column not in columns:
            continue
        rows = _load_rows(conn, table, id_column, code_column)
        rows_by_table[table] = rows
        conflicts.extend(_find_code_conflicts(table, rows, prefixes))
        for row_id, old_code in rows:
            new_code = transform_code(old_code, prefixes)
            if new_code:
                mappings.append(
                    {
                        "table": table,
                        "id_column": id_column,
                        "code_column": code_column,
                        "row_id": row_id,
                        "old_code": old_code,
                        "new_code": new_code,
                    }
                )

    store_columns = (
        {column["name"] for column in inspector.get_columns("tb_store")}
        if inspector.has_table("tb_store")
        else set()
    )

    moves: list[dict[str, str]] = []
    planned_sources: set[str] = set()

    def add_move(source: Path, destination: Path, probe: Path | None = None) -> None:
        try:
            move = _path_move(source, destination, probe)
        except ValueError as exc:
            conflicts.append(str(exc))
            return
        if move:
            source_key = str(Path(move["from"]).resolve()).casefold()
            if source_key not in planned_sources:
                planned_sources.add(source_key)
                moves.append(move)

    brands_root = PROJECT_ROOT / "static" / "images" / "brands"
    for namespace in ("clients", "customers"):
        namespace_root = brands_root / namespace
        if not namespace_root.is_dir():
            continue
        for client_dir in sorted(namespace_root.iterdir()):
            new_client_code = transform_code(client_dir.name, ("CLT", "MER"))
            if not new_client_code or not client_dir.is_dir():
                continue
            add_move(
                client_dir,
                namespace_root / new_client_code,
            )

        for client_dir in sorted(namespace_root.iterdir()):
            if not client_dir.is_dir():
                continue
            target_client_code = transform_code(client_dir.name, ("CLT", "MER")) or client_dir.name
            stores_dir = client_dir / "stores"
            if not stores_dir.is_dir():
                continue
            target_stores_dir = namespace_root / target_client_code / "stores"
            for store_dir in sorted(stores_dir.iterdir()):
                new_store_code = transform_code(store_dir.name, ("CLT", "STR", "CUST"))
                if new_store_code and store_dir.is_dir():
                    add_move(
                        target_stores_dir / store_dir.name,
                        target_stores_dir / new_store_code,
                        store_dir,
                    )

    for parent, prefixes in (
        (data_root / "company", ("CLT", "STR", "CUST")),
        (backup_root / "company", ("CLT", "STR", "CUST")),
    ):
        if not parent.is_dir():
            continue
        for code_dir in sorted(parent.iterdir()):
            new_code = transform_code(code_dir.name, prefixes)
            if new_code and code_dir.is_dir():
                add_move(code_dir, parent / new_code)

    reference_mappings = list(mappings)
    reference_mappings.extend(
        {
            "old_code": Path(move["from"]).name,
            "new_code": Path(move["to"]).name,
        }
        for move in moves
        if Path(move["from"]).name != Path(move["to"]).name
    )

    path_updates: list[dict] = []
    if inspector.has_table("tb_store") and "c_db_path" in store_columns:
        for store_id, old_path in conn.execute(
            text('SELECT "i_store_id", "c_db_path" FROM "tb_store" WHERE "c_db_path" IS NOT NULL')
        ).all():
            new_path = replace_code_tokens(old_path, reference_mappings)
            if new_path == old_path:
                continue
            path_updates.append(
                {
                    "store_id": store_id,
                    "old_path": old_path,
                    "new_path": new_path,
                }
            )

            old_parts = Path(old_path).parts
            for item in reference_mappings:
                if item["old_code"] not in old_parts:
                    continue
                index = old_parts.index(item["old_code"])
                old_directory = Path(*old_parts[: index + 1])
                add_move(old_directory, old_directory.with_name(item["new_code"]))
                break

    return mappings, path_updates, moves, database_mappings, conflicts


def _mapping_file(data_root: Path, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return data_root / "platform-admin" / "data" / "audit" / "code-migrations" / f"business-code-underscores-{stamp}.json"


def _write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def _print_mappings(mappings: list[dict]) -> None:
    if not mappings:
        print("No business codes need conversion.")
        return
    for item in mappings:
        print(
            f'{item["table"]} id={item["row_id"]}: '
            f'{item["old_code"]} -> {item["new_code"]}'
        )


def _apply_database_changes(engine, manifest: dict, reverse: bool = False) -> None:
    with engine.begin() as conn:
        mappings = reversed(manifest["mappings"]) if reverse else manifest["mappings"]
        for item in mappings:
            source = item["new_code"] if reverse else item["old_code"]
            destination = item["old_code"] if reverse else item["new_code"]
            current = conn.execute(
                text(
                    f'SELECT "{item["code_column"]}" FROM "{item["table"]}" '
                    f'WHERE "{item["id_column"]}" = :row_id'
                ),
                {"row_id": item["row_id"]},
            ).scalar()
            if current == destination:
                continue
            if current != source:
                raise RuntimeError(
                    f'Database row changed during migration: {item["table"]} '
                    f'id={item["row_id"]} expected {source}, found {current}.'
                )
            result = conn.execute(
                text(
                    f'UPDATE "{item["table"]}" SET "{item["code_column"]}" = :destination '
                    f'WHERE "{item["id_column"]}" = :row_id '
                    f'AND "{item["code_column"]}" = :source'
                ),
                {
                    "destination": destination,
                    "row_id": item["row_id"],
                    "source": source,
                },
            )
            if result.rowcount != 1:
                raise RuntimeError(
                    f'Database row changed during migration: {item["table"]} '
                    f'id={item["row_id"]} expected {source}.'
                )

        path_updates = reversed(manifest["path_updates"]) if reverse else manifest["path_updates"]
        for item in path_updates:
            source = item["new_path"] if reverse else item["old_path"]
            destination = item["old_path"] if reverse else item["new_path"]
            current = conn.execute(
                text('SELECT "c_db_path" FROM "tb_store" WHERE "i_store_id" = :store_id'),
                {"store_id": item["store_id"]},
            ).scalar()
            if current == destination:
                continue
            if current != source:
                raise RuntimeError(
                    f'Database path reference changed during migration: store id={item["store_id"]}.'
                )
            result = conn.execute(
                text(
                    'UPDATE "tb_store" SET "c_db_path" = :destination '
                    'WHERE "i_store_id" = :store_id AND "c_db_path" = :source'
                ),
                {
                    "destination": destination,
                    "store_id": item["store_id"],
                    "source": source,
                },
            )
            if result.rowcount != 1:
                raise RuntimeError(
                    f'Database path reference changed during migration: store id={item["store_id"]}.'
                )


def _sync_ops_tenant_code(db_url: str, database_name: str, source: str, destination: str) -> None:
    database_url = make_url(db_url).set(database=database_name)
    ops_engine = create_engine(database_url, pool_pre_ping=True, future=True)
    try:
        inspector = inspect(ops_engine)
        if not inspector.has_table("tb_ops_meta"):
            return
        columns = {column["name"] for column in inspector.get_columns("tb_ops_meta")}
        if "c_tenant_code" not in columns:
            return
        with ops_engine.begin() as conn:
            conn.execute(
                text(
                    'UPDATE "tb_ops_meta" SET "c_tenant_code" = :destination '
                    'WHERE "c_tenant_code" = :source'
                ),
                {"source": source, "destination": destination},
            )
    finally:
        ops_engine.dispose()


def _rename_databases(
    db_url: str,
    mappings: list[dict[str, str]],
    reverse: bool = False,
    completed: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    if completed is None:
        completed = []
    if not mappings:
        return completed
    parsed_url = make_url(db_url)
    admin_database = os.getenv("PLATFORM_PG_ADMIN_DB", "postgres")
    if any(
        admin_database.casefold() in {item["old_name"].casefold(), item["new_name"].casefold()}
        for item in mappings
    ):
        raise RuntimeError("The PostgreSQL admin database cannot be renamed by this migration.")

    admin_engine = create_engine(
        parsed_url.set(database=admin_database),
        isolation_level="AUTOCOMMIT",
        future=True,
    )
    try:
        ordered_mappings = reversed(mappings) if reverse else mappings
        with admin_engine.connect() as conn:
            for item in ordered_mappings:
                source = item["new_name"] if reverse else item["old_name"]
                destination = item["old_name"] if reverse else item["new_name"]
                names = {
                    name.casefold()
                    for name in conn.execute(
                        text("SELECT datname FROM pg_database WHERE datname IN (:source, :destination)"),
                        {"source": source, "destination": destination},
                    ).scalars().all()
                }
                if source.casefold() not in names:
                    if destination.casefold() in names:
                        continue
                    raise RuntimeError(f'PostgreSQL database "{source}" is missing.')
                if destination.casefold() in names:
                    raise RuntimeError(
                        f'PostgreSQL database "{destination}" already exists; refusing to overwrite it.'
                    )

                active_sessions = conn.execute(
                    text("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = :db_name"),
                    {"db_name": source},
                ).scalar() or 0
                if active_sessions:
                    action = "rollback" if reverse else "migration"
                    raise RuntimeError(
                        f'PostgreSQL database "{source}" has {active_sessions} active connection(s); '
                        f"stop those clients before {action}."
                    )

                if not re.fullmatch(r"(?:CUST|STORE)_?[0-9]+", source) or not re.fullmatch(
                    r"(?:CUST|STORE)_?[0-9]+", destination
                ):
                    raise RuntimeError("Unexpected PostgreSQL tenant database name in the mapping.")
                conn.execute(text(f'ALTER DATABASE "{source}" RENAME TO "{destination}"'))
                completed.append(item)
                _sync_ops_tenant_code(db_url, destination, source, destination)
    finally:
        admin_engine.dispose()
    return completed


def _restore_databases(db_url: str, mappings: list[dict[str, str]], reverse: bool = False) -> list[str]:
    failures = []
    try:
        _rename_databases(db_url, mappings, reverse=not reverse)
    except Exception as exc:
        failures.append(str(exc))
    return failures


def _move_files(
    moves: list[dict[str, str]],
    reverse: bool = False,
    completed: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    if completed is None:
        completed = []
    ordered = reversed(moves) if reverse else moves
    for move in ordered:
        source = Path(move["to"] if reverse else move["from"])
        destination = Path(move["from"] if reverse else move["to"])
        if not source.exists():
            if destination.exists():
                continue
            raise RuntimeError(f"Migration path is missing: {source}")
        if destination.exists():
            raise RuntimeError(f"Path collision; refusing to overwrite: {destination}")
        source.rename(destination)
        completed.append(move)
    return completed


def _restore_files(moves: list[dict[str, str]], reverse: bool = False) -> list[str]:
    failures = []
    for move in reversed(moves):
        source = Path(move["from"] if reverse else move["to"])
        destination = Path(move["to"] if reverse else move["from"])
        try:
            if source.exists() and not destination.exists():
                source.rename(destination)
            elif source.exists() and destination.exists():
                failures.append(f"both paths exist: {source} and {destination}")
            elif not source.exists() and not destination.exists():
                failures.append(f"both paths are missing: {source} and {destination}")
        except OSError as exc:
            failures.append(f"{source} -> {destination}: {exc}")
    return failures


def _apply(engine, db_url: str, manifest_path: Path, manifest: dict) -> None:
    _write_manifest(manifest_path, manifest)
    completed: list[dict[str, str]] = []
    renamed_databases: list[dict[str, str]] = []
    database_changes_applied = False
    try:
        _move_files(manifest["filesystem_moves"], completed=completed)
        _apply_database_changes(engine, manifest)
        database_changes_applied = True
        _rename_databases(db_url, manifest["database_mappings"], completed=renamed_databases)
    except Exception as exc:
        database_failures = _restore_databases(db_url, renamed_databases)
        if database_changes_applied:
            try:
                _apply_database_changes(engine, manifest, reverse=True)
            except Exception as rollback_exc:
                database_failures.append(f"database code restoration failed: {rollback_exc}")
        failures = _restore_files(completed)
        manifest["status"] = "failed"
        manifest["failure"] = str(exc)
        _write_manifest(manifest_path, manifest)
        failures.extend(database_failures)
        if failures:
            raise RuntimeError(
                f"Migration failed ({exc}); restoration also failed: "
                f'{"; ".join(failures)}. Mapping: {manifest_path}'
            ) from exc
        raise RuntimeError(
            f"Migration failed; filesystem changes were restored. Mapping: {manifest_path}. {exc}"
        ) from exc

    manifest["status"] = "applied"
    _write_manifest(manifest_path, manifest)
    print(f"Applied migration. Recovery mapping: {manifest_path}")


def _rollback(engine, db_url: str, manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") == "rolled_back":
        print("This migration has already been rolled back.")
        return

    completed: list[dict[str, str]] = []
    renamed_databases: list[dict[str, str]] = []
    database_changes_applied = False
    try:
        _move_files(manifest["filesystem_moves"], reverse=True, completed=completed)
        _apply_database_changes(engine, manifest, reverse=True)
        database_changes_applied = True
        _rename_databases(db_url, manifest.get("database_mappings", []), reverse=True, completed=renamed_databases)
    except Exception as exc:
        database_failures = _restore_databases(db_url, renamed_databases, reverse=True)
        if database_changes_applied:
            try:
                _apply_database_changes(engine, manifest)
            except Exception as rollback_exc:
                database_failures.append(f"database code restoration failed: {rollback_exc}")
        failures = _restore_files(completed, reverse=True)
        failures.extend(database_failures)
        manifest["status"] = "rollback_failed"
        manifest["rollback_failure"] = str(exc)
        _write_manifest(manifest_path, manifest)
        if failures:
            raise RuntimeError(
                f"Rollback failed ({exc}); file restoration also failed: "
                f'{"; ".join(failures)}. Mapping: {manifest_path}'
            ) from exc
        raise RuntimeError(f"Rollback failed; filesystem changes were restored. {exc}") from exc

    manifest["status"] = "rolled_back"
    _write_manifest(manifest_path, manifest)
    print(f"Rolled back migration using {manifest_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely remove underscores from business codes.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply after preflight; default is preview only.")
    mode.add_argument("--rollback", metavar="MAPPING_JSON", help="Restore codes and paths from a mapping file.")
    parser.add_argument("--mapping-file", help="Output mapping JSON path when applying.")
    parser.add_argument("--data-root", help="Override AWSUM_STORAGE_ROOT for Store database directories.")
    parser.add_argument("--backup-root", help="Override the legacy db_backups root inspected during code conversion.")
    args = parser.parse_args()

    data_root = Path(args.data_root).expanduser().resolve() if args.data_root else get_data_root()
    backup_root = (
        Path(args.backup_root).expanduser().resolve()
        if args.backup_root
        else PROJECT_ROOT / "db_backups"
    )
    engine = create_engine(get_platform_db_url(), pool_pre_ping=True, future=True)

    try:
        if args.rollback:
            _rollback(engine, get_platform_db_url(), Path(args.rollback).expanduser().resolve())
            return 0

        with engine.connect() as conn:
            mappings, path_updates, file_moves, database_mappings, conflicts = _build_plan(
                conn, data_root, backup_root
            )

        _print_mappings(mappings)
        if path_updates:
            print(f"Store database path references to update: {len(path_updates)}")
        for item in database_mappings:
            print(f'PostgreSQL database: {item["old_name"]} -> {item["new_name"]}')
        if conflicts:
            for conflict in conflicts:
                print(f"CONFLICT: {conflict}", file=sys.stderr)
            print("No changes were made. Resolve the conflicts and run the preview again.", file=sys.stderr)
            return 2

        manifest_path = _mapping_file(data_root, args.mapping_file)
        if not mappings and not path_updates and not file_moves and not database_mappings:
            print("No changes required.")
            return 0

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "prepared",
            "data_root": str(data_root),
            "backup_root": str(backup_root),
            "mappings": mappings,
            "path_updates": path_updates,
            "filesystem_moves": file_moves,
            "database_mappings": database_mappings,
        }
        for move in file_moves:
            print(f'PATH: {move["from"]} -> {move["to"]}')

        if not args.apply:
            print("Preview only; no database or filesystem changes were made.")
            print("Use --apply to proceed.")
            return 0

        if manifest_path.exists():
            raise FileExistsError(f"Mapping file already exists: {manifest_path}")
        _apply(engine, get_platform_db_url(), manifest_path, manifest)
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
