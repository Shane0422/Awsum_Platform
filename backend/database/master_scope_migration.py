"""Keep existing Platform masters global; new tenant masters are Merchant scoped."""
import logging

from sqlalchemy import inspect, text


def migrate_master_scopes(engine) -> None:
    with engine.begin() as conn:
        for table in ("tb_tax", "tb_category1", "tb_category2", "tb_category3"):
            inspector = inspect(conn)
            if not inspector.has_table(table):
                logging.getLogger(__name__).info("Master scope migration deferred for new table %s.", table)
                continue
            if conn.dialect.name == "postgresql":
                conn.execute(text(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE"))
            columns = [column["name"] for column in inspector.get_columns(table)]
            projection = ", ".join(conn.dialect.identifier_preparer.quote(column) for column in columns)
            snapshot = text(f"SELECT {projection} FROM {table} ORDER BY 1")
            existing_rows = conn.execute(snapshot).all()
            if "i_merchant_id" not in columns:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN i_merchant_id INTEGER REFERENCES tb_merchant(i_merchant_id)"
                ))
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_i_merchant_id ON {table} (i_merchant_id)"))
            if conn.execute(snapshot).all() != existing_rows:
                raise RuntimeError(f"Master scope migration changed existing {table} rows; rolling back.")
