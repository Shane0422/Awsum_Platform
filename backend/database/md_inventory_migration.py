"""Add nullable Inventory Type / Keep to Category III (defaults) and MD Master (overrides).

NULL is meaningful: on MD it means "inherit from Category III", on Category III "not set". Existing rows are
therefore left NULL and never stamped with a value. Repeatable; nothing else is altered.

Earlier builds of this feature:
- c_inventory_option (text code): when present, explicit non-NONE codes are copied to i_inventory_type once,
  in the same step the column is added ('NONE' was only the old default and stays NULL = inherit). The old
  column is left untouched.
- c_keep / i_inventory_type created NOT NULL with a default (PostgreSQL): NOT NULL and the default are dropped
  and the old 'NONE' Keep placeholder becomes NULL.
"""
from sqlalchemy import inspect, text

LEGACY_INVENTORY_OPTION_TO_TYPE = {"STOCK": 2, "RECIPE": 3, "RECEIVE_CONSUME": 4}
TABLES = ("tb_category3", "tb_md_master")
COLUMNS = (("i_inventory_type", "INTEGER"), ("c_keep", "VARCHAR(20)"))


def migrate_md_inventory(engine) -> None:
    with engine.begin() as conn:
        for table in TABLES:
            inspector = inspect(conn)
            if not inspector.has_table(table):
                continue
            existing = {column["name"]: column for column in inspector.get_columns(table)}
            postgres = conn.dialect.name == "postgresql"
            exists_guard = "IF NOT EXISTS " if postgres else ""
            for name, ddl in COLUMNS:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {exists_guard}{name} {ddl}"))
                    if name == "i_inventory_type" and "c_inventory_option" in existing:
                        for code, type_id in LEGACY_INVENTORY_OPTION_TO_TYPE.items():
                            conn.execute(text(
                                f"UPDATE {table} SET i_inventory_type = :type_id WHERE c_inventory_option = :code"
                            ), {"type_id": type_id, "code": code})
                elif postgres and not existing[name]["nullable"]:
                    conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {name} DROP NOT NULL"))
                    conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {name} DROP DEFAULT"))
            if "c_keep" in existing and (postgres or existing["c_keep"]["nullable"]):
                conn.execute(text(f"UPDATE {table} SET c_keep = NULL WHERE c_keep = 'NONE'"))
