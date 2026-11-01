import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.pg_platform import PlatformBase
from backend.models_admin.invoice import InvoiceLine
from backend.models_admin.master_group import Category1, Category2, Category3, MdMaster, Tax
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
        tables = [m.__table__ for m in (Tax, Category1, Category2, Category3, MdMaster, InvoiceLine)]
        PlatformBase.metadata.create_all(engine, tables=tables)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        self.addCleanup(self.db.close)

    def md_payload(self, **kw):
        c1 = svc.create_item(self.db, "category1", {"name": "Hardware"}, 1)
        base = {
            "name": "POS Terminal", "item_kind": "HARDWARE", "category1_id": c1["id"], "unit": "EA",
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
        self.assertIn("item_kind", self.errors("md", {**p, "item_kind": "OTHER"}))
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

    def test_client_supplied_price_ignored_in_discount_mode(self):
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
        PlatformBase.metadata.create_all(
            engine, tables=[m.__table__ for m in (Tax, Category1, Category2, Category3, MdMaster, InvoiceLine)]
        )
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
        return SimpleNamespace(cookies=cookies, headers=headers, url=SimpleNamespace(netloc="localhost:8000"))

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
        body = {"name": "Item", "item_kind": "HARDWARE", "unit": "EA", "billing_cycle": "ONE_TIME",
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


if __name__ == "__main__":
    unittest.main()


