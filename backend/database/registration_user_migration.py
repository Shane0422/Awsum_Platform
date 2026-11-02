"""Extend existing PlatformUser scopes without changing existing credentials."""
import logging

from sqlalchemy import inspect, text

from backend.models_admin.platform_user import MERCHANT_ADMIN_ROLE_NAME, STORE_ADMIN_ROLE_NAME
from backend.models_admin.role import Role


def migrate_registration_users(engine) -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        if not inspector.has_table("tb_platform_user"):
            logging.getLogger(__name__).info("Registration User migration deferred until initial schema creation.")
            return
        if conn.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE tb_platform_user IN SHARE ROW EXCLUSIVE MODE"))
        columns = [column["name"] for column in inspector.get_columns("tb_platform_user")]
        projection = ", ".join(conn.dialect.identifier_preparer.quote(column) for column in columns)
        snapshot_sql = text(f"SELECT {projection} FROM tb_platform_user ORDER BY i_platform_user_id")
        existing_users = conn.execute(snapshot_sql).all()
        if "i_merchant_id" not in columns:
            conn.execute(text(
                "ALTER TABLE tb_platform_user ADD COLUMN i_merchant_id "
                "INTEGER REFERENCES tb_merchant(i_merchant_id)"
            ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_tb_platform_user_i_merchant_id ON tb_platform_user (i_merchant_id)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_user_merchant_email "
            "ON tb_platform_user (i_merchant_id, lower(trim(c_email))) WHERE i_merchant_id IS NOT NULL"
        ))
        if conn.dialect.name == "postgresql":
            index_sql = conn.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
                "AND indexname = 'uq_platform_user_store_email'"
            )).scalar()
        elif conn.dialect.name == "sqlite":
            index_sql = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'uq_platform_user_store_email'"
            )).scalar()
        else:
            raise RuntimeError("Registration User migration requires PostgreSQL or the SQLite test database.")
        if not index_sql or "i_merchant_id" not in index_sql:
            conn.execute(text("DROP INDEX IF EXISTS uq_platform_user_store_email"))
            conn.execute(text(
                "CREATE UNIQUE INDEX uq_platform_user_store_email "
                "ON tb_platform_user (i_store_id, lower(trim(c_email))) "
                "WHERE i_agency_id IS NULL AND i_merchant_id IS NULL"
            ))
        for name in (MERCHANT_ADMIN_ROLE_NAME, STORE_ADMIN_ROLE_NAME):
            if conn.execute(text("SELECT i_role_id FROM tb_role WHERE c_name = :name"), {"name": name}).first() is None:
                conn.execute(Role.__table__.insert().values(c_name=name, c_description=name, c_status="active"))
        if conn.execute(snapshot_sql).all() != existing_users:
            raise RuntimeError("Registration User migration changed existing user data; rolling back.")
