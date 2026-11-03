"""Add MD's optional Second Name without altering existing rows or related keys."""
from sqlalchemy import inspect, text


def migrate_md_second_name(engine) -> None:
    with engine.begin() as conn:
        if not inspect(conn).has_table("tb_md_master"):
            return
        columns = {column["name"] for column in inspect(conn).get_columns("tb_md_master")}
        if "c_second_name" not in columns:
            exists_guard = "IF NOT EXISTS " if conn.dialect.name == "postgresql" else ""
            conn.execute(text(f"ALTER TABLE tb_md_master ADD COLUMN {exists_guard}c_second_name VARCHAR(200)"))
