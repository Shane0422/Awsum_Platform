"""Employee Management schema: additive only, one transaction per step, safe to run again.

STEP 1A
1. tb_store: UNIQUE INDEX uq_tb_store_id_merchant (i_store_id, i_merchant_id), the target of the composite FKs that
   keep an Employee and its Stores in the same Merchant. i_store_id is the PK, so no existing row can violate it;
   it is still checked first and the step stops instead of changing data.
2. New tables (CREATE TABLE only when missing): tb_portal_role, tb_portal_role_permission, tb_employee,
   tb_employee_store, tb_employee_policy, with their constraints and partial unique indexes.

STEP 1A-2 (login IDs + address, on an existing tb_employee)
3. tb_employee: ADD COLUMN only for the missing ones (address: c_address_line1/2, c_city, c_state, c_zip, c_country;
   POS Login: b_pos_login_enabled (false), c_pos_login_id (NULL)). Existing rows keep every value; their POS Login
   stays disabled with no ID.
4. UNIQUE INDEX uq_tb_employee_pos_login (i_merchant_id, c_pos_login_id) and, on PostgreSQL, the CHECK constraints
   ck_tb_employee_pos_login_id / ck_tb_employee_pos_login (new columns are empty, so no row can violate them).

No existing value is changed or removed. The tb_role "Employee" row is seeded by db_init_platform (default roles).
Runs from db_init_platform before the generic schema step (which would otherwise create tb_employee_store before the
tb_store index exists).
"""
from sqlalchemy import inspect, text

EMPLOYEE_TABLES = ("tb_portal_role", "tb_portal_role_permission", "tb_employee", "tb_employee_store",
                   "tb_employee_policy")
STORE_INDEX = "uq_tb_store_id_merchant"
# STEP 1A-2 columns: name -> DDL type (portable PostgreSQL / SQLite).
EMPLOYEE_LOGIN_COLUMNS = {
    "c_address_line1": "VARCHAR", "c_address_line2": "VARCHAR", "c_city": "VARCHAR", "c_state": "VARCHAR",
    "c_zip": "VARCHAR", "c_country": "VARCHAR",
    "b_pos_login_enabled": "BOOLEAN NOT NULL DEFAULT false",
    "c_pos_login_id": "VARCHAR(10)",
}
POS_LOGIN_INDEX = "uq_tb_employee_pos_login"
POS_LOGIN_CHECKS = {
    "ck_tb_employee_pos_login_id": "c_pos_login_id IS NULL OR length(c_pos_login_id) BETWEEN 4 AND 10",
    "ck_tb_employee_pos_login": "NOT b_pos_login_enabled OR c_pos_login_id IS NOT NULL",
}


def pending_employee_schema(engine) -> dict:
    """Read-only: what migrate_employee_schema would create."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    store_index = "tb_store" in tables and any(
        ix["name"] == STORE_INDEX for ix in inspector.get_indexes("tb_store"))
    columns, login_index = [], False
    if "tb_employee" in tables:
        present = {column["name"] for column in inspector.get_columns("tb_employee")}
        columns = [name for name in EMPLOYEE_LOGIN_COLUMNS if name not in present]
        login_index = not any(ix["name"] == POS_LOGIN_INDEX for ix in inspector.get_indexes("tb_employee"))
    return {"tables": [name for name in EMPLOYEE_TABLES if name not in tables], "store_index": not store_index,
            "employee_columns": columns, "pos_login_index": login_index}


def _migrate_login_schema(conn, pending: dict) -> None:
    """STEP 1A-2 on an existing tb_employee: add the missing columns, then the index and CHECKs."""
    for name in pending["employee_columns"]:
        conn.execute(text(f"ALTER TABLE tb_employee ADD COLUMN {name} {EMPLOYEE_LOGIN_COLUMNS[name]}"))
    if pending["pos_login_index"]:
        duplicates = conn.execute(text(
            "SELECT count(*) FROM (SELECT i_merchant_id, c_pos_login_id FROM tb_employee WHERE c_pos_login_id IS NOT NULL "
            "GROUP BY i_merchant_id, c_pos_login_id HAVING count(*) > 1) d")).scalar()
        if duplicates:
            raise RuntimeError(f"tb_employee has {duplicates} duplicate POS Login IDs in a Merchant; nothing was changed.")
        conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {POS_LOGIN_INDEX} "
                          "ON tb_employee (i_merchant_id, c_pos_login_id)"))
    if conn.dialect.name == "postgresql":  # SQLite cannot add a CHECK to an existing table (new tables have them)
        existing = set(conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'tb_employee'::regclass")).scalars())
        for name, condition in POS_LOGIN_CHECKS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE tb_employee ADD CONSTRAINT {name} CHECK ({condition})"))


def migrate_employee_schema(engine) -> dict:
    from backend.database.pg_platform import PlatformBase
    import backend.models_admin.employee  # noqa: F401  (register the tables on the metadata)
    import backend.models_admin.portal_role  # noqa: F401

    pending = pending_employee_schema(engine)
    result = {"created_tables": pending["tables"], "store_index_created": pending["store_index"],
              "added_employee_columns": pending["employee_columns"],
              "pos_login_index_created": pending["pos_login_index"]}
    if not pending["tables"] and not pending["store_index"]:
        if pending["employee_columns"] or pending["pos_login_index"]:
            with engine.begin() as conn:
                _migrate_login_schema(conn, pending)
        return result
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
        if "tb_employee" not in pending["tables"] and (pending["employee_columns"] or pending["pos_login_index"]):
            _migrate_login_schema(conn, pending)
    if "tb_employee" in pending["tables"]:  # created with every column / index / CHECK by create_all
        result.update(added_employee_columns=[], pos_login_index_created=False)
    return result
