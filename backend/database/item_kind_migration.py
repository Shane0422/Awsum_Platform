"""Seed the Platform Item Kind System Master and link legacy MD Item Kind values; Display Names are never overwritten.

Item Kind No (c_item_kind_no, IKC00001 ...): an auto-issued management code next to the System Code.
Additive only: the column is added when missing and only rows without a number get one (display order,
then id). System Codes, names, order and status of existing rows are never changed.
"""
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
# Required System Codes: program logic keys that can never be changed or deleted.
SYSTEM_ITEM_KIND_CODES = frozenset(code for code, _name in SYSTEM_ITEM_KINDS)
ITEM_KIND_NO_PREFIX = "IKC"
ITEM_KIND_NO_DIGITS = 5


def item_kind_no(number: int) -> str:
    return f"{ITEM_KIND_NO_PREFIX}{number:0{ITEM_KIND_NO_DIGITS}d}"


def _top_number(conn) -> int:
    top = 0
    for (value,) in conn.execute(text("SELECT c_item_kind_no FROM tb_item_kind WHERE c_item_kind_no IS NOT NULL")):
        digits = str(value)[len(ITEM_KIND_NO_PREFIX):]
        if str(value).startswith(ITEM_KIND_NO_PREFIX) and digits.isdigit():
            top = max(top, int(digits))
    return top


def migrate_item_kind_numbers(conn) -> int:
    """Add c_item_kind_no when missing, number the rows that have none and add the unique index. Returns rows numbered."""
    columns = {column["name"] for column in inspect(conn).get_columns("tb_item_kind")}
    if "c_item_kind_no" not in columns:
        conn.execute(text("ALTER TABLE tb_item_kind ADD COLUMN c_item_kind_no VARCHAR(20)"))
    pending = [row_id for (row_id,) in conn.execute(text(
        "SELECT i_item_kind_id FROM tb_item_kind WHERE c_item_kind_no IS NULL ORDER BY i_display_order, i_item_kind_id"
    ))]
    top = _top_number(conn)
    for offset, row_id in enumerate(pending, start=1):
        conn.execute(text("UPDATE tb_item_kind SET c_item_kind_no = :no WHERE i_item_kind_id = :id"),
                     {"no": item_kind_no(top + offset), "id": row_id})
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_tb_item_kind_c_item_kind_no ON tb_item_kind (c_item_kind_no)"
    ))
    return len(pending)


def migrate_item_kinds(engine) -> None:
    if not inspect(engine).has_table("tb_item_kind"):
        raise RuntimeError("Item Kind seed requires tb_item_kind.")
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE tb_item_kind IN SHARE ROW EXCLUSIVE MODE"))
        existing = {code for (code,) in conn.execute(text("SELECT c_item_kind_code FROM tb_item_kind"))}
        for order, (code, name) in enumerate(SYSTEM_ITEM_KINDS, start=1):
            if code in existing:
                continue
            conn.execute(text("""
                INSERT INTO tb_item_kind (c_item_kind_code, c_item_kind_name, i_display_order, b_active, dt_created, dt_updated)
                VALUES (:code, :name, :sort, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), {"code": code, "name": name, "sort": order * 10})
        migrate_item_kind_numbers(conn)
        # Legacy MD Item Kind fixed values are linked to System Codes; SERVICE already matches.
        if inspect(conn).has_table("tb_md_master"):
            conn.execute(text(
                "UPDATE tb_md_master SET c_item_kind = 'ITEM' WHERE c_item_kind IN ('HARDWARE', 'SOFTWARE')"
            ))
