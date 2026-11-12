"""Backfill one default detail for each existing MD Master record."""
from sqlalchemy import inspect, text


def migrate_md_details(engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table("tb_md_master") or not inspector.has_table("tb_md_detail"):
        raise RuntimeError("MD Detail migration requires tb_md_master and tb_md_detail.")

    owner = "i_owner_key" in {c["name"] for c in inspector.get_columns("tb_md_detail")}  # the MD's owner (STEP 2)
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE tb_md_master, tb_md_detail IN SHARE ROW EXCLUSIVE MODE"))
        conn.execute(text(f"""
            INSERT INTO tb_md_detail (
                i_md_id, {"i_owner_key, " if owner else ""}i_detail_no, b_is_default, c_unit, n_cost, n_price,
                dt_created, i_created_by, dt_updated, i_updated_by
            )
            SELECT md.i_md_id, {"md.i_owner_key, " if owner else ""}1, TRUE, md.c_unit, md.n_cost, md.n_default_unit_price,
                   md.dt_created, md.i_created_by, md.dt_updated, md.i_updated_by
            FROM tb_md_master AS md
            WHERE NOT EXISTS (
                SELECT 1 FROM tb_md_detail AS detail WHERE detail.i_md_id = md.i_md_id
            )
        """))
        conn.execute(text("""
            UPDATE tb_md_detail
            SET b_is_default = TRUE
            WHERE i_md_detail_id IN (
                SELECT MIN(detail.i_md_detail_id)
                FROM tb_md_detail AS detail
                GROUP BY detail.i_md_id
                HAVING SUM(CASE WHEN detail.b_is_default THEN 1 ELSE 0 END) = 0
            )
        """))
