"""Common Maintenance seed: COLOR and UNIT groups in tb_common_group / tb_common_code.

Additive and rerun-safe; nothing existing is ever updated or deleted:
- Groups are inserted only when their c_group_code is missing (an existing group is never overwritten).
- COLOR: the default colors are inserted only into an empty COLOR group of a database without the earlier
  tb_color (a new installation). A database created before COLOR step 6 still has tb_color: its rows not yet
  copied become COLOR codes instead (backend.database.color_migration.copy_legacy_colors).
- UNIT: the approved initial list is inserted only into an empty UNIT group.
So codes an admin later renames, deactivates or deletes are never brought back or overwritten.
SIZE and ITEM_KIND are not Common Maintenance groups.
"""
from sqlalchemy import inspect, text

from backend.database.color_migration import copy_legacy_colors

COMMON_GROUPS = (
    # code, name, code mode, prefix, max length, value type, value label, display order
    ("COLOR", "Color", "AUTO", "CLR", 20, "COLOR", "Swatch HEX", 10),
    ("UNIT", "Unit", "MANUAL", None, 30, None, None, 20),
)
DEFAULT_COLORS = (
    ("Black", "#000000"),
    ("White", "#FFFFFF"),
    ("Gray", "#808080"),
    ("Navy", "#000080"),
    ("Blue", "#0000FF"),
    ("Red", "#FF0000"),
    ("Green", "#008000"),
    ("Yellow", "#FFFF00"),
    ("Brown", "#8B4513"),
    ("Beige", "#F5F5DC"),
    ("Pink", "#FFC0CB"),
    ("Purple", "#800080"),
    ("Orange", "#FFA500"),
)
UNIT_CODES = (
    ("EA", "Each"), ("PK", "Pack"), ("BOX", "Box"), ("CS", "Case"), ("DZ", "Dozen"),
    ("LB", "Pound"), ("OZ", "Ounce"), ("KG", "Kilogram"), ("G", "Gram"),
    ("GAL", "Gallon"), ("L", "Liter"), ("ML", "Milliliter"), ("HR", "Hour"), ("DAY", "Day"),
)


def _group_ids(conn) -> dict:
    return {code: group_id for code, group_id in conn.execute(text(
        "SELECT c_group_code, i_common_group_id FROM tb_common_group"))}


def _seed_groups(conn) -> list[str]:
    existing = _group_ids(conn)
    added = []
    for code, name, mode, prefix, max_length, value_type, value_label, order in COMMON_GROUPS:
        if code in existing:
            continue
        conn.execute(text("""
            INSERT INTO tb_common_group (c_group_code, c_group_name, c_code_mode, c_code_prefix, i_code_max_length,
                c_value_type, c_value_label, b_value_required, b_merchant_extendable, i_display_order, b_active,
                dt_created, dt_updated)
            VALUES (:code, :name, :mode, :prefix, :max_length, :value_type, :value_label, FALSE, FALSE, :sort, TRUE,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {"code": code, "name": name, "mode": mode, "prefix": prefix, "max_length": max_length,
               "value_type": value_type, "value_label": value_label, "sort": order})
        added.append(code)
    return added


def _seed_colors(conn, group_id: int) -> int:
    """Default colors (CLR00001 Black ...) directly as COLOR codes: only into an empty COLOR group."""
    if conn.execute(text("SELECT 1 FROM tb_common_code WHERE i_common_group_id = :g LIMIT 1"), {"g": group_id}).first():
        return 0
    for order, (name, swatch) in enumerate(DEFAULT_COLORS, start=1):
        conn.execute(text("""
            INSERT INTO tb_common_code (i_common_group_id, i_merchant_id, c_code, c_name, c_value_text, i_display_order,
                b_active, dt_created, dt_updated)
            VALUES (:g, NULL, :code, :name, :swatch, :sort, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {"g": group_id, "code": f"CLR{order:05d}", "name": name, "swatch": swatch, "sort": order * 10})
    return len(DEFAULT_COLORS)


def _seed_units(conn, group_id: int) -> int:
    if conn.execute(text("SELECT 1 FROM tb_common_code WHERE i_common_group_id = :g LIMIT 1"), {"g": group_id}).first():
        return 0
    for order, (code, name) in enumerate(UNIT_CODES, start=1):
        conn.execute(text("""
            INSERT INTO tb_common_code (i_common_group_id, i_merchant_id, c_code, c_name, i_display_order, b_active,
                dt_created, dt_updated)
            VALUES (:g, NULL, :code, :name, :sort, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {"g": group_id, "code": code, "name": name, "sort": order * 10})
    return len(UNIT_CODES)


def migrate_common_codes(engine) -> dict:
    """Seed the COLOR / UNIT groups and codes (runs after safe_schema_migrate created the tables)."""
    inspector = inspect(engine)
    if not (inspector.has_table("tb_common_group") and inspector.has_table("tb_common_code")):
        raise RuntimeError("Common Maintenance seed requires tb_common_group and tb_common_code.")
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE tb_common_group, tb_common_code IN SHARE ROW EXCLUSIVE MODE"))
        groups = _seed_groups(conn)
        ids = _group_ids(conn)
        if inspect(conn).has_table("tb_color"):  # created before COLOR step 6: copy, never seed defaults
            colors, skipped = copy_legacy_colors(conn, ids["COLOR"])
        else:
            colors, skipped = _seed_colors(conn, ids["COLOR"]), []
        units = _seed_units(conn, ids["UNIT"])
    for code in skipped:
        print(f"[COMMON][WARN] COLOR code {code} already exists with another source row; tb_color row not copied.")
    if groups or colors or units:
        print(f"[COMMON] groups added: {groups or '-'}; COLOR added: {colors}; UNIT seeded: {units}")
    return {"groups": groups, "colors": colors, "units": units, "skipped_colors": skipped}
