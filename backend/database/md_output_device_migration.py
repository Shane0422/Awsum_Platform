"""Add MD's Output Device bitmask (#1-#8, 0 = not output) without altering existing rows or related keys.

Existing MD rows receive 0 through the column default, which matches their current behavior (no output).
"""
from sqlalchemy import inspect, text


def migrate_md_output_devices(engine) -> None:
    with engine.begin() as conn:
        if not inspect(conn).has_table("tb_md_master"):
            return
        columns = {column["name"] for column in inspect(conn).get_columns("tb_md_master")}
        if "i_output_devices" not in columns:
            exists_guard = "IF NOT EXISTS " if conn.dialect.name == "postgresql" else ""
            conn.execute(text(
                f"ALTER TABLE tb_md_master ADD COLUMN {exists_guard}i_output_devices INTEGER NOT NULL DEFAULT 0"
            ))
