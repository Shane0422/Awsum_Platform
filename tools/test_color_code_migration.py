"""COLOR step 6: an older database (tb_color, tb_md_detail.i_color_id, tb_common_code.i_legacy_id) is moved to COLOR
codes and its earlier structure removed. The normal migration only copies / fills (additive); drop_legacy_color()
first checks every mapping (any problem stops it with nothing changed), archives tb_color and the mapping to JSON,
keeps each Color's original creation record on its COLOR code, drops FK / index / columns / table, and is a no-op
on rerun. IDs are deliberately offset (tb_color 101-103 -> codes 3-5, UNIT 1-2) so an id copy would be caught.
SQLite only (the PostgreSQL run is verified separately on a restored copy)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from backend.database.color_migration import (COLOR_CODE_INDEX, backfill_md_detail_color_codes, drop_legacy_color,
                                              legacy_color_targets, migrate_md_detail_color_code)
from backend.database.common_code_migration import migrate_common_codes
from backend.database.migration_policy import MigrationBlocked
from backend.database.pg_platform import PlatformBase
from backend.models_admin.common_code import CommonCode, CommonGroup
from backend.models_admin.merchant import Merchant

LEGACY_TB_COLOR = """CREATE TABLE tb_color (
    i_color_id INTEGER PRIMARY KEY, c_color_code VARCHAR(20) NOT NULL UNIQUE, c_color_name VARCHAR(100) NOT NULL,
    c_swatch_hex VARCHAR(7), i_display_order INTEGER NOT NULL, b_active BOOLEAN NOT NULL, c_notes TEXT,
    dt_created TIMESTAMP, i_created_by INTEGER, dt_updated TIMESTAMP, i_updated_by INTEGER)"""
# tb_md_detail before step 5-1 (SQLite cannot drop a column declared with REFERENCES; PostgreSQL drops the FK).
LEGACY_MD_DETAIL = """CREATE TABLE tb_md_detail (
    i_md_detail_id INTEGER PRIMARY KEY, i_md_id INTEGER NOT NULL, i_detail_no INTEGER NOT NULL,
    b_is_default BOOLEAN NOT NULL, c_unit VARCHAR(30) NOT NULL, c_size VARCHAR(100), i_color_id INTEGER,
    dt_updated TIMESTAMP)"""
DETAILS = ((11, 103), (12, 101), (13, None), (14, 103), (15, 102))  # (detail id, i_color_id)
EXPECTED_CODES = {11: 5, 12: 3, 13: None, 14: 5, 15: 4}


class ColorLegacyRemovalTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        folder = tempfile.TemporaryDirectory(prefix="awsum-color-archive-")
        self.addCleanup(folder.cleanup)
        self.archive_dir = Path(folder.name)
        PlatformBase.metadata.create_all(self.engine, tables=[Merchant.__table__, CommonGroup.__table__, CommonCode.__table__])
        with self.engine.begin() as conn:
            conn.execute(text(LEGACY_TB_COLOR))
            conn.execute(text(LEGACY_MD_DETAIL))
            conn.execute(text("CREATE INDEX ix_tb_md_detail_i_color_id ON tb_md_detail (i_color_id)"))
            conn.execute(text("INSERT INTO tb_common_group (i_common_group_id, c_group_code, c_group_name, c_code_mode, "
                              "i_code_max_length, b_value_required, b_merchant_extendable, i_display_order, b_active) "
                              "VALUES (1, 'UNIT', 'Unit', 'MANUAL', 30, FALSE, FALSE, 20, TRUE)"))
            for code_id, code in ((1, "EA"), (2, "PK")):
                conn.execute(text("INSERT INTO tb_common_code (i_common_code_id, i_common_group_id, c_code, c_name, "
                                  "i_display_order, b_active) VALUES (:id, 1, :code, :code, 10, TRUE)"), {"id": code_id, "code": code})
            for color_id, code, name, hex_ in ((101, "CLR00001", "Black", "#000000"), (102, "CLR00002", "White", "#FFFFFF"),
                                               (103, "CLR00003", "Navy", "#000080")):
                conn.execute(text("INSERT INTO tb_color VALUES (:id, :code, :name, :hex, :order, TRUE, :notes, "
                                  "'2026-01-02 03:04:05', 7, '2026-02-03 04:05:06', 8)"),
                             {"id": color_id, "code": code, "name": name, "hex": hex_, "order": color_id - 100,
                              "notes": f"note {name}"})
            for n, (detail_id, color_id) in enumerate(DETAILS, start=1):
                conn.execute(text("INSERT INTO tb_md_detail VALUES (:id, 7, :no, :default, 'EA', 'M', :color, "
                                  "'2026-11-01 10:00:00')"), {"id": detail_id, "no": n, "default": n == 1, "color": color_id})
        migrate_md_detail_color_code(self.engine)  # the normal migration on an older database (additive steps)
        self.quiet(migrate_common_codes, self.engine)
        self.quiet(backfill_md_detail_color_codes, self.engine)

    def quiet(self, fn, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            result = fn(*args)
        self.output = out.getvalue()
        return result

    def rows(self, sql, **params):
        with self.engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(sql), params)]

    def columns(self, table):
        return {c["name"] for c in inspect(self.engine).get_columns(table)}

    def color_codes(self):
        return self.rows("SELECT i_common_code_id, c_code, c_name, c_value_text, i_display_order, b_active, c_notes, "
                         "dt_updated, i_updated_by FROM tb_common_code WHERE i_common_group_id = 2 ORDER BY 1")

    def drop(self):
        return self.quiet(drop_legacy_color, self.engine, self.archive_dir)

    def test_normal_migration_copies_and_fills_through_the_link_only(self):
        self.assertEqual(self.rows("SELECT i_legacy_id, i_common_code_id FROM tb_common_code WHERE i_legacy_id IS NOT NULL "
                                   "ORDER BY 1"), [(101, 3), (102, 4), (103, 5)])
        self.assertEqual(dict(self.rows("SELECT i_md_detail_id, i_color_code_id FROM tb_md_detail")), EXPECTED_CODES)
        with self.engine.connect() as conn:  # still pending; the normal migration never drops anything
            self.assertEqual(legacy_color_targets(conn), ["table tb_color", "column tb_md_detail.i_color_id",
                                                          "column tb_common_code.i_legacy_id"])

    def test_drop_archives_keeps_creation_record_and_removes_the_structure(self):
        with self.engine.begin() as conn:  # codes copied by the earlier step 2 carry the copy time, not the original
            conn.execute(text("UPDATE tb_common_code SET dt_created = '2026-11-08 07:47:50', i_created_by = NULL "
                              "WHERE i_legacy_id IS NOT NULL"))
        codes_before = self.color_codes()
        common_audit_before = self.rows("SELECT i_common_code_id, dt_created, i_created_by FROM tb_common_code "
                                        "WHERE i_legacy_id IS NOT NULL ORDER BY 1")
        result = self.drop()
        self.assertEqual(result["dropped"], ["table tb_color", "column tb_md_detail.i_color_id",
                                             "column tb_common_code.i_legacy_id"])
        self.assertFalse(inspect(self.engine).has_table("tb_color"))
        self.assertNotIn("i_color_id", self.columns("tb_md_detail"))
        self.assertNotIn("i_legacy_id", self.columns("tb_common_code"))
        self.assertNotIn("ux_tb_common_code_legacy_id", {i["name"] for i in inspect(self.engine).get_indexes("tb_common_code")})
        self.assertNotIn("ix_tb_md_detail_i_color_id", {i["name"] for i in inspect(self.engine).get_indexes("tb_md_detail")})
        # COLOR data and MD Detail Colors are kept exactly; only the creation record moved to the COLOR codes.
        self.assertEqual(self.color_codes(), codes_before)
        self.assertEqual(dict(self.rows("SELECT i_md_detail_id, i_color_code_id FROM tb_md_detail")), EXPECTED_CODES)
        self.assertEqual(self.rows("SELECT i_common_code_id, dt_created, i_created_by FROM tb_common_code "
                                   "WHERE i_common_group_id = 2 ORDER BY 1"),
                         [(3, "2026-01-02 03:04:05", 7), (4, "2026-01-02 03:04:05", 7), (5, "2026-01-02 03:04:05", 7)])
        self.assertEqual(result["audit_restored"], 3)
        archive = json.loads(Path(result["archive"]).read_text(encoding="utf-8"))
        self.assertEqual([(r["i_color_id"], r["c_color_code"], r["c_color_name"], r["dt_created"], r["i_created_by"])
                          for r in archive["tb_color"]],
                         [(101, "CLR00001", "Black", "2026-01-02 03:04:05", 7), (102, "CLR00002", "White", "2026-01-02 03:04:05", 7),
                          (103, "CLR00003", "Navy", "2026-01-02 03:04:05", 7)])
        self.assertEqual([(m["i_color_id"], m["i_common_code_id"], m["c_code"]) for m in archive["mapping"]],
                         [(101, 3, "CLR00001"), (102, 4, "CLR00002"), (103, 5, "CLR00003")])
        # The replaced COLOR code creation record (the copy time) is archived too.
        self.assertEqual([(m["i_common_code_id"], m["dt_created"], m["i_created_by"]) for m in archive["mapping"]],
                         [(cid, str(created), by) for cid, created, by in common_audit_before])
        self.assertEqual({(d["i_md_detail_id"], d["i_color_id"], d["i_color_code_id"]) for d in archive["md_detail_colors"]},
                         {(d, c, EXPECTED_CODES[d]) for d, c in DETAILS})
        snapshot = (self.rows("SELECT * FROM tb_common_code ORDER BY 1"), self.rows("SELECT * FROM tb_md_detail ORDER BY 1"))
        self.assertEqual(self.drop(), {"dropped": [], "archive": None})  # rerun: nothing left, nothing changed
        self.assertEqual((self.rows("SELECT * FROM tb_common_code ORDER BY 1"), self.rows("SELECT * FROM tb_md_detail ORDER BY 1")),
                         snapshot)
        self.assertEqual(self.quiet(migrate_common_codes, self.engine)["colors"], 0)  # no defaults added afterwards
        self.assertEqual(len(list(self.archive_dir.glob("color-legacy-archive-*.json"))), 1)

    def test_any_mapping_problem_stops_with_nothing_changed(self):
        cases = (
            ("UPDATE tb_common_code SET c_name = 'Ivory' WHERE i_legacy_id = 102", "differ from their COLOR code"),
            ("DELETE FROM tb_common_code WHERE i_legacy_id = 102", "without a COLOR code"),
            ("UPDATE tb_md_detail SET i_color_code_id = 4 WHERE i_md_detail_id = 11", "differs from i_color_id"),
            ("UPDATE tb_md_detail SET i_color_code_id = NULL WHERE i_md_detail_id = 12", "has no COLOR code"),
        )
        for change, message in cases:
            with self.subTest(change=change):
                before = (self.rows("SELECT * FROM tb_color ORDER BY 1"), self.rows("SELECT * FROM tb_common_code ORDER BY 1"))
                with self.engine.begin() as conn:
                    conn.execute(text(change))
                changed = (self.rows("SELECT * FROM tb_common_code ORDER BY 1"), self.rows("SELECT * FROM tb_md_detail ORDER BY 1"))
                with self.assertRaises(MigrationBlocked) as ctx:
                    self.drop()
                self.assertIn(message, str(ctx.exception))
                self.assertEqual((self.rows("SELECT * FROM tb_common_code ORDER BY 1"),
                                  self.rows("SELECT * FROM tb_md_detail ORDER BY 1")), changed)
                self.assertTrue(inspect(self.engine).has_table("tb_color"))
                self.assertIn("i_color_id", self.columns("tb_md_detail"))
                self.assertEqual(list(self.archive_dir.glob("*.json")), [])  # nothing archived before the checks pass
                self.tearDown_case(before)

    def tearDown_case(self, before):
        """Restore the fixture between sub-cases (the checks above changed it on purpose)."""
        colors, codes = before
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM tb_common_code"))
            columns = [c["name"] for c in inspect(conn).get_columns("tb_common_code")]
            for row in codes:
                conn.execute(text(f"INSERT INTO tb_common_code ({', '.join(columns)}) VALUES "
                                  f"({', '.join(':' + c for c in columns)})"), dict(zip(columns, row)))
            for detail_id, code in EXPECTED_CODES.items():
                conn.execute(text("UPDATE tb_md_detail SET i_color_code_id = :c WHERE i_md_detail_id = :d"),
                             {"c": code, "d": detail_id})

    def test_current_model_has_only_the_color_code(self):
        from backend.models_admin.master_group import MdDetail
        column = MdDetail.__table__.c.get("i_color_code_id")
        self.assertEqual([fk.target_fullname for fk in column.foreign_keys], ["tb_common_code.i_common_code_id"])
        self.assertTrue(column.nullable)
        self.assertIn(COLOR_CODE_INDEX, {index.name for index in MdDetail.__table__.indexes})
        self.assertNotIn("i_color_id", MdDetail.__table__.c)
        self.assertNotIn("i_legacy_id", CommonCode.__table__.c)
        self.assertNotIn("tb_color", PlatformBase.metadata.tables)


if __name__ == "__main__":
    unittest.main()
