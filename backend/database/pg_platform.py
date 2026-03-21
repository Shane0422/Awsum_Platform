import os
import re

from sqlalchemy import create_engine, func, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

# Fallback URL used when no env var is set.
# Keep credentials aligned with the default local dev policy.
DEFAULT_PLATFORM_DB_URL = "postgresql+psycopg://postgres:Awsum123!@127.0.0.1:5432/awsum_platform"
ADMIN_STORE_ID = 1000001
ACCOUNT_STORE_ID_START = 1100001


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


def ensure_platform_database_exists() -> None:
    """Ensure the awsum_platform database exists before app startup."""
    db_url = get_platform_db_url()
    parsed = make_url(db_url)

    db_name = parsed.database
    if not db_name:
        raise RuntimeError("Platform database name is missing in DATABASE_URL")

    admin_db_name = os.getenv("PLATFORM_PG_ADMIN_DB", "postgres")
    admin_url = parsed.set(database=admin_db_name)
    admin_engine = create_engine(admin_url.render_as_string(hide_password=False), future=True)

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", db_name):
        raise RuntimeError(f"Unsafe database name: {db_name}")

    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
            {"db_name": db_name},
        ).scalar()
        if not exists:
            conn.execution_options(isolation_level="AUTOCOMMIT").execute(
                text(f'CREATE DATABASE "{db_name}"')
            )

    admin_engine.dispose()


platform_engine = create_engine(get_platform_db_url(), pool_pre_ping=True, future=True)
PlatformSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=platform_engine)
PlatformBase = declarative_base()


# Backward-compatible aliases for gradually migrated code
AdminBase = PlatformBase
AdminSessionLocal = PlatformSessionLocal
admin_engine = platform_engine


def get_next_account_store_id(db):
    from backend.models_admin.store import Store

    max_id = db.query(func.max(Store.i_store_id)).filter(Store.i_store_id >= ACCOUNT_STORE_ID_START).scalar()
    return ACCOUNT_STORE_ID_START if not max_id else max_id + 1


def get_next_customer_store_id(db):
    # Backward-compatible alias for older imports.
    return get_next_account_store_id(db)
