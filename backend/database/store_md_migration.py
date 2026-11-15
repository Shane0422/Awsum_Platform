"""Store Override (SO-1a) schema: additive only, one transaction, safe to run again.

1. tb_md_detail: UNIQUE INDEX uq_tb_md_detail_id_md (i_md_detail_id, i_md_id), the target of the composite FK that
   keeps a Store price on a Detail of its own MD. i_md_detail_id is the PK, so no existing row can violate it; it is
   still checked first and the step stops instead of changing data. Only an index is added: no row or column changes.
2. New tables (CREATE TABLE only when missing): tb_store_md_price, tb_store_md (models_admin.store_md), with their
   composite FKs, unique keys, CHECKs and indexes. Both start empty (no row = the Merchant value applies).

The tb_store (i_store_id, i_merchant_id) and tb_md_master (i_md_id, i_owner_key) FK targets already exist
(employee_migration / md_owner_scope_migration run before this step). Runs from db_init_platform before the generic
schema step (which would otherwise try to create the tables before the tb_md_detail index exists). A new database
(tb_store / tb_md_master / tb_md_detail not created yet) is skipped here: the generic step creates every table, the
tb_md_detail index included (it is part of the model).
"""
from sqlalchemy import inspect, text

STORE_MD_TABLES = ("tb_store_md_price", "tb_store_md")
BASE_TABLES = ("tb_store", "tb_md_master", "tb_md_detail")  # FK targets; missing = new database (generic step)
DETAIL_INDEX = "uq_tb_md_detail_id_md"


def pending_store_md_schema(engine) -> dict:
    """Read-only: what migrate_store_md_schema would create."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not all(name in tables for name in BASE_TABLES):
        return {"tables": [], "detail_index": False}
    detail_index = "tb_md_detail" in tables and any(
        ix["name"] == DETAIL_INDEX for ix in inspector.get_indexes("tb_md_detail"))
    return {"tables": [name for name in STORE_MD_TABLES if name not in tables],
            "detail_index": "tb_md_detail" in tables and not detail_index}


def migrate_store_md_schema(engine) -> dict:
    from backend.database.pg_platform import PlatformBase
    import backend.models_admin.store_md  # noqa: F401  (register the tables on the metadata)

    pending = pending_store_md_schema(engine)
    result = {"created_tables": pending["tables"], "detail_index_created": pending["detail_index"]}
    if not pending["tables"] and not pending["detail_index"]:
        return result
    with engine.begin() as conn:
        if pending["detail_index"]:
            duplicates = conn.execute(text(
                "SELECT count(*) FROM (SELECT i_md_detail_id, i_md_id FROM tb_md_detail "
                "GROUP BY i_md_detail_id, i_md_id HAVING count(*) > 1) d")).scalar()
            if duplicates:
                raise RuntimeError(f"tb_md_detail has {duplicates} duplicate (i_md_detail_id, i_md_id) pairs; "
                                   "the Store Override schema was not created.")
            conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {DETAIL_INDEX} ON tb_md_detail (i_md_detail_id, i_md_id)"))
        tables = [PlatformBase.metadata.tables[name] for name in STORE_MD_TABLES]
        PlatformBase.metadata.create_all(conn, tables=tables, checkfirst=True)
    return result
