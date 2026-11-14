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
7. Gender list change (MALE / FEMALE only): on PostgreSQL an existing ck_tb_employee_gender whose code list differs
   from models_admin.employee.EMPLOYEE_GENDERS is replaced (DROP + ADD in one transaction). It is never replaced while a
   row holds a code outside the list: the step stops and no value is changed or removed.

STEP 1A-4 (HISTORY / BACKGROUND)
8. New tables (CREATE TABLE only when missing): tb_employee_history, tb_employee_education, tb_employee_experience,
   tb_employee_certification, tb_employee_skill (models_admin.employee_records), each owned by its Employee through the
   composite FK (i_employee_id, i_merchant_id) -> tb_employee; the Skill references a tb_common_code (JOB_SKILL).
9. tb_employee: ADD COLUMN c_background_remark (TEXT NULL), NULL for every existing row.

STEP 1A-5 (automatic employment HISTORY, on an existing tb_employee_history)
10. tb_employee_history: ADD COLUMN c_record_type (VARCHAR(10) NOT NULL DEFAULT 'MANUAL': every existing row reads as
    MANUAL without being rewritten), c_event_type, d_hire_date, d_previous_hire_date, d_termination_date (NULL) and
    c_termination_reason; on PostgreSQL the CHECKs ck_tb_employee_history_record_type / _event_type / _system; the
    partial unique index uq_tb_employee_history_hired (one HIRED record per Employee). No existing row is changed.

No existing value is changed or removed. The tb_role "Employee" row is seeded by db_init_platform (default roles).
Runs from db_init_platform before the generic schema step (which would otherwise create tb_employee_store before the
tb_store index exists).
"""
import re

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
# STEP 1A-4: HISTORY / BACKGROUND tables (models_admin.employee_records.RECORD_TABLES) and the overall remark column.
RECORD_TABLES = ("tb_employee_history", "tb_employee_education", "tb_employee_experience", "tb_employee_certification",
                 "tb_employee_skill")
REMARK_COLUMN = ("c_background_remark", "TEXT")
# STEP 1A-5: tb_employee_history columns (name -> DDL), CHECKs and the HIRED index (models_admin.employee_records).
HISTORY_COLUMNS = {
    "c_record_type": "VARCHAR(10) NOT NULL DEFAULT 'MANUAL'", "c_event_type": "VARCHAR(20)", "d_hire_date": "DATE",
    "d_previous_hire_date": "DATE", "d_termination_date": "DATE", "c_termination_reason": "VARCHAR(200)",
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
    job_columns, job_fks, job_indexes = [], [], []
    personal_columns, gender_check, gender_check_replace = [], False, False
    present = set()
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
            checks = {ck["name"]: ck["sqltext"] for ck in inspector.get_check_constraints("tb_employee")}
            gender_check = GENDER_CHECK_NAME not in checks
            gender_check_replace = (not gender_check
                                    and gender_check_codes(checks[GENDER_CHECK_NAME]) != _allowed_genders())
    return {"tables": [name for name in EMPLOYEE_TABLES if name not in tables], "store_index": not store_index,
            "employee_columns": columns, "pos_login_index": login_index,
            "job_columns": job_columns, "job_fks": job_fks, "job_indexes": job_indexes,
            "personal_columns": personal_columns, "gender_check": gender_check,
            "gender_check_replace": gender_check_replace,
            "record_tables": [name for name in RECORD_TABLES if name not in tables],
            "remark_column": "tb_employee" in tables and REMARK_COLUMN[0] not in present,
            **_pending_history(engine, inspector, tables)}


def _pending_history(engine, inspector, tables) -> dict:
    """STEP 1A-5 on an existing tb_employee_history (a new one is created complete by STEP 1A-4)."""
    from backend.models_admin.employee_records import HIRED_INDEX, HISTORY_CHECKS
    if "tb_employee_history" not in tables:
        return {"history_columns": [], "history_checks": [], "history_index": False}
    present = {column["name"] for column in inspector.get_columns("tb_employee_history")}
    checks = []
    if engine.dialect.name == "postgresql":  # SQLite cannot add a CHECK to an existing table
        existing = {ck["name"] for ck in inspector.get_check_constraints("tb_employee_history")}
        checks = [name for name in HISTORY_CHECKS if name not in existing]
    indexes = {ix["name"] for ix in inspector.get_indexes("tb_employee_history")}
    return {"history_columns": [name for name in HISTORY_COLUMNS if name not in present], "history_checks": checks,
            "history_index": HIRED_INDEX not in indexes}


def _allowed_genders() -> set[str]:
    from backend.models_admin.employee import EMPLOYEE_GENDERS
    return {code for code, _label in EMPLOYEE_GENDERS}


def gender_check_codes(definition: str) -> set[str]:
    """The quoted codes of a ck_tb_employee_gender definition (as reflected or written)."""
    return set(re.findall(r"'([A-Z_]+)'", definition or ""))


def _job_pending(pending: dict) -> bool:
    """Anything of STEP 1A-3 (Job Grade / Job Title, Personal Information) still missing."""
    return bool(pending["job_columns"] or pending["job_fks"] or pending["job_indexes"]
                or pending["personal_columns"] or pending["gender_check"] or pending["gender_check_replace"])


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
    from backend.models_admin.employee import GENDER_CHECK
    if pending["gender_check_replace"]:  # PostgreSQL only: the code list changed (never while a row is outside it)
        allowed = sorted(_allowed_genders())
        outside = conn.execute(text("SELECT c_gender, count(*) FROM tb_employee WHERE c_gender IS NOT NULL AND "
                                    "c_gender <> ALL(:allowed) GROUP BY c_gender ORDER BY c_gender"),
                               {"allowed": allowed}).fetchall()
        if outside:
            raise RuntimeError(f"tb_employee has Gender codes outside {allowed}: {[tuple(r) for r in outside]}; "
                               f"{GENDER_CHECK_NAME} was not changed and no value was changed.")
        conn.execute(text(f"ALTER TABLE tb_employee DROP CONSTRAINT {GENDER_CHECK_NAME}"))
    if pending["gender_check"] or pending["gender_check_replace"]:  # PostgreSQL only (pending_employee_schema)
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


def _migrate_history(engine, pending: dict) -> None:
    """STEP 1A-5 (one transaction): the record / event columns, CHECKs and the HIRED index of tb_employee_history."""
    if not (pending["history_columns"] or pending["history_checks"] or pending["history_index"]):
        return
    from backend.models_admin.employee_records import HIRED_INDEX, HISTORY_CHECKS
    with engine.begin() as conn:
        for name in pending["history_columns"]:
            conn.execute(text(f"ALTER TABLE tb_employee_history ADD COLUMN {name} {HISTORY_COLUMNS[name]}"))
        for name in pending["history_checks"]:
            conn.execute(text(f"ALTER TABLE tb_employee_history ADD CONSTRAINT {name} CHECK ({HISTORY_CHECKS[name]})"))
        if pending["history_index"]:
            conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {HIRED_INDEX} ON tb_employee_history (i_employee_id) "
                              "WHERE c_event_type = 'HIRED'"))


def _migrate_records(engine, pending: dict) -> None:
    """STEP 1A-4 (one transaction): the HISTORY / BACKGROUND tables and tb_employee.c_background_remark."""
    if not (pending["record_tables"] or pending["remark_column"]):
        return
    from backend.database.pg_platform import PlatformBase
    import backend.models_admin.employee_records  # noqa: F401  (register the tables on the metadata)
    with engine.begin() as conn:
        _ensure_common_tables(conn)  # the Skill FK target
        if pending["remark_column"]:
            conn.execute(text(f"ALTER TABLE tb_employee ADD COLUMN {REMARK_COLUMN[0]} {REMARK_COLUMN[1]}"))
        tables = [PlatformBase.metadata.tables[name] for name in pending["record_tables"]]
        PlatformBase.metadata.create_all(conn, tables=tables, checkfirst=True)


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
              "gender_check_created": pending["gender_check"],
              "gender_check_replaced": pending["gender_check_replace"],
              "created_record_tables": pending["record_tables"],
              "background_remark_added": pending["remark_column"],
              "added_history_columns": pending["history_columns"], "added_history_checks": pending["history_checks"],
              "history_index_created": pending["history_index"]}
    if not pending["tables"] and not pending["store_index"]:
        if pending["employee_columns"] or pending["pos_login_index"]:
            with engine.begin() as conn:
                _migrate_login_schema(conn, pending)
        if _job_pending(pending):
            with engine.begin() as conn:
                _migrate_job_schema(conn, pending)
        _migrate_records(engine, pending)
        _migrate_history(engine, pending)
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
                      added_job_fks=[], created_job_indexes=[], added_personal_columns=[], gender_check_created=False,
                      gender_check_replaced=False)
    _migrate_records(engine, pending)  # after tb_employee exists (the owner FK target)
    _migrate_history(engine, pending)
    return result
