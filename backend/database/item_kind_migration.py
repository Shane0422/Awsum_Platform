"""Seed the Platform Item Kind System Master and link legacy MD Item Kind values; Display Names are never overwritten."""
from sqlalchemy import inspect, text

SYSTEM_ITEM_KINDS = (
    ("ITEM", "Item"),
    ("SERVICE", "Service"),
    ("ADMISSION", "Admission / Ticket"),
    ("GIFT_CARD", "Gift Card"),
    ("GIFT_CERTIFICATE", "Gift Certificate"),
    ("COUPON", "Coupon"),
    ("TIP", "Tip"),
    ("LOTTERY", "Lottery"),
    ("OTHER", "Other"),
)


def migrate_item_kinds(engine) -> None:
    if not inspect(engine).has_table("tb_item_kind"):
        raise RuntimeError("Item Kind seed requires tb_item_kind.")
    with engine.begin() as conn:
        existing = {code for (code,) in conn.execute(text("SELECT c_item_kind_code FROM tb_item_kind"))}
        for order, (code, name) in enumerate(SYSTEM_ITEM_KINDS, start=1):
            if code in existing:
                continue
            conn.execute(text("""
                INSERT INTO tb_item_kind (c_item_kind_code, c_item_kind_name, i_display_order, b_active, dt_created, dt_updated)
                VALUES (:code, :name, :sort, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), {"code": code, "name": name, "sort": order * 10})
        # Legacy MD Item Kind fixed values are linked to System Codes; SERVICE already matches.
        if inspect(conn).has_table("tb_md_master"):
            conn.execute(text(
                "UPDATE tb_md_master SET c_item_kind = 'ITEM' WHERE c_item_kind IN ('HARDWARE', 'SOFTWARE')"
            ))
