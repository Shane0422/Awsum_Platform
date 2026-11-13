"""Common Maintenance seed: COLOR / UNIT groups, default colors created directly as COLOR codes (COLOR step 6),
UNIT initial list, typed Value columns and a rerun-safe seed that never duplicates or overwrites. A database created
before step 6 (earlier tb_color) has its colors copied instead of the defaults. SQLite only."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from backend.database.common_code_migration import (DEFAULT_COLORS, JOB_GRADES, JOB_TITLES, UNIT_CODES,
                                                    migrate_common_codes)
from backend.database import common_code_migration
from backend.database.pg_platform import PlatformBase
from backend.models_admin.common_code import CommonCode, CommonGroup
from backend.models_admin.merchant import Merchant

# The earlier Product Color master, as databases created before COLOR step 6 have it.
LEGACY_TB_COLOR = """CREATE TABLE tb_color (
    i_color_id INTEGER PRIMARY KEY, c_color_code VARCHAR(20) NOT NULL UNIQUE, c_color_name VARCHAR(100) NOT NULL,
    c_swatch_hex VARCHAR(7), i_display_order INTEGER NOT NULL, b_active BOOLEAN NOT NULL, c_notes TEXT,
    dt_created TIMESTAMP, i_created_by INTEGER, dt_updated TIMESTAMP, i_updated_by INTEGER)"""


class CommonCodeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine, tables=[Merchant.__table__, CommonGroup.__table__, CommonCode.__table__])

    def rows(self, sql, **params):
        with self.engine.connect() as conn:
            return [tuple(row) for row in conn.execute(text(sql), params)]

    def migrate(self):
        with redirect_stdout(io.StringIO()) as out:
            result = migrate_common_codes(self.engine)
        return result, out.getvalue()

    def codes(self, group):
        return self.rows("SELECT c.c_code, c.c_name, c.c_value_text, c.n_value, c.i_display_order, c.b_active, c.c_notes, "
                         "c.i_merchant_id FROM tb_common_code c JOIN tb_common_group g "
                         "ON g.i_common_group_id = c.i_common_group_id WHERE g.c_group_code = :g "
                         "ORDER BY c.i_display_order, c.c_code", g=group)

    def legacy_colors(self):
        with self.engine.begin() as conn:
            conn.execute(text(LEGACY_TB_COLOR))
            for color_id, code, name, hex_, order, active, notes in (
                    (101, "CLR00001", "Jet Black", None, 5, False, "kept"), (102, "CLR00002", "White", "#FFFFFF", 20, True, None),
                    (103, "CLR00099", "Gold", "", 150, True, None)):
                conn.execute(text("INSERT INTO tb_color VALUES (:id, :code, :name, :hex, :order, :active, :notes, "
                                  "'2026-01-02 03:04:05', 7, '2026-02-03 04:05:06', 8)"),
                             {"id": color_id, "code": code, "name": name, "hex": hex_, "order": order, "active": active,
                              "notes": notes})

    # ---- new installation: no tb_color ----
    def test_new_database_seeds_default_colors_and_units_directly(self):
        result, _ = self.migrate()
        self.assertEqual((result["groups"], result["colors"], result["units"], result["job_grades"], result["job_titles"]),
                         (["COLOR", "UNIT", "JOB_GRADE", "JOB_TITLE"], 13, 14, 8, 10))
        self.assertFalse(inspect(self.engine).has_table("tb_color"))
        groups = self.rows("SELECT c_group_code, c_code_mode, c_code_prefix, i_code_max_length, c_value_type, "
                           "c_value_label, b_merchant_extendable, b_active FROM tb_common_group ORDER BY i_display_order")
        self.assertEqual(groups, [("COLOR", "AUTO", "CLR", 20, "COLOR", "Swatch HEX", 0, 1),
                                  ("UNIT", "MANUAL", None, 30, None, None, 0, 1),
                                  ("JOB_GRADE", "AUTO", "GRD", 20, None, None, 0, 1),
                                  ("JOB_TITLE", "AUTO", "JOB", 20, None, None, 0, 1)])
        self.assertEqual(self.codes("COLOR"), [(f"CLR{n:05d}", name, hex_, None, n * 10, 1, None, None)
                                               for n, (name, hex_) in enumerate(DEFAULT_COLORS, start=1)])
        self.assertEqual([(c[0], c[1], c[4]) for c in self.codes("UNIT")],
                         [(code, name, n * 10) for n, (code, name) in enumerate(UNIT_CODES, start=1)])
        # Employee Master defaults (approved lists), AUTO codes, Platform scope, no Value.
        self.assertEqual(self.codes("JOB_GRADE"), [(f"GRD{n:05d}", name, None, None, n * 10, 1, None, None)
                                                   for n, name in enumerate(JOB_GRADES, start=1)])
        self.assertEqual(self.codes("JOB_TITLE"), [(f"JOB{n:05d}", name, None, None, n * 10, 1, None, None)
                                                   for n, name in enumerate(JOB_TITLES, start=1)])
        self.assertEqual((JOB_GRADES[0], JOB_GRADES[-1], len(JOB_GRADES)), ("Entry Level", "Executive", 8))
        self.assertEqual((JOB_TITLES[0], JOB_TITLES[-1], len(JOB_TITLES)), ("Cashier", "General Manager", 10))
        self.assertNotIn("SIZE", {g[0] for g in groups})
        self.assertNotIn("ITEM_KIND", {g[0] for g in groups})
        self.assertNotIn("i_legacy_id", {c["name"] for c in inspect(self.engine).get_columns("tb_common_code")})

    def test_rerun_never_duplicates_or_overwrites(self):
        self.migrate()
        with self.engine.begin() as conn:  # admin edits after the first run
            conn.execute(text("UPDATE tb_common_group SET c_group_name = 'Colours', b_active = FALSE "
                              "WHERE c_group_code = 'COLOR'"))
            conn.execute(text("UPDATE tb_common_code SET c_name = 'Midnight', c_value_text = '#000001' WHERE c_code = 'CLR00004'"))
            conn.execute(text("DELETE FROM tb_common_code WHERE c_code = 'CLR00006'"))  # a deleted default stays deleted
            conn.execute(text("UPDATE tb_common_code SET c_name = 'Piece' WHERE c_code = 'EA'"))
            conn.execute(text("DELETE FROM tb_common_code WHERE c_code = 'DAY'"))
            conn.execute(text("UPDATE tb_common_code SET c_name = 'Lead Cashier', b_active = FALSE WHERE c_code = 'JOB00001'"))
            conn.execute(text("DELETE FROM tb_common_code WHERE c_code = 'GRD00008'"))  # deleted default stays deleted
        snapshot = self.rows("SELECT * FROM tb_common_code ORDER BY i_common_code_id")
        groups = self.rows("SELECT * FROM tb_common_group ORDER BY i_common_group_id")
        result, out = self.migrate()
        self.assertEqual((result["groups"], result["colors"], result["units"], result["job_grades"], result["job_titles"]),
                         ([], 0, 0, 0, 0))
        self.assertEqual(self.rows("SELECT * FROM tb_common_code ORDER BY i_common_code_id"), snapshot)
        self.assertEqual(self.rows("SELECT * FROM tb_common_group ORDER BY i_common_group_id"), groups)
        self.assertEqual(out, "")

    def test_existing_database_gets_only_the_job_groups_and_keeps_color_and_unit(self):
        """The real database (COLOR / UNIT seeded and edited before this step): only JOB_GRADE / JOB_TITLE are added."""
        with patch.object(common_code_migration, "COMMON_GROUPS", common_code_migration.COMMON_GROUPS[:2]),                 patch.object(common_code_migration, "AUTO_CODE_SEEDS", ()):
            before, _ = self.migrate()
        self.assertEqual(before["groups"], ["COLOR", "UNIT"])
        with self.engine.begin() as conn:  # Merchant / admin changes made before this step
            conn.execute(text("UPDATE tb_common_code SET c_name = 'Piece' WHERE c_code = 'EA'"))
        old_codes = self.rows("SELECT * FROM tb_common_code ORDER BY i_common_code_id")
        old_groups = self.rows("SELECT * FROM tb_common_group ORDER BY i_common_group_id")
        result, out = self.migrate()
        self.assertEqual((result["groups"], result["colors"], result["units"], result["job_grades"], result["job_titles"]),
                         (["JOB_GRADE", "JOB_TITLE"], 0, 0, 8, 10))
        self.assertEqual(self.rows("SELECT * FROM tb_common_code ORDER BY i_common_code_id")[:len(old_codes)], old_codes)
        self.assertEqual(self.rows("SELECT * FROM tb_common_group ORDER BY i_common_group_id")[:len(old_groups)], old_groups)
        self.assertEqual(len(self.codes("JOB_GRADE")) + len(self.codes("JOB_TITLE")), 18)
        self.assertIn("JOB_GRADE seeded: 8; JOB_TITLE seeded: 10", out)

    # ---- database created before COLOR step 6: tb_color is copied, defaults are not seeded ----
    def test_earlier_tb_color_is_copied_with_its_creation_record(self):
        self.legacy_colors()
        result, _ = self.migrate()
        self.assertEqual((result["colors"], result["skipped_colors"]), (3, []))
        self.assertEqual(self.rows("SELECT c.i_legacy_id, c.c_code, c.c_name, c.c_value_text, c.i_display_order, "
                                   "c.b_active, c.c_notes, c.dt_created, c.i_created_by FROM tb_common_code c "
                                   "WHERE c.i_legacy_id IS NOT NULL ORDER BY 1"),
                         [(101, "CLR00001", "Jet Black", None, 5, 0, "kept", "2026-01-02 03:04:05", 7),
                          (102, "CLR00002", "White", "#FFFFFF", 20, 1, None, "2026-01-02 03:04:05", 7),
                          (103, "CLR00099", "Gold", None, 150, 1, None, "2026-01-02 03:04:05", 7)])  # blank HEX -> NULL
        self.assertEqual(len(self.codes("COLOR")), 3)  # no defaults added next to the copied colors
        with self.engine.begin() as conn:  # a later tb_color row, and one whose code another COLOR row already uses
            conn.execute(text("INSERT INTO tb_color (i_color_id, c_color_code, c_color_name, i_display_order, b_active) "
                              "VALUES (104, 'CLR00014', 'Teal', 140, TRUE)"))
            conn.execute(text("INSERT INTO tb_color (i_color_id, c_color_code, c_color_name, i_display_order, b_active) "
                              "VALUES (105, 'CLR00015', 'Olive', 141, TRUE)"))
            group = conn.execute(text("SELECT i_common_group_id FROM tb_common_group WHERE c_group_code='COLOR'")).scalar()
            conn.execute(text("INSERT INTO tb_common_code (i_common_group_id, c_code, c_name, i_display_order, b_active) "
                              "VALUES (:g, 'CLR00015', 'Other', 1, TRUE)"), {"g": group})
        result, out = self.migrate()
        self.assertEqual((result["colors"], result["skipped_colors"]), (1, ["CLR00015"]))
        self.assertIn("[COMMON][WARN] COLOR code CLR00015", out)
        self.assertEqual(self.rows("SELECT c_name FROM tb_common_code WHERE c_code = 'CLR00015'"), [("Other",)])
        snapshot = self.rows("SELECT * FROM tb_common_code ORDER BY i_common_code_id")
        self.assertEqual(self.migrate()[0]["colors"], 0)  # rerun: nothing more
        self.assertEqual(self.rows("SELECT * FROM tb_common_code ORDER BY i_common_code_id"), snapshot)

    def test_value_columns_and_constraints(self):
        self.migrate()
        columns = {c["name"]: c for c in inspect(self.engine).get_columns("tb_common_code")}
        self.assertEqual((columns["n_value"]["type"].precision, columns["n_value"]["type"].scale), (18, 6))
        self.assertEqual(columns["c_value_text"]["type"].length, 200)
        unit = self.rows("SELECT i_common_group_id FROM tb_common_group WHERE c_group_code = 'UNIT'")[0][0]
        insert = ("INSERT INTO tb_common_code (i_common_group_id, i_merchant_id, c_code, c_name, c_value_text, n_value, "
                  "i_display_order, b_active) VALUES (:g, :m, :code, 'x', :t, :n, 1, TRUE)")
        with self.engine.begin() as conn:  # 0 is a value, distinct from NULL
            conn.execute(text(insert), {"g": unit, "m": None, "code": "ZERO", "t": None, "n": 0})
            conn.execute(text(insert), {"g": unit, "m": 7, "code": "EA", "t": None, "n": None})  # Merchant scope
        self.assertEqual(self.rows("SELECT n_value IS NULL, n_value FROM tb_common_code WHERE c_code = 'ZERO'"), [(0, 0)])
        for params in ({"code": "EA", "t": None, "n": None, "m": None},       # Platform code duplicate
                       {"code": "BOTH", "t": "a", "n": 1, "m": None},  # two Value columns
                       {"code": "EA", "t": None, "n": None, "m": 7}):        # Merchant code duplicate
            with self.subTest(params=params), self.assertRaises(IntegrityError), self.engine.begin() as conn:
                conn.execute(text(insert), {"g": unit, **params})
        with self.assertRaises(IntegrityError), self.engine.begin() as conn:
            conn.execute(text("INSERT INTO tb_common_group (c_group_code, c_group_name, c_code_mode, i_code_max_length, "
                              "c_value_type, b_value_required, b_merchant_extendable, i_display_order, b_active) "
                              "VALUES ('BAD', 'Bad', 'AUTO', 20, 'FLOAT', FALSE, FALSE, 1, TRUE)"))

    def test_platform_models_include_common_tables(self):
        from backend.database.db_init_platform import PLATFORM_MODEL_MODULES
        self.assertIn("common_code", PLATFORM_MODEL_MODULES)


if __name__ == "__main__":
    unittest.main()
