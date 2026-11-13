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

STEP 1A-3 (Job Grade / Job Title, on an existing tb_employee)
5. tb_employee: ADD COLUMN i_job_grade_code_id / i_job_title_code_id (INTEGER NULL) referencing
   tb_common_code (i_common_code_id) ON DELETE RESTRICT (fk_tb_employee_job_grade_code / fk_tb_employee_job_title_code)
   and their indexes. A column that exists without its FK (e.g. added by the generic step) gets the FK on PostgreSQL,
   which validates every existing value. New columns are NULL for every existing row. tb_common_group /
   tb_common_code are created first when missing (new installation), as for the MD Detail Color FK.
6. tb_employee: ADD COLUMN d_birth_date (DATE NULL) and c_gender (VARCHAR(20) NULL), and on PostgreSQL the CHECK
   ck_tb_employee_gender (the new column is empty, so no row can violate it). No index (never searched or sorted).

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
# STEP 1A-3 columns: name -> (FK constraint name, index name); all INTEGER NULL -> tb_common_code.
JOB_COLUMNS = {
    "i_job_grade_code_id": ("fk_tb_employee_job_grade_code", "ix_tb_employee_i_job_grade_code_id"),
    "i_job_title_code_id": ("fk_tb_employee_job_title_code", "ix_tb_employee_i_job_title_code_id"),
}
# STEP 1A-3 Personal Information columns: name -> DDL type.
PERSONAL_COLUMNS = {"d_birth_date": "DATE", "c_gender": "VARCHAR(20)"}
GENDER_CHECK_NAME = "ck_tb_employee_gender"
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
    job_columns, job_fks, job_indexes = [], [], []
    personal_columns, gender_check = [], False
    if "tb_employee" in tables:
        present = {column["name"] for column in inspector.get_columns("tb_employee")}
        columns = [name for name in EMPLOYEE_LOGIN_COLUMNS if name not in present]
        indexes = {ix["name"] for ix in inspector.get_indexes("tb_employee")}
        login_index = POS_LOGIN_INDEX not in indexes
        job_columns = [name for name in JOB_COLUMNS if name not in present]
        if engine.dialect.name == "postgresql":  # SQLite cannot add an FK to an existing column
            linked = {tuple(fk["constrained_columns"]) for fk in inspector.get_foreign_keys("tb_employee")}
            job_fks = [name for name in JOB_COLUMNS if name in present and (name,) not in linked]
        job_indexes = [name for name, (_fk, index) in JOB_COLUMNS.items() if index not in indexes]
        personal_columns = [name for name in PERSONAL_COLUMNS if name not in present]
        if engine.dialect.name == "postgresql":  # SQLite cannot add a CHECK to an existing table
            gender_check = GENDER_CHECK_NAME not in {ck["name"] for ck in inspector.get_check_constraints("tb_employee")}
    return {"tables": [name for name in EMPLOYEE_TABLES if name not in tables], "store_index": not store_index,
            "employee_columns": columns, "pos_login_index": login_index,
            "job_columns": job_columns, "job_fks": job_fks, "job_indexes": job_indexes,
            "personal_columns": personal_columns, "gender_check": gender_check}


def _job_pending(pending: dict) -> bool:
    """Anything of STEP 1A-3 (Job Grade / Job Title, Personal Information) still missing."""
    return bool(pending["job_columns"] or pending["job_fks"] or pending["job_indexes"]
                or pending["personal_columns"] or pending["gender_check"])


def _ensure_common_tables(conn) -> None:
    """tb_common_group / tb_common_code (the FK target) on a new installation, before any reference to them."""
    from backend.models_admin.common_code import CommonCode, CommonGroup
    CommonGroup.__table__.create(conn, checkfirst=True)
    CommonCode.__table__.create(conn, checkfirst=True)


def _migrate_job_schema(conn, pending: dict) -> None:
    """STEP 1A-3 on an existing tb_employee: the Job Grade / Job Title columns with their FKs and indexes, and the
    Personal Information columns (Birthday, Gender + its CHECK on PostgreSQL)."""
    _ensure_common_tables(conn)
    postgres = conn.dialect.name == "postgresql"
    for name in pending["job_columns"]:
        fk_name, _index = JOB_COLUMNS[name]
        reference = f"CONSTRAINT {fk_name} " if postgres else ""
        conn.execute(text(f"ALTER TABLE tb_employee ADD COLUMN {name} INTEGER "
                          f"{reference}REFERENCES tb_common_code (i_common_code_id) ON DELETE RESTRICT"))
    for name in pending["job_fks"]:  # PostgreSQL validates every existing value (stops the step if one is invalid)
        fk_name, _index = JOB_COLUMNS[name]
        conn.execute(text(f"ALTER TABLE tb_employee ADD CONSTRAINT {fk_name} FOREIGN KEY ({name}) "
                          "REFERENCES tb_common_code (i_common_code_id) ON DELETE RESTRICT"))
    for name in pending["job_indexes"]:
        _fk, index = JOB_COLUMNS[name]
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index} ON tb_employee ({name})"))
    for name in pending["personal_columns"]:
        conn.execute(text(f"ALTER TABLE tb_employee ADD COLUMN {name} {PERSONAL_COLUMNS[name]}"))
    if pending["gender_check"]:  # PostgreSQL only (pending_employee_schema)
        from backend.models_admin.employee import GENDER_CHECK
        conn.execute(text(f"ALTER TABLE tb_employee ADD CONSTRAINT {GENDER_CHECK_NAME} CHECK ({GENDER_CHECK})"))


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
              "pos_login_index_created": pending["pos_login_index"],
              "added_job_columns": pending["job_columns"], "added_job_fks": pending["job_fks"],
              "created_job_indexes": pending["job_indexes"],
              "added_personal_columns": pending["personal_columns"],
              "gender_check_created": pending["gender_check"]}
    if not pending["tables"] and not pending["store_index"]:
        if pending["employee_columns"] or pending["pos_login_index"]:
            with engine.begin() as conn:
                _migrate_login_schema(conn, pending)
        if _job_pending(pending):
            with engine.begin() as conn:
                _migrate_job_schema(conn, pending)
        return result
    with engine.begin() as conn:
        if "tb_employee" in pending["tables"]:  # create_all below references tb_common_code (Job Grade / Title)
            _ensure_common_tables(conn)
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
        if "tb_employee" not in pending["tables"] and _job_pending(pending):
            _migrate_job_schema(conn, pending)
    if "tb_employee" in pending["tables"]:  # created with every column / index / CHECK / FK by create_all
        result.update(added_employee_columns=[], pos_login_index_created=False, added_job_columns=[],
                      added_job_fks=[], created_job_indexes=[], added_personal_columns=[], gender_check_created=False)
    return result
