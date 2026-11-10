"""Approved Platform database migrations: the way to change the Platform schema / data when
AWSUM_AUTO_MIGRATE is OFF (the default, Production), and the only way to run destructive cleanups.

    python -m tools.migrate_platform --check
        Read-only (default). Shows the target database and pending schema changes. Never changes the DB.
    python -m tools.migrate_platform --init --confirm-db <database>
        New installation only: the database must be missing or have no tables. Creates and initializes it.
    python -m tools.migrate_platform --apply --confirm-db <database> --backup auto|<file.dump>
        Existing database: verifies a backup (file, manifest, target DB, age <= 24 h, pg_restore --list),
        takes the migration advisory lock and runs the existing init_platform_db() migration.
        Destructive legacy cleanups also need --allow-destructive (a separately approved migration).

Stop a running ``uvicorn --reload`` before --init / --apply. The migration logic itself is the existing
backend.database.db_init_platform.init_platform_db(); this tool only adds the safety checks around it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from backend.core.local_env import load_local_env

load_local_env()

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from backend.database import migration_policy as policy  # noqa: E402
from backend.database import platform_backup  # noqa: E402
from backend.database import pg_platform  # noqa: E402
from backend.database.db_init_platform import check_platform_schema, describe_schema_check, init_platform_db  # noqa: E402


class MigrationAborted(RuntimeError):
    pass


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="python -m tools.migrate_platform", description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only check (default)")
    mode.add_argument("--init", action="store_true", help="initialize a NEW (missing or empty) database")
    mode.add_argument("--apply", action="store_true", help="migrate an EXISTING database after a verified backup")
    parser.add_argument("--confirm-db", help="must equal the target database name for --init / --apply")
    parser.add_argument("--backup", help="--apply: 'auto' (take a pg_dump now) or an existing verified .dump file")
    parser.add_argument("--allow-destructive", action="store_true",
                        help="--apply: allow approved destructive legacy cleanup (DROP TABLE / COLUMN)")
    args = parser.parse_args(argv)
    if args.allow_destructive and not args.apply:
        parser.error("--allow-destructive is only valid with --apply")
    if args.backup and not args.apply:
        parser.error("--backup is only valid with --apply")
    return args


def describe_target(url) -> dict:
    parsed = make_url(str(url))
    return {"url": parsed.render_as_string(hide_password=True), "host": parsed.host, "port": parsed.port,
            "database": parsed.database, "auto_migrate": policy.auto_migrate_enabled(),
            "test_process": policy.is_test_process()}


def has_tables(engine) -> bool:
    with engine.connect() as conn:
        return bool(inspect(conn).get_table_names())


def print_check(plan: dict) -> None:
    lines = describe_schema_check(plan)
    print("[CHECK] " + ("pending: " + str(len(lines)) if lines else "no missing tables / columns or destructive cleanup"))
    for line in lines:
        print(f"[CHECK]   {line}")
    for table, columns in plan["extra_columns"].items():
        print(f"[CHECK]   kept extra DB columns (not dropped): {table}.{', '.join(columns)}")
    print("[CHECK] index / data steps inside individual migrations are not detectable; --apply runs them.")


def confirm_target(args, target: dict) -> None:
    if not args.confirm_db:
        raise MigrationAborted("--confirm-db <database> is required.")
    if args.confirm_db != target["database"]:
        raise MigrationAborted(f"--confirm-db {args.confirm_db!r} does not match the target {target['database']!r}.")


def server_version(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SHOW server_version")).scalar()


def write_run_record(target: dict, mode: str, backup: str | None, before: dict, after: dict | None, error: str | None):
    folder = platform_backup.backup_directory()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc)
    record = {"mode": mode, "finished_at_utc": stamp.isoformat(), "target": {k: target[k] for k in ("host", "port", "database")},
              "backup": backup, "pending_before": describe_schema_check(before),
              "pending_after": describe_schema_check(after) if after else None, "error": error}
    path = folder / f"{target['database']}-migration-{mode}-{stamp:%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def run(argv=None, *, engine=None, url=None, database_exists=None, create_database=None, migrate=None) -> int:
    args = parse_args(argv)
    engine = engine or pg_platform.platform_engine
    url = url or pg_platform.get_platform_db_url()
    database_exists = database_exists or pg_platform.platform_database_exists
    create_database = create_database or pg_platform.ensure_platform_database_exists
    migrate = migrate or init_platform_db
    target = describe_target(url)
    print(f"[TARGET] {target['url']} (database={target['database']}, host={target['host']}, port={target['port']})")
    print(f"[TARGET] AWSUM_AUTO_MIGRATE={'ON' if target['auto_migrate'] else 'OFF'}")

    if not (args.init or args.apply):  # --check: read-only, never changes the DB
        if not database_exists():
            print("[CHECK] database does not exist: run --init for a new installation.")
            return 0
        print_check(check_platform_schema(engine))
        return 0

    confirm_target(args, target)
    policy.assert_schema_change_allowed(url)
    if args.init:
        if database_exists() and has_tables(engine):
            raise MigrationAborted("--init is for a new installation only; this database already has tables. Use --apply.")
        create_database()
        with policy.migration_lock(engine):
            if has_tables(engine):
                raise MigrationAborted("Tables appeared while waiting for the lock; use --apply.")
            migrate()
        after = check_platform_schema(engine)
        print_check(after)
        print(f"[INIT] done. Record: {write_run_record(target, 'init', None, {'missing_tables': [], 'missing_columns': {}, 'destructive': []}, after, None)}")
        return 0

    # --apply: existing database only, after a verified backup.
    if not database_exists() or not has_tables(engine):
        raise MigrationAborted("--apply needs an existing initialized database; use --init for a new installation.")
    if not args.backup:
        raise MigrationAborted("--backup auto|<file.dump> is required for --apply.")
    before = check_platform_schema(engine)
    print_check(before)
    if before["destructive"] and not args.allow_destructive:
        raise MigrationAborted("Destructive legacy cleanup is pending; it needs a separately approved migration "
                               "(--allow-destructive).")
    with policy.migration_lock(engine):
        dump = (platform_backup.create_backup(url, "before-migration", server_version=server_version(engine))
                if args.backup == "auto" else args.backup)
        manifest = platform_backup.verify_backup(dump, url)
        print(f"[BACKUP] verified {dump} ({manifest['size_bytes']} bytes, created {manifest['created_at_utc']})")
        error = None
        try:
            migrate(allow_destructive=args.allow_destructive)
        except Exception as exc:  # recorded, then re-raised
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            after = None if error else check_platform_schema(engine)
            record = write_run_record(target, "apply", str(dump), before, after, error)
            print(f"[APPLY] record: {record}")
    print_check(after)
    print("[APPLY] done.")
    return 0


def main(argv=None) -> int:
    try:
        return run(argv)
    except (MigrationAborted, policy.MigrationBlocked, platform_backup.BackupInvalid) as exc:
        print(f"[ABORTED] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
