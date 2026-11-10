"""MD Detail Color (tb_md_detail.i_color_code_id -> tb_common_code COLOR) and the removal of the earlier Color
master (COLOR step 6).

Colors are COLOR codes of Common Maintenance. The earlier structure (tb_color, tb_md_detail.i_color_id and the
mapping column tb_common_code.i_legacy_id) only exists in databases created before step 6:

- While it exists, the normal (non-destructive) migration still moves its data over: tb_color rows not copied yet
  become COLOR codes (linked through i_legacy_id, never by equal ids) and empty i_color_code_id values are filled
  through that link. Nothing is dropped or overwritten here.
- drop_legacy_color() removes it, only inside the approved destructive migration
  (tools.migrate_platform --apply --allow-destructive, after a verified backup): every mapping is checked first
  (any problem stops it before anything changes), tb_color and the mapping are archived to JSON, the original
  creation record of each Color is kept on its COLOR code, then FK / index / columns / table are dropped in one
  transaction.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text

COLOR_CODE_FK = "tb_md_detail_i_color_code_id_fkey"
COLOR_CODE_INDEX = "ix_tb_md_detail_i_color_code_id"
LEGACY_LINK_INDEX = "ux_tb_common_code_legacy_id"
LEGACY_COLOR_INDEX = "ix_tb_md_detail_i_color_id"
COLOR_FIELDS = (("c_color_code", "c_code"), ("c_color_name", "c_name"), ("c_swatch_hex", "c_value_text"),
                ("i_display_order", "i_display_order"), ("b_active", "b_active"), ("c_notes", "c_notes"))


def migrate_md_detail_color_code(engine) -> None:
    """Before safe_schema_migrate: add tb_md_detail.i_color_code_id with its FK and index (that step adds columns
    without FKs). New database: create_all builds the column, FK and index from the model."""
    from backend.models_admin.common_code import CommonCode, CommonGroup

    with engine.begin() as conn:
        if not inspect(conn).has_table("tb_md_detail"):
            return
        CommonGroup.__table__.create(conn, checkfirst=True)
        CommonCode.__table__.create(conn, checkfirst=True)
        columns = {column["name"] for column in inspect(conn).get_columns("tb_md_detail")}
        postgres = conn.dialect.name == "postgresql"
        if "i_color_code_id" not in columns:
            guard = "IF NOT EXISTS " if postgres else ""
            reference = f"CONSTRAINT {COLOR_CODE_FK} " if postgres else ""
            conn.execute(text(f"ALTER TABLE tb_md_detail ADD COLUMN {guard}i_color_code_id INTEGER "
                              f"{reference}REFERENCES tb_common_code (i_common_code_id)"))
        elif postgres and not any(fk["constrained_columns"] == ["i_color_code_id"]
                                  for fk in inspect(conn).get_foreign_keys("tb_md_detail")):
            # The column exists without its FK (e.g. added by a generic step): add the FK; PostgreSQL validates
            # every existing value and refuses (stops the migration) if one does not reference tb_common_code.
            conn.execute(text(f"ALTER TABLE tb_md_detail ADD CONSTRAINT {COLOR_CODE_FK} FOREIGN KEY (i_color_code_id) "
                              "REFERENCES tb_common_code (i_common_code_id)"))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {COLOR_CODE_INDEX} ON tb_md_detail (i_color_code_id)"))


# ---------- earlier structure (databases created before step 6) ----------

def _columns(conn, table: str) -> set:
    inspector = inspect(conn)
    return {c["name"] for c in inspector.get_columns(table)} if inspector.has_table(table) else set()


def legacy_color_targets(conn) -> list[str]:
    """Read-only: the earlier Color objects drop_legacy_color() removes (data loss). Empty = nothing left."""
    targets = []
    if inspect(conn).has_table("tb_color"):
        targets.append("table tb_color")
    if "i_color_id" in _columns(conn, "tb_md_detail"):
        targets.append("column tb_md_detail.i_color_id")
    if "i_legacy_id" in _columns(conn, "tb_common_code"):
        targets.append("column tb_common_code.i_legacy_id")
    return targets


def copy_legacy_colors(conn, group_id: int) -> tuple[int, list[str]]:
    """tb_color rows not copied yet -> COLOR codes (same code, name, HEX, order, active, notes; i_legacy_id link).
    A code already used by another COLOR row is skipped with a warning (never overwritten). Additive only."""
    if not inspect(conn).has_table("tb_color"):
        return 0, []
    if "i_legacy_id" not in _columns(conn, "tb_common_code"):  # created from the current model: add the link back
        conn.execute(text("ALTER TABLE tb_common_code ADD COLUMN i_legacy_id INTEGER"))
    conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {LEGACY_LINK_INDEX} ON tb_common_code "
                      "(i_common_group_id, i_legacy_id) WHERE i_legacy_id IS NOT NULL"))
    copied_ids = {legacy for (legacy,) in conn.execute(text(
        "SELECT i_legacy_id FROM tb_common_code WHERE i_common_group_id = :g AND i_legacy_id IS NOT NULL"),
        {"g": group_id})}
    used_codes = {code for (code,) in conn.execute(text(
        "SELECT c_code FROM tb_common_code WHERE i_common_group_id = :g AND i_merchant_id IS NULL"), {"g": group_id})}
    copied, skipped = 0, []
    for row in conn.execute(text(
        "SELECT i_color_id, c_color_code, c_color_name, c_swatch_hex, i_display_order, b_active, c_notes, "
        "dt_created, i_created_by FROM tb_color ORDER BY i_color_id"
    )).mappings().all():
        if row["i_color_id"] in copied_ids:
            continue
        if row["c_color_code"] in used_codes:
            skipped.append(row["c_color_code"])
            continue
        conn.execute(text("""
            INSERT INTO tb_common_code (i_common_group_id, i_merchant_id, c_code, c_name, c_value_text, n_value,
                i_legacy_id, i_display_order, b_active, c_notes, dt_created, i_created_by, dt_updated)
            VALUES (:g, NULL, :code, :name, :swatch, NULL, :legacy, :sort, :active, :notes,
                COALESCE(:created, CURRENT_TIMESTAMP), :created_by, CURRENT_TIMESTAMP)
        """), {"g": group_id, "code": row["c_color_code"], "name": row["c_color_name"],
               "swatch": (row["c_swatch_hex"] or "").strip() or None, "legacy": row["i_color_id"],
               "sort": row["i_display_order"], "active": row["b_active"], "notes": row["c_notes"],
               "created": row["dt_created"], "created_by": row["i_created_by"]})
        used_codes.add(row["c_color_code"])
        copied += 1
    return copied, skipped


def color_code_mapping_problems(conn, group_id: int) -> dict:
    """Read-only checks of tb_md_detail.i_color_code_id against the COLOR legacy mapping."""
    def ids(sql):
        return [tuple(row) for row in conn.execute(text(sql), {"g": group_id})]

    mapped = ("SELECT c.i_common_code_id FROM tb_common_code c WHERE c.i_common_group_id = :g "
              "AND c.i_merchant_id IS NULL AND c.i_legacy_id = d.i_color_id")
    return {
        # A saved Color with no COLOR common code (not copied, e.g. a code conflict): left NULL, reported.
        "unmapped": ids(f"SELECT d.i_md_detail_id, d.i_color_id FROM tb_md_detail d WHERE d.i_color_id IS NOT NULL "
                        f"AND NOT EXISTS ({mapped}) ORDER BY d.i_md_detail_id"),
        # A filled value that differs from the mapping (or is set without i_color_id / outside COLOR): never fixed here.
        "mismatched": ids(f"SELECT d.i_md_detail_id, d.i_color_id, d.i_color_code_id FROM tb_md_detail d "
                          f"WHERE d.i_color_code_id IS NOT NULL AND (d.i_color_id IS NULL "
                          f"OR d.i_color_code_id IS DISTINCT FROM ({mapped})) ORDER BY d.i_md_detail_id"),
    }


def _color_group_id(conn):
    return conn.execute(text("SELECT i_common_group_id FROM tb_common_group WHERE c_group_code = 'COLOR'")).scalar()


def backfill_md_detail_color_codes(engine) -> dict:
    """Fill empty tb_md_detail.i_color_code_id from the COLOR mapping while i_color_id still exists; rerun-safe.

    Only NULL values with a mapped i_color_id are filled; nothing else changes. A filled value that disagrees with
    the mapping stops the migration with MigrationBlocked (no automatic repair); an unmapped Color is left NULL
    with a warning. No earlier structure (new database / after step 6): nothing to do.
    """
    from backend.database.migration_policy import MigrationBlocked

    empty = {"filled": 0, "unmapped": [], "mismatched": []}
    inspector = inspect(engine)
    if not (inspector.has_table("tb_md_detail") and inspector.has_table("tb_common_code")):
        return empty
    detail_columns = {c["name"] for c in inspector.get_columns("tb_md_detail")}
    if "i_color_id" not in detail_columns or "i_legacy_id" not in {c["name"] for c in inspector.get_columns("tb_common_code")}:
        return empty
    if "i_color_code_id" not in detail_columns:
        raise MigrationBlocked("tb_md_detail.i_color_code_id is missing; migrate_md_detail_color_code must run first.")
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE tb_md_detail, tb_common_code IN SHARE ROW EXCLUSIVE MODE"))
        group_id = _color_group_id(conn)
        if group_id is None:
            print("[COLOR][WARN] COLOR Common Group is missing; MD Detail Color codes were not filled.")
            return empty
        problems = color_code_mapping_problems(conn, group_id)
        if problems["mismatched"]:
            raise MigrationBlocked(
                "tb_md_detail.i_color_code_id differs from the COLOR mapping (i_md_detail_id, i_color_id, "
                f"i_color_code_id): {problems['mismatched']}. Nothing was changed; review before migrating again.")
        mapped = ("SELECT c.i_common_code_id FROM tb_common_code c WHERE c.i_common_group_id = :g "
                  "AND c.i_merchant_id IS NULL AND c.i_legacy_id = tb_md_detail.i_color_id")
        filled = conn.execute(text(
            f"UPDATE tb_md_detail SET i_color_code_id = ({mapped}) "
            f"WHERE i_color_id IS NOT NULL AND i_color_code_id IS NULL AND EXISTS ({mapped})"), {"g": group_id}).rowcount
    for detail_id, color_id in problems["unmapped"]:
        print(f"[COLOR][WARN] MD Detail {detail_id}: Color id {color_id} has no COLOR common code "
              "(tb_color row not copied, e.g. a code conflict); i_color_code_id left empty.")
    if filled:
        print(f"[COLOR] MD Detail Color codes filled from the COLOR mapping: {filled}")
    return {"filled": filled, "unmapped": problems["unmapped"], "mismatched": []}


def legacy_color_problems(conn) -> list[str]:
    """Read-only: reasons the earlier structure cannot be dropped without losing data (empty = safe to drop)."""
    columns = _columns(conn, "tb_common_code")
    group_id = _color_group_id(conn)
    if group_id is None:
        return ["COLOR Common Group is missing."]
    has_color = inspect(conn).has_table("tb_color")
    if has_color and "i_legacy_id" not in columns:
        return ["tb_common_code.i_legacy_id is missing: tb_color rows cannot be matched to COLOR codes."]
    problems = []
    if has_color:
        equal = " AND ".join(f"k.{a} IS NOT DISTINCT FROM c.{b}" for a, b in COLOR_FIELDS if a != "c_swatch_hex")
        rows = conn.execute(text(
            "SELECT k.i_color_id, k.c_color_code, c.i_common_code_id, "
            f"CASE WHEN c.i_common_code_id IS NULL THEN NULL ELSE ({equal} AND "
            "NULLIF(TRIM(COALESCE(k.c_swatch_hex, '')), '') IS NOT DISTINCT FROM c.c_value_text) END "
            "FROM tb_color k LEFT JOIN tb_common_code c ON c.i_common_group_id = :g AND c.i_merchant_id IS NULL "
            "AND c.i_legacy_id = k.i_color_id ORDER BY k.i_color_id"), {"g": group_id}).all()
        unmapped = [f"{r[0]}:{r[1]}" for r in rows if r[2] is None]
        different = [f"{r[0]}:{r[1]}->{r[2]}" for r in rows if r[2] is not None and not r[3]]
        if unmapped:
            problems.append(f"tb_color rows without a COLOR code: {unmapped}")
        if different:
            problems.append(f"tb_color rows that differ from their COLOR code: {different}")
    if "i_color_id" in _columns(conn, "tb_md_detail"):
        if "i_legacy_id" not in columns:
            missing = conn.execute(text("SELECT count(*) FROM tb_md_detail WHERE i_color_id IS NOT NULL")).scalar()
            if missing:
                problems.append(f"{missing} MD Details have i_color_id but no mapping column exists.")
        else:
            mapping = color_code_mapping_problems(conn, group_id)
            empty = [tuple(r) for r in conn.execute(text(
                "SELECT i_md_detail_id, i_color_id FROM tb_md_detail WHERE i_color_id IS NOT NULL "
                "AND i_color_code_id IS NULL ORDER BY 1"))]
            if mapping["unmapped"] or empty:
                problems.append(f"MD Details whose Color has no COLOR code: {sorted(set(mapping['unmapped'] + empty))}")
            if mapping["mismatched"]:
                problems.append(f"MD Details whose color_code_id differs from i_color_id: {mapping['mismatched']}")
    return problems


def _snapshot(conn, sql, params=None) -> list[dict]:
    return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
            for row in conn.execute(text(sql), params or {}).mappings()]


def drop_legacy_color(engine, archive_dir) -> dict:
    """COLOR step 6 (destructive; only from the approved migration): drop tb_color, tb_md_detail.i_color_id and
    tb_common_code.i_legacy_id with their FK / indexes, after checking and archiving every mapping.

    One transaction: any problem (unmapped / different Color, mismatched MD Detail, an unexpected dependency)
    raises before or during the drops and nothing changes. The archive JSON is written before the drops.
    The original creation record (dt_created, i_created_by) of each tb_color row replaces the COLOR code's
    (that record is only the step-2 copy time; it is archived too). dt_updated / i_updated_by are not touched.
    """
    from backend.database.migration_policy import MigrationBlocked

    with engine.begin() as conn:
        targets = legacy_color_targets(conn)
        if not targets:
            return {"dropped": [], "archive": None}
        postgres = conn.dialect.name == "postgresql"
        if postgres:
            present = [t for t in ("tb_color", "tb_md_detail", "tb_common_code") if inspect(conn).has_table(t)]
            conn.execute(text(f"LOCK TABLE {', '.join(present)} IN ACCESS EXCLUSIVE MODE"))
        problems = legacy_color_problems(conn)
        if problems:
            raise MigrationBlocked("COLOR legacy removal stopped; nothing was changed: " + " | ".join(problems))
        group_id = _color_group_id(conn)
        has_color = "table tb_color" in targets
        has_link = "column tb_common_code.i_legacy_id" in targets
        has_detail_color = "column tb_md_detail.i_color_id" in targets
        color_codes_sql = ("SELECT i_common_code_id, c_code, c_name, c_value_text, i_display_order, b_active, c_notes, "
                           "c_description, dt_updated, i_updated_by FROM tb_common_code WHERE i_common_group_id = :g "
                           "ORDER BY i_common_code_id")
        details_sql = "SELECT i_md_detail_id, i_color_code_id FROM tb_md_detail ORDER BY i_md_detail_id"
        codes_before = _snapshot(conn, color_codes_sql, {"g": group_id})
        details_before = _snapshot(conn, details_sql)
        archive = {
            "kind": "awsum-color-legacy-archive", "created_at": datetime.now().isoformat(), "targets": targets,
            "tb_color": _snapshot(conn, "SELECT * FROM tb_color ORDER BY i_color_id") if has_color else [],
            # i_color_id -> i_common_code_id (the mapping removed with i_legacy_id) and the COLOR code audit replaced.
            "mapping": _snapshot(conn, "SELECT i_legacy_id AS i_color_id, i_common_code_id, c_code, dt_created, "
                                       "i_created_by FROM tb_common_code WHERE i_common_group_id = :g AND "
                                       "i_legacy_id IS NOT NULL ORDER BY i_legacy_id", {"g": group_id}) if has_link else [],
            "md_detail_colors": _snapshot(conn, "SELECT i_md_detail_id, i_color_id, i_color_code_id FROM tb_md_detail "
                                                "ORDER BY i_md_detail_id") if has_detail_color else [],
        }
        folder = Path(archive_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"color-legacy-archive-{datetime.now():%Y%m%d-%H%M%S}.json"
        payload = json.dumps(archive, indent=2, ensure_ascii=False, default=str)
        path.write_text(payload, encoding="utf-8")
        audit = 0
        if has_color and has_link:
            audit = conn.execute(text(
                "UPDATE tb_common_code SET dt_created = (SELECT k.dt_created FROM tb_color k WHERE k.i_color_id = "
                "tb_common_code.i_legacy_id), i_created_by = (SELECT k.i_created_by FROM tb_color k WHERE "
                "k.i_color_id = tb_common_code.i_legacy_id) WHERE i_common_group_id = :g AND i_legacy_id IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM tb_color k WHERE k.i_color_id = tb_common_code.i_legacy_id AND "
                "(k.dt_created IS DISTINCT FROM tb_common_code.dt_created OR k.i_created_by IS DISTINCT FROM "
                "tb_common_code.i_created_by))"), {"g": group_id}).rowcount
        if has_detail_color:
            if postgres:
                for fk in inspect(conn).get_foreign_keys("tb_md_detail"):
                    if fk["constrained_columns"] == ["i_color_id"]:
                        conn.execute(text(f'ALTER TABLE tb_md_detail DROP CONSTRAINT "{fk["name"]}"'))
            conn.execute(text(f"DROP INDEX IF EXISTS {LEGACY_COLOR_INDEX}"))
            conn.execute(text("ALTER TABLE tb_md_detail DROP COLUMN i_color_id"))
        if has_link:
            conn.execute(text(f"DROP INDEX IF EXISTS {LEGACY_LINK_INDEX}"))
            conn.execute(text("ALTER TABLE tb_common_code DROP COLUMN i_legacy_id"))
        if has_color:
            conn.execute(text("DROP TABLE tb_color"))  # no CASCADE: an unexpected dependency stops everything
        if _snapshot(conn, color_codes_sql, {"g": group_id}) != codes_before or _snapshot(conn, details_sql) != details_before:
            raise MigrationBlocked("COLOR codes or MD Detail Colors changed during the legacy removal; rolled back.")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    print(f"[COLOR] legacy removed: {', '.join(targets)}; creation records kept on {audit} COLOR codes; "
          f"archive {path} (sha256 {digest})")
    return {"dropped": targets, "archive": str(path), "sha256": digest, "audit_restored": audit}
