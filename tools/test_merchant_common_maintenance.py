"""STEP 1: Merchant Portal menu (same order as Platform) and per-Merchant Common Maintenance (own codes, copied once
from the Platform defaults; tb_merchant_common_init decides, so deleted codes never come back).

Isolated SQLite app (tools.test_common_app_navigation fixture): Merchant 10 (MER26ABCD) with Store 10 (STR26ABCD)
and another Merchant 11. No real database is touched.
"""
import io
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from backend.database.common_code_migration import migrate_common_codes
from backend.database.common_code_migration import UNIT_CODES
from backend.models_admin.common_code import CommonCode, CommonGroup, MerchantCommonInit
from backend.services import master_group as svc
from backend.services.app_navigation import MASTER_SLUGS
from tools import test_common_app_navigation

API = "/{scope}/api/master-group/common_code"
PLATFORM_MASTER_ORDER = ["Common Maintenance", "Tax", "Category I", "Category II", "Category III", "MD Master",
                         "Location Master"]
PLATFORM_UNITS = [code for code, _name in UNIT_CODES]  # EA, PK, BOX ... (display order)


class MerchantCommonMaintenanceTests(unittest.TestCase):
    def setUp(self):
        test_common_app_navigation.CommonAppNavigationTests.setUp(self)
        with redirect_stdout(io.StringIO()):
            migrate_common_codes(self.engine)  # Platform COLOR (13) / UNIT (14) codes, as in the real database
        with self.factory() as db:
            self.other_code = svc.create_item(db, "common_code", {"code": "CTN", "name": "Other Carton"}, 1,
                                              merchant_id=11, group="UNIT")["id"]
            self.platform_ea = db.query(CommonCode.i_common_code_id).filter(
                CommonCode.c_code == "EA", CommonCode.i_merchant_id.is_(None)).scalar()

    def login(self, scope):
        test_common_app_navigation.CommonAppNavigationTests.login(self, scope)

    def url(self, scope="merchant", item_id=None, group="UNIT", **params):
        query = "&".join(f"{k}={v}" for k, v in {"group": group, **params}.items())
        return API.format(scope=scope) + (f"/{item_id}" if item_id else "") + f"?{query}"

    def codes(self, merchant_id, group="UNIT"):
        with self.factory() as db:
            return sorted(code for (code,) in db.query(CommonCode.c_code).join(
                CommonGroup, CommonGroup.i_common_group_id == CommonCode.i_common_group_id).filter(
                CommonGroup.c_group_code == group, svc.common_code_scope(merchant_id)))

    def items(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["items"]

    # ---- Platform: unchanged ----
    def test_platform_common_maintenance_is_unchanged_and_never_shows_merchant_codes(self):
        unit = self.items(self.client.get(self.url("platform", size=100)))
        self.assertEqual(len(unit), 14)
        self.assertNotIn("Other Carton", {i["name"] for i in unit})
        self.assertEqual(len(self.items(self.client.get(self.url("platform", group="COLOR", size=100)))), 13)
        created = self.client.post(self.url("platform"), json={"code": "PLT", "name": "Pallet"})
        self.assertEqual(created.status_code, 201, created.text)
        with self.factory() as db:
            self.assertIsNone(db.get(CommonCode, created.json()["item"]["id"]).i_merchant_id)
        options = self.client.get("/platform/api/master-group/options").json()
        self.assertEqual([g["code"] for g in options["common_groups"]], ["COLOR", "UNIT"])
        self.assertEqual(len(options["units"]), 15)
        self.assertEqual(len(options["color_codes"]), 13)
        export = self.client.get(self.url("platform", format="csv").replace("common_code?", "common_code/export?"))
        self.assertEqual(export.status_code, 200)
        self.assertNotIn("Other Carton", export.text)

    # ---- Merchant CRUD ----
    def test_merchant_crud_on_its_own_unit_and_color_codes(self):
        self.login("MERCHANT")
        listed = self.items(self.client.get(self.url(size=100)))  # first visit: the Platform defaults, copied once
        self.assertEqual([i["code"] for i in listed], PLATFORM_UNITS)
        created = self.client.post(self.url(), json={"code": " ctn ", "name": "Carton", "i_merchant_id": 11,
                                                     "i_common_group_id": 1})
        self.assertEqual(created.status_code, 201, created.text)
        item = created.json()["item"]
        with self.factory() as db:
            self.assertEqual(db.get(CommonCode, item["id"]).i_merchant_id, 10)  # the scope, never a payload value
        for payload, field in (({"code": "CTN", "name": "Carton 2"}, "code"), ({"code": "pk", "name": "Pack 2"}, "code"),
                               ({"code": "NEW", "name": "each"}, "name")):
            response = self.client.post(self.url(), json=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn(field, response.json()["errors"])
        updated = self.client.put(self.url(item_id=item["id"]), json={"code": "XYZ", "name": "Carton Box"})
        self.assertEqual((updated.status_code, updated.json()["item"]["code"], updated.json()["item"]["name"]),
                         (200, "CTN", "Carton Box"))
        self.assertEqual(self.client.delete(self.url(item_id=item["id"])).status_code, 200)
        self.assertEqual(self.codes(10), sorted(PLATFORM_UNITS))

        colors = self.items(self.client.get(self.url(group="COLOR", size=100)))
        self.assertEqual([(c["code"], c["name"]) for c in colors][:2], [("CLR00001", "Black"), ("CLR00002", "White")])
        color = self.client.post(self.url(group="COLOR"), json={"name": "Teal", "value": "#008080"})
        self.assertEqual(color.status_code, 201, color.text)
        self.assertEqual((color.json()["item"]["code"], color.json()["item"]["value"]), ("CLR00014", "#008080"))  # own sequence
        options = self.client.get("/merchant/api/master-group/options").json()
        self.assertEqual([g["code"] for g in options["common_groups"]], ["COLOR", "UNIT"])
        self.assertFalse(any(g["read_only"] for g in options["common_groups"]))
        self.assertEqual([u["code"] for u in options["units"]], PLATFORM_UNITS)
        self.assertEqual(len(options["color_codes"]), 14)
        export = self.client.get(self.url(format="csv").replace("common_code?", "common_code/export?"))
        self.assertEqual(export.status_code, 200)
        self.assertIn("Pound", export.text)  # the Merchant's own copy
        self.assertNotIn("Other Carton", export.text)
        self.assertEqual(len(self.codes(None)), 14)  # Platform codes untouched
        self.assertEqual(len(self.codes(None, "COLOR")), 13)

    def init_rows(self, merchant_id=10):
        with self.factory() as db:
            return sorted((r.c_group_code, r.i_copied_count, r.i_skipped_count) for r in
                          db.query(MerchantCommonInit).filter(MerchantCommonInit.i_merchant_id == merchant_id))

    def test_existing_merchant_code_is_kept_and_the_rest_copied_once(self):
        # DA ONE case: EA already exists (created before this initialization) and keeps its ID and values.
        with self.factory() as db:
            unit = db.query(CommonGroup).filter(CommonGroup.c_group_code == "UNIT").one().i_common_group_id
            ea = CommonCode(i_common_group_id=unit, i_merchant_id=10, c_code="EA", c_name="Each", i_display_order=10,
                            b_active=True, c_description="kept")
            db.add(ea)
            db.commit()
            ea_id = ea.i_common_code_id
        self.login("MERCHANT")
        self.assertEqual(len(self.items(self.client.get(self.url(size=100)))), 14)
        self.assertEqual(self.init_rows(), [("COLOR", 13, 0), ("UNIT", 13, 1)])
        with self.factory() as db:
            kept = db.get(CommonCode, ea_id)
            self.assertEqual((kept.c_code, kept.c_name, kept.c_description, kept.i_merchant_id), ("EA", "Each", "kept", 10))
            self.assertEqual(db.query(CommonCode).filter(CommonCode.i_merchant_id == 10, CommonCode.c_code == "EA").count(), 1)
        self.assertEqual(self.codes(10), sorted(PLATFORM_UNITS))
        self.assertEqual(len(self.codes(10, "COLOR")), 13)
        self.assertEqual(self.codes(11), ["CTN"])  # another Merchant is untouched
        self.assertEqual(self.client.delete(self.url(item_id=ea_id)).status_code, 409)  # EA stays protected
        self.assertEqual(self.client.put(self.url(item_id=ea_id), json={"name": "Each", "active": False}).status_code, 422)

    def test_deleted_codes_and_platform_changes_are_never_copied_again(self):
        self.login("MERCHANT")
        pk = next(i for i in self.items(self.client.get(self.url(size=100))) if i["code"] == "PK")
        black = self.items(self.client.get(self.url(group="COLOR", size=100)))[0]
        self.assertEqual(self.client.delete(self.url(item_id=pk["id"])).status_code, 200)
        self.assertEqual(self.client.delete(self.url(group="COLOR", item_id=black["id"])).status_code, 200)
        with self.factory() as db:  # a later Platform change
            svc.create_item(db, "common_code", {"code": "PLT", "name": "Pallet"}, 1, group="UNIT")
        for _ in range(2):  # later visits (next login)
            self.items(self.client.get(self.url(size=100)))
            self.client.get("/merchant/api/master-group/options")
        self.assertNotIn("PK", self.codes(10))
        self.assertNotIn("PLT", self.codes(10))
        self.assertNotIn("CLR00001", self.codes(10, "COLOR"))
        self.assertEqual(len(self.codes(10)), 13)
        self.assertEqual(self.init_rows(), [("COLOR", 13, 0), ("UNIT", 14, 0)])
        with self.factory() as db:
            self.assertEqual(svc.initialize_merchant_common_codes(db, 10, 1), {})  # the init rows decide

    def test_concurrent_first_visit_never_duplicates_or_fails(self):
        with self.factory() as db:  # the claim (init row primary key) refuses a second initialization of a group
            db.add(MerchantCommonInit(i_merchant_id=10, c_group_code="UNIT"))
            db.commit()
            db.add(MerchantCommonInit(i_merchant_id=10, c_group_code="UNIT"))
            with self.assertRaises(IntegrityError):
                db.commit()
        # UNIT was claimed by "another request": this visit copies COLOR only, no duplicate, no error.
        self.login("MERCHANT")
        self.assertEqual(self.items(self.client.get(self.url(size=100))), [])
        self.assertEqual(len(self.codes(10, "COLOR")), 13)
        # A concurrent request winning the claim while this one copies: rolled back quietly, the page still answers.
        with self.factory() as db:
            db.query(MerchantCommonInit).filter(MerchantCommonInit.c_group_code == "COLOR").delete()
            db.commit()
        raced = IntegrityError("INSERT INTO tb_merchant_common_init", {}, Exception("duplicate key"))
        with patch.object(svc, "_copy_platform_group", side_effect=raced):
            response = self.client.get(self.url(group="COLOR", size=100))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.codes(10, "COLOR")), 13)

    def test_new_merchant_gets_its_own_copy_when_created(self):
        from backend.models_admin.business_type import BusinessType
        with self.factory() as db:
            db.add(BusinessType(i_business_type_id=1, c_name="Retail", c_business_code="RETAIL", c_status="active"))
            db.commit()
        base = {"primary_agency_id": 1, "business_type": "Retail", "status": "inactive"}
        response = self.client.post("/platform/merchant", json={**base, "merchant_name": "Fresh Shop"})
        self.assertEqual(response.status_code, 200, response.text)
        merchant_id = response.json()["id"]
        self.assertEqual(self.codes(merchant_id), sorted(PLATFORM_UNITS))
        self.assertEqual(len(self.codes(merchant_id, "COLOR")), 13)
        self.assertEqual(self.init_rows(merchant_id), [("COLOR", 13, 0), ("UNIT", 14, 0)])
        self.assertEqual(len(self.codes(None)), 14)
        # The copy is part of the Merchant registration: when it fails, the Merchant is not created either.
        with patch("backend.routers.platform_store.initialize_merchant_common_codes",
                   side_effect=IntegrityError("x", {}, Exception("copy failed"))):
            failed = self.client.post("/platform/merchant", json={**base, "merchant_name": "Broken Shop"})
        self.assertGreaterEqual(failed.status_code, 400)
        with self.factory() as db:
            from backend.models_admin.merchant import Merchant
            self.assertIsNone(db.query(Merchant).filter(Merchant.c_merchant_name == "Broken Shop").first())

    def test_store_login_never_initializes(self):
        self.login("STORE")
        self.assertEqual(self.items(self.client.get(self.url("store", size=100))), [])
        self.client.get("/store/api/master-group/options")
        self.assertEqual(self.init_rows(), [])
        self.assertEqual(self.codes(10), [])

    def test_other_merchant_and_platform_codes_are_not_reachable(self):
        self.login("MERCHANT")
        listed = {i["id"] for i in self.items(self.client.get(self.url(size=100)))}
        self.assertFalse(listed & {self.other_code, self.platform_ea})
        for foreign in (self.other_code, self.platform_ea):
            self.assertEqual(self.client.put(self.url(item_id=foreign), json={"name": "Hijack"}).status_code, 404)
            self.assertEqual(self.client.delete(self.url(item_id=foreign)).status_code, 404)
        with self.factory() as db:
            self.assertEqual(db.get(CommonCode, self.other_code).c_name, "Other Carton")
            self.assertEqual(db.get(CommonCode, self.platform_ea).c_name, "Each")
        self.assertEqual(self.client.get(self.url("platform")).status_code, 403)  # no Platform route for a Merchant

    def test_unique_policy_is_per_scope(self):
        with self.factory() as db:
            unit = db.query(CommonGroup).filter(CommonGroup.c_group_code == "UNIT").one().i_common_group_id
            # Same Code in another Merchant and in Platform: allowed (separate scopes)
            svc.create_item(db, "common_code", {"code": "CTN", "name": "Carton"}, 1, merchant_id=10, group="UNIT")
            svc.create_item(db, "common_code", {"code": "CTN", "name": "Carton"}, 1, group="UNIT")
            # Same Code twice in one Merchant: refused by the service and by the DB unique index
            with self.assertRaises(svc.FieldErrors):
                svc.create_item(db, "common_code", {"code": "ctn", "name": "Carton 3"}, 1, merchant_id=10, group="UNIT")
            db.rollback()
            db.add(CommonCode(i_common_group_id=unit, i_merchant_id=10, c_code="CTN", c_name="Raw"))
            with self.assertRaises(IntegrityError):
                db.commit()
        self.assertEqual(self.codes(10), ["CTN"])
        self.assertEqual(self.codes(11), ["CTN"])
        self.assertIn("CTN", self.codes(None))

    # ---- Store: inherits the Merchant's codes, view only ----
    def test_store_login_views_its_merchant_codes_only(self):
        with self.factory() as db:
            mine = svc.create_item(db, "common_code", {"code": "CTN", "name": "Carton"}, 1, merchant_id=10, group="UNIT")["id"]
        self.login("STORE")
        rows = self.items(self.client.get(self.url("store", size=100)))
        self.assertEqual([(r["id"], r["read_only"]) for r in rows], [(mine, True)])
        self.assertEqual(self.codes(10), ["CTN"])  # a Store visit never creates the Merchant's EA
        options = self.client.get("/store/api/master-group/options").json()
        self.assertTrue(all(g["read_only"] for g in options["common_groups"]))
        for response in (self.client.post(self.url("store"), json={"code": "BAG", "name": "Bag"}),
                         self.client.put(self.url("store", item_id=mine), json={"name": "Changed"}),
                         self.client.delete(self.url("store", item_id=mine))):
            self.assertEqual(response.status_code, 403, response.text)
        with self.factory() as db:
            self.assertEqual(db.get(CommonCode, mine).c_name, "Carton")

    # ---- Navigation and access ----
    def test_merchant_menu_follows_the_platform_master_group_order(self):
        def master_menu(html):
            chunk = html.split('id="mega-master-group"', 1)[1].split("</section>", 1)[0]
            return re.findall(r'class="platform-menu-link[^"]*"[^>]*>([^<]+)<', chunk)

        platform_order = master_menu(self.client.get("/platform/master-group/tax").text)
        self.assertEqual(platform_order, PLATFORM_MASTER_ORDER)
        self.assertEqual([label for _slug, label, _available in MASTER_SLUGS], platform_order)
        for scope in ("MERCHANT", "STORE"):
            self.login(scope)
            prefix = "/" + scope.lower()
            page = self.client.get(f"{prefix}/master-group/common-maintenance")
            self.assertEqual(page.status_code, 200, page.text[:300])
            # Same order. Location Master needs Use Location Management (STEP 3-3C): this fixture's Merchant has no
            # Business Option for it (ADDON) and its Store's option is OFF, so both logins hide it.
            expected = platform_order[:-1]
            self.assertEqual(master_menu(page.text), expected)
            self.assertIn(f'href="{prefix}/master-group/common-maintenance"', page.text)
            self.assertIn(f'href="{prefix}/master-group/md-master"', page.text)  # STEP 2: Merchant MD Master (Store: view)
            business = page.text.split('id="mega-business"', 1)[1].split("</section>", 1)[0]
            self.assertIn("Organizations", business)
            self.assertRegex(business, r'disabled[^>]*>Stores<span class="platform-menu-soon">Soon</span>')
            self.assertNotIn("href=", business)
            self.assertEqual(self.client.get(f"{prefix}/master-group/md-master").status_code, 200)
            self.assertEqual(self.client.get(f"{prefix}/api/master-group/md").status_code, 200)
            self.assertEqual(self.client.get(f"{prefix}/api/master-group/item_kind").status_code, 403)  # Item Kind: Platform only

    def test_item_kind_stays_global(self):
        self.login("MERCHANT")
        options = self.client.get("/merchant/api/master-group/options").json()
        with self.factory() as db:
            from backend.models_admin.master_group import ItemKind
            self.assertFalse(hasattr(ItemKind, "i_merchant_id"))
        self.assertIn("item_kinds", options)  # the shared Item Kind list is still offered to every scope
        self.assertEqual(self.client.post("/merchant/api/master-group/item_kind", json={"name": "X"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
