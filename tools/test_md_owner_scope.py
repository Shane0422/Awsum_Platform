"""STEP 2-1: MD ownership (owner key 0 = Platform, Merchant ID = that Merchant) and barcode uniqueness per owner.

SQLite with foreign keys ON, tables from the models (the same constraints PostgreSQL gets from the migration).
No real database is touched.
"""
import io
import unittest
from contextlib import redirect_stdout

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.item_kind_migration import migrate_item_kinds
from backend.database.md_owner_scope_migration import migrate_md_owner_scope
from backend.database.pg_platform import PlatformBase
from backend.database.db_init_platform import load_platform_models
from backend.models_admin.master_group import Category1, MdDetail, MdMaster
from backend.models_admin.merchant import Merchant
from backend.services import master_group as svc
from backend.services.barcode import LEGACY_UNIQUE_INDEX, UNIQUE_INDEX

load_platform_models()


def sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    return engine


class MdOwnerScopeTests(unittest.TestCase):
    def setUp(self):
        self.engine = sqlite_engine()
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine)
        migrate_item_kinds(self.engine)  # ITEM etc. (MD needs an active Item Kind)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)
        self.db.add_all([Merchant(i_merchant_id=20, c_merchant_code="MER26AAAA", c_merchant_name="DA ONE"),
                         Merchant(i_merchant_id=21, c_merchant_code="MER26BBBB", c_merchant_name="Other")])
        self.db.commit()
        self.platform_category = self.setup_category()
        self.platform_md = svc.create_item(self.db, "md", self.md_payload("Platform Item"), 1)

    def md_payload(self, name):
        return {"name": name, "item_kind": "ITEM", "category1_id": self.platform_category, "unit": "EA",
                "retail_price": "1", "billing_cycle": "ONE_TIME"}

    def setup_category(self, merchant_id=None):
        self._categories = getattr(self, "_categories", 0) + 1
        row = Category1(c_category_code=f"CAT{self._categories:05d}", c_category_name=f"Cat {self._categories}",
                        i_merchant_id=merchant_id)
        self.db.add(row)
        self.db.commit()
        return row.i_category1_id

    def merchant_md(self, merchant_id, code):
        """A Merchant's MD (+ Default Detail) written directly: Merchant MD screens are a later STEP 2 part."""
        md = MdMaster(i_merchant_id=merchant_id, i_owner_key=merchant_id, c_item_code=code, c_item_name=code,
                      c_item_kind="ITEM", i_category1_id=self.setup_category(merchant_id), c_unit="EA",
                      n_default_unit_price=1, c_billing_cycle="ONE_TIME")
        self.db.add(md)
        self.db.flush()
        self.db.add(MdDetail(i_md_id=md.i_md_id, i_owner_key=merchant_id, i_detail_no=1, b_is_default=True, c_unit="EA",
                             n_price=1))
        self.db.commit()
        return md.i_md_id

    @staticmethod
    def detail(barcode, **extra):
        return {"barcode": barcode, "unit": "EA", "price": "1", **extra}

    def errors(self, fn, *args, **kwargs):
        with self.assertRaises(svc.FieldErrors) as ctx:
            fn(*args, **kwargs)
        self.db.rollback()
        return ctx.exception.errors

    # ---- owner key on create ----
    def test_platform_md_and_its_details_get_owner_key_0(self):
        md = self.db.get(MdMaster, self.platform_md["id"])
        self.assertEqual((md.i_merchant_id, md.i_owner_key), (None, 0))
        svc.create_md_detail(self.db, md.i_md_id, self.detail("P-1"), 1)
        self.assertEqual({d.i_owner_key for d in self.db.query(MdDetail).filter(MdDetail.i_md_id == md.i_md_id)}, {0})

    # ---- barcode per owner ----
    def test_barcode_unique_inside_platform(self):
        other = svc.create_item(self.db, "md", self.md_payload("Platform Two"), 1)
        svc.create_md_detail(self.db, self.platform_md["id"], self.detail("880001"), 1)
        message = self.errors(svc.create_md_detail, self.db, other["id"], self.detail(" 880001 "), 1)["barcode"]
        self.assertEqual(message, f"Barcode 880001 is already used by {self.platform_md['code']} (Detail 2).")
        default = self.db.query(MdDetail).filter(MdDetail.i_md_id == other["id"], MdDetail.b_is_default.is_(True)).one()
        self.assertIn("barcode", self.errors(svc.update_md_detail, self.db, other["id"], default.i_md_detail_id,
                                             self.detail("880001", is_default=True), 1))  # Default Detail too
        self.assertFalse(svc.check_default_barcode(self.db, other["id"], "880001")["available"])

    def test_barcode_unique_inside_one_merchant_and_shared_by_its_stores(self):
        a, b = self.merchant_md(20, "MD90001"), self.merchant_md(20, "MD90002")
        svc.create_md_detail(self.db, a, self.detail("770001"), 1, merchant_id=20)
        message = self.errors(svc.create_md_detail, self.db, b, self.detail("770001"), 1, merchant_id=20)["barcode"]
        self.assertEqual(message, "Barcode 770001 is already used by MD90001 (Detail 2).")
        self.assertFalse(svc.check_default_barcode(self.db, b, "770001", merchant_id=20)["available"])
        # The database refuses it as well (a save that skipped the check, e.g. a race)
        self.db.add(MdDetail(i_md_id=b, i_owner_key=20, i_detail_no=9, c_barcode=" 770001", c_unit="EA", n_price=0))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_same_barcode_allowed_across_merchants_and_platform(self):
        a, b = self.merchant_md(20, "MD90001"), self.merchant_md(21, "MD90002")
        svc.create_md_detail(self.db, self.platform_md["id"], self.detail("660001"), 1)
        svc.create_md_detail(self.db, a, self.detail("660001"), 1, merchant_id=20)
        svc.create_md_detail(self.db, b, self.detail("660001"), 1, merchant_id=21)
        self.assertEqual(self.db.query(MdDetail).filter(MdDetail.c_barcode == "660001").count(), 3)
        self.assertFalse(svc.check_default_barcode(self.db, None, "660001")["available"])  # taken inside Platform
        self.assertTrue(svc.check_default_barcode(self.db, None, "660001", merchant_id=22)["available"])  # free elsewhere

    # ---- DB integrity ----
    def test_owner_key_mismatch_and_merchant_zero_are_refused_by_the_database(self):
        merchant_md = self.merchant_md(20, "MD90001")
        cases = (
            ("Detail owner differs from its MD", MdDetail(i_md_id=merchant_md, i_owner_key=0, i_detail_no=5, c_unit="EA",
                                                          n_price=0)),
            ("Detail owner of another Merchant", MdDetail(i_md_id=merchant_md, i_owner_key=21, i_detail_no=6, c_unit="EA",
                                                          n_price=0)),
            ("MD owner key differs from its Merchant", MdMaster(i_merchant_id=20, i_owner_key=0, c_item_code="MDX1",
                                                                c_item_name="x", c_item_kind="ITEM",
                                                                i_category1_id=self.setup_category(20))),
            ("Platform MD with a Merchant owner key", MdMaster(i_merchant_id=None, i_owner_key=20, c_item_code="MDX2",
                                                               c_item_name="x", c_item_kind="ITEM",
                                                               i_category1_id=self.setup_category())),
            ("Merchant ID 0", Merchant(i_merchant_id=0, c_merchant_code="MER00000", c_merchant_name="Zero")),
        )
        for label, row in cases:
            with self.subTest(label):
                self.db.add(row)
                with self.assertRaises(IntegrityError):
                    self.db.commit()
                self.db.rollback()
        with self.engine.begin() as conn:  # a raw UPDATE cannot split a Detail from its MD's owner either
            with self.assertRaises(IntegrityError):
                conn.execute(text("UPDATE tb_md_detail SET i_owner_key = 21 WHERE i_md_id = :md"), {"md": merchant_md})

    # ---- scope ----
    def test_other_merchant_and_platform_cannot_reach_a_merchant_md_detail(self):
        md = self.merchant_md(20, "MD90001")
        detail = self.db.query(MdDetail).filter(MdDetail.i_md_id == md).one().i_md_detail_id
        self.assertEqual(len(svc.list_md_details(self.db, md, merchant_id=20)), 1)
        for scope in (21, None):  # another Merchant, and Platform
            with self.subTest(scope=scope):
                for call in (lambda: svc.list_md_details(self.db, md, merchant_id=scope),
                             lambda: svc.create_md_detail(self.db, md, self.detail("X1"), 1, merchant_id=scope),
                             lambda: svc.update_md_detail(self.db, md, detail, self.detail("X2"), 1, merchant_id=scope),
                             lambda: svc.delete_md_detail(self.db, md, detail, merchant_id=scope)):
                    with self.assertRaises(svc.NotFound):
                        call()
                    self.db.rollback()
        self.assertEqual(self.db.query(MdDetail).filter(MdDetail.i_md_id == md).count(), 1)
        # A Merchant cannot reach the Platform MD's details either
        with self.assertRaises(svc.NotFound):
            svc.list_md_details(self.db, self.platform_md["id"], merchant_id=20)

    def test_platform_md_screens_show_platform_md_only(self):
        self.merchant_md(20, "MD90001")
        listed = [i["code"] for i in svc.list_items(self.db, "md", size=100)["items"]]
        self.assertEqual(listed, [self.platform_md["code"]])
        merchant_md = self.db.query(MdMaster).filter(MdMaster.c_item_code == "MD90001").one().i_md_id
        with self.assertRaises(svc.NotFound):
            svc.update_item(self.db, "md", merchant_md, self.md_payload("Hijack"), 1)
        self.db.rollback()
        # STEP 2 Merchant MD: the Merchant lists only its own MD (never the Platform one).
        self.assertEqual([i["code"] for i in svc.list_items(self.db, "md", merchant_id=20)["items"]], ["MD90001"])
        self.assertEqual(svc.list_items(self.db, "md", merchant_id=12)["total"], 0)


class MdOwnerScopeMigrationTests(unittest.TestCase):
    """The migration on an existing database where a generic startup step already added the three columns
    (nullable, empty): they are kept and completed, the data is unchanged, the barcode index is swapped."""

    def test_existing_nullable_columns_are_completed_and_data_kept(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_merchant (i_merchant_id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE tb_md_master (i_md_id INTEGER PRIMARY KEY, c_item_code VARCHAR(20), "
                              "i_merchant_id INTEGER, i_owner_key INTEGER)"))
            conn.execute(text("CREATE TABLE tb_md_detail (i_md_detail_id INTEGER PRIMARY KEY, i_md_id INTEGER, "
                              "i_detail_no INTEGER, b_is_default BOOLEAN, c_barcode VARCHAR(100), c_unit VARCHAR(30), "
                              "n_price NUMERIC, i_owner_key INTEGER)"))
            conn.execute(text(f"CREATE UNIQUE INDEX {LEGACY_UNIQUE_INDEX} ON tb_md_detail ((trim(c_barcode))) "
                              "WHERE c_barcode IS NOT NULL AND trim(c_barcode) <> ''"))
            conn.execute(text("INSERT INTO tb_md_master (i_md_id, c_item_code) VALUES (1, 'MD00001'), (2, 'MD00002')"))
            conn.execute(text("INSERT INTO tb_md_detail (i_md_detail_id, i_md_id, i_detail_no, b_is_default, c_barcode, "
                              "c_unit, n_price) VALUES (1, 1, 1, 1, '8801116056934', 'EA', 1), (2, 2, 1, 1, NULL, 'EA', 2), "
                              "(3, 2, 2, 0, '047400145009', 'EA', 3)"))
        rows = lambda: engine.connect().execute(text(
            "SELECT i_md_detail_id, i_md_id, i_detail_no, b_is_default, c_barcode, c_unit, n_price FROM tb_md_detail "
            "ORDER BY 1")).all()
        before = rows()
        with redirect_stdout(io.StringIO()):
            report = migrate_md_owner_scope(engine)
        self.assertEqual(report["duplicates"], [])
        self.assertEqual(rows(), before)  # IDs and values unchanged
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT i_owner_key FROM tb_md_master ORDER BY 1")).scalars().all(), [0, 0])
            self.assertEqual(conn.execute(text("SELECT i_owner_key FROM tb_md_detail ORDER BY 1")).scalars().all(), [0, 0, 0])
            names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'index'"))}
        self.assertIn(UNIQUE_INDEX, names)
        self.assertNotIn(LEGACY_UNIQUE_INDEX, names)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(migrate_md_owner_scope(engine)["steps"], [])  # re-run: nothing to do
        self.assertEqual(rows(), before)


if __name__ == "__main__":
    unittest.main()
