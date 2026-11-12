"""STEP 3-3C-1: Merchant BUSINESS OPTIONS (Platform contract layer, tb_merchant_feature) through the Merchant form API.

Platform Admin reads / changes (changed values only, saved with the Merchant form); Merchant Admin reads only; Gift Card
is Coming soon; KIOSK / Online Ordering are not part of this tab; Merchant choices, Store Options and other Merchants are
never touched; a failed read is never shown as allowed."""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.models_admin.business_type import BusinessType
from backend.models_admin.merchant import Merchant
from backend.models_admin.merchant_feature import MerchantFeature, MerchantFeatureChoice
from backend.models_admin.store import Store
from tools import test_registration_email

KEYS = ["customer", "loyalty", "gift_card", "time_attendance", "inventory", "location_management", "accounting"]


class MerchantBusinessOptionTests(unittest.TestCase):
    def setUp(self):
        test_registration_email.RegistrationEmailTests.setUp(self)
        with self.factory() as db:
            db.add(BusinessType(i_business_type_id=1, c_name="Retail", c_business_code="RETAIL", c_status="active"))
            db.add(Merchant(i_merchant_id=11, c_merchant_code="MER26OTHR", c_merchant_name="Other", c_status="active", i_agency_id=1))
            db.add_all([MerchantFeature(i_merchant_id=10, c_feature_key="customer", b_enabled=True, c_source="MIGRATION"),
                        MerchantFeature(i_merchant_id=10, c_feature_key="kiosk", b_enabled=True, c_source="MIGRATION"),
                        MerchantFeature(i_merchant_id=11, c_feature_key="loyalty", b_enabled=True, c_source="MIGRATION"),
                        MerchantFeatureChoice(i_merchant_id=10, c_feature_key="customer", b_enabled=False)])
            db.get(Store, 10).b_opt_customer = True
            db.commit()

    def rows(self):
        with self.factory() as db:
            return {(r.i_merchant_id, r.c_feature_key): (r.b_enabled, r.c_source)
                    for r in db.query(MerchantFeature).order_by(MerchantFeature.i_merchant_feature_id)}

    def untouched(self):
        with self.factory() as db:
            return ([(c.i_merchant_id, c.c_feature_key, c.b_enabled) for c in db.query(MerchantFeatureChoice)],
                    db.get(Store, 10).b_opt_customer)

    def options(self, merchant_id):
        response = self.client.get(f"/platform/api/merchants/{merchant_id}/business-options")
        self.assertEqual(response.status_code, 200, response.text)
        return {i["key"]: (i["enabled"], i["saved"]) for i in response.json()["items"]}

    def test_registry_rows_in_order_with_saved_values_and_defaults(self):
        listed = {m["id"]: m for m in self.client.get("/platform/merchants").json()}
        items = listed[10]["business_options"]
        self.assertEqual([i["key"] for i in items], KEYS)  # registry order; no KIOSK / Online Ordering
        self.assertEqual([i["label"] for i in items][:3], ["Customer Management", "Loyalty / Points", "Gift Card"])
        gift = items[2]
        self.assertEqual((gift["available"], gift["enabled"], gift["saved"]), (False, False, False))
        self.assertEqual(self.options(10)["customer"], (True, True))       # saved MIGRATION row
        self.assertEqual(self.options(10)["inventory"], (False, False))    # no row: ADDON default Off
        self.assertEqual(self.options(11)["loyalty"], (True, True))

    def test_platform_saves_changed_values_only_with_the_merchant_form(self):
        before_rows, before_untouched = self.rows(), self.untouched()
        # Only BUSINESS OPTIONS in the payload (no other Merchant field changed).
        response = self.client.put("/platform/merchant/10", json={"business_options": {
            "customer": True, "loyalty": False, "inventory": True, "gift_card": False}})
        self.assertEqual(response.status_code, 200, response.text)
        rows = self.rows()
        self.assertEqual(rows[(10, "inventory")], (True, "PLATFORM"))   # changed: written
        self.assertNotIn((10, "loyalty"), rows)                        # unchanged default: still no row
        self.assertEqual(rows[(10, "customer")], (True, "MIGRATION"))   # unchanged saved row: untouched
        self.assertNotIn((10, "gift_card"), rows)
        self.assertEqual({k: v for k, v in rows.items() if k[0] == 11 or k[1] == "kiosk"},
                         {k: v for k, v in before_rows.items() if k[0] == 11 or k[1] == "kiosk"})  # other Merchant / KIOSK
        self.assertEqual(self.untouched(), before_untouched)            # Merchant choice + Store Option kept
        # Back to the original value: the row stays (b_enabled false), every other value unchanged.
        self.assertEqual(self.client.put("/platform/merchant/10", json={"business_options": {"inventory": False}}).status_code, 200)
        self.assertEqual(self.options(10)["inventory"], (False, True))
        self.assertEqual(self.client.put("/platform/merchant/10", json={"business_options": {"customer": False}}).status_code, 200)
        self.assertEqual(self.rows()[(10, "customer")], (False, "PLATFORM"))
        with self.factory() as db:
            audit = db.query(MerchantFeature).filter_by(i_merchant_id=10, c_feature_key="customer").one()
            self.assertEqual(audit.i_updated_by, 1)
            self.assertIsNotNone(audit.dt_updated)

    def test_invalid_values_are_refused_and_nothing_is_saved(self):
        before = self.rows()
        for options, message in (({"gift_card": True}, "Gift Card is Coming soon"), ({"kiosk": True}, "Unknown feature: kiosk"),
                                 ({"customer": "maybe"}, "Invalid value for customer"), (["customer"], "must be an object")):
            response = self.client.put("/platform/merchant/10", json={"merchant_name": "Renamed", "business_options": options})
            self.assertEqual(response.status_code, 400, options)
            self.assertIn(message, response.json()["detail"])
        self.assertEqual(self.rows(), before)
        with self.factory() as db:
            self.assertEqual(db.get(Merchant, 10).c_merchant_name, "Merchant <One>")  # whole save refused

    def test_new_merchant_uses_registry_defaults_and_saves_set_options(self):
        base = {"merchant_name": "Fresh", "primary_agency_id": 1, "business_type": "Retail", "status": "inactive"}
        plain = self.client.post("/platform/merchant", json=base)
        self.assertEqual(plain.status_code, 200, plain.text)
        self.assertEqual({k: v for k, v in self.options(plain.json()["id"]).items()},
                         {k: (False, False) for k in KEYS})                 # no rows: registry defaults (ADDON Off)
        made = self.client.post("/platform/merchant", json={**base, "merchant_name": "Fresh Two",
                                                            "business_options": {"customer": True, "loyalty": False}})
        self.assertEqual(made.status_code, 200, made.text)
        new_id = made.json()["id"]
        # Merchant Code: generated by the server (MER + year + 4), returned and listed; a save cannot change it.
        code = made.json()["merchant_code"]
        self.assertRegex(code, r"^MER\d{2}[A-Z2-9]{4}$")
        listed = {m["id"]: m for m in self.client.get("/platform/merchants").json()}
        self.assertEqual(listed[new_id]["merchant_code"], code)
        self.assertEqual(self.client.put(f"/platform/merchant/{new_id}", json={"merchant_code": "HACKED1"}).status_code, 200)
        with self.factory() as db:
            self.assertEqual(db.get(Merchant, new_id).c_merchant_code, code)
        self.assertEqual({k: v for k, v in self.rows().items() if k[0] == new_id}, {(new_id, "customer"): (True, "PLATFORM")})

    def test_merchant_admin_reads_only(self):
        test_registration_email.RegistrationEmailTests.activate(self, "MERCHANT")
        merchant = TestClient(self.app)
        login = merchant.post("/auth/login", data={"store_code": "MER26ABCD", "email": "primary@example.test",
                                                   "password": "Awsum@5858"}, follow_redirects=False)
        self.assertEqual(login.status_code, 303, login.text)
        own = merchant.get("/merchant/api/business-options")
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual((own.json()["merchant_id"], own.json()["editable"]), (10, False))
        self.assertEqual({i["key"]: i["enabled"] for i in own.json()["items"]}["customer"], True)
        before = self.rows()
        self.assertEqual(merchant.put("/platform/merchant/10", json={"business_options": {"inventory": True}}).status_code, 403)
        self.assertEqual(merchant.get("/platform/api/merchants/11/business-options").status_code, 403)
        self.assertIn(merchant.put("/merchant/api/business-options", json={"inventory": True}).status_code, (404, 405))
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.client.get("/merchant/api/business-options").status_code, 403)  # Platform session is not a Merchant

    def test_read_failure_is_never_shown_as_allowed(self):
        with self.factory() as db:
            db.execute(text("DROP TABLE tb_merchant_feature"))
            db.commit()
        listed = {m["id"]: m for m in self.client.get("/platform/merchants").json()}
        self.assertIsNone(listed[10]["business_options"])
        self.assertIn("temporarily unavailable", listed[10]["business_options_error"])
        self.assertEqual(self.client.get("/platform/api/merchants/10/business-options").status_code, 503)


if __name__ == "__main__":
    unittest.main()
