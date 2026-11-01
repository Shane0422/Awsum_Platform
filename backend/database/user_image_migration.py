"""Add a nullable logical profile image reference, preserving existing user security fields."""
from sqlalchemy import inspect, text


def migrate_user_images(engine) -> None:
    with engine.begin() as conn:
        if not inspect(conn).has_table("tb_platform_user"):
            return
        columns = {column["name"] for column in inspect(conn).get_columns("tb_platform_user")}
        if "c_image_path" not in columns:
            guard = "IF NOT EXISTS " if conn.dialect.name == "postgresql" else ""
            conn.execute(text(f"ALTER TABLE tb_platform_user ADD COLUMN {guard}c_image_path VARCHAR(1000)"))
