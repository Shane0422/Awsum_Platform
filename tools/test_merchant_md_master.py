"""STEP 2: Merchant MD Master on the shared MD screen and API.

Merchant MD is owned by one Merchant (i_owner_key = merchant id) and shared read-only by its Stores. It uses the
Merchant's own Category I-III / Tax / UNIT / COLOR and the global Item Kind; barcodes are unique within the
Merchant; images live under /merchant/{MERCHANT_CODE}/images/master/. Platform-only fields (Price Level P1-P3,
Billing Cycle, License) and the Recommended Location are never taken from a Merchant payload.

Isolated SQLite app (tools.test_common_app_navigation fixture): Merchant 10 (MER26ABCD) with Store 10 (STR26ABCD)
and another Merchant 11 (MER26EFGH). Images go to a temporary folder. No real database or file is touched.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.database.common_code_migration import migrate_common_codes
from backend.database.item_kind_migration import migrate_item_kinds
from backend.models_admin.common_code import CommonCode
from backend.models_admin.master_group import MdDetail, MdMaster
from backend.routers import images as image_routes
from backend.services import images
from backend.services import master_group as svc
from tools import test_common_app_navigation

API = "/{scope}/api/master-group"
PLATFORM_FIELDS = {  # sent by a crafted Merchant payload: must never be saved on a Merchant MD
    "billing_cycle": "MONTHLY", "license_required": True, "license_type": "POS", "licenses_per_unit": 3,
    "p1_mode": "FIXED", "p1_price": "5.00", "p2_mode": "DISCOUNT", "p2_discount_pct": "10", "p3_mode": "FIXED",
    "p3_price": "1", "sales_location": "S01-B01-C01", "warehouse_location": "W01-R01-C01",
    "i_merchant_id": 11, "i_owner_key": 0,
}


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(out, "PNG")
    return out.getvalue()


class MerchantMdMasterTests(unittest.TestCase):
    def setUp(self):
        test_common_app_navigation.CommonAppNavigationTests.setUp(self)
        with redirect_stdout(io.StringIO()):
            migrate_common_codes(self.engine)  # Platform COLOR / UNIT codes
            migrate_item_kinds(self.engine)  # global Item Kind (ITEM ...)
        temp = tempfile.TemporaryDirectory(prefix="awsum-merchant-md-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        storage = patch.object(images, "image_storage_root", return_value=self.root)
        storage.start()
        self.addCleanup(storage.stop)
        self.app.include_router(image_routes.router)
        self.app.dependency_overrides[image_routes.get_db] = self.app.dependency_overrides[test_common_app_navigation.platform_store.get_db]
        with self.factory() as db:
            # Platform MD (owner 0) with barcode 8801 and Merchant 11's MD with barcode M11-1.
            self.platform_md = svc.create_item(db, "md", self.md_payload(self.platform_category, "Platform Item"), 1)
            self.platform_tax = svc.create_item(db, "tax", {"name": "Platform tax", "rate": "6"}, 1)["id"]
            db.query(MdDetail).filter(MdDetail.i_md_id == self.platform_md["id"]).update({"c_barcode": "8801"})
            svc.initialize_merchant_common_codes(db, 11, 1)
            self.other_md = svc.create_item(db, "md", self.md_payload(self.other_category, "Other Item"), 1,
                                            merchant_id=11)
            db.query(MdDetail).filter(MdDetail.i_md_id == self.other_md["id"]).update({"c_barcode": "M11-1"})
            self.platform_color = db.query(CommonCode.i_common_code_id).filter(
                CommonCode.i_merchant_id.is_(None), CommonCode.c_code == "CLR00001").scalar()
            other_unit = svc.create_item(db, "common_code", {"code": "ZZ", "name": "Other only unit"}, 1,
                                         merchant_id=11, group="UNIT")
            self.assertTrue(other_unit["id"])
            db.commit()

    def login(self, scope):
        test_common_app_navigation.CommonAppNavigationTests.login(self, scope)

    @staticmethod
    def md_payload(category1_id, name="Merchant Item", **extra):
        return {"name": name, "item_kind": "ITEM", "category1_id": category1_id, "retail_price": "10",
                "unit": "EA", "billing_cycle": "ONE_TIME", **extra}

    def url(self, path="md", scope="merchant"):
        return f"{API.format(scope=scope)}/{path}"

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def merchant_category(self):
        self.ok(self.client.get(self.url("options")))  # first visit: the Merchant's own UNIT / COLOR codes
        return self.ok(self.client.post(self.url("category1"), json={"name": "Merchant cat"}), 201)["item"]["id"]

    def merchant_md(self, name="Merchant Item", **extra):
        category = self.merchant_category() if not hasattr(self, "_cat") else self._cat
        self._cat = category
        return self.ok(self.client.post(self.url(), json=self.md_payload(category, name, **extra)), 201)["item"]

    def row(self, md_id):
        with self.factory() as db:
            return db.get(MdMaster, md_id)

    # ---- Merchant CRUD ----
    def test_merchant_md_crud_with_details_and_default_detail(self):
        self.login("MERCHANT")
        options = self.ok(self.client.get(self.url("options")))
        self.assertEqual((options["md_platform_fields"], options["md_recommended_location"], options["view_only_entities"],
                          options["detail_cost"], options["can_view_cost"]), (False, False, [], True, True))
        category = self.merchant_category()
        created = self.ok(self.client.post(self.url(), json=self.md_payload(
            category, cost="3.00", serial_required=True, **PLATFORM_FIELDS)), 201)["item"]
        self.assertEqual(created["cost"], "3.00")  # Merchant Admin manages its own Cost
        row = self.row(created["id"])
        self.assertEqual((row.i_merchant_id, row.i_owner_key, row.c_billing_cycle, row.b_license_required,
                          row.c_license_type, row.i_licenses_per_unit, row.c_p1_mode, row.n_p1_price, row.c_p2_mode,
                          row.n_p2_discount_pct, row.c_p3_mode, row.c_sales_location, row.c_warehouse_location,
                          row.b_serial_required, row.n_cost),
                         (10, 10, "ONE_TIME", False, None, None, None, None, None, None, None, None, None, True,
                          Decimal("3.00")))
        details = self.ok(self.client.get(self.url(f"md/{created['id']}/details")))["items"]
        self.assertEqual([(d["is_default"], d["unit"], d["price"], d["cost"]) for d in details],
                         [(True, "EA", "10.00", "3.00")])
        with self.factory() as db:
            self.assertEqual({d.i_owner_key for d in db.query(MdDetail).filter(MdDetail.i_md_id == created["id"])}, {10})
        self.assertEqual([i["id"] for i in self.ok(self.client.get(self.url()))["items"]], [created["id"]])

        updated = self.ok(self.client.put(self.url(f"md/{created['id']}"), json=self.md_payload(
            category, "Renamed", retail_price="12", cost="4", **PLATFORM_FIELDS)))["item"]
        self.assertEqual((updated["name"], updated["retail_price"], updated["cost"]), ("Renamed", "12.00", "4.00"))
        row = self.row(created["id"])
        self.assertEqual((row.c_billing_cycle, row.b_license_required, row.c_p1_mode, row.c_sales_location),
                         ("ONE_TIME", False, None, None))

        detail = self.ok(self.client.post(self.url(f"md/{created['id']}/details"), json={
            "barcode": "D-1", "unit": "PK", "price": "20", "cost": "8", "color_code_id": self.merchant_color()}), 201)["item"]
        self.assertEqual((detail["barcode"], detail["unit"]), ("D-1", "PK"))
        changed = self.ok(self.client.put(self.url(f"md/{created['id']}/details/{detail['id']}"), json={
            "barcode": "D-2", "unit": "EA", "price": "21"}))["item"]
        self.assertEqual(changed["barcode"], "D-2")
        self.assertEqual(len(self.ok(self.client.get(self.url(f"md/{created['id']}/details")))["items"]), 2)
        self.ok(self.client.delete(self.url(f"md/{created['id']}/details/{detail['id']}")))

        export = self.client.get(self.url("md/export?format=csv"))
        self.assertEqual(export.status_code, 200, export.text)
        header = export.text.splitlines()[0]
        self.assertIn("Cost", header)
        self.assertIn("Serial Number Required", header)
        for hidden in ("Billing Cycle", "P1 Mode", "P3 Price", "License Required", "Licenses per Unit",
                       "Sales Floor Location"):
            self.assertNotIn(hidden, header)
        self.assertNotIn("Platform Item", export.text)
        self.assertNotIn("Other Item", export.text)

        self.ok(self.client.delete(self.url(f"md/{created['id']}")))
        self.assertEqual(self.ok(self.client.get(self.url()))["total"], 0)
        with self.factory() as db:
            self.assertEqual(db.query(MdDetail).filter(MdDetail.i_md_id == created["id"]).count(), 0)

    def merchant_color(self):
        with self.factory() as db:
            return db.query(CommonCode.i_common_code_id).filter(CommonCode.i_merchant_id == 10,
                                                                CommonCode.c_code == "CLR00001").scalar()

    # ---- isolation ----
    def test_merchant_reaches_only_its_own_md_and_master_data(self):
        self.login("MERCHANT")
        own = self.merchant_md()
        self.assertEqual([i["id"] for i in self.ok(self.client.get(self.url()))["items"]], [own["id"]])
        for foreign in (self.platform_md["id"], self.other_md["id"]):
            for method, path, body in (
                ("put", f"md/{foreign}", self.md_payload(self._cat)), ("delete", f"md/{foreign}", None),
                ("get", f"md/{foreign}/details", None), ("post", f"md/{foreign}/details", {"unit": "EA", "price": "1"}),
            ):
                kwargs = {"json": body} if body is not None else {}
                response = getattr(self.client, method)(self.url(path), **kwargs)
                self.assertEqual(response.status_code, 404, (method, path, response.text))
            image = self.client.put(self.url(f"md/{foreign}/with-image"),
                                    data={"payload": json.dumps(self.md_payload(self._cat))})
            self.assertEqual(image.status_code, 404, image.text)
        with self.factory() as db:
            foreign_detail = db.query(MdDetail.i_md_detail_id).filter(MdDetail.i_md_id == self.other_md["id"]).scalar()
        self.assertEqual(self.client.put(self.url(f"md/{own['id']}/details/{foreign_detail}"),
                                         json={"unit": "EA", "price": "1"}).status_code, 404)
        self.assertEqual(self.row(self.platform_md["id"]).c_item_name, "Platform Item")
        self.assertEqual(self.row(self.other_md["id"]).c_item_name, "Other Item")

        # Another scope's Category / Tax / Unit / Color is refused (422); Item Kind is the global list.
        for payload, field in ((self.md_payload(self.platform_category), "category1_id"),
                               (self.md_payload(self.other_category), "category1_id"),
                               (self.md_payload(self._cat, default_tax_id=self.platform_tax), "default_tax_id"),
                               (self.md_payload(self._cat, unit="ZZ"), "unit")):
            response = self.client.post(self.url(), json=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn(field, response.json()["errors"])
        color = self.client.post(self.url(f"md/{own['id']}/details"),
                                 json={"unit": "EA", "price": "1", "color_code_id": self.platform_color})
        self.assertEqual(color.status_code, 422, color.text)
        self.assertIn("color_code_id", color.json()["errors"])
        options = self.ok(self.client.get(self.url("options")))
        with self.factory() as db:
            platform_kinds = svc.lookup_options(db)["item_kinds"]
        self.assertEqual(options["item_kinds"], platform_kinds)
        self.assertEqual(self.client.get(self.url("item_kind")).status_code, 403)

        # Platform screens never show a Merchant MD.
        with self.factory() as db:
            self.assertEqual([i["id"] for i in svc.list_items(db, "md", size=100)["items"]], [self.platform_md["id"]])
            self.assertEqual([i["id"] for i in svc.list_items(db, "md", merchant_id=11)["items"]], [self.other_md["id"]])

    # ---- barcode ----
    def test_barcode_unique_within_the_merchant_only(self):
        self.login("MERCHANT")
        first = self.merchant_md("First")
        second = self.merchant_md("Second")
        check = lambda code, md=None: self.ok(self.client.get(
            self.url(f"md/barcode-check?barcode={code}" + (f"&md_id={md}" if md else ""))))
        # Platform's and another Merchant's barcodes are free inside this Merchant.
        self.assertTrue(check("8801")["available"])
        self.assertTrue(check("M11-1")["available"])
        self.ok(self.client.post(self.url(f"md/{first['id']}/details"),
                                 json={"barcode": "8801", "unit": "EA", "price": "1"}), 201)
        self.ok(self.client.post(self.url(f"md/{first['id']}/details"),
                                 json={"barcode": "M11-1", "unit": "EA", "price": "1"}), 201)
        # ... but unique inside it (Detail, trimmed; Default Detail pre-save check).
        duplicate = self.client.post(self.url(f"md/{second['id']}/details"),
                                     json={"barcode": " 8801 ", "unit": "EA", "price": "1"})
        self.assertEqual(duplicate.status_code, 422, duplicate.text)  # the existing barcode field error
        self.assertIn(first["code"], duplicate.json()["errors"]["barcode"])  # names this Merchant's own item
        self.assertFalse(check("8801", second["id"])["available"])
        self.assertTrue(check("NEW-1", second["id"])["available"])
        with self.factory() as db:  # the Platform / other Merchant rows are unchanged
            self.assertEqual(db.query(MdDetail).filter(MdDetail.c_barcode == "8801").count(), 2)

    # ---- Store: view only ----
    def test_store_views_its_merchant_md_without_cost_and_cannot_change_it(self):
        self.login("MERCHANT")
        own = self.merchant_md(cost="3")
        detail = self.ok(self.client.post(self.url(f"md/{own['id']}/details"),
                                          json={"barcode": "S-1", "unit": "EA", "price": "2", "cost": "1"}), 201)["item"]
        self.login("STORE")
        options = self.ok(self.client.get(self.url("options", "store")))
        self.assertEqual((options["can_view_cost"], options["detail_cost"], options["md_platform_fields"]),
                         (False, False, False))
        self.assertIn("md", options["view_only_entities"])
        items = self.ok(self.client.get(self.url("md", "store")))["items"]
        self.assertEqual([i["id"] for i in items], [own["id"]])  # its Merchant's MD only
        self.assertTrue(items[0]["read_only"])
        self.assertNotIn("cost", items[0])
        details = self.ok(self.client.get(self.url(f"md/{own['id']}/details", "store")))["items"]
        self.assertEqual(len(details), 2)
        self.assertFalse(any("cost" in d for d in details))
        self.assertEqual(self.client.get("/store/master-group/md-master").status_code, 200)
        export = self.client.get(self.url("md/export?format=csv", "store"))
        self.assertEqual(export.status_code, 200)
        self.assertNotIn("Cost", export.text.splitlines()[0])
        for method, path, kwargs in (
            ("post", "md", {"json": self.md_payload(self._cat)}),
            ("put", f"md/{own['id']}", {"json": self.md_payload(self._cat, "Changed")}),
            ("delete", f"md/{own['id']}", {}),
            ("post", "md/with-image", {"data": {"payload": json.dumps(self.md_payload(self._cat))}}),
            ("put", f"md/{own['id']}/with-image", {"data": {"payload": json.dumps(self.md_payload(self._cat)),
                                                             "remove_image": "true"}}),
            ("post", f"md/{own['id']}/details", {"json": {"unit": "EA", "price": "1"}}),
            ("put", f"md/{own['id']}/details/{detail['id']}", {"json": {"unit": "EA", "price": "9"}}),
            ("delete", f"md/{own['id']}/details/{detail['id']}", {}),
        ):
            response = getattr(self.client, method)(self.url(path, "store"), **kwargs)
            self.assertEqual(response.status_code, 403, (method, path, response.text))
        self.assertNotEqual(self.client.get(self.url("md/barcode-check?barcode=S-1", "store")).status_code, 200)
        self.assertEqual(self.row(own["id"]).c_item_name, "Merchant Item")
        with self.factory() as db:
            self.assertEqual(db.query(MdDetail).filter(MdDetail.i_md_id == own["id"]).count(), 2)
        # A Store never reaches the Platform MD or another Merchant's MD.
        for foreign in (self.platform_md["id"], self.other_md["id"]):
            self.assertEqual(self.client.get(self.url(f"md/{foreign}/details", "store")).status_code, 404)

    # ---- images ----
    def test_merchant_image_is_stored_for_the_merchant_and_shared_with_its_stores_only(self):
        self.login("MERCHANT")
        category = self.merchant_category()
        created = self.ok(self.client.post(self.url("md/with-image"),
                                           data={"payload": json.dumps(self.md_payload(category))},
                                           files={"image": ("item.png", png(), "image/png")}), 201)["item"]
        path = created["image_path"]
        self.assertEqual(path, f"/merchant/MER26ABCD/images/master/{created['code']}.webp")
        self.assertTrue((self.root / "merchant" / "MER26ABCD" / "images" / "master" / f"{created['code']}.webp").is_file())
        self.assertEqual(self.client.get(path).status_code, 200)
        # The generic image API cannot replace or delete an MD image (only MD Save / Delete can): refused either by
        # its existing role rule (403) or by the MD image protection (409).
        upload = self.client.post(f"/api/images/merchant/MER26ABCD/master?entity_key={created['code']}",
                                  files={"file": ("x.png", png(), "image/png")})
        self.assertIn(upload.status_code, (403, 409), upload.text)
        self.assertIn(self.client.delete(f"/api/images?image_path={path}").status_code, (403, 409))
        self.assertTrue((self.root / "merchant" / "MER26ABCD" / "images" / "master" / f"{created['code']}.webp").is_file())
        self.login("STORE")
        self.assertEqual(self.client.get(path).status_code, 200)  # its Merchant's MD image: view
        self.assertEqual(self.client.get("/merchant/MER26EFGH/images/master/MD00002.webp").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/images?image_path={path}").status_code, 403)
        self.login("MERCHANT")
        removed = self.ok(self.client.put(self.url(f"md/{created['id']}/with-image"),
                                          data={"payload": json.dumps(self.md_payload(category)), "remove_image": "true"}))
        self.assertIsNone(removed["item"]["image_path"])
        self.assertFalse((self.root / "merchant" / "MER26ABCD" / "images" / "master" / f"{created['code']}.webp").exists())

    # ---- Platform: unchanged ----
    def test_platform_md_keeps_platform_fields_and_never_lists_merchant_md(self):
        options = self.ok(self.client.get(self.url("options", "platform")))
        self.assertEqual((options["md_platform_fields"], options["md_recommended_location"], options["view_only_entities"],
                          options["detail_cost"]), (True, True, [], True))
        listed = self.ok(self.client.get(self.url("md", "platform")))["items"]
        self.assertEqual([i["id"] for i in listed], [self.platform_md["id"]])
        saved = self.ok(self.client.put(self.url(f"md/{self.platform_md['id']}", "platform"), json=self.md_payload(
            self.platform_category, "Platform Item", billing_cycle="MONTHLY", p1_mode="FIXED", p1_price="5",
            license_required=True, license_type="POS", licenses_per_unit=2)))["item"]
        self.assertEqual((saved["billing_cycle"], saved["p1_mode"], saved["license_required"], saved["licenses_per_unit"]),
                         ("MONTHLY", "FIXED", True, 2))
        self.assertEqual(self.client.get(self.url(f"md/{self.other_md['id']}/details", "platform")).status_code, 404)
        # Platform Admin passes the generic image API's role rule; a Merchant MD image is still protected (409).
        upload = self.client.post(f"/api/images/merchant/MER26EFGH/master?entity_key={self.other_md['code']}",
                                  files={"file": ("x.png", png(), "image/png")}, headers={"origin": "http://testserver"})
        self.assertEqual(upload.status_code, 409, upload.text)

    def test_category3_impact_counts_only_the_scope_md(self):
        self.login("MERCHANT")
        category = self.merchant_category()
        c2 = self.ok(self.client.post(self.url("category2"), json={"name": "C2", "category1_id": category}), 201)["item"]["id"]
        c3 = self.ok(self.client.post(self.url("category3"), json={"name": "C3", "category1_id": category,
                                                                    "category2_id": c2}), 201)["item"]["id"]
        self._cat = category
        self.merchant_md(category2_id=c2, category3_id=c3)
        impact = self.ok(self.client.get(self.url(f"category3/{c3}/inventory-impact")))
        self.assertEqual((impact["md_total"], impact["can_apply_to_md"]), (1, False))
        refused = self.client.put(self.url(f"category3/{c3}"), json={"name": "C3", "category1_id": category,
                                                                     "category2_id": c2, "apply_inventory_to_md": True})
        self.assertEqual(refused.status_code, 403)


class MerchantMdScreenTests(unittest.TestCase):
    """The one shared MD screen (no Merchant copy) adapts to the scope from /options."""

    def test_shared_screen_hides_platform_fields_location_and_store_writes(self):
        js = (Path(__file__).resolve().parents[1] / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
        self.assertIn("o.md_platform_fields === false", js)
        self.assertIn("o.md_recommended_location === false", js)
        for marker in ("field('billing_cycle', 'Billing Cycle', select('billing_cycle'), { required: true, col: 'col-md-4', feature: 'platform_md' })",
                       "field('license_required', 'License Required', select('license_required'), { col: 'col-md-4', feature: 'platform_md' })",
                       'aria-labelledby="mgMdTabOption1" tabindex="0" data-item-feature="platform_md"',
                       "f('license_required').value === 'true' && featureOn('platform_md')",
                       "options.view_only_entities", "options.detail_cost === false",
                       "$('mgDetailAdd').disabled = !row || !!row.read_only"):
            self.assertIn(marker, js)
        # Hidden features never reach the save payload (the existing OFF-feature rule), Serial Required stays.
        self.assertIn("if (el.closest('[data-item-feature-off]')) return;", js)
        self.assertNotIn("field('serial_required', 'Serial Number Required', select('serial_required'), { col: 'col-md-6', feature", js)
        html = (Path(__file__).resolve().parents[1] / "templates" / "platform" / "master_group.html").read_text(encoding="utf-8")
        self.assertIn('src="/static/js/master_group.js?v=88"', html)


if __name__ == "__main__":
    unittest.main()
