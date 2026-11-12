"""STEP 3-3C: Feature enforcement through the Common Feature Resolver (services.features) on the operational checks.

Use Location Management (Location Master, its selectors, Category III / MD Location fields) and the MD Item Features
now follow Business Options (contract) + Merchant Feature Entitlements + Store Options + Store Status. Turning a
feature OFF only limits access: Store Options, scopes and Location / MD data are never changed. Unreadable layers
fail closed. The Admin Store stays exempt.

Isolated SQLite app (tools.test_common_app_navigation fixture): Merchant 10 (MER26ABCD) with Store 10 (STR26ABCD).
"""
import unittest
from unittest.mock import patch

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.models_admin.merchant_feature import MerchantFeature, MerchantFeatureChoice
from backend.models_admin.store import Store
from backend.models_admin.store_location import StoreLocation
from backend.services import features
from backend.services import master_group as svc
from backend.services.app_navigation import AppScope
from backend.services.features import FeatureCheckUnavailable, FeatureSnapshot
from backend.services.merchant_features import save_business_options
from tools import test_common_app_navigation

LOC = "/{p}/api/master-group/location"


class FeatureEnforcementTests(unittest.TestCase):
    def setUp(self):
        test_common_app_navigation.CommonAppNavigationTests.setUp(self)
        with self.factory() as db:
            # Existing Location data and Category III location values: must survive every OFF state unchanged.
            db.add(StoreLocation(i_store_id=10, c_location_code="S01-B01-C01", c_location_name="Front",
                                 c_location_area="SALES_FLOOR", i_display_order=1, b_active=True))
            c1 = svc.create_item(db, "category1", {"name": "Merchant cat"}, 1, merchant_id=10)["id"]
            c2 = svc.create_item(db, "category2", {"name": "C2", "category1_id": c1}, 1, merchant_id=10)
            self.c3 = svc.create_item(db, "category3", {"name": "C3", "category1_id": c2["category1_id"],
                                                        "category2_id": c2["id"]}, 1, merchant_id=10)
            from backend.models_admin.master_group import Category3
            db.get(Category3, self.c3["id"]).c_sales_location = "S01-B01-C01"
            db.commit()

    def login(self, scope):
        test_common_app_navigation.CommonAppNavigationTests.login(self, scope)

    def contract(self, enabled=True):
        with self.factory() as db:
            save_business_options(db, 10, {"location_management": enabled}, 1)
            db.commit()

    def choice(self, key, enabled):
        with self.factory() as db:
            row = db.query(MerchantFeatureChoice).filter_by(i_merchant_id=10, c_feature_key=key).first()
            if row is None:
                db.add(MerchantFeatureChoice(i_merchant_id=10, c_feature_key=key, b_enabled=enabled))
            else:
                row.b_enabled = enabled
            db.commit()

    def store_option(self, enabled):
        with self.factory() as db:
            db.get(Store, 10).b_opt_use_shelf_location = enabled
            db.commit()

    def location_access(self, scope):
        """(options flag, menu link, page, list, selector codes) for this login."""
        p = scope.lower()
        self.login(scope)
        options = self.client.get(f"/{p}/api/master-group/options").json()
        page = self.client.get(f"/{p}/master-group/common-maintenance").text
        return (options["location_management"], f'href="/{p}/master-group/location-master"' in page,
                self.client.get(f"/{p}/master-group/location-master").status_code,
                self.client.get(LOC.format(p=p), params={"store_id": 10}).status_code,
                self.client.get(LOC.format(p=p) + "/codes", params={"location_area": "SALES_FLOOR"}).status_code)

    def data_unchanged(self):
        with self.factory() as db:
            from backend.models_admin.master_group import Category3
            store = db.get(Store, 10)
            self.assertEqual(db.query(StoreLocation).filter_by(i_store_id=10).count(), 1)
            self.assertEqual(db.get(Category3, self.c3["id"]).c_sales_location, "S01-B01-C01")
            return store.b_opt_use_shelf_location

    # ---- Location Management: every layer ----
    def test_location_management_follows_contract_choice_and_store_option(self):
        OFF = (False, False, 403, 403, 403)
        ON = (True, True, 200, 200, 200)
        # No Business Option (ADDON, not contracted): neither login has Location Master.
        self.assertEqual(self.location_access("MERCHANT"), OFF)
        self.assertEqual(self.location_access("STORE"), OFF)
        self.assertFalse(self.data_unchanged())
        # Contracted: the Merchant manages it; the Store still needs its own Store Option.
        self.contract(True)
        self.assertEqual(self.location_access("MERCHANT"), ON)
        self.assertEqual(self.location_access("STORE"), OFF)
        self.store_option(True)
        self.assertEqual(self.location_access("STORE"), ON)
        # Merchant turns its Feature Entitlement OFF: both lose access; the Store Option and data stay.
        self.choice("location_management", False)
        self.assertEqual(self.location_access("MERCHANT"), OFF)
        self.assertEqual(self.location_access("STORE"), OFF)
        self.assertTrue(self.data_unchanged())
        self.choice("location_management", True)  # back ON: the same result as before
        self.assertEqual(self.location_access("MERCHANT"), ON)
        self.assertEqual(self.location_access("STORE"), ON)
        # Platform contract OFF again: blocked, nothing deleted.
        self.contract(False)
        self.assertEqual(self.location_access("MERCHANT"), OFF)
        self.assertTrue(self.data_unchanged())
        with self.factory() as db:
            self.assertEqual(db.query(MerchantFeatureChoice).filter_by(i_merchant_id=10).count(), 1)  # choice kept

    def test_location_fields_without_the_feature_keep_their_saved_values(self):
        self.login("MERCHANT")
        body = {"name": "C3", "category1_id": self.c3["category1_id"], "category2_id": self.c3["category2_id"],
                "sales_location": "", "warehouse_location": "W01-R01-C01"}
        saved = self.client.put(f"/merchant/api/master-group/category3/{self.c3['id']}", json=body)
        self.assertEqual(saved.status_code, 200, saved.text)  # other fields save; Location keys are ignored
        self.assertEqual((saved.json()["item"]["sales_location"], saved.json()["item"]["warehouse_location"]),
                         ("S01-B01-C01", None))
        self.assertEqual(self.client.get(LOC.format(p="merchant") + "/lookup", params={"store_id": 10}).status_code, 403)

    # ---- Item Features ----
    def test_item_features_follow_the_merchant_layers(self):
        self.login("MERCHANT")
        flags = lambda p: self.client.get(f"/{p}/api/master-group/options").json()["item_features"]
        all_on = {"color": True, "modifier": True, "bottle_deposit": True, "sugar_tax": True, "output_device": True}
        self.assertEqual(flags("merchant"), all_on)  # BASE: contracted by default, no choice = ON (unchanged)
        self.choice("color", False)
        self.assertFalse(flags("merchant")["color"])
        self.login("STORE")
        self.assertFalse(flags("store")["color"])  # Store login: Merchant OFF wins over its Store Option
        self.choice("color", True)
        self.assertEqual(flags("store")["modifier"], True)
        with self.factory() as db:  # an explicit contract OFF (Platform) works the same way
            db.add(MerchantFeature(i_merchant_id=10, c_feature_key="modifier", b_enabled=False))
            db.commit()
        self.assertFalse(flags("store")["modifier"])

    # ---- fail-closed ----
    def test_unreadable_layers_fail_closed_without_blocking_the_login(self):
        self.contract(True)
        self.login("MERCHANT")
        with patch.object(features, "_read_layer", side_effect=FeatureCheckUnavailable("down")):
            options = self.client.get("/merchant/api/master-group/options")
            self.assertEqual(options.status_code, 200, options.text)
            self.assertFalse(options.json()["location_management"])
            self.assertFalse(any(options.json()["item_features"].values()))
            self.assertEqual(self.client.get(LOC.format(p="merchant"), params={"store_id": 10}).status_code, 403)
            self.assertEqual(self.client.get("/merchant/api/master-group/category1").status_code, 200)

    # ---- Admin Store ----
    def test_admin_store_stays_exempt(self):
        admin = Store(i_store_id=ADMIN_STORE_ID, i_merchant_id=1, c_status="active", b_opt_customer=True,
                      b_opt_use_shelf_location=True)
        contracted_nothing = FeatureSnapshot(merchant_id=1, business={"customer": False, "location_management": False},
                                             choices={"customer": False})
        scope = AppScope("STORE", 1, "Admin", True, feature_snapshot=contracted_nothing)
        self.assertTrue(scope.feature_on("customer", admin))
        self.assertTrue(scope.feature_on("location_management", admin))
        self.assertFalse(scope.feature_on("customer", Store(i_store_id=5, i_merchant_id=1, c_status="active",
                                                            b_opt_customer=True)))
        self.assertFalse(AppScope("STORE", 1, "Admin", True).feature_on("customer", admin))  # no snapshot: OFF


if __name__ == "__main__":
    unittest.main()
