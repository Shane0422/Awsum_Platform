"""Employee Management STEP 1A migration (backend.database.employee_migration) on SQLite.

An "existing" database (every other table, data rows, no Employee tables and no tb_store (store, merchant) index)
gets the index and the 5 tables; a second run changes nothing; existing rows are untouched. PostgreSQL behaviour
(composite / scope FKs, partial indexes, the real data) is verified by the rehearsal on a temporary database.
"""
import unittest

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from backend.database.employee_migration import (EMPLOYEE_TABLES, STORE_INDEX, migrate_employee_schema,
                                                 pending_employee_schema)
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
        self.assertEqual(pending_employee_schema(self.engine), {"tables": list(EMPLOYEE_TABLES), "store_index": True})
        result = migrate_employee_schema(self.engine)
        self.assertEqual(result, {"created_tables": list(EMPLOYEE_TABLES), "store_index_created": True})
        inspector = inspect(self.engine)
        self.assertTrue(set(EMPLOYEE_TABLES) <= set(inspector.get_table_names()))
        self.assertIn(STORE_INDEX, {ix["name"] for ix in inspector.get_indexes("tb_store")})
        indexes = {ix["name"] for ix in inspector.get_indexes("tb_employee_store")}
        self.assertTrue({"uq_tb_employee_store_open", "uq_tb_employee_store_primary"} <= indexes)
        self.assertEqual(pending_employee_schema(self.engine), {"tables": [], "store_index": False})
        self.assertEqual(migrate_employee_schema(self.engine), {"created_tables": [], "store_index_created": False})
        self.assertEqual(self.snapshot(), before)
        with self.engine.connect() as conn:
            for name in EMPLOYEE_TABLES:
                self.assertEqual(conn.execute(text(f"SELECT count(*) FROM {name}")).scalar(), 0)  # nothing seeded
            self.assertEqual(conn.execute(text("SELECT count(*) FROM tb_role WHERE c_name = 'Employee'")).scalar(), 0)


if __name__ == "__main__":
    unittest.main()
