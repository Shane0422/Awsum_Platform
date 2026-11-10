"""Location Management schema for databases created before it (runs before safe_schema_migrate).

- tb_store_location: c_location_area (NOT NULL, default SALES_FLOOR) and the (id, store, area) unique key.
  The earlier Location Type and parent tree are removed only by drop_location_hierarchy() (destructive; approved
  migration only). Until then c_location_type gets a column default so new rows (which no longer send it) save.
- tb_store_md_location: c_location_area copied from the linked location, the (location, store, area) foreign key,
  and one Primary per Store + Detail + Area (replaces the earlier one-Primary-per-Detail index).
- tb_store_category3_location is new and comes from create_all.

Existing rows keep every value; added columns get their default (links take their location's area).
Every step is guarded, so a rerun changes nothing. New databases skip this (create_all builds the tables).
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text

LOCATION_PARENT_FK = "fk_tb_store_location_parent"
LOCATION_PARENT_INDEX = "ix_tb_store_location_i_parent_location_id"
LOCATION_ID_STORE_KEY = "uq_tb_store_location_id_store"  # only the parent FK referenced it


def migrate_recommended_locations(engine) -> None:
    """Common recommended positions on Category III / MD Master: nullable text columns only (existing rows stay NULL)."""
    with engine.begin() as conn:
        insp = inspect(conn)
        guard = "IF NOT EXISTS " if conn.dialect.name == "postgresql" else ""
        for table in ("tb_category3", "tb_md_master"):
            if not insp.has_table(table):
                continue  # new database: create_all adds the columns
            columns = {c["name"] for c in insp.get_columns(table)}
            for column in ("c_sales_location", "c_warehouse_location"):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {guard}{column} VARCHAR(50)"))


def _constraint_exists(conn, name: str) -> bool:
    return conn.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": name}).first() is not None


def migrate_store_locations(engine) -> None:
    with engine.begin() as conn:
        insp = inspect(conn)
        if not insp.has_table("tb_store_location"):
            return
        pg = conn.dialect.name == "postgresql"
        guard = "IF NOT EXISTS " if pg else ""
        location_cols = {c["name"] for c in insp.get_columns("tb_store_location")}
        if "c_location_area" not in location_cols:
            conn.execute(text(f"ALTER TABLE tb_store_location ADD COLUMN {guard}c_location_area VARCHAR(20) "
                              "NOT NULL DEFAULT 'SALES_FLOOR'"))
        if pg:
            if not _constraint_exists(conn, "uq_tb_store_location_id_store_area"):
                conn.execute(text("ALTER TABLE tb_store_location ADD CONSTRAINT uq_tb_store_location_id_store_area "
                                  "UNIQUE (i_location_id, i_store_id, c_location_area)"))
            if "c_location_type" in location_cols:
                # Not dropped yet (needs the approved migration): new rows no longer send it. Additive only.
                conn.execute(text("ALTER TABLE tb_store_location ALTER COLUMN c_location_type SET DEFAULT 'SHELF'"))

        if not insp.has_table("tb_store_md_location"):
            return
        link_cols = {c["name"] for c in insp.get_columns("tb_store_md_location")}
        if "c_location_area" not in link_cols:
            conn.execute(text(f"ALTER TABLE tb_store_md_location ADD COLUMN {guard}c_location_area VARCHAR(20) "
                              "NOT NULL DEFAULT 'SALES_FLOOR'"))
            # Existing links take the area of their location (their location's own value, nothing invented).
            conn.execute(text("UPDATE tb_store_md_location SET c_location_area = ("
                              "SELECT s.c_location_area FROM tb_store_location s "
                              "WHERE s.i_location_id = tb_store_md_location.i_location_id)"))
        if pg:
            if not _constraint_exists(conn, "fk_tb_store_md_location_location_area"):
                conn.execute(text("ALTER TABLE tb_store_md_location ADD CONSTRAINT fk_tb_store_md_location_location_area "
                                  "FOREIGN KEY (i_location_id, i_store_id, c_location_area) REFERENCES "
                                  "tb_store_location (i_location_id, i_store_id, c_location_area)"))
            # The (location, store, area) key above implies the earlier (location, store) key.
            conn.execute(text("ALTER TABLE tb_store_md_location DROP CONSTRAINT IF EXISTS "
                              "fk_tb_store_md_location_location_store"))
        conn.execute(text("DROP INDEX IF EXISTS uq_tb_store_md_location_primary"))
        where = "b_is_primary = true" if pg else "b_is_primary = 1"
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_tb_store_md_location_area_primary "
                          f"ON tb_store_md_location (i_store_id, i_md_detail_id, c_location_area) WHERE {where}"))


# ---------- Location Type / parent tree removal (destructive; approved migration only) ----------

def legacy_location_targets(conn) -> list[str]:
    """Read-only: the earlier Location Type / parent tree objects drop_location_hierarchy() removes."""
    insp = inspect(conn)
    if not insp.has_table("tb_store_location"):
        return []
    columns = {c["name"] for c in insp.get_columns("tb_store_location")}
    return [f"column tb_store_location.{name}" for name in ("i_parent_location_id", "c_location_type") if name in columns]


def _location_snapshot(conn) -> list[dict]:
    """Everything that must not change (code / name / area / Store / order / status / notes / audit)."""
    return [dict(row) for row in conn.execute(text(
        "SELECT i_location_id, i_store_id, c_location_code, c_location_name, c_location_area, i_display_order, "
        "b_active, c_notes, c_zone, c_aisle, c_bay, c_level, dt_created, i_created_by, dt_updated, i_updated_by "
        "FROM tb_store_location ORDER BY i_location_id")).mappings()]


def drop_location_hierarchy(engine, archive_dir) -> dict:
    """Drop tb_store_location.i_parent_location_id (with its FK and index), c_location_type and the (id, store)
    unique key that only the parent FK used.

    One transaction under an exclusive lock: every row's parent / type is archived to JSON first; the remaining
    columns (Store, code, name, area, order, status, notes, audit) are compared before and after, and any
    difference or unexpected dependency (no CASCADE) rolls everything back. The Store + Location Code unique
    index and the (id, store, area) key used by the Area foreign keys stay.
    """
    from backend.database.migration_policy import MigrationBlocked

    with engine.begin() as conn:
        targets = legacy_location_targets(conn)
        if not targets:
            return {"dropped": [], "archive": None}
        postgres = conn.dialect.name == "postgresql"
        if postgres:
            conn.execute(text("LOCK TABLE tb_store_location IN ACCESS EXCLUSIVE MODE"))
        columns = {c["name"] for c in inspect(conn).get_columns("tb_store_location")}
        has_parent, has_type = "i_parent_location_id" in columns, "c_location_type" in columns
        before = _location_snapshot(conn)
        parent_sql = ("p.c_location_code AS parent_code, s.i_parent_location_id" if has_parent
                      else "NULL AS parent_code, NULL AS i_parent_location_id")
        join = " LEFT JOIN tb_store_location p ON p.i_location_id = s.i_parent_location_id" if has_parent else ""
        archive = {
            "kind": "awsum-location-hierarchy-archive", "created_at": datetime.now().isoformat(), "targets": targets,
            "rows": [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
                     for row in conn.execute(text(
                         f"SELECT s.i_location_id, s.i_store_id, s.c_location_code, {parent_sql}, "
                         f"{'s.c_location_type' if has_type else 'NULL'} AS c_location_type "
                         f"FROM tb_store_location s{join} ORDER BY s.i_location_id")).mappings()],
        }
        folder = Path(archive_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"location-hierarchy-archive-{datetime.now():%Y%m%d-%H%M%S}.json"
        payload = json.dumps(archive, indent=2, ensure_ascii=False, default=str)
        path.write_text(payload, encoding="utf-8")
        if has_parent:
            if postgres:
                conn.execute(text(f"ALTER TABLE tb_store_location DROP CONSTRAINT IF EXISTS {LOCATION_PARENT_FK}"))
            conn.execute(text(f"DROP INDEX IF EXISTS {LOCATION_PARENT_INDEX}"))
            conn.execute(text("ALTER TABLE tb_store_location DROP COLUMN i_parent_location_id"))
        if has_type:
            conn.execute(text("ALTER TABLE tb_store_location DROP COLUMN c_location_type"))
        if postgres and _constraint_exists(conn, LOCATION_ID_STORE_KEY):
            # No CASCADE: if another foreign key still used this key, PostgreSQL refuses and nothing changes.
            conn.execute(text(f"ALTER TABLE tb_store_location DROP CONSTRAINT {LOCATION_ID_STORE_KEY}"))
        if _location_snapshot(conn) != before:
            raise MigrationBlocked("Location data changed during the Location Type / parent removal; rolled back.")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    print(f"[LOCATION] removed: {', '.join(targets)}; {len(archive['rows'])} rows archived to {path} (sha256 {digest})")
    return {"dropped": targets, "archive": str(path), "sha256": digest, "rows": len(archive["rows"])}
