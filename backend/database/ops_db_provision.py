import os
import re

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, text
from sqlalchemy.engine.url import make_url

from backend.database.pg_platform import get_platform_db_url


OPS_META = MetaData()

# Skeleton tables for future per-tenant operational databases.
tb_ops_meta = Table(
    "tb_ops_meta",
    OPS_META,
    Column("i_meta_id", Integer, primary_key=True, autoincrement=True),
    Column("c_tenant_code", String, nullable=False, index=True),
    Column("c_status", String, nullable=True),
    Column("dt_created", DateTime, nullable=True),
)

tb_ops_health = Table(
    "tb_ops_health",
    OPS_META,
    Column("i_health_id", Integer, primary_key=True, autoincrement=True),
    Column("c_component", String, nullable=False),
    Column("c_state", String, nullable=False),
    Column("dt_checked", DateTime, nullable=True),
)


def build_ops_db_name(prefix: str, numeric_id: int) -> str:
    normalized_prefix = re.sub(r"[^A-Z0-9]", "", (prefix or "").upper())
    if not normalized_prefix:
        raise ValueError("prefix is required")
    return f"{normalized_prefix}_{int(numeric_id):05d}"


def build_customer_db_name(store_id: int) -> str:
    return build_ops_db_name("CUST", store_id)


def build_store_db_name(store_id: int) -> str:
    return build_ops_db_name("STORE", store_id)


def _admin_url_for_create(db_name: str):
    platform_url = get_platform_db_url()
    parsed = make_url(platform_url)
    admin_db = os.getenv("PLATFORM_PG_ADMIN_DB", "postgres")
    admin_url = parsed.set(database=admin_db)
    return admin_url, parsed, db_name


def ensure_ops_database_exists(db_name: str) -> None:
    admin_url, _, safe_db_name = _admin_url_for_create(db_name)

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", safe_db_name):
        raise RuntimeError(f"Unsafe operational DB name: {safe_db_name}")

    admin_engine = create_engine(admin_url.render_as_string(hide_password=False), future=True)
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
            {"db_name": safe_db_name},
        ).scalar()
        if not exists:
            conn.execution_options(isolation_level="AUTOCOMMIT").execute(
                text(f'CREATE DATABASE "{safe_db_name}"')
            )
    admin_engine.dispose()


def get_ops_engine(db_name: str):
    platform_url = get_platform_db_url()
    parsed = make_url(platform_url)
    ops_url = parsed.set(database=db_name)
    return create_engine(ops_url.render_as_string(hide_password=False), pool_pre_ping=True, future=True)


def init_ops_tables(db_name: str) -> None:
    engine = get_ops_engine(db_name)
    OPS_META.create_all(bind=engine)
    engine.dispose()


def provision_ops_database(prefix: str, numeric_id: int) -> str:
    db_name = build_ops_db_name(prefix, numeric_id)
    ensure_ops_database_exists(db_name)
    init_ops_tables(db_name)
    return db_name
