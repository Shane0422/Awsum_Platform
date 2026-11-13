"""Employee Management STEP 1A migration (backend.database.employee_migration) on SQLite.

An "existing" database (every other table, data rows, no Employee tables and no tb_store (store, merchant) index)
gets the index and the 5 tables; a second run changes nothing; existing rows are untouched. PostgreSQL behaviour
(composite / scope FKs, partial indexes, the real data) is verified by the rehearsal on a temporary database.
"""
import unittest

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from backend.database.employee_migration import (EMPLOYEE_LOGIN_COLUMNS, EMPLOYEE_TABLES, POS_LOGIN_INDEX,
                                                 STORE_INDEX, migrate_employee_schema, pending_employee_schema)
from backend.database.pg_platform import PlatformBase
import backend.models_admin  # noqa: F401


class EmployeeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            for name in reversed(EMPLOYEE_TABLES):
                conn.execute(text(f"DROP TABLE {name}"))
            conn.execute(text(f"DROP INDEX {STORE_INDEX}"))
            conn.execute(text("INSERT INTO tb_merchant (i_merchant_id, c_merchant_code, c_merchant_name, c_status) "
                              "VALUES (20, 'MER26ZNUA', 'DA ONE', 'active')"))
            conn.execute(text("INSERT INTO tb_store (i_store_id, i_merchant_id, c_store_code, c_store_name, c_status) "
                              "VALUES (1100002, 20, 'STR26Y6F7', 'Daone 1', 'active')"))

    def snapshot(self):
        with self.engine.connect() as conn:
            return (conn.execute(text("SELECT * FROM tb_merchant ORDER BY 1")).fetchall(),
                    conn.execute(text("SELECT * FROM tb_store ORDER BY 1")).fetchall())

    def test_creates_the_index_and_tables_once_without_touching_existing_rows(self):
        before = self.snapshot()
        self.assertEqual(pending_employee_schema(self.engine), {"tables": list(EMPLOYEE_TABLES), "store_index": True,
                                                                "employee_columns": [], "pos_login_index": False})
        result = migrate_employee_schema(self.engine)
        self.assertEqual(result, {"created_tables": list(EMPLOYEE_TABLES), "store_index_created": True,
                                  "added_employee_columns": [], "pos_login_index_created": False})
        inspector = inspect(self.engine)
        self.assertTrue(set(EMPLOYEE_TABLES) <= set(inspector.get_table_names()))
        self.assertIn(STORE_INDEX, {ix["name"] for ix in inspector.get_indexes("tb_store")})
        indexes = {ix["name"] for ix in inspector.get_indexes("tb_employee_store")}
        self.assertTrue({"uq_tb_employee_store_open", "uq_tb_employee_store_primary"} <= indexes)
        self.assertEqual(pending_employee_schema(self.engine), {"tables": [], "store_index": False,
                                                                "employee_columns": [], "pos_login_index": False})
        self.assertEqual(migrate_employee_schema(self.engine), {"created_tables": [], "store_index_created": False,
                                                                "added_employee_columns": [],
                                                                "pos_login_index_created": False})
        self.assertIn(POS_LOGIN_INDEX, {ix["name"] for ix in inspector.get_indexes("tb_employee")})
        self.assertEqual(self.snapshot(), before)
        with self.engine.connect() as conn:
            for name in EMPLOYEE_TABLES:
                self.assertEqual(conn.execute(text(f"SELECT count(*) FROM {name}")).scalar(), 0)  # nothing seeded
            self.assertEqual(conn.execute(text("SELECT count(*) FROM tb_role WHERE c_name = 'Employee'")).scalar(), 0)


class EmployeeLoginMigrationTests(unittest.TestCase):
    """STEP 1A-2 on a database migrated by STEP 1A (tb_employee without the address / POS Login columns)."""

    def setUp(self):
        from backend.models_admin.employee import Employee
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine)
        old = [c for c in Employee.__table__.columns if c.name not in EMPLOYEE_LOGIN_COLUMNS]
        ddl = ", ".join(f"{c.name} {c.type.compile(self.engine.dialect)}" + (" PRIMARY KEY" if c.primary_key else "")
                        for c in old)
        with self.engine.begin() as conn:
            conn.execute(text("DROP TABLE tb_employee_store"))
            conn.execute(text("DROP TABLE tb_employee"))
            conn.execute(text(f"CREATE TABLE tb_employee ({ddl})"))
            conn.execute(text("INSERT INTO tb_merchant (i_merchant_id, c_merchant_code, c_merchant_name, c_status) "
                              "VALUES (20, 'MER26ZNUA', 'DA ONE', 'active')"))
            conn.execute(text("INSERT INTO tb_employee (i_employee_id, i_merchant_id, c_employee_code, i_pos_number, "
                              "c_first_name, c_phone, c_status, c_merchant_portal_scope, i_pin_version, "
                              "i_pin_failed_count, i_row_version) VALUES (1, 20, 'EMP00001', 1001, 'Existing', "
                              "'972-555-1234', 'active', 'MERCHANT', 0, 0, 1)"))
            conn.execute(text("CREATE TABLE tb_employee_store (i_employee_store_id INTEGER PRIMARY KEY)"))

    def employee(self):
        with self.engine.connect() as conn:
            return dict(conn.execute(text("SELECT * FROM tb_employee")).mappings().one())

    def test_adds_only_the_missing_columns_and_index_and_keeps_the_rows(self):
        before = self.employee()
        pending = pending_employee_schema(self.engine)
        self.assertEqual((pending["tables"], pending["employee_columns"], pending["pos_login_index"]),
                         ([], list(EMPLOYEE_LOGIN_COLUMNS), True))
        result = migrate_employee_schema(self.engine)
        self.assertEqual((result["added_employee_columns"], result["pos_login_index_created"]),
                         (list(EMPLOYEE_LOGIN_COLUMNS), True))
        after = self.employee()
        self.assertEqual({k: after[k] for k in before}, before)                      # every saved value kept
        self.assertEqual((after["b_pos_login_enabled"], after["c_pos_login_id"], after["c_address_line1"]),
                         (0, None, None))
        self.assertIn(POS_LOGIN_INDEX, {ix["name"] for ix in inspect(self.engine).get_indexes("tb_employee")})
        again = pending_employee_schema(self.engine)
        self.assertEqual((again["employee_columns"], again["pos_login_index"]), ([], False))
        second = migrate_employee_schema(self.engine)
        self.assertEqual((second["added_employee_columns"], second["pos_login_index_created"]), ([], False))
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE tb_employee SET c_pos_login_id = '1234' WHERE i_employee_id = 1"))
        with self.assertRaises(Exception):
            with self.engine.begin() as conn:
                conn.execute(text("INSERT INTO tb_employee (i_employee_id, i_merchant_id, c_employee_code, i_pos_number, "
                                  "c_first_name, c_status, c_merchant_portal_scope, c_pos_login_id) VALUES "
                                  "(2, 20, 'EMP00002', 1002, 'Dup', 'active', 'MERCHANT', '1234')"))


if __name__ == "__main__":
    unittest.main()
