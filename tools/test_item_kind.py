"""Item Kind System Master (step 1): auto Item Kind No (IKC00001), protected System Codes, default ITEM,
in-use check and the additive migration for existing databases."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.item_kind_migration import SYSTEM_ITEM_KIND_CODES, SYSTEM_ITEM_KINDS, migrate_item_kinds
from backend.database.pg_platform import PlatformBase
from backend.models_admin.invoice import InvoiceLine
from backend.models_admin.master_group import Category1, Category2, Category3, ItemKind, MdDetail, MdMaster, Tax
from backend.models_admin.store_location import StoreLocation, StoreMdLocation
from backend.services import master_group as svc

TABLES = (Tax, ItemKind, Category1, Category2, Category3, MdMaster, MdDetail, InvoiceLine, StoreLocation, StoreMdLocation)


def sqlite_engine():
    return create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


class ItemKindMigrationTests(unittest.TestCase):
    def test_existing_table_gets_numbers_without_changing_its_rows(self):
        engine = sqlite_engine()
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:  # a database from before the Item Kind No column
            conn.execute(text("""CREATE TABLE tb_item_kind (
                i_item_kind_id INTEGER PRIMARY KEY, c_item_kind_code VARCHAR(30) NOT NULL UNIQUE,
                c_item_kind_name VARCHAR(100) NOT NULL, i_display_order INTEGER NOT NULL, b_active BOOLEAN NOT NULL,
                c_notes TEXT, dt_created TIMESTAMP, dt_updated TIMESTAMP, i_created_by INTEGER, i_updated_by INTEGER)"""))
            for row_id, (code, name) in enumerate(SYSTEM_ITEM_KINDS, start=1):
                order = 10 * (len(SYSTEM_ITEM_KINDS) - row_id + 1) if code == "OTHER" else 10 * row_id
                conn.execute(text("INSERT INTO tb_item_kind VALUES (:id, :code, :name, :sort, :active, 'kept', NULL, NULL, NULL, NULL)"),
                             {"id": row_id, "code": code, "name": f"{name} (renamed)", "sort": order,
                              "active": code != "LOTTERY"})
        before = self.rows(engine, "i_item_kind_id, c_item_kind_code, c_item_kind_name, i_display_order, b_active, c_notes")

        migrate_item_kinds(engine)
        migrate_item_kinds(engine)  # rerun: nothing changes, nothing renumbered

        self.assertEqual(self.rows(engine, "i_item_kind_id, c_item_kind_code, c_item_kind_name, i_display_order, b_active, c_notes"),
                         before)
        numbers = dict(self.rows(engine, "c_item_kind_code, c_item_kind_no"))
        self.assertEqual(numbers["ITEM"], "IKC00001")
        self.assertEqual(sorted(numbers.values()), [f"IKC{n:05d}" for n in range(1, 10)])
        # Display order, then id: OTHER was moved to order 10 (same as ITEM, higher id) so it is second.
        self.assertEqual(numbers["OTHER"], "IKC00002")
        indexes = {i["name"]: i for i in inspect(engine).get_indexes("tb_item_kind")}
        self.assertTrue(indexes["ix_tb_item_kind_c_item_kind_no"]["unique"])

    def test_new_database_seed_numbers_system_codes_in_order(self):
        engine = sqlite_engine()
        self.addCleanup(engine.dispose)
        PlatformBase.metadata.create_all(engine, tables=[ItemKind.__table__])
        migrate_item_kinds(engine)
        self.assertEqual(self.rows(engine, "c_item_kind_code, c_item_kind_no"),
                         [(code, f"IKC{n:05d}") for n, (code, _name) in enumerate(SYSTEM_ITEM_KINDS, start=1)])

    @staticmethod
    def rows(engine, columns):
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(f"SELECT {columns} FROM tb_item_kind ORDER BY i_item_kind_id"))]


class ItemKindRuleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="awsum-item-kind-")
        self.addCleanup(directory.cleanup)
        storage = patch.object(svc.images, "image_storage_root", return_value=Path(directory.name))
        storage.start()
        self.addCleanup(storage.stop)
        self.engine = sqlite_engine()
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine, tables=[m.__table__ for m in TABLES])
        migrate_item_kinds(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)

    def kind(self, code):
        return self.db.query(ItemKind).filter(ItemKind.c_item_kind_code == code).one()

    def md(self, item_kind):
        c1 = svc.create_item(self.db, "category1", {"name": "Hardware"}, 1)
        return svc.create_item(self.db, "md", {"name": "POS", "item_kind": item_kind, "category1_id": c1["id"],
                                               "unit": "EA", "retail_price": "0", "billing_cycle": "ONE_TIME"}, 1)

    def test_system_codes_are_protected_and_default_item_stays_active(self):
        self.assertEqual(SYSTEM_ITEM_KIND_CODES, {code for code, _ in SYSTEM_ITEM_KINDS})
        gift = self.kind("GIFT_CARD")
        item = svc.update_item(self.db, "item_kind", gift.i_item_kind_id,
                               {"name": "E-Gift", "code": "X", "system_code": "X", "active": False}, 1)
        self.assertEqual((item["code"], item["system_code"], item["name"], item["active"], item["system"]),
                         ("IKC00004", "GIFT_CARD", "E-Gift", False, True))
        with self.assertRaises(svc.Conflict):
            svc.delete_item(self.db, "item_kind", gift.i_item_kind_id)
        default = self.kind("ITEM")
        with self.assertRaises(svc.FieldErrors) as ctx:
            svc.update_item(self.db, "item_kind", default.i_item_kind_id, {"name": "Item", "active": False}, 1)
        self.assertIn("active", ctx.exception.errors)
        self.db.rollback()
        self.assertTrue(self.kind("ITEM").b_active)
        self.assertEqual(svc.serialize(self.db, "item_kind", self.kind("ITEM"))["active_locked"], True)

    def test_added_kind_has_its_own_system_code_and_is_not_treated_as_item(self):
        # The System Code is entered (upper-cased) and validated; the IKC number is issued separately.
        for bad, message in (("", "System Code is required."), ("9X", None), ("RENT-AL", None),
                             ("IKC00099", "IKC is reserved for the auto-issued Item Kind No."),
                             ("gift_card", "This System Code already exists.")):
            with self.subTest(bad=bad):
                with self.assertRaises(svc.FieldErrors) as ctx:
                    svc.create_item(self.db, "item_kind", {"name": "X", "system_code": bad}, 1)
                self.assertIn("system_code", ctx.exception.errors)
                if message:
                    self.assertEqual(ctx.exception.errors["system_code"], message)
                self.db.rollback()
        added = svc.create_item(self.db, "item_kind", {"name": "Rental", "system_code": " rental ", "code": "X",
                                                       "display_order": 95}, 1)
        self.assertEqual((added["code"], added["system_code"], added["system"], added["active_locked"], added["logic_defined"]),
                         ("IKC00010", "RENTAL", False, False, False))
        # The System Code never changes after create.
        updated = svc.update_item(self.db, "item_kind", added["id"], {"name": "Rentals", "system_code": "OTHER2"}, 1)
        self.assertEqual((updated["code"], updated["system_code"]), ("IKC00010", "RENTAL"))
        # No program logic yet: not offered to MD Master, and an MD save never accepts it (never treated as ITEM).
        self.assertNotIn("RENTAL", [k["code"] for k in svc.lookup_options(self.db)["item_kinds"]])
        with self.assertRaises(svc.FieldErrors) as ctx:
            self.md("RENTAL")
        self.assertIn("program logic", ctx.exception.errors["item_kind"])
        self.db.rollback()
        second = svc.create_item(self.db, "item_kind", {"name": "Deposit", "system_code": "DEPOSIT"}, 1)
        self.assertEqual(second["code"], "IKC00011")
        svc.delete_item(self.db, "item_kind", second["id"])  # unused added codes can be deleted
        self.assertIsNone(self.db.get(ItemKind, second["id"]))
        # The 9 System Codes keep their logic and stay selectable.
        self.assertEqual([k["code"] for k in svc.lookup_options(self.db)["item_kinds"]], [c for c, _ in SYSTEM_ITEM_KINDS])
        # A required System Code in use by MD reports in_use too (its delete is blocked either way).
        self.assertTrue(svc.serialize(self.db, "item_kind", self.kind("ITEM"))["in_use"] is False)
        self.md("ITEM")
        self.assertTrue(svc.serialize(self.db, "item_kind", self.kind("ITEM"))["in_use"])

    def test_search_sort_and_export_use_code_and_system_code(self):
        found = svc.list_items(self.db, "item_kind", q="gift")
        self.assertEqual({r["system_code"] for r in found["items"]}, {"GIFT_CARD", "GIFT_CERTIFICATE"})
        self.assertEqual([r["system_code"] for r in svc.list_items(self.db, "item_kind", q="IKC00003")["items"]], ["ADMISSION"])
        by_system = svc.list_items(self.db, "item_kind", sort="system_code", size=100)["items"]
        self.assertEqual([r["system_code"] for r in by_system][:2], ["ADMISSION", "COUPON"])
        by_code = svc.list_items(self.db, "item_kind", sort="code", direction="desc", size=100)["items"]
        self.assertEqual(by_code[0]["code"], "IKC00009")
        headers, data = svc.export_table(self.db, "item_kind")
        self.assertEqual(headers, ["Code", "System Code", "Display Name", "Display Order", "Status", "Notes"])
        self.assertEqual(data[0][:3], ["IKC00001", "ITEM", "Item"])

    def test_md_item_kind_rules_are_unchanged(self):
        self.assertEqual(self.md("SERVICE")["item_kind"], "SERVICE")
        gift = self.kind("GIFT_CARD")
        svc.update_item(self.db, "item_kind", gift.i_item_kind_id, {"name": "Gift Card", "active": False}, 1)
        with self.assertRaises(svc.FieldErrors) as ctx:
            self.md("GIFT_CARD")
        self.assertIn("item_kind", ctx.exception.errors)


if __name__ == "__main__":
    unittest.main()
