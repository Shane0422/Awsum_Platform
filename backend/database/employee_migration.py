"""Employee Management STEP 1A schema: additive only, one transaction, safe to run again.

1. tb_store: UNIQUE INDEX uq_tb_store_id_merchant (i_store_id, i_merchant_id), the target of the composite FKs that
   keep an Employee and its Stores in the same Merchant. i_store_id is the PK, so no existing row can violate it;
   it is still checked first and the step stops instead of changing data.
2. New tables (CREATE TABLE only when missing): tb_portal_role, tb_portal_role_permission, tb_employee,
   tb_employee_store, tb_employee_policy, with their constraints and partial unique indexes.

No existing table, column or row is changed, and nothing is seeded (the tb_role 'Employee' row comes in STEP 1C).
Runs from db_init_platform before the generic schema step (which would otherwise create tb_employee_store before the
tb_store index exists).
"""
from sqlalchemy import inspect, text

EMPLOYEE_TABLES = ("tb_portal_role", "tb_portal_role_permission", "tb_employee", "tb_employee_store",
                   "tb_employee_policy")
STORE_INDEX = "uq_tb_store_id_merchant"


def pending_employee_schema(engine) -> dict:
    """Read-only: what migrate_employee_schema would create."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    store_index = "tb_store" in tables and any(
        ix["name"] == STORE_INDEX for ix in inspector.get_indexes("tb_store"))
    return {"tables": [name for name in EMPLOYEE_TABLES if name not in tables], "store_index": not store_index}


def migrate_employee_schema(engine) -> dict:
    from backend.database.pg_platform import PlatformBase
    import backend.models_admin.employee  # noqa: F401  (register the tables on the metadata)
    import backend.models_admin.portal_role  # noqa: F401

    pending = pending_employee_schema(engine)
    if not pending["tables"] and not pending["store_index"]:
        return {"created_tables": [], "store_index_created": False}
    with engine.begin() as conn:
        if pending["store_index"]:
            duplicates = conn.execute(text(
                "SELECT count(*) FROM (SELECT i_store_id, i_merchant_id FROM tb_store "
                "GROUP BY i_store_id, i_merchant_id HAVING count(*) > 1) d")).scalar()
            if duplicates:
                raise RuntimeError(f"tb_store has {duplicates} duplicate (i_store_id, i_merchant_id) pairs; "
                                   "the Employee schema was not created.")
            conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {STORE_INDEX} ON tb_store (i_store_id, i_merchant_id)"))
        tables = [PlatformBase.metadata.tables[name] for name in EMPLOYEE_TABLES]
        PlatformBase.metadata.create_all(conn, tables=tables, checkfirst=True)
    return {"created_tables": pending["tables"], "store_index_created": pending["store_index"]}
