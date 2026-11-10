import os
import re

from sqlalchemy import create_engine, func, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

# Fallback URL used when no env var is set.
# Keep credentials aligned with the default local dev policy.
DEFAULT_PLATFORM_DB_URL = "postgresql+psycopg://postgres:Awsum123!@127.0.0.1:5432/awsum_platform"
ADMIN_STORE_ID = 1000001
MERCHANT_STORE_ID_START = 1100001


def _resolve_platform_db_url_raw() -> tuple[str, str]:
    env_primary = os.getenv("AWSUM_PLATFORM_DATABASE_URL")
    if env_primary:
        return env_primary, "AWSUM_PLATFORM_DATABASE_URL"

    env_fallback = os.getenv("PLATFORM_DATABASE_URL")
    if env_fallback:
        return env_fallback, "PLATFORM_DATABASE_URL"

    return DEFAULT_PLATFORM_DB_URL, "default"


def get_platform_db_url_source() -> str:
    _, source = _resolve_platform_db_url_raw()
    return source


def get_platform_db_url() -> str:
    raw_url, _ = _resolve_platform_db_url_raw()
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if raw_url.startswith("postgres://"):
        return raw_url.replace("postgres://", "postgresql+psycopg://", 1)
    return raw_url


def mask_db_url(url: str) -> str:
    try:
        parsed = make_url(url)
        return parsed.render_as_string(hide_password=True)
    except Exception:
        # Best-effort fallback in case of malformed URL.
        return "<invalid-db-url>"


def _platform_admin_engine():
    """(engine on the admin database, Platform database name); the name is validated."""
    parsed = make_url(get_platform_db_url())
    db_name = parsed.database
    if not db_name:
        raise RuntimeError("Platform database name is missing in DATABASE_URL")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", db_name):
        raise RuntimeError(f"Unsafe database name: {db_name}")
    admin_db_name = os.getenv("PLATFORM_PG_ADMIN_DB", "postgres")
    admin_engine = create_engine(
        parsed.set(database=admin_db_name).render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        future=True,
    )
    return admin_engine, db_name


def platform_database_exists() -> bool:
    """Read-only: whether the Platform database exists on its server."""
    admin_engine, db_name = _platform_admin_engine()
    try:
        with admin_engine.connect() as conn:
            return bool(conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
                                     {"db_name": db_name}).scalar())
    finally:
        admin_engine.dispose()


def ensure_platform_database_exists() -> None:
    """Create the Platform database when missing (startup with AWSUM_AUTO_MIGRATE=ON / migrate_platform --init)."""
    from backend.database.migration_policy import assert_schema_change_allowed

    assert_schema_change_allowed(get_platform_db_url())
    admin_engine, db_name = _platform_admin_engine()
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
                {"db_name": db_name},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        admin_engine.dispose()


platform_engine = create_engine(get_platform_db_url(), pool_pre_ping=True, future=True)
PlatformSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=platform_engine)
PlatformBase = declarative_base()


# Backward-compatible aliases for gradually migrated code
AdminBase = PlatformBase
AdminSessionLocal = PlatformSessionLocal
admin_engine = platform_engine


def get_next_merchant_store_id(db):
    from backend.models_admin.store import Store

    max_id = db.query(func.max(Store.i_store_id)).filter(Store.i_store_id >= MERCHANT_STORE_ID_START).scalar()
    return MERCHANT_STORE_ID_START if not max_id else max_id + 1
