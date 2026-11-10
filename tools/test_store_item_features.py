"""Store Options > Item Features: defaults, save/reload, Portal Scope flags and OFF keeping saved MD data."""
import unittest

from backend.models_admin.store import Store
from backend.routers import master_group, platform_store
from backend.services import store_item_features as features
from tools import test_registration_email

KEYS = ("opt_use_color", "opt_use_modifier", "opt_use_bottle_deposit", "opt_use_sugar_tax", "opt_use_output_device")
DEFAULTS = {"opt_use_color": "off", "opt_use_modifier": "on", "opt_use_bottle_deposit": "off",
            "opt_use_sugar_tax": "off", "opt_use_output_device": "on"}
ALL_ON = {"color": True, "modifier": True, "bottle_deposit": True, "sugar_tax": True, "output_device": True}


class StoreItemFeatureUnitTests(unittest.TestCase):
    def test_defaults_for_stores_without_saved_values(self):
        data = platform_store._store_license_and_options(Store())
        self.assertEqual({key: data[key] for key in KEYS}, DEFAULTS)
        # Existing Store Options keep their OFF default.
        self.assertEqual((data["opt_customer"], data["opt_accounting"]), ("off", "off"))
        self.assertEqual(features.item_features(Store()), {
            "color": False, "modifier": True, "bottle_deposit": False, "sugar_tax": False, "output_device": True,
        })
        self.assertEqual(features.item_features(), ALL_ON)

    def test_location_management_is_a_business_option_on_the_kept_column(self):
        # Not an Item Feature any more; stored in the earlier b_opt_use_shelf_location column (saved values kept).
        self.assertNotIn("shelf_location", features.ITEM_FEATURES)
        self.assertEqual(platform_store.STORE_OPTION_FIELDS["opt_location_management"], ("b_opt_use_shelf_location", None, None))
        self.assertEqual(platform_store._store_license_and_options(Store())["opt_location_management"], "off")  # default OFF
        self.assertEqual(platform_store._store_license_and_options(Store(b_opt_use_shelf_location=True))["opt_location_management"], "on")
        store = Store(c_store_license_key="SL-TEST0002", b_opt_inventory=False)
        platform_store._apply_store_license_and_options(None, store, {}, creating=True)
        self.assertFalse(store.b_opt_use_shelf_location)
        # Independent of Inventory Management.
        platform_store._apply_store_license_and_options(None, store, {"opt_location_management": "on", "opt_inventory": "off"}, creating=False)
        self.assertEqual((store.b_opt_use_shelf_location, store.b_opt_inventory), (True, False))
        platform_store._apply_store_license_and_options(None, store, {"opt_customer": "on"}, creating=False)
        self.assertTrue(store.b_opt_use_shelf_location)  # absent key keeps the saved option

    def test_create_uses_defaults_and_save_reload_toggles_each_option(self):
        store = Store(c_store_license_key="SL-TEST0001")
        platform_store._apply_store_license_and_options(None, store, {}, creating=True)
        self.assertEqual({key: platform_store._store_license_and_options(store)[key] for key in KEYS}, DEFAULTS)
        flipped = {key: ("on" if value == "off" else "off") for key, value in DEFAULTS.items()}
        platform_store._apply_store_license_and_options(None, store, dict(flipped), creating=False)
        self.assertEqual({key: platform_store._store_license_and_options(store)[key] for key in KEYS}, flipped)
        # Keys absent on update keep the saved option.
        platform_store._apply_store_license_and_options(None, store, {"opt_customer": "on"}, creating=False)
        self.assertEqual({key: platform_store._store_license_and_options(store)[key] for key in KEYS}, flipped)

    def test_off_features_are_dropped_from_md_payload_only(self):
        payload = {"name": "Cola", "modifier": "No Ice", "output_devices": ["1"]}
        self.assertIs(features.without_disabled_item_features(payload, ALL_ON), payload)
        off = {**ALL_ON, "modifier": False, "output_device": False}
        self.assertEqual(features.without_disabled_item_features(payload, off), {"name": "Cola"})
        self.assertEqual(payload["output_devices"], ["1"])  # caller's payload is not mutated
        # Bottle Deposit / Sugar Tax have no MD field yet: OFF changes nothing.
        self.assertIs(features.without_disabled_item_features(
            payload, {**ALL_ON, "bottle_deposit": False, "sugar_tax": False}), payload)
        # Use Color OFF drops the MD Detail Color, so the saved Color is kept.
        detail = {"size": "M", "color_code_id": 3}
        self.assertEqual(features.without_disabled_item_features(detail, {**ALL_ON, "color": False}), {"size": "M"})
        self.assertIs(features.without_disabled_item_features(detail, ALL_ON), detail)


class StoreItemFeatureScopeTests(unittest.TestCase):
    def setUp(self):
        test_registration_email.RegistrationEmailTests.setUp(self)
        self.app.include_router(master_group.router)
        self.app.dependency_overrides[master_group.get_db] = self.app.dependency_overrides[platform_store.get_db]
        for owner in ("MERCHANT", "STORE"):
            test_registration_email.RegistrationEmailTests.activate(self, owner)
        with self.factory() as db:
            store = db.get(Store, 10)
            store.b_opt_use_modifier = False
            store.b_opt_use_output_device = False
            store.b_opt_use_color = True
            db.commit()

    def login(self, scope):
        codes = {"MERCHANT": "MER26ABCD", "STORE": "STR26ABCD"}
        response = self.client.post("/auth/login", data={
            "store_code": codes[scope], "email": "primary@example.test", "password": "Awsum@5858",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)

    def test_platform_and_merchant_always_all_store_follows_its_options(self):
        platform = self.client.get("/platform/api/master-group/options")
        self.assertEqual(platform.status_code, 200, platform.text)
        self.assertEqual(platform.json()["item_features"], ALL_ON)
        self.login("MERCHANT")
        self.assertEqual(self.client.get("/merchant/api/master-group/options").json()["item_features"], ALL_ON)
        self.login("STORE")
        self.assertEqual(self.client.get("/store/api/master-group/options").json()["item_features"], {
            "color": True, "modifier": False, "bottle_deposit": False, "sugar_tax": False, "output_device": False,
        })
        # MD Master access is unchanged: still Platform-only for a Store login (the earlier Color API was removed).
        self.assertEqual(self.client.get("/store/api/master-group/md").status_code, 403)
        self.assertEqual(self.client.post("/store/api/master-group/md", json={"modifier": "x"}).status_code, 403)
        self.assertEqual(self.client.get("/store/api/master-group/color").status_code, 404)  # removed (COLOR step 6)
        self.login("MERCHANT")
        self.assertEqual(self.client.get("/merchant/api/master-group/color").status_code, 404)
        # Category III "Apply to all MD Master" is Platform-only: blocked before any record is read or saved.
        for prefix in ("merchant", "store"):
            if prefix == "store":
                self.login("STORE")
            response = self.client.put(f"/{prefix}/api/master-group/category3/1",
                                       json={"name": "Leaf", "inventory_type": 3, "apply_inventory_to_md": True},
                                       headers={"origin": "http://testserver"})
            self.assertEqual(response.status_code, 403, response.text)
            self.assertIn("Platform Admin", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
