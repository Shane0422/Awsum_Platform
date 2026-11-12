"""DB-level guarantee that a non-empty barcode is used by only one MD Detail of the same owner
(partial UNIQUE on (i_owner_key, trim(c_barcode)); STEP 2 replaced the earlier system-wide index).
md_owner_scope_migration normally creates it (and drops the earlier index) in its own transaction; this step only
completes a database where duplicates blocked it before.

Existing duplicates are never deleted or changed. While any exist the index is not created; they are logged
(and returned) so they can be fixed by hand, and the service-level check still blocks new duplicates.
Repeatable: once the data is clean, the next run creates the index.
"""
import logging

from sqlalchemy import inspect, text

from backend.services.barcode import INDEX_WHERE, LEGACY_UNIQUE_INDEX, UNIQUE_INDEX, duplicate_barcodes

log = logging.getLogger(__name__)


def barcode_index_exists(conn, name: str = UNIQUE_INDEX) -> bool:
    # Catalog lookup: expression indexes are not reliably reflected by the SQLAlchemy inspector.
    if conn.dialect.name == "postgresql":
        sql = "SELECT 1 FROM pg_indexes WHERE tablename = 'tb_md_detail' AND indexname = :name"
    else:
        sql = "SELECT 1 FROM sqlite_master WHERE type = 'index' AND tbl_name = 'tb_md_detail' AND name = :name"
    return conn.execute(text(sql), {"name": name}).first() is not None


def migrate_md_barcode_unique(engine) -> list[tuple[str, str]]:
    with engine.begin() as conn:
        if not inspect(conn).has_table("tb_md_detail"):
            return []
        if barcode_index_exists(conn):
            return []
        if "i_owner_key" not in {c["name"] for c in inspect(conn).get_columns("tb_md_detail")}:
            return []  # owner scope not migrated yet (md_owner_scope_migration runs first)
        if conn.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE tb_md_detail IN SHARE ROW EXCLUSIVE MODE"))
        duplicates = duplicate_barcodes(conn)
        if duplicates:
            log.warning(
                "Barcode UNIQUE index %s not created: %d duplicated barcode(s) must be fixed manually: %s",
                UNIQUE_INDEX, len(duplicates), "; ".join(f"{code} -> {owners}" for code, owners in duplicates),
            )
            return duplicates
        conn.execute(text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {UNIQUE_INDEX} ON tb_md_detail (i_owner_key, (trim(c_barcode))) "
            f"WHERE {INDEX_WHERE}"
        ))
        conn.execute(text(f"DROP INDEX IF EXISTS {LEGACY_UNIQUE_INDEX}"))  # only once the per-owner index exists
        return []
