"""STEP 3-3B: three-layer Feature resolver (Business Option + Merchant Feature Entitlement + Store Option + Store
Status), ON/OFF/NULL, OFF -> ON restore without writes, fail-closed loading and the common Scope integration."""
import itertools
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import merchant_feature_migration as M
from backend.database.pg_platform import ADMIN_STORE_ID, PlatformBase
from backend.models_admin.license import License
from backend.models_admin.merchant import Merchant
from backend.models_admin.merchant_feature import MerchantFeature, MerchantFeatureChoice
from backend.models_admin.store import Store
from backend.models_admin.store_entitlement import StoreEntitlement
from backend.services import features as F
from backend.services import store_item_features as items
from backend.services.app_navigation import AppScope
from backend.services.store_locations import location_management_enabled
from tools.test_features import NoWriteStore

TRI = (None, True, False)


def snap(business=None, choices=None, **kw):
    return F.FeatureSnapshot(merchant_id=1, business=business or {}, choices=choices or {}, **kw)


class LayerResolverTests(unittest.TestCase):
    def test_every_on_off_null_combination_for_addon_and_base(self):
        # ADDON (Inventory: contract default OFF, store default OFF) and BASE (Modifier: contract default ON, store default ON).
        for key, column in (("inventory", "b_opt_inventory"), ("modifier", "b_opt_use_modifier")):
            d = F.feature(key)
            for business, choice, store_value, status in itertools.product(TRI, TRI, TRI, ("active", "inactive")):
                with self.subTest(key=key, business=business, choice=choice, store=store_value, status=status):
                    state = F.resolve_feature(key, NoWriteStore(**{column: store_value, "c_status": status}),
                                              snap({} if business is None else {key: business},
                                                   {} if choice is None else {key: choice}))
                    contracted = (d.tier == F.BASE) if business is None else business
                    merchant_on = True if choice is None else choice   # no Merchant choice = follows the contract
                    assigned = d.store_default if store_value is None else store_value
                    expected = (F.NOT_CONTRACTED if not contracted else F.MERCHANT_OFF if not merchant_on
                                else F.STORE_OFF if not assigned else F.STORE_INACTIVE if status != "active" else None)
                    self.assertEqual((state.reason, state.effective), (expected, expected is None))
                    self.assertEqual((state.contracted, state.merchant_enabled, state.assigned, state.entitled),
                                     (contracted, merchant_on, assigned, contracted and merchant_on))
                    self.assertEqual(state.merchant_source, "default" if choice is None else "merchant")
                    self.assertEqual(state.assigned_source, "default" if store_value is None else "store")

    def test_platform_off_on_and_merchant_off_on_restore_store_settings_without_writes(self):
        stores = {"A": NoWriteStore(b_opt_customer=True, c_status="active"),
                  "B": NoWriteStore(b_opt_customer=False, c_status="active"), "C": NoWriteStore(c_status="active")}
        run = lambda business, choice: {n: F.feature_enabled("customer", s, snap({"customer": business},
                                                                                  {} if choice is None else {"customer": choice}))
                                        for n, s in stores.items()}
        before = run(True, None)
        self.assertEqual(before, {"A": True, "B": False, "C": False})
        self.assertEqual(run(False, None), dict.fromkeys(stores, False))   # Platform OFF
        self.assertEqual(run(True, None), before)                          # Platform ON again
        self.assertEqual(run(True, False), dict.fromkeys(stores, False))   # Merchant OFF
        self.assertEqual(run(True, True), before)                          # Merchant ON again
        self.assertEqual(run(False, True), dict.fromkeys(stores, False))   # Merchant ON cannot exceed the contract
        self.assertEqual(stores["A"]._values, {"b_opt_customer": True, "c_status": "active"})  # NoWriteStore: never written
        off = F.resolve_feature("customer", stores["A"], snap({"customer": True}, {"customer": False}))
        self.assertEqual((off.reason, off.assigned, off.assigned_source), (F.MERCHANT_OFF, True, "store"))  # saved value visible

    def test_unavailable_dependency_and_merchant_level(self):
        gift = F.resolve_feature("gift_card", None, snap({"gift_card": True}, {"gift_card": True}))
        self.assertEqual((gift.reason, gift.effective, gift.contracted), (F.NOT_AVAILABLE, False, False))
        # Merchant level (no Store): Store Options and Status are not considered.
        level = F.resolve_features(None, snap({"inventory": True}, {"loyalty": False}))
        self.assertEqual((level["inventory"].effective, level["inventory"].store_active), (True, True))
        self.assertEqual(level["loyalty"].reason, F.NOT_CONTRACTED)    # contract first, then the Merchant choice
        self.assertEqual(level["color"].effective, True)                # BASE: contracted by default
        registry = (F.FeatureDef("inventory", "Inventory", "business", F.ADDON, "b_opt_inventory", False, "opt_inventory"),
                    F.FeatureDef("location_management", "Location", "business", F.ADDON, "b_opt_use_shelf_location", False,
                                 "opt_location_management", requires=("inventory",)))
        store = NoWriteStore(b_opt_inventory=True, b_opt_use_shelf_location=True, c_status="active")
        states = F.resolve_features(store, snap({"inventory": True, "location_management": True}, {"inventory": False}),
                                    registry=registry)
        self.assertEqual((states["location_management"].reason, states["location_management"].blocked_by), (F.REQUIRES, ("inventory",)))

    def test_admin_store_is_exempt_and_legacy_callers_are_unchanged(self):
        admin = NoWriteStore(i_store_id=ADMIN_STORE_ID, b_opt_accounting=True, c_status="active")
        state = F.resolve_feature("accounting", admin, F.FeatureSnapshot(exempt=True))
        self.assertEqual((state.effective, state.contracted, state.merchant_source), (True, True, None))
        # Existing operational callers still pass no layers (no new enforcement in STEP 3-3B): unchanged results,
        # Store Status not applied (an Inactive Store reads exactly as before).
        inactive = Store(c_status="inactive", b_opt_use_shelf_location=True, b_opt_use_color=True)
        self.assertTrue(location_management_enabled(inactive))
        self.assertEqual(items.item_features(inactive)["color"], True)
        self.assertEqual(F.resolve_feature("color", inactive, {"color": True}).reason, None)  # mapping: contract only


class FailClosedLoaderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        PlatformBase.metadata.create_all(self.engine, tables=[t.__table__ for t in (
            Merchant, Store, StoreEntitlement, License, MerchantFeature, MerchantFeatureChoice)])
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([Merchant(i_merchant_id=1, c_merchant_code="M1", c_merchant_name="One"),
                        Merchant(i_merchant_id=2, c_merchant_code="M2", c_merchant_name="Two")])
            db.add_all([Store(i_store_id=10, i_merchant_id=1, c_store_code="S10", c_store_name="A", c_status="active",
                              b_opt_inventory=True),
                        Store(i_store_id=ADMIN_STORE_ID, i_merchant_id=1, c_store_code="Admin", c_store_name="Admin",
                              c_status="active")])
            db.add_all([MerchantFeature(i_merchant_id=1, c_feature_key="inventory", b_enabled=True, c_source="MIGRATION"),
                        MerchantFeature(i_merchant_id=1, c_feature_key="customer", b_enabled=False, c_source="MIGRATION"),
                        MerchantFeatureChoice(i_merchant_id=1, c_feature_key="inventory", b_enabled=False)])
            db.commit()

    def test_snapshot_reads_both_layers(self):
        with self.Session() as db:
            store = db.get(Store, 10)
            snapshot = F.load_feature_snapshot(db, store=store)
            self.assertEqual((snapshot.business, snapshot.choices, snapshot.exempt),
                             ({"inventory": True, "customer": False}, {"inventory": False}, False))
            self.assertEqual(F.resolve_feature("inventory", store, snapshot).reason, F.MERCHANT_OFF)
            self.assertEqual(F.load_feature_snapshot(db, merchant_id=2), F.FeatureSnapshot(2, {}, {}))  # no rows
            self.assertEqual(F.load_feature_snapshot(db, store=Store(i_store_id=5, i_merchant_id=None)), F.FeatureSnapshot(None, {}, {}))
            self.assertTrue(F.load_feature_snapshot(db, store=db.get(Store, ADMIN_STORE_ID)).exempt)

    def test_missing_table_and_database_error_are_fail_closed(self):
        with self.Session() as db:
            with patch.object(db, "execute", side_effect=OperationalError("SELECT", {}, Exception("connection lost"))):
                with self.assertRaises(F.FeatureCheckUnavailable):
                    F.load_feature_snapshot(db, merchant_id=1)
        MerchantFeatureChoice.__table__.drop(self.engine)
        with self.Session() as db:
            store = db.get(Store, 10)
            with self.assertRaises(F.FeatureCheckUnavailable):
                F.load_feature_snapshot(db, store=store)
            # The read ran in a SAVEPOINT: the caller's session stays usable after the failure.
            self.assertEqual(db.execute(text("SELECT count(*) FROM tb_merchant_feature")).scalar(), 2)
            self.assertTrue(F.load_feature_snapshot(db, store=db.get(Store, ADMIN_STORE_ID)).exempt)  # no read needed

    def test_common_scope_uses_the_loader_and_its_own_merchant(self):
        scope = AppScope("MERCHANT", 1, "Merchant Admin", True)
        with self.Session() as db:
            level = scope.feature_states(db)
            self.assertEqual((level["inventory"].reason, level["customer"].reason, level["color"].effective),
                             (F.MERCHANT_OFF, F.NOT_CONTRACTED, True))
            self.assertEqual(scope.feature_states(db, db.get(Store, 10))["inventory"].reason, F.MERCHANT_OFF)
            other = Store(i_store_id=99, i_merchant_id=2, c_status="active")
            with self.assertRaises(Exception):
                scope.feature_states(db, other)
        MerchantFeature.__table__.drop(self.engine)
        with self.Session() as db, self.assertRaises(F.FeatureCheckUnavailable):
            scope.feature_states(db)

    def test_impact_report_lists_merchant_level_changes_with_location_data(self):
        with self.engine.connect() as conn:
            result = M.policy_impact(conn)
        merchants = {m["id"]: m for m in result["merchants"]}
        self.assertEqual(merchants[1]["lost_at_merchant_level"], ["location_management"])
        self.assertEqual(merchants[1]["reasons"], {"location_management": F.NOT_CONTRACTED})
        self.assertEqual((result["business_rows"], result["choice_rows"]), (2, 1))
        s10 = next(s for s in result["stores"] if s["id"] == 10)
        self.assertEqual(s10["lost"], ["inventory"])  # Merchant turned Inventory OFF: listed as a problem
        self.assertEqual(result["problems"], [{"store": "S10", "id": 10, "lost": ["inventory"]}])


if __name__ == "__main__":
    unittest.main()
