"""Database migration policy: when the schema / data may be changed, and against which database.

- AWSUM_AUTO_MIGRATE: the code default is OFF (Production). Development turns it ON in its local ``.env``
  (``AWSUM_AUTO_MIGRATE=on``): startup / ``uvicorn --reload`` then runs the non-destructive migration (new tables,
  new columns, missing master rows). DROP / recreate / data deletion never run automatically: they are skipped
  or stopped with an error and a warning. OFF: startup never changes the schema or data; it only reports.
- Manual migrations with backup verification: ``python -m tools.migrate_platform`` (--check / --init / --apply).
- A test process can never change a real PostgreSQL database: only databases whose name ends in ``_test``
  (or SQLite) are allowed while unittest / pytest is running.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy.engine import make_url

AUTO_MIGRATE_ENV = "AWSUM_AUTO_MIGRATE"
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})
TEST_DATABASE_SUFFIX = "_test"


class MigrationBlocked(RuntimeError):
    """A schema / data change was refused by the migration policy."""


def auto_migrate_enabled() -> bool:
    value = os.getenv(AUTO_MIGRATE_ENV, "").strip().lower()
    if value not in TRUE_VALUES | FALSE_VALUES:
        print(f"[MIGRATION] Unknown {AUTO_MIGRATE_ENV}={value!r}; treated as OFF.")
    return value in TRUE_VALUES


def is_test_process() -> bool:
    """True while unittest / pytest is running this process."""
    main_spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    main_name = getattr(main_spec, "name", "") or ""
    return (main_name.split(".")[0] in {"unittest", "pytest"} or "pytest" in sys.modules
            or os.getenv("AWSUM_TEST_RUN", "").strip().lower() in TRUE_VALUES)


def database_of(target) -> tuple[str, str | None]:
    """(dialect, database name) of an engine, connection or URL."""
    engine = getattr(target, "engine", None)  # Engine.engine is itself; Connection.engine is its Engine
    url = engine.url if engine is not None else make_url(str(target))
    return url.get_backend_name(), url.database


def assert_schema_change_allowed(target) -> None:
    """Refuse schema / data changes on a real PostgreSQL database from a test process."""
    dialect, database = database_of(target)
    if dialect == "postgresql" and is_test_process() and not (database or "").endswith(TEST_DATABASE_SUFFIX):
        raise MigrationBlocked(
            f"Test runs cannot change PostgreSQL database {database!r}; "
            f"use a database whose name ends in {TEST_DATABASE_SUFFIX!r}."
        )


# pg_advisory_lock key shared by every Platform migration run ("AWSM" as a bigint).
MIGRATION_LOCK_KEY = 0x4157534D


class migration_lock:
    """Hold the Platform migration advisory lock on a dedicated connection; refuses when another run holds it."""

    def __init__(self, engine):
        self.engine = engine
        self.conn = None

    def __enter__(self):
        self.conn = self.engine.connect()
        if self.conn.dialect.name == "postgresql":
            from sqlalchemy import text

            got = self.conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}).scalar()
            self.conn.commit()
            if not got:
                self.conn.close()
                raise MigrationBlocked("Another Platform migration is running (advisory lock is held).")
        return self

    def __exit__(self, *exc):
        if self.conn.dialect.name == "postgresql":
            from sqlalchemy import text

            self.conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
            self.conn.commit()
        self.conn.close()
        return False
