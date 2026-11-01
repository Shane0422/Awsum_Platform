"""Extend existing users without changing existing platform accounts."""
from sqlalchemy import inspect, text


def migrate_agency_users(engine) -> None:
    with engine.begin() as conn:
        if not inspect(conn).has_table("tb_platform_user"):
            return
        columns = {column["name"] for column in inspect(conn).get_columns("tb_platform_user")}
        if "i_agency_id" not in columns:
            guard = "IF NOT EXISTS " if conn.dialect.name == "postgresql" else ""
            conn.execute(text(
                f"ALTER TABLE tb_platform_user ADD COLUMN {guard}"
                "i_agency_id INTEGER REFERENCES tb_agency(i_agency_id)"
            ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_tb_platform_user_i_agency_id "
            "ON tb_platform_user (i_agency_id)"
        ))
        # Create scope protection before replacing the old cross-scope constraints.
        if conn.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE tb_platform_user IN SHARE ROW EXCLUSIVE MODE"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_user_agency_email "
            "ON tb_platform_user (i_agency_id, lower(trim(c_email))) WHERE i_agency_id IS NOT NULL"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_user_store_email "
            "ON tb_platform_user (i_store_id, lower(trim(c_email))) WHERE i_agency_id IS NULL"
        ))
        inspector = inspect(conn)
        legacy_columns = {frozenset({"c_email"}), frozenset({"i_store_id", "c_email"})}
        for constraint in inspector.get_unique_constraints("tb_platform_user"):
            if frozenset(constraint["column_names"]) not in legacy_columns:
                continue
            if conn.dialect.name != "postgresql":
                raise RuntimeError("Migrating legacy User UNIQUE constraints requires the Platform PostgreSQL database.")
            name = conn.dialect.identifier_preparer.quote(constraint["name"])
            conn.execute(text(f"ALTER TABLE tb_platform_user DROP CONSTRAINT {name}"))
        for index in inspector.get_indexes("tb_platform_user"):
            if (index["unique"] and not index.get("duplicates_constraint")
                    and frozenset(index["column_names"]) in legacy_columns):
                name = conn.dialect.identifier_preparer.quote(index["name"])
                conn.execute(text(f"DROP INDEX {name}"))
