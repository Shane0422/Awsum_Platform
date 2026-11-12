"""Initial Merchant Feature Entitlements: plan / dry run / verified apply / seed-on-creation and the entitlement
loader, on SQLite (the real database is never touched here)."""
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import db_init_platform
from backend.database import merchant_feature_migration as M
from backend.database.pg_platform import ADMIN_STORE_ID, PlatformBase
from backend.models_admin.license import License
from backend.models_admin.merchant import Merchant
from backend.models_admin.merchant_feature import MerchantFeature, MerchantFeatureChoice
from backend.models_admin.store import Store
from backend.models_admin.store_entitlement import StoreEntitlement
from backend.services import features as F
from backend.services.features import resolve_feature


class MerchantFeatureMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        PlatformBase.metadata.create_all(self.engine, tables=[t.__table__ for t in (
            Merchant, Store, StoreEntitlement, License, MerchantFeature, MerchantFeatureChoice)])
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([Merchant(i_merchant_id=1, c_merchant_code="M1", c_merchant_name="One"),
                        Merchant(i_merchant_id=2, c_merchant_code="M2", c_merchant_name="Two"),
                        Merchant(i_merchant_id=3, c_merchant_code="M3", c_merchant_name="Three")])
            db.add_all([
                Store(i_store_id=10, i_merchant_id=1, c_store_code="S10", c_store_name="A", c_status="active",
                      b_opt_customer=True, c_customer_scope="MERCHANT", b_opt_inventory=False, b_opt_use_color=True),
                Store(i_store_id=11, i_merchant_id=1, c_store_code="S11", c_store_name="B", c_status="inactive",
                      b_opt_use_shelf_location=True),
                Store(i_store_id=ADMIN_STORE_ID, i_merchant_id=1, c_store_code="Admin", c_store_name="Admin",
                      b_opt_accounting=True),
                Store(i_store_id=20, i_merchant_id=2, c_store_code="S20", c_store_name="C", c_status="active"),
            ])
            db.add_all([StoreEntitlement(i_store_id=20, i_pos_qty=1, i_kiosk_qty=0, b_customer_ordering=False),
                        StoreEntitlement(i_store_id=10, i_pos_qty=1, i_kiosk_qty=2, b_customer_ordering=False)])
            db.add(License(i_store_id=20, c_license_key="ORD-1", c_license_type="CUSTOMER_ORDERING", c_status="active"))
            db.add(License(i_store_id=20, c_license_key="KSK-1", c_license_type="KIOSK", c_status="cancelled"))
            db.commit()

    def rows(self):
        with self.engine.connect() as conn:
            return {(r.i_merchant_id, r.c_feature_key): (bool(r.b_enabled), r.c_source)
                    for r in conn.execute(text("SELECT * FROM tb_merchant_feature"))}

    def fingerprint(self):
        with self.engine.connect() as conn:
            return M._store_fingerprint(conn, M.read_usage(conn))

    def test_plan_counts_current_usage_and_excludes_the_admin_store(self):
        result = M.dry_run(self.engine)
        plan = {m["i_merchant_id"]: {r["feature"]: r for r in m["rows"]} for m in result["plan"]["merchants"]}
        self.assertEqual(result["plan"]["seed_features"],
                         ["customer", "loyalty", "time_attendance", "inventory", "location_management", "accounting",
                          "kiosk", "online_ordering"])
        self.assertNotIn("gift_card", plan[1])
        self.assertNotIn("color", plan[1])  # BASE: no row (entitled by default)
        on = lambda m: sorted(k for k, r in plan[m].items() if r["enabled"])
        # Inactive Store counts (keep rather than block); Admin Store's Accounting does not.
        self.assertEqual(on(1), ["customer", "kiosk", "location_management"])
        self.assertEqual(plan[1]["location_management"]["evidence"], ["S11 (inactive): b_opt_use_shelf_location=true"])
        self.assertEqual(plan[1]["kiosk"]["evidence"], ["S10: KIOSK qty 2"])
        self.assertEqual(on(2), ["online_ordering"])  # active ordering license; the cancelled KIOSK license does not count
        self.assertEqual(on(3), [])
        self.assertEqual(result["plan"]["excluded_stores"], [{"store": "Admin", "id": ADMIN_STORE_ID, "reason": "Admin Store",
                                                               "uses": ["accounting"]}])
        self.assertEqual(result["comparison"]["problems"], [])
        admin = next(s for s in result["comparison"]["stores"] if s["admin"])
        self.assertEqual(admin["before"], admin["after"])

    def test_dry_run_never_writes(self):
        before = (self.fingerprint(), self.rows())
        M.dry_run(self.engine)
        self.assertEqual((self.fingerprint(), self.rows()), before)

    def test_dry_run_works_before_the_table_exists(self):
        MerchantFeature.__table__.drop(self.engine)
        result = M.dry_run(self.engine)
        self.assertEqual((result["plan"]["table_exists"], result["plan"]["will_seed"]), (False, True))
        with self.assertRaisesRegex(M.FeatureMigrationError, "does not exist"):
            M.apply_merchant_features(self.engine)

    def test_apply_seeds_once_verified_and_never_changes_stores(self):
        before = self.fingerprint()
        result = M.apply_merchant_features(self.engine, actor_id=7)
        self.assertEqual((result["seeded"], result["enabled"]), (24, 4))  # 3 Merchants x 8 ADDON features
        rows = self.rows()
        self.assertEqual(rows[(1, "customer")], (True, "MIGRATION"))
        self.assertEqual(rows[(1, "accounting")], (False, "MIGRATION"))
        self.assertEqual(self.fingerprint(), before)  # options, Scope, quantities and licenses untouched
        with self.Session() as db:
            store = db.get(Store, 10)
            self.assertEqual((store.b_opt_customer, store.c_customer_scope, store.b_opt_inventory), (True, "MERCHANT", False))
        # Seeded once: a later Store change by Platform never grants an entitlement by itself.
        with self.Session() as db:
            db.get(Store, 20).b_opt_inventory = True
            db.commit()
        self.assertIn("already has 24", M.apply_merchant_features(self.engine)["skipped"])
        self.assertEqual(self.rows(), rows)

    def test_a_feature_that_would_be_blocked_stops_apply_without_any_row(self):
        with self.Session() as db:
            db.add(Store(i_store_id=30, i_merchant_id=None, c_store_code="S30", c_store_name="Orphan", b_opt_inventory=True))
            db.commit()
        plan = M.dry_run(self.engine)
        self.assertEqual(plan["comparison"]["problems"], [{"store": "S30", "id": 30, "lost": ["inventory"]}])
        self.assertEqual({s["store"]: s["reason"] for s in plan["plan"]["excluded_stores"]},
                         {"S30": "no Merchant", "Admin": "Admin Store"})
        with self.assertRaisesRegex(M.FeatureMigrationError, "would be blocked"):
            M.apply_merchant_features(self.engine)
        self.assertEqual(self.rows(), {})

    def test_merchant_off_then_on_restores_store_settings(self):
        M.apply_merchant_features(self.engine)
        load = lambda: {k: e for (m, k), (e, _s) in self.rows().items() if m == 1}
        with self.Session() as db:
            store = db.get(Store, 10)
            self.assertTrue(resolve_feature("customer", store, load()).effective)
            db.execute(text("UPDATE tb_merchant_feature SET b_enabled = :e, c_source = 'PLATFORM' "
                            "WHERE i_merchant_id = 1 AND c_feature_key = 'customer'"), {"e": False})
            db.commit()
            off = resolve_feature("customer", store, load())
            self.assertEqual((off.effective, off.reason, off.assigned), (False, "not_contracted", True))
            db.execute(text("UPDATE tb_merchant_feature SET b_enabled = :e WHERE i_merchant_id = 1 AND c_feature_key = 'customer'"),
                       {"e": True})
            db.commit()
            self.assertTrue(resolve_feature("customer", store, load()).effective)
            db.refresh(store)
            self.assertEqual((store.b_opt_customer, store.c_customer_scope), (True, "MERCHANT"))

    def test_table_is_registered_and_seeded_only_in_the_run_that_creates_it(self):
        self.assertIn("merchant_feature", db_init_platform.PLATFORM_MODEL_MODULES)
        self.assertEqual((MerchantFeature.__table__.c.b_enabled.nullable, MerchantFeature.__table__.c.c_feature_key.nullable),
                         (False, False))
        # The table existed before this run: never seeded (rows change only through Platform).
        self.assertIsNone(M.seed_new_merchant_feature_table(self.engine, existed_before=True))
        self.assertEqual(self.rows(), {})
        # Created in this run: seeded once.
        self.assertEqual(M.seed_new_merchant_feature_table(self.engine, existed_before=False)["seeded"], 24)
        self.assertIn("skipped", M.seed_new_merchant_feature_table(self.engine, existed_before=False))
        self.assertEqual(len(self.rows()), 24)
        MerchantFeature.__table__.drop(self.engine)
        self.assertIsNone(M.seed_new_merchant_feature_table(self.engine, existed_before=False))  # no table: nothing

    def test_loader_admin_store_exception_and_never_none_once_the_table_exists(self):
        M.apply_merchant_features(self.engine)
        with self.Session() as db:
            self.assertTrue(F.entitlement_exempt(db.get(Store, ADMIN_STORE_ID)))
            self.assertTrue(F.entitlement_exempt(ADMIN_STORE_ID) and F.entitlement_exempt({"i_store_id": ADMIN_STORE_ID}))
            self.assertFalse(F.entitlement_exempt(db.get(Store, 10)))
            self.assertIsNone(F.merchant_entitlements(db, store=db.get(Store, ADMIN_STORE_ID)))  # Admin Store: LEGACY
            ent = F.merchant_entitlements(db, store=db.get(Store, 10))
            self.assertEqual({k for k, v in ent.items() if v}, {"customer", "kiosk", "location_management"})
            self.assertEqual(F.merchant_entitlements(db, merchant_id=99), {})  # no rows: tier defaults, never None
            self.assertEqual(F.merchant_entitlements(db, store=Store(i_store_id=5, i_merchant_id=None)), {})
            self.assertFalse(resolve_feature("inventory", Store(b_opt_inventory=True), F.merchant_entitlements(db, merchant_id=99)).effective)
            self.assertTrue(resolve_feature("color", Store(b_opt_use_color=True), F.merchant_entitlements(db, merchant_id=99)).effective)

    def test_loader_is_fail_closed_when_a_table_is_missing(self):
        # STEP 3-3B: no LEGACY "everything allowed" for a missing table any more (Admin Store only stays exempt).
        MerchantFeature.__table__.drop(self.engine)
        with self.Session() as db:
            with self.assertRaises(F.FeatureCheckUnavailable):
                F.merchant_entitlements(db, merchant_id=1)
            self.assertIsNone(F.merchant_entitlements(db, store=db.get(Store, ADMIN_STORE_ID)))


if __name__ == "__main__":
    unittest.main()
