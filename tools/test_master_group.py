import json
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.pg_platform import PlatformBase
from backend.models_admin.invoice import InvoiceLine
from backend.models_admin.master_group import Category1, Category2, Category3, ItemKind, MdDetail, MdMaster, Tax
from backend.database.item_kind_migration import migrate_item_kinds
from backend.routers import master_group as router_module
from backend.services import master_group as svc


class MasterGroupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="awsum-master-group-")
        self.addCleanup(directory.cleanup)
        storage = patch.object(svc.images, "image_storage_root", return_value=Path(directory.name))
        storage.start()
        self.addCleanup(storage.stop)
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        tables = [m.__table__ for m in (Tax, ItemKind, Category1, Category2, Category3, MdMaster, MdDetail, InvoiceLine)]
        PlatformBase.metadata.create_all(engine, tables=tables)
        migrate_item_kinds(engine)
        self.Session = sessionmaker(bind=engine)
        self.engine = engine
        self.db = self.Session()
        self.addCleanup(self.db.close)

    def md_payload(self, **kw):
        c1 = svc.create_item(self.db, "category1", {"name": "Hardware"}, 1)
        base = {
            "name": "POS Terminal", "item_kind": "ITEM", "category1_id": c1["id"], "unit": "EA",
            "retail_price": "0", "billing_cycle": "ONE_TIME",
        }
        base.update(kw)
        return base

    def errors(self, entity, payload):
        with self.assertRaises(svc.FieldErrors) as ctx:
            svc.create_item(self.db, entity, payload, 1)
        return ctx.exception.errors

    # --- codes ---
    def test_codes_have_no_underscore_and_survive_rename(self):
        a = svc.create_item(self.db, "tax", {"name": "VAT", "rate": "0"}, 1)
        b = svc.create_item(self.db, "tax", {"name": "GST", "rate": "5.25"}, 1)
        self.assertEqual((a["code"], b["code"]), ("TAX00001", "TAX00002"))
        renamed = svc.update_item(self.db, "tax", a["id"], {"name": "VAT2", "rate": "10", "code": "HACK"}, 1)
        self.assertEqual(renamed["code"], "TAX00001")
        self.assertNotIn("_", renamed["code"])

    def test_code_continues_after_delete_of_older_record(self):
        a = svc.create_item(self.db, "category1", {"name": "A"}, 1)
        svc.create_item(self.db, "category1", {"name": "B"}, 1)
        svc.delete_item(self.db, "category1", a["id"])
        self.assertEqual(svc.create_item(self.db, "category1", {"name": "C"}, 1)["code"], "CA100003")

    # --- tax ---
    def test_tax_decimal_rules(self):
        zero = svc.create_item(self.db, "tax", {"name": "Exempt", "rate": 0}, 1)
        self.assertEqual(zero["rate"], "0.0000")
        self.assertIn("rate", self.errors("tax", {"name": "x", "rate": "-1"}))
        self.assertIn("rate", self.errors("tax", {"name": "x", "rate": "100.1"}))
        self.assertIn("rate", self.errors("tax", {"name": "x", "rate": "abc"}))
        self.assertIn("rate", self.errors("tax", {"name": "x", "rate": "NaN"}))
        self.assertIn("rate", self.errors("tax", {"name": "x", "rate": "1.00001"}))
        self.assertIn("name", self.errors("tax", {"name": " ", "rate": "1"}))

    # --- categories ---
    def test_category3_tax_migration_preserves_existing_rows_and_is_repeatable(self):
        from backend.database.master_scope_migration import migrate_master_scopes

        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_category3 (i_category3_id INTEGER PRIMARY KEY, c_category_name TEXT)"))
            conn.execute(text("INSERT INTO tb_category3 VALUES (1, 'Existing')"))
        migrate_master_scopes(engine)
        migrate_master_scopes(engine)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT i_category3_id, c_category_name, i_tax1_id, i_tax2_id, i_tax3_id FROM tb_category3"
            )).one()
            self.assertEqual(tuple(row), (1, "Existing", None, None, None))
            self.assertEqual(
                {fk["constrained_columns"][0] for fk in inspect(conn).get_foreign_keys("tb_category3")},
                {"i_merchant_id", "i_tax1_id", "i_tax2_id", "i_tax3_id"},
            )

    def test_category3_tax_slots_are_scoped_nullable_and_distinct(self):
        for merchant_id in (None, 42):
            with self.subTest(merchant_id=merchant_id):
                def create(entity, payload):
                    return svc.create_item(self.db, entity, payload, 1, merchant_id=merchant_id)

                c1 = create("category1", {"name": "One"})
                c2 = create("category2", {"name": "Two", "category1_id": c1["id"]})
                zero = create("tax", {"name": "Exempt", "rate": "0"})
                other = create("tax", {"name": "Sales", "rate": "6.25"})
                payload = {"name": "Three", "category1_id": c1["id"], "category2_id": c2["id"]}
                category = create("category3", payload)
                self.assertEqual([category[f"tax{n}_id"] for n in (1, 2, 3)], [None] * 3)
                item = svc.update_item(self.db, "category3", category["id"], {
                    **payload, "tax1_id": zero["id"], "tax2_id": other["id"], "tax3_id": "",
                }, 1, merchant_id=merchant_id)
                self.assertEqual([item[f"tax{n}_id"] for n in (1, 2, 3)], [zero["id"], other["id"], None])
                lookup = next(row for row in svc.lookup_options(self.db, merchant_id=merchant_id)["category3"]
                              if row["id"] == category["id"])
                self.assertEqual([lookup[f"tax{n}_id"] for n in (1, 2, 3)], [zero["id"], other["id"], None])
                unchanged = svc.update_item(self.db, "category3", category["id"], payload, 1, merchant_id=merchant_id)
                self.assertEqual(unchanged["tax1_id"], zero["id"])
                with self.assertRaises(svc.FieldErrors) as duplicate:
                    create("category3", {**payload, "tax1_id": zero["id"], "tax2_id": zero["id"]})
                self.assertIn("tax2_id", duplicate.exception.errors)
                foreign = svc.create_item(self.db, "tax", {"name": "Foreign", "rate": "1"}, 1, merchant_id=99)
                for tax_id in (foreign["id"], 99999, 0):
                    with self.assertRaises(svc.FieldErrors):
                        create("category3", {**payload, "tax1_id": tax_id})
                options = svc.lookup_options(self.db, merchant_id=merchant_id)["taxes"]
                self.assertNotIn(foreign["id"], [option["id"] for option in options])
                self.assertEqual(next(option["rate"] for option in options if option["id"] == zero["id"]), "0.0000")
                with self.assertRaises(svc.Conflict):
                    svc.delete_item(self.db, "tax", zero["id"], merchant_id=merchant_id)
                cleared = svc.update_item(self.db, "category3", category["id"], {
                    **payload, "tax1_id": None, "tax2_id": "", "tax3_id": None,
                }, 1, merchant_id=merchant_id)
                self.assertIsNone(cleared["tax1_id"])

    def test_category_chain_validation(self):
        c1 = svc.create_item(self.db, "category1", {"name": "One"}, 1)
        other = svc.create_item(self.db, "category1", {"name": "Other"}, 1)
        c2 = svc.create_item(self.db, "category2", {"name": "Two", "category1_id": c1["id"]}, 1)
        self.assertIn("category1_id", self.errors("category2", {"name": "x"}))
        self.assertIn("category2_id", self.errors("category3", {"name": "x", "category1_id": other["id"], "category2_id": c2["id"]}))
        c3 = svc.create_item(self.db, "category3", {"name": "Three", "category1_id": c1["id"], "category2_id": c2["id"]}, 1)
        self.assertEqual(c3["category2_name"], "Two")

    def test_used_category_cannot_be_deleted_but_can_be_deactivated(self):
        c1 = svc.create_item(self.db, "category1", {"name": "One"}, 1)
        svc.create_item(self.db, "category2", {"name": "Two", "category1_id": c1["id"]}, 1)
        with self.assertRaises(svc.Conflict):
            svc.delete_item(self.db, "category1", c1["id"])
        out = svc.update_item(self.db, "category1", c1["id"], {"name": "One", "active": False}, 1)
        self.assertFalse(out["active"])

    def test_inactive_parent_rejected_for_new_child(self):
        c1 = svc.create_item(self.db, "category1", {"name": "One"}, 1)
        svc.update_item(self.db, "category1", c1["id"], {"name": "One", "active": False}, 1)
        self.assertIn("category1_id", self.errors("category2", {"name": "x", "category1_id": c1["id"]}))

    # --- md master ---
    def test_md_create_and_defaults(self):
        item = svc.create_item(self.db, "md", self.md_payload(), 1)
        self.assertEqual(item["code"], "MD00001")
        self.assertEqual(item["retail_price"], "0.00")
        self.assertFalse(item["license_required"])
        self.assertIsNone(item["license_type"])
        self.assertFalse(item["serial_required"])
        details = svc.list_md_details(self.db, item["id"])
        self.assertEqual(len(details), 1)
        self.assertEqual(
            (details[0]["detail_no"], details[0]["is_default"], details[0]["unit"], details[0]["price"]),
            (1, True, "EA", "0.00"),
        )

    def test_md_detail_add_update_default_and_delete(self):
        item = svc.create_item(self.db, "md", self.md_payload(retail_price="12.50"), 1)
        default = svc.list_md_details(self.db, item["id"])[0]
        second = svc.create_md_detail(self.db, item["id"], {
            "barcode": "123456789012", "unit": "Pack", "pk_size": "6-pack",
            "size": "Large", "cost": "4.25", "price": "25.00", "modifier": "Extra",
        }, 1)
        self.assertEqual((second["detail_no"], second["is_default"]), (2, False))
        with self.assertRaises(svc.Conflict):
            svc.delete_md_detail(self.db, item["id"], default["id"])

        updated = svc.update_md_detail(self.db, item["id"], second["id"], {
            "barcode": "123456789012", "unit": "Pack", "pk_size": "6-pack",
            "size": "Large", "cost": "4.50", "price": "30.00", "modifier": "No ice",
            "is_default": True,
        }, 2)
        self.assertTrue(updated["is_default"])
        self.assertEqual(self.db.get(MdMaster, item["id"]).n_default_unit_price, Decimal("30.00"))
        first = next(detail for detail in svc.list_md_details(self.db, item["id"]) if detail["id"] == default["id"])
        self.assertFalse(first["is_default"])
        with self.assertRaises(svc.FieldErrors):
            svc.update_md_detail(self.db, item["id"], updated["id"], {
                "unit": "Pack", "price": "30", "is_default": False,
            }, 2)
        svc.delete_md_detail(self.db, item["id"], default["id"])
        self.assertEqual(len(svc.list_md_details(self.db, item["id"])), 1)

    def test_md_create_and_legacy_update_keep_default_detail_compatible(self):
        item = svc.create_item(
            self.db, "md", self.md_payload(retail_price="15.25", cost="5.00"), 1, can_cost=True,
        )
        detail = svc.list_md_details(self.db, item["id"])[0]
        self.assertEqual((detail["price"], detail["cost"]), ("15.25", "5.00"))
        payload = self.md_payload(retail_price="19.50", cost="6.00")
        svc.update_item(self.db, "md", item["id"], payload, 1, can_cost=True)
        detail = svc.list_md_details(self.db, item["id"])[0]
        self.assertEqual((detail["price"], detail["cost"]), ("19.50", "6.00"))

    def test_md_detail_migration_backfills_legacy_values_idempotently(self):
        from backend.database.md_detail_migration import migrate_md_details

        item = svc.create_item(
            self.db, "md", self.md_payload(retail_price="7.75", cost="2.25"), 1, can_cost=True,
        )
        self.db.query(MdDetail).filter(MdDetail.i_md_id == item["id"]).delete()
        self.db.commit()
        migrate_md_details(self.engine)
        migrate_md_details(self.engine)
        detail = svc.list_md_details(self.db, item["id"])[0]
        self.assertEqual(
            (detail["detail_no"], detail["is_default"], detail["unit"], detail["price"], detail["cost"]),
            (1, True, "EA", "7.75", "2.25"),
        )

    def test_md_second_name_saved_and_migration_is_repeatable(self):
        from sqlalchemy import create_engine, inspect, text
        from backend.database.md_second_name_migration import migrate_md_second_name

        item = svc.create_item(self.db, "md", self.md_payload(second_name="  Alt Name  "), 1)
        self.assertEqual(item["second_name"], "Alt Name")
        item = svc.update_item(self.db, "md", item["id"], self.md_payload(second_name=""), 1)
        self.assertIsNone(item["second_name"])
        self.assertIn("second_name", self.errors("md", self.md_payload(second_name="x" * 201)))

        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_md_master (i_md_id INTEGER PRIMARY KEY, c_item_code VARCHAR(20))"))
            conn.execute(text("INSERT INTO tb_md_master VALUES (42, 'MD00042')"))
        migrate_md_second_name(engine)
        migrate_md_second_name(engine)
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT * FROM tb_md_master")).one(), (42, "MD00042", None))
        self.assertIn("c_second_name", {col["name"] for col in inspect(engine).get_columns("tb_md_master")})

    def test_inventory_type_common_behavior(self):
        from backend.services import inventory as inv
        table = {b.type_id: (b.code, b.name, b.tracks_onhand, b.deduct_on, b.cost_recognition, b.keep_required)
                 for b in inv.inventory_types()}
        self.assertEqual(table, {
            1: ("NONE", "None", False, "none", "sale", False),
            2: ("STOCK", "Stock", True, "sale", "sale", True),
            3: ("RECIPE", "Recipe", False, "bom", "bom", False),
            4: ("EXPENSE_ON_RECEIPT", "Consume on Receipt", False, "receipt", "receipt", False),
            5: ("EXPENSE_ON_ISSUE", "Consume on Issue", True, "issue", "issue", False),
        })
        self.assertEqual([b.type_id for b in inv.inventory_types()], [1, 2, 3, 4, 5])
        self.assertEqual((inv.NONE, inv.STOCK, inv.RECIPE, inv.EXPENSE_ON_RECEIPT, inv.EXPENSE_ON_ISSUE), (1, 2, 3, 4, 5))
        for bad in (0, 6, None, "2", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                inv.inventory_behavior(bad)
        self.assertEqual((inv.keep_name("COLD"), inv.keep_name(None)), ("Cold", ""))
        # Effective values: MD override > Category III > system default (1 None / DRY); NULL means inherit.
        self.assertEqual((inv.DEFAULT_INVENTORY_TYPE, inv.DEFAULT_KEEP), (1, "DRY"))
        r = inv.resolve_inventory(None, None, None, None)
        self.assertEqual((r.behavior.type_id, r.keep, r.type_source, r.keep_source), (1, "DRY", "default", "default"))
        r = inv.resolve_inventory(2, None, None, None)
        self.assertEqual((r.behavior.type_id, r.keep, r.keep_source), (2, "DRY", "default"))
        r = inv.resolve_inventory(None, None, 2, "COLD")
        self.assertEqual((r.behavior.type_id, r.keep, r.type_source, r.keep_source), (2, "COLD", "category", "category"))
        r = inv.resolve_inventory(5, "FROZEN", 2, "COLD")
        self.assertEqual((r.behavior.type_id, r.keep, r.type_source, r.keep_source), (5, "FROZEN", "item", "item"))

    def _category3(self, **kw):
        c1 = svc.create_item(self.db, "category1", {"name": f"Group {len(kw)}{kw.get('name', '')}"}, 1)
        c2 = svc.create_item(self.db, "category2", {"name": "Sub", "category1_id": c1["id"]}, 1)
        c3 = svc.create_item(self.db, "category3", {"name": "Leaf", "category1_id": c1["id"],
                                                    "category2_id": c2["id"], **kw}, 1)
        return c1, c2, c3

    def test_md_inventory_overrides_inherit_from_category3(self):
        c1, c2, c3 = self._category3(inventory_type=2, keep="COLD")
        self.assertEqual((c3["inventory_type"], c3["keep"]), (2, "COLD"))
        cats = dict(category1_id=c1["id"], category2_id=c2["id"], category3_id=c3["id"])
        # New MD stores NULL (inherit) and resolves through Category III.
        item = svc.create_item(self.db, "md", self.md_payload(**cats), 1)
        self.assertEqual((item["inventory_type"], item["keep"]), (None, None))
        self.assertEqual((item["effective_inventory_type"], item["effective_keep"], item["inventory_type_source"],
                          item["keep_source"]), (2, "COLD", "category", "category"))
        row = self.db.get(MdMaster, item["id"])
        self.assertEqual((row.i_inventory_type, row.c_keep), (None, None))
        # Override, keep saved values when keys are absent, then return to inherit with blanks.
        item = svc.update_item(self.db, "md", item["id"], self.md_payload(**cats, inventory_type="5", keep=" frozen "), 1)
        self.assertEqual((item["inventory_type"], item["keep"], item["effective_inventory_type"], item["keep_source"]),
                         (5, "FROZEN", 5, "item"))
        item = svc.update_item(self.db, "md", item["id"], self.md_payload(**cats, active=False), 1)
        self.assertEqual((item["inventory_type"], item["keep"]), (5, "FROZEN"))
        item = svc.update_item(self.db, "md", item["id"], self.md_payload(**cats, inventory_type="", keep=None), 1)
        self.assertEqual((item["inventory_type"], item["keep"], item["effective_inventory_type"]), (None, None, 2))
        # An item overriding Stock inherits Keep; clearing Category III falls back to the system default Dry.
        leaf = {"name": "Leaf", "category1_id": c1["id"], "category2_id": c2["id"]}
        stock = svc.create_item(self.db, "md", self.md_payload(**cats, name="Cola", inventory_type=2), 1)
        self.assertEqual((stock["keep"], stock["effective_keep"]), (None, "COLD"))
        cleared = svc.update_item(self.db, "category3", c3["id"], {**leaf, "inventory_type": "", "keep": ""}, 1)
        self.assertEqual((cleared["inventory_type"], cleared["keep"]), (None, None))
        inherited = svc.serialize(self.db, "md", self.db.get(MdMaster, stock["id"]))
        self.assertEqual((inherited["keep"], inherited["effective_keep"], inherited["keep_source"]), (None, "DRY", "default"))
        svc.update_item(self.db, "category3", c3["id"], {**leaf, "inventory_type": 2, "keep": "COLD"}, 1)
        # Export shows effective values and marks inherited ones.
        headers, rows = svc.export_table(self.db, "md", sort="name")
        exported = [dict(zip(headers, line)) for line in rows]
        self.assertEqual([(e["Item Name"], e["Inventory Type"], e["Keep"]) for e in exported],
                         [("Cola", "Stock", "Cold (Category III)"), ("POS Terminal", "Stock (Category III)", "Cold (Category III)")])
        headers, rows = svc.export_table(self.db, "category3")
        self.assertEqual((dict(zip(headers, rows[0]))["Inventory Type"], dict(zip(headers, rows[0]))["Keep"]), ("Stock", "Cold"))
        self.assertEqual([(c["inventory_type"], c["keep"]) for c in svc.lookup_options(self.db)["category3"]], [(2, "COLD")])

    def test_md_inventory_keep_required_uses_effective_type(self):
        # Without Category III values the effective type is the default (1 None): Keep optional.
        item = svc.create_item(self.db, "md", self.md_payload(), 1)
        self.assertEqual((item["inventory_type"], item["effective_inventory_type"], item["inventory_type_source"]),
                         (None, 1, "default"))
        # Stock override without Keep anywhere resolves to the system default Dry (stored NULL).
        bare = svc.create_item(self.db, "md", self.md_payload(name="Bare", inventory_type=2), 1)
        self.assertEqual((bare["keep"], bare["effective_keep"], bare["keep_source"]), (None, "DRY", "default"))
        self.assertIsNone(self.db.get(MdMaster, bare["id"]).c_keep)
        c1, c2, c3 = self._category3(name="K", keep="DRY")
        stock = svc.create_item(self.db, "md", self.md_payload(
            category1_id=c1["id"], category2_id=c2["id"], category3_id=c3["id"], inventory_type=2), 1)
        self.assertEqual((stock["keep"], stock["effective_keep"], stock["keep_source"]), (None, "DRY", "category"))
        # Category III may leave Keep unset (system default Dry); both fields may stay unset.
        _, _, s3 = self._category3(name="S", inventory_type=2)
        self.assertEqual((s3["inventory_type"], s3["keep"]), (2, None))
        self.assertEqual(svc.lookup_options(self.db)["default_keep"], "DRY")
        _, _, plain = self._category3(name="P")
        self.assertEqual((plain["inventory_type"], plain["keep"]), (None, None))
        # Only Inventory Type numbers 1-5 and Keep codes are accepted, never display names.
        for bad in ("0", "6", "Stock", "2.5", True):
            with self.subTest(bad=bad):
                self.assertIn("inventory_type", self.errors("md", self.md_payload(inventory_type=bad)))
        self.assertIn("keep", self.errors("md", self.md_payload(keep="Chilled")))
        options = svc.lookup_options(self.db)
        self.assertEqual([(t["type_id"], t["name"], t["keep_required"], t["cost_recognition"]) for t in options["inventory_types"]], [
            (1, "None", False, "sale"), (2, "Stock", True, "sale"), (3, "Recipe", False, "bom"),
            (4, "Consume on Receipt", False, "receipt"), (5, "Consume on Issue", False, "issue")])
        self.assertEqual([k["code"] for k in options["keep_types"]], ["DRY", "COLD", "FROZEN"])
        self.assertEqual(options["default_inventory_type"], 1)

    def test_md_inventory_migration_keeps_existing_rows_null_and_is_repeatable(self):
        from sqlalchemy import create_engine, inspect, text
        from backend.database.md_inventory_migration import migrate_md_inventory

        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_md_master (i_md_id INTEGER PRIMARY KEY, c_item_code VARCHAR(20))"))
            conn.execute(text("CREATE TABLE tb_category3 (i_category3_id INTEGER PRIMARY KEY, c_category_code VARCHAR(20))"))
            conn.execute(text("INSERT INTO tb_md_master VALUES (42, 'MD00042')"))
            conn.execute(text("INSERT INTO tb_category3 VALUES (7, 'CA300007')"))
        migrate_md_inventory(engine)
        migrate_md_inventory(engine)
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT * FROM tb_md_master")).one(), (42, "MD00042", None, None))
            self.assertEqual(conn.execute(text("SELECT * FROM tb_category3")).one(), (7, "CA300007", None, None))
        for table in ("tb_md_master", "tb_category3"):
            columns = {col["name"]: col for col in inspect(engine).get_columns(table)}
            self.assertTrue(columns["i_inventory_type"]["nullable"] and columns["c_keep"]["nullable"])
            self.assertIsNone(columns["i_inventory_type"]["default"])
            self.assertIsNone(columns["c_keep"]["default"])

    def test_md_inventory_migration_maps_earlier_text_codes_once(self):
        from sqlalchemy import create_engine, text
        from backend.database.md_inventory_migration import migrate_md_inventory

        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_md_master (i_md_id INTEGER PRIMARY KEY, "
                              "c_inventory_option VARCHAR(20), c_keep VARCHAR(20))"))
            conn.execute(text("INSERT INTO tb_md_master VALUES (1, 'NONE', 'NONE'), (2, 'STOCK', 'COLD'), "
                              "(3, 'RECIPE', NULL), (4, 'RECEIVE_CONSUME', 'DRY'), (5, NULL, NULL)"))
        migrate_md_inventory(engine)
        with engine.begin() as conn:
            conn.execute(text("UPDATE tb_md_master SET i_inventory_type = 5 WHERE i_md_id = 5"))
        migrate_md_inventory(engine)  # repeat run must not re-map over later edits
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT i_md_id, c_inventory_option, c_keep, i_inventory_type "
                                     "FROM tb_md_master ORDER BY i_md_id")).all()
        # The old 'NONE' default is not an explicit choice: it stays NULL (inherit).
        self.assertEqual(rows, [(1, "NONE", None, None), (2, "STOCK", "COLD", 2), (3, "RECIPE", None, 3),
                                (4, "RECEIVE_CONSUME", "DRY", 4), (5, None, None, 5)])

    def _default_detail(self, md_id):
        return next(d for d in svc.list_md_details(self.db, md_id) if d["is_default"])

    def _detail_payload(self, **kw):
        return {"unit": "EA", "price": "1.00", **kw}

    def test_barcode_unique_across_all_items_and_details(self):
        a = svc.create_item(self.db, "md", self.md_payload(name="A"), 1)
        b = svc.create_item(self.db, "md", self.md_payload(name="B"), 1)
        a_default = self._default_detail(a["id"])
        saved = svc.update_md_detail(self.db, a["id"], a_default["id"], self._detail_payload(barcode="  880123  "), 1)
        self.assertEqual(saved["barcode"], "880123")  # surrounding whitespace normalized
        expected = f"Barcode 880123 is already used by {a['code']} (Detail 1)."
        cases = (
            ("same Item, new Detail", lambda: svc.create_md_detail(self.db, a["id"], self._detail_payload(barcode="880123"), 1)),
            ("other Item, new Detail", lambda: svc.create_md_detail(self.db, b["id"], self._detail_payload(barcode=" 880123"), 1)),
            ("other Item, Default Detail", lambda: svc.update_md_detail(
                self.db, b["id"], self._default_detail(b["id"])["id"], self._detail_payload(barcode="880123 "), 1)),
        )
        for label, action in cases:
            with self.subTest(label), self.assertRaises(svc.FieldErrors) as ctx:
                action()
            self.assertEqual((ctx.exception.errors, ctx.exception.message), ({"barcode": expected}, expected))
            self.db.rollback()
        self.assertEqual(self.db.query(MdDetail).filter(MdDetail.c_barcode == "880123").count(), 1)
        # Updating the owner without changing its barcode is fine; blanks are never compared.
        svc.update_md_detail(self.db, a["id"], a_default["id"], self._detail_payload(barcode="880123", size="L"), 1)
        for md in (a, b):
            svc.create_md_detail(self.db, md["id"], self._detail_payload(barcode="   "), 1)
            svc.create_md_detail(self.db, md["id"], self._detail_payload(), 1)
        self.assertEqual(self.db.query(MdDetail).filter(MdDetail.c_barcode.is_(None)).count(), 5)
        # MD form pre-check (the MD's Barcode is its Default Detail's barcode).
        self.assertEqual(svc.check_default_barcode(self.db, b["id"], " 880123 "),
                         {"barcode": "880123", "available": False, "message": expected})
        self.assertTrue(svc.check_default_barcode(self.db, a["id"], "880123")["available"])
        self.assertTrue(svc.check_default_barcode(self.db, None, "NEW-1")["available"])
        self.assertTrue(svc.check_default_barcode(self.db, b["id"], "")["available"])

    def test_barcode_db_unique_index_blocks_concurrent_duplicates(self):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        a = svc.create_item(self.db, "md", self.md_payload(name="A"), 1)
        b = svc.create_item(self.db, "md", self.md_payload(name="B"), 1)
        svc.create_md_detail(self.db, a["id"], self._detail_payload(barcode="RACE-1"), 1)
        # A save that passed the pre-check before the other commit landed still cannot create a duplicate.
        with patch.object(svc, "_ensure_barcode_free"), self.assertRaises(svc.FieldErrors) as ctx:
            svc.create_md_detail(self.db, b["id"], self._detail_payload(barcode="RACE-1"), 1)
        self.assertIn(a["code"], ctx.exception.errors["barcode"])
        self.assertEqual(self.db.query(MdDetail).filter(MdDetail.c_barcode == "RACE-1").count(), 1)
        # The index compares trimmed values and ignores blanks.
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO tb_md_detail (i_md_id, i_detail_no, b_is_default, c_barcode, c_unit, n_price) "
                              "VALUES (:md, 90, 0, '', 'EA', 0), (:md, 91, 0, '', 'EA', 0)"), {"md": b["id"]})
        with self.assertRaises(IntegrityError), self.engine.begin() as conn:
            conn.execute(text("INSERT INTO tb_md_detail (i_md_id, i_detail_no, b_is_default, c_barcode, c_unit, n_price) "
                              "VALUES (:md, 92, 0, ' RACE-1 ', 'EA', 0)"), {"md": b["id"]})

    def test_barcode_unique_migration_reports_duplicates_without_changing_data(self):
        from sqlalchemy import create_engine, inspect, text
        from backend.database.md_barcode_unique_migration import barcode_index_exists, migrate_md_barcode_unique
        from backend.services.barcode import UNIQUE_INDEX

        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_md_master (i_md_id INTEGER PRIMARY KEY, c_item_code VARCHAR(20))"))
            conn.execute(text("CREATE TABLE tb_md_detail (i_md_detail_id INTEGER PRIMARY KEY, i_md_id INTEGER, "
                              "i_detail_no INTEGER, c_barcode VARCHAR(100))"))
            conn.execute(text("INSERT INTO tb_md_master VALUES (1, 'MD00001'), (2, 'MD00002')"))
            conn.execute(text("INSERT INTO tb_md_detail VALUES (1, 1, 1, '8801116056934'), (2, 2, 1, '8801116056934 '), "
                              "(3, 1, 2, NULL), (4, 2, 2, ''), (5, 2, 3, '')"))
        snapshot = lambda: engine.connect().execute(text("SELECT * FROM tb_md_detail ORDER BY 1")).all()
        before = snapshot()
        index_exists = lambda: barcode_index_exists(engine.connect())
        with self.assertLogs("backend.database.md_barcode_unique_migration", "WARNING") as logs:
            duplicates = migrate_md_barcode_unique(engine)
        self.assertEqual(duplicates, [("8801116056934", "MD00001#1, MD00002#1")])
        self.assertIn("MD00001#1, MD00002#1", logs.output[0])
        self.assertEqual(snapshot(), before)  # nothing deleted or changed
        self.assertFalse(index_exists())
        # After a manual fix the next startup creates the index; repeat runs are no-ops.
        with engine.begin() as conn:
            conn.execute(text("UPDATE tb_md_detail SET c_barcode = '8801116056941' WHERE i_md_detail_id = 2"))
        self.assertEqual(migrate_md_barcode_unique(engine), [])
        self.assertEqual(migrate_md_barcode_unique(engine), [])
        self.assertTrue(index_exists())

    def test_item_kind_seed_is_idempotent_and_keeps_display_names(self):
        from backend.database.item_kind_migration import SYSTEM_ITEM_KINDS
        item = self.db.query(ItemKind).filter(ItemKind.c_item_kind_code == "GIFT_CARD").one()
        item.c_item_kind_name = "E-Gift Card"
        self.db.commit()
        migrate_item_kinds(self.engine)
        self.db.expire_all()
        rows = self.db.query(ItemKind).order_by(ItemKind.i_display_order).all()
        self.assertEqual([r.c_item_kind_code for r in rows], [code for code, _ in SYSTEM_ITEM_KINDS])
        self.assertEqual(rows[2].c_item_kind_name, "Admission / Ticket")
        self.assertEqual(self.db.query(ItemKind).filter(ItemKind.c_item_kind_code == "GIFT_CARD").one().c_item_kind_name,
                         "E-Gift Card")
        listed = svc.list_items(self.db, "item_kind", size=100)
        self.assertEqual(listed["total"], 9)
        headers, data = svc.export_table(self.db, "item_kind")
        self.assertEqual(headers[:2], ["System Code", "Display Name"])
        with self.assertRaises(svc.FieldErrors):
            svc.update_item(self.db, "item_kind", rows[0].i_item_kind_id, {"name": " "}, 1)
        # MD Master Item Kind uses active System Master rows, default ITEM; legacy values are linked.
        options = svc.lookup_options(self.db)
        self.assertEqual(options["default_item_kind"], "ITEM")
        self.assertEqual([k["code"] for k in options["item_kinds"]][:2], ["ITEM", "SERVICE"])
        self.assertIn({"code": "GIFT_CARD", "name": "E-Gift Card"}, options["item_kinds"])
        gift = self.db.query(ItemKind).filter(ItemKind.c_item_kind_code == "GIFT_CARD").one()
        svc.update_item(self.db, "item_kind", gift.i_item_kind_id, {"name": gift.c_item_kind_name, "active": False}, 1)
        self.assertNotIn("GIFT_CARD", [k["code"] for k in svc.lookup_options(self.db)["item_kinds"]])
        self.assertIn("item_kind", self.errors("md", self.md_payload(item_kind="GIFT_CARD")))
        self.assertIn("item_kind", self.errors("md", self.md_payload(item_kind="HARDWARE")))
        md = svc.create_item(self.db, "md", {k: v for k, v in self.md_payload().items() if k != "item_kind"}, 1)
        self.assertEqual(md["item_kind"], "ITEM")
        svc.create_item(self.db, "md", self.md_payload(name="Setup", item_kind="SERVICE"), 1)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE tb_md_master SET c_item_kind = 'HARDWARE' WHERE c_item_name = 'POS Terminal'"))
        migrate_item_kinds(self.engine)
        self.db.expire_all()
        kinds = {r.c_item_name: r.c_item_kind for r in self.db.query(MdMaster)}
        self.assertEqual(kinds, {"POS Terminal": "ITEM", "Setup": "SERVICE"})

    def test_md_license_rules(self):
        p = self.md_payload(license_required=True)
        self.assertIn("license_type", self.errors("md", p))
        self.assertIn("licenses_per_unit", self.errors("md", {**p, "license_type": "POS", "licenses_per_unit": 0}))
        self.assertIn("licenses_per_unit", self.errors("md", {**p, "license_type": "POS", "licenses_per_unit": "1.5"}))
        self.assertIn("license_type", self.errors("md", {**p, "license_type": "BOGUS"}))
        ok = svc.create_item(self.db, "md", {**p, "license_type": "KIOSK"}, 1)
        self.assertEqual((ok["license_type"], ok["licenses_per_unit"]), ("KIOSK", 1))

    def test_md_license_fields_ignored_when_not_required_and_serial_independent(self):
        item = svc.create_item(
            self.db, "md",
            self.md_payload(license_required=False, license_type="POS", licenses_per_unit=5, serial_required=True), 1,
        )
        self.assertIsNone(item["license_type"])
        self.assertIsNone(item["licenses_per_unit"])
        self.assertTrue(item["serial_required"])

    def test_md_price_and_category_rules(self):
        p = self.md_payload()
        self.assertIn("retail_price", self.errors("md", {**p, "retail_price": "-0.01"}))
        self.assertIn("retail_price", self.errors("md", {**p, "retail_price": "1.234"}))
        self.assertIn("item_kind", self.errors("md", {**p, "item_kind": "UNKNOWN"}))
        self.assertIn("billing_cycle", self.errors("md", {**p, "billing_cycle": "WEEKLY"}))
        c2 = svc.create_item(self.db, "category2", {"name": "Two", "category1_id": p["category1_id"]}, 1)
        c3 = svc.create_item(self.db, "category3", {"name": "Three", "category1_id": p["category1_id"], "category2_id": c2["id"]}, 1)
        self.assertIn("category3_id", self.errors("md", {**p, "category3_id": c3["id"]}))
        other = svc.create_item(self.db, "category1", {"name": "Other"}, 1)
        self.assertIn("category2_id", self.errors("md", {**p, "category1_id": other["id"], "category2_id": c2["id"]}))
        ok = svc.create_item(self.db, "md", {**p, "category2_id": c2["id"], "category3_id": c3["id"]}, 1)
        self.assertEqual(ok["category3_name"], "Three")

    def test_md_tax_link_and_used_tax_guard(self):
        tax = svc.create_item(self.db, "tax", {"name": "VAT", "rate": "10"}, 1)
        item = svc.create_item(self.db, "md", self.md_payload(default_tax_id=tax["id"]), 1)
        self.assertEqual(item["default_tax_rate"], "10.0000")
        with self.assertRaises(svc.Conflict):
            svc.delete_item(self.db, "tax", tax["id"])
        self.assertIn("default_tax_id", self.errors("md", self.md_payload(default_tax_id=9999)))

    def test_list_search_filter_sort_page(self):
        for name in ("Alpha", "Beta", "Gamma"):
            svc.create_item(self.db, "category1", {"name": name}, 1)
        svc.update_item(self.db, "category1", 2, {"name": "Beta", "active": False}, 1)
        res = svc.list_items(self.db, "category1", q="alp")
        self.assertEqual([i["name"] for i in res["items"]], ["Alpha"])
        res = svc.list_items(self.db, "category1", active="inactive")
        self.assertEqual([i["name"] for i in res["items"]], ["Beta"])
        res = svc.list_items(self.db, "category1", sort="name", direction="desc", size=2, page=2)
        self.assertEqual((res["total"], [i["name"] for i in res["items"]]), (3, ["Alpha"]))

    # --- pricing ---
    def test_discount_formula_and_rounding(self):
        from decimal import Decimal
        d = Decimal
        self.assertEqual(svc.calc_discount_price(d("100"), d("20")), d("80.00"))
        self.assertEqual(svc.calc_discount_price(d("100"), d("40")), d("60.00"))
        self.assertEqual(svc.calc_discount_price(d("100"), d("50")), d("50.00"))
        self.assertEqual(svc.calc_discount_price(d("0.05"), d("50")), d("0.03"))  # 0.025 rounds half up
        self.assertEqual(svc.calc_discount_price(d("19.99"), d("33.33")), d("13.33"))
        self.assertEqual(svc.calc_discount_price(d("100"), d("100")), d("0.00"))

    def test_levels_default_to_unset_not_zero(self):
        item = svc.create_item(self.db, "md", self.md_payload(retail_price="100"), 1)
        for n in (1, 2, 3):
            self.assertIsNone(item[f"p{n}_mode"])
            self.assertIsNone(item[f"p{n}_price"])
        row = self.db.get(MdMaster, item["id"])
        with self.assertRaises(svc.PriceNotSet):
            svc.resolve_unit_price(row, "P1")
        self.assertEqual(str(svc.resolve_unit_price(row, "RETAIL")), "100.00")

    def test_zero_fixed_price_is_distinct_from_unset(self):
        item = svc.create_item(self.db, "md", self.md_payload(retail_price="100", p1_mode="FIXED", p1_price="0"), 1)
        self.assertEqual(item["p1_price"], "0.00")
        self.assertEqual(str(svc.resolve_unit_price(self.db.get(MdMaster, item["id"]), "P1")), "0.00")
        self.assertIsNone(item["p2_price"])

    def test_retail_change_recalculates_only_discount_levels(self):
        item = svc.create_item(self.db, "md", self.md_payload(
            retail_price="100", p1_mode="DISCOUNT", p1_discount_pct="20", p2_mode="DISCOUNT", p2_discount_pct="40",
            p3_mode="FIXED", p3_price="45"), 1)
        self.assertEqual((item["p1_price"], item["p2_price"], item["p3_price"]), ("80.00", "60.00", "45.00"))
        payload = {**self.md_payload_for(item), "retail_price": "200"}
        out = svc.update_item(self.db, "md", item["id"], payload, 1)
        self.assertEqual((out["p1_price"], out["p2_price"], out["p3_price"]), ("160.00", "120.00", "45.00"))
        self.assertEqual(out["p1_discount_pct"], "20.00")

    def test_merchant_supplied_price_ignored_in_discount_mode(self):
        item = svc.create_item(self.db, "md", self.md_payload(
            retail_price="100", p1_mode="DISCOUNT", p1_discount_pct="20", p1_price="1.00"), 1)
        self.assertEqual(item["p1_price"], "80.00")

    def test_pricing_validation(self):
        p = self.md_payload(retail_price="100")
        self.assertIn("p1_discount_pct", self.errors("md", {**p, "p1_mode": "DISCOUNT", "p1_discount_pct": "100.01"}))
        self.assertIn("p1_discount_pct", self.errors("md", {**p, "p1_mode": "DISCOUNT", "p1_discount_pct": "-1"}))
        self.assertIn("p1_discount_pct", self.errors("md", {**p, "p1_mode": "DISCOUNT"}))
        self.assertIn("p2_price", self.errors("md", {**p, "p2_mode": "FIXED"}))
        self.assertIn("p2_price", self.errors("md", {**p, "p2_mode": "FIXED", "p2_price": "-1"}))
        self.assertIn("p3_price", self.errors("md", {**p, "p3_mode": "FIXED", "p3_price": "1.999"}))
        self.assertIn("p1_mode", self.errors("md", {**p, "p1_mode": "BOGUS"}))
        self.assertIn("cost", self.errors_cost({**p, "cost": "-5"}))

    def errors_cost(self, payload):
        with self.assertRaises(svc.FieldErrors) as ctx:
            svc.create_item(self.db, "md", payload, 1, can_cost=True)
        return ctx.exception.errors

    def md_payload_for(self, item):
        keys = ("name", "description", "item_kind", "category1_id", "category2_id", "category3_id", "unit",
                "billing_cycle", "retail_price", "p1_mode", "p1_discount_pct", "p1_price", "p2_mode",
                "p2_discount_pct", "p2_price", "p3_mode", "p3_discount_pct", "p3_price")
        return {k: item.get(k) for k in keys}

    def test_cost_only_for_authorized_and_preserved_otherwise(self):
        p = self.md_payload(retail_price="10", cost="4.25")
        hidden = svc.create_item(self.db, "md", p, 1, can_cost=False)
        self.assertNotIn("cost", hidden)
        self.assertIsNone(self.db.get(MdMaster, hidden["id"]).n_cost)
        shown = svc.create_item(self.db, "md", {**p, "name": "B"}, 1, can_cost=True)
        self.assertEqual(shown["cost"], "4.25")
        # An unauthorized edit neither reveals nor overwrites the stored cost.
        out = svc.update_item(self.db, "md", shown["id"], {**p, "name": "B2", "cost": "99"}, 1, can_cost=False)
        self.assertNotIn("cost", out)
        self.assertEqual(str(self.db.get(MdMaster, shown["id"]).n_cost), "4.25")
        self.assertNotIn("cost", svc.list_items(self.db, "md")["items"][0])
        self.assertIn("cost", svc.list_items(self.db, "md", can_cost=True)["items"][0])
        cleared = svc.update_item(self.db, "md", shown["id"], {**p, "cost": ""}, 1, can_cost=True)
        self.assertIsNone(cleared["cost"])

    def test_retail_reuses_existing_column(self):
        self.assertIn("n_default_unit_price", MdMaster.__table__.columns)
        self.assertNotIn("n_retail_price", MdMaster.__table__.columns)

    def test_no_serial_or_license_key_columns(self):
        cols = set(MdMaster.__table__.columns.keys())
        self.assertFalse({c for c in cols if "serial_no" in c or "license_key" in c})


class MasterGroupApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="awsum-master-group-api-")
        self.addCleanup(directory.cleanup)
        storage = patch.object(svc.images, "image_storage_root", return_value=Path(directory.name))
        storage.start()
        self.addCleanup(storage.stop)
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        PlatformBase.metadata.create_all(
            engine, tables=[m.__table__ for m in (Tax, ItemKind, Category1, Category2, Category3, MdMaster, MdDetail, InvoiceLine)]
        )
        migrate_item_kinds(engine)
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)
        self.role = 1
        self.cookie = "x"

        def fake_user(request, db):
            if not request.cookies.get("access_token"):
                raise HTTPException(status_code=401, detail="Authentication required.")
            return SimpleNamespace(i_platform_user_id=1, login_role=self.role)

        patcher = patch.object(router_module, "get_current_user", fake_user)
        patcher.start()
        self.addCleanup(patcher.stop)
        super_patcher = patch.object(router_module, "_is_super_admin", lambda db, user: False)
        super_patcher.start()
        self.addCleanup(super_patcher.stop)

    def req(self, origin=None):
        headers = {"origin": origin} if origin else {}
        cookies = {"access_token": self.cookie} if self.cookie else {}
        return SimpleNamespace(cookies=cookies, headers=headers, state=SimpleNamespace(),
                               url=SimpleNamespace(netloc="localhost:8000", path="/platform/api/master-group"))

    def status(self, fn, *args, **kw):
        try:
            res = fn(*args, **kw)
        except HTTPException as exc:
            return exc.status_code, None
        body = json.loads(res.body) if hasattr(res, "body") else None
        return res.status_code, body

    def test_unauthenticated_and_non_admin_blocked(self):
        self.cookie = ""
        self.assertEqual(self.status(router_module.master_group_list, "tax", self.req(), db=self.db)[0], 401)
        self.cookie = "x"
        self.role = 2
        self.assertEqual(self.status(router_module.master_group_list, "tax", self.req(), db=self.db)[0], 403)
        self.assertEqual(
            self.status(router_module.master_group_create, "tax", {"name": "x", "rate": 1}, self.req(), self.db)[0], 403
        )
        self.assertEqual(self.status(router_module.master_group_options, self.req(), self.db)[0], 403)
        self.assertEqual(self.db.query(Tax).count(), 0)

    def test_cost_hidden_in_api_for_non_super_admin(self):
        body = {"name": "Item", "item_kind": "ITEM", "unit": "EA", "billing_cycle": "ONE_TIME",
                "retail_price": "10", "cost": "3"}
        c1 = svc.create_item(self.db, "category1", {"name": "C"}, 1)
        body["category1_id"] = c1["id"]
        with patch.object(router_module, "_is_super_admin", lambda db, user: True):
            code, res = self.status(router_module.master_group_create, "md", body, self.req(), self.db)
            self.assertEqual((code, res["item"]["cost"]), (201, "3.00"))
            self.assertTrue(self.status(router_module.master_group_options, self.req(), self.db)[1]["can_view_cost"])
        with patch.object(router_module, "_is_super_admin", lambda db, user: False):
            code, res = self.status(router_module.master_group_list, "md", self.req(), db=self.db)
            self.assertNotIn("cost", res["items"][0])
            self.assertEqual(res["items"][0]["retail_price"], "10.00")
            self.assertFalse(self.status(router_module.master_group_options, self.req(), self.db)[1]["can_view_cost"])
            code, res = self.status(
                router_module.master_group_update, "md", 1, {**body, "name": "Item2", "cost": "999"}, self.req(), self.db
            )
            self.assertNotIn("cost", res["item"])
        self.assertEqual(str(self.db.get(MdMaster, 1).n_cost), "3.00")

    def test_md_detail_common_api_list_add_update_delete(self):
        category = svc.create_item(self.db, "category1", {"name": "Hardware"}, 1)
        body = {
            "name": "POS", "item_kind": "ITEM", "unit": "EA",
            "category1_id": category["id"], "billing_cycle": "ONE_TIME", "retail_price": "10",
        }
        code, created = self.status(router_module.master_group_create, "md", body, self.req(), self.db)
        self.assertEqual(code, 201)
        md_id = created["item"]["id"]
        code, result = self.status(router_module.md_detail_list, md_id, self.req(), self.db)
        self.assertEqual(code, 200)
        default = result["items"][0]
        self.assertTrue(default["is_default"])
        self.assertEqual(default["price"], "10.00")

        payload = {
            "unit": "Case", "pk_size": "12", "size": "Large", "cost": "3.50",
            "price": "36", "barcode": "123", "modifier": "No ice",
        }
        code, result = self.status(router_module.md_detail_create, md_id, payload, self.req(), self.db)
        self.assertEqual((code, result["item"]["detail_no"], result["item"]["is_default"]), (201, 2, False))
        detail_id = result["item"]["id"]
        code, result = self.status(
            router_module.md_detail_update, md_id, detail_id,
            {**payload, "price": "40", "is_default": True}, self.req(), self.db,
        )
        self.assertEqual((code, result["item"]["is_default"]), (200, True))
        self.assertEqual(self.db.get(MdMaster, md_id).n_default_unit_price, Decimal("40.00"))

        code, _ = self.status(router_module.md_detail_delete, md_id, detail_id, self.req(), self.db)
        self.assertEqual(code, 409)
        code, _ = self.status(router_module.md_detail_delete, md_id, default["id"], self.req(), self.db)
        self.assertEqual(code, 200)
        self.assertEqual(len(svc.list_md_details(self.db, md_id)), 1)

    def test_create_update_error_shapes_and_origin(self):
        code, body = self.status(router_module.master_group_create, "tax", {"name": "", "rate": "-1"}, self.req(), self.db)
        self.assertEqual(code, 422)
        self.assertEqual(set(body["errors"]), {"name", "rate"})
        code, body = self.status(router_module.master_group_create, "tax", {"name": "VAT", "rate": "0"}, self.req(), self.db)
        self.assertEqual(code, 201)
        item = body["item"]
        code, body = self.status(
            router_module.master_group_update, "tax", item["id"], {"name": "VAT2", "rate": "7"}, self.req(), self.db
        )
        self.assertEqual(body["item"]["code"], item["code"])
        self.assertEqual(self.status(router_module.master_group_update, "tax", 999, {"name": "a", "rate": 1}, self.req(), self.db)[0], 404)
        self.assertEqual(self.status(router_module.master_group_create, "bogus", {}, self.req(), self.db)[0], 404)
        code, _ = self.status(
            router_module.master_group_create, "tax", {"name": "x", "rate": 1}, self.req("http://evil.example"), self.db
        )
        self.assertEqual(code, 403)
        code, body = self.status(router_module.master_group_list, "tax", self.req(), db=self.db)
        self.assertEqual(body["total"], 1)
        code, body = self.status(router_module.master_group_delete, "tax", item["id"], self.req(), self.db)
        self.assertEqual(code, 200)

    def test_item_kind_system_master_api(self):
        code, body = self.status(router_module.master_group_list, "item_kind", self.req(), db=self.db)
        self.assertEqual(code, 200)
        self.assertEqual(body["items"][0]["code"], "ITEM")
        self.assertTrue(body["items"][0]["system"])
        item_id = body["items"][0]["id"]
        self.assertEqual(self.status(router_module.master_group_create, "item_kind",
                                     {"name": "New"}, self.req(), self.db)[0], 409)
        self.assertEqual(self.status(router_module.master_group_delete, "item_kind", item_id, self.req(), self.db)[0], 409)
        code, body = self.status(router_module.master_group_update, "item_kind", item_id,
                                 {"name": "Merchandise", "code": "HACK", "display_order": 5}, self.req(), self.db)
        self.assertEqual((code, body["item"]["code"], body["item"]["name"]), (200, "ITEM", "Merchandise"))
        merchant_req = self.req()
        merchant_req.state.master_merchant_id = 9
        with self.assertRaises(HTTPException) as ctx:
            router_module._entity("item_kind", merchant_req)
        self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(svc.NotFound):
            svc.list_items(self.db, "item_kind", merchant_id=9)
        self.assertEqual(router_module.PAGES["item-kind"], ("item_kind", "Item Kind"))


if __name__ == "__main__":
    unittest.main()
