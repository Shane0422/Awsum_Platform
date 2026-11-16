"""SO-1c: Store Settings API (GET / PUT /{merchant,store}/api/master-group/md/{id}/store-settings) and the Store
Merchant change check.

Isolated SQLite app (tools.test_merchant_md_master fixture): Merchant 10 (MER26ABCD) with Store 10 (STR26ABCD, the
Store login) plus Stores 21 / 22 (active) and 23 (inactive) added here; Merchant 11 with Store 31. No real database
is touched; the PostgreSQL API check runs on a temporary copy (see the SO-1c report).
"""
import unittest
from dataclasses import replace
from unittest.mock import patch

from backend.models_admin.master_group import MdDetail, MdMaster
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.role import Role
from backend.models_admin.store import Store
from backend.models_admin.store_md import StoreMd, StoreMdPrice
from backend.routers import master_group
from tools import test_merchant_md_master as base

ORIGIN = {"origin": "http://testserver"}


class StoreSettingsApiTests(unittest.TestCase):
    url = base.MerchantMdMasterTests.url
    ok = base.MerchantMdMasterTests.ok
    login = base.MerchantMdMasterTests.login
    merchant_category = base.MerchantMdMasterTests.merchant_category
    merchant_md = base.MerchantMdMasterTests.merchant_md
    md_payload = staticmethod(base.MerchantMdMasterTests.md_payload)
    row = base.MerchantMdMasterTests.row

    def setUp(self):
        base.MerchantMdMasterTests.setUp(self)
        with self.factory() as db:
            db.add_all([Store(i_store_id=21, i_merchant_id=10, c_store_code="STR26B", c_store_name="B Store", c_status="active"),
                        Store(i_store_id=22, i_merchant_id=10, c_store_code="STR26C", c_store_name="C Store", c_status="active"),
                        Store(i_store_id=23, i_merchant_id=10, c_store_code="STR26D", c_store_name="D Store", c_status="inactive"),
                        Store(i_store_id=31, i_merchant_id=11, c_store_code="STR26X", c_store_name="X Store", c_status="active")])
            db.commit()
        self.login("MERCHANT")
        self.md = self.merchant_md("Coffee", retail_price="5")
        self.second = self.ok(self.client.post(self.url(f"md/{self.md['id']}/details"),
                                               json={"unit": "EA", "price": "9", "size": "L"}), 201)["item"]
        self.default = next(d for d in self.ok(self.client.get(self.url(f"md/{self.md['id']}/details")))["items"]
                            if d["is_default"])

    def settings_url(self, scope="merchant", md_id=None):
        return self.url(f"md/{md_id or self.md['id']}/store-settings", scope)

    def grid(self, scope="merchant"):
        return self.ok(self.client.get(self.settings_url(scope)))

    def put(self, prices=(), visibility=(), scope="merchant", md_id=None):
        return self.client.put(self.settings_url(scope, md_id), json={"prices": list(prices), "visibility": list(visibility)},
                               headers=ORIGIN)

    @staticmethod
    def cell(grid, store, detail):
        return next(s for s in grid["stores"] if s["id"] == store)["prices"][str(detail)]

    @staticmethod
    def price(store, detail, price, base="0"):
        return {"store_id": store, "detail_id": detail, "price": price, "base_version": base}

    def audit(self):
        with self.factory() as db:
            md, detail = db.get(MdMaster, self.md["id"]), db.get(MdDetail, self.default["id"])
            return (md.dt_updated, md.i_updated_by, detail.dt_updated, detail.i_updated_by, detail.n_price)

    # ---- Merchant Portal ----
    def test_merchant_manages_every_own_store_and_only_changed_cells(self):
        grid = self.grid()
        self.assertEqual([(s["id"], s["read_only"]) for s in grid["stores"]],
                         [(21, False), (22, False), (23, True), (10, False)])  # by Store name; Inactive D view only
        self.assertTrue(grid["can_edit"])
        self.assertTrue(all(c["source"] == "merchant" for s in grid["stores"] for c in s["prices"].values()))
        before = self.audit()
        d1, d2 = self.default["id"], self.second["id"]
        saved = self.ok(self.put([self.price(10, d1, "0"), self.price(21, d2, "9.00")],
                                 [{"store_id": 22, "pos_visible": False, "base_version": "0"}]))
        self.assertEqual(saved["message"], "Saved.")
        self.assertEqual((self.cell(saved, 10, d1)["price"], self.cell(saved, 10, d1)["source"]), ("0.00", "store"))
        self.assertEqual(self.cell(saved, 21, d2)["source"], "store")  # equal to the Merchant price: still an override
        self.assertEqual((self.cell(saved, 22, d1)["source"], self.cell(saved, 21, d1)["source"]), ("merchant", "merchant"))
        self.assertEqual([s["pos_visible"] for s in saved["stores"]], [True, False, True, True])  # only Store C hidden
        self.assertEqual(self.audit(), before)  # the common MD / Detail audit is unchanged
        with self.factory() as db:
            row = db.query(StoreMdPrice).filter_by(i_store_id=10, i_md_detail_id=d1).one()
            merchant_user = db.query(PlatformUser).filter(PlatformUser.i_merchant_id == 10).one().i_platform_user_id
            self.assertEqual((row.i_created_by, row.i_updated_by), (merchant_user, merchant_user))  # the server login user
        # null reverts (row deleted), unlike $0.00
        reverted = self.ok(self.put([self.price(10, d1, None, self.cell(saved, 10, d1)["version"])]))
        self.assertEqual((self.cell(reverted, 10, d1)["price"], self.cell(reverted, 10, d1)["source"]), ("5.00", "merchant"))
        with self.factory() as db:
            self.assertEqual(db.query(StoreMdPrice).filter_by(i_store_id=10).count(), 0)

    def test_merchant_price_change_reaches_inheriting_stores_and_p2_save_still_works(self):
        d1 = self.default["id"]
        self.ok(self.put([self.price(21, d1, "4")]))
        # P2: the MD save carries the Default Detail fields in one transaction and syncs the Merchant price.
        self.ok(self.client.put(self.url(f"md/{self.md['id']}"), json=self.md_payload(
            self._cat, "Coffee", retail_price="6", default_detail={"barcode": "SO-1C", "size": "M"})))
        default = next(d for d in self.ok(self.client.get(self.url(f"md/{self.md['id']}/details")))["items"] if d["is_default"])
        self.assertEqual((default["price"], default["barcode"], default["size"]), ("6.00", "SO-1C", "M"))
        grid = self.grid()
        self.assertEqual((self.cell(grid, 21, d1)["price"], self.cell(grid, 22, d1)["price"], self.cell(grid, 10, d1)["price"]),
                         ("4.00", "6.00", "6.00"))

    def test_scope_inactive_store_and_validation(self):
        d1 = self.default["id"]
        cases = (("inactive Store (view only)", [self.price(23, d1, "1")], 403),
                 ("another Merchant's Store", [self.price(31, d1, "1")], 404),
                 ("a Detail of another MD", [self.price(21, self.second["id"] + 1000, "1")], 404),
                 ("negative price", [self.price(21, d1, "-1")], 422))
        for label, prices, status in cases:
            with self.subTest(label):
                response = self.put([self.price(22, d1, "2"), *prices])  # the valid cell is never saved alone
                self.assertEqual(response.status_code, status, response.text)
        for md_id in (self.platform_md["id"], self.other_md["id"]):  # Platform MD / another Merchant's MD
            self.assertEqual(self.client.get(self.settings_url(md_id=md_id)).status_code, 404)
            self.assertEqual(self.put([self.price(21, d1, "1")], md_id=md_id).status_code, 404)
        with self.factory() as db:
            self.assertEqual((db.query(StoreMdPrice).count(), db.query(StoreMd).count()), (0, 0))
        cross_origin = self.client.put(self.settings_url(), json={"prices": [self.price(21, d1, "1")]},
                                       headers={"origin": "http://evil.example"})
        self.assertEqual(cross_origin.status_code, 403, cross_origin.text)

    def test_conflict_is_409_and_saves_nothing(self):
        d1, d2 = self.default["id"], self.second["id"]
        first = self.grid()
        self.ok(self.put([self.price(21, d1, "4")]))  # another user saved this cell first
        response = self.put([self.price(22, d2, "7"), self.price(21, d1, "3", self.cell(first, 21, d1)["version"])])
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertEqual([(c["store_id"], c["detail_id"], c["price"]) for c in body["conflicts"]], [(21, d1, "4.00")])
        grid = self.grid()
        self.assertEqual((self.cell(grid, 21, d1)["price"], self.cell(grid, 22, d2)["source"]), ("4.00", "merchant"))
        # creating the same new cell twice from "0": the second is a conflict
        self.ok(self.put(visibility=[{"store_id": 21, "pos_visible": False, "base_version": "0"}]))
        again = self.put(visibility=[{"store_id": 21, "pos_visible": False, "base_version": "0"}])
        self.assertEqual(again.status_code, 409, again.text)

    # ---- Store Portal ----
    def test_store_login_sees_and_changes_only_its_own_store(self):
        d1 = self.default["id"]
        self.ok(self.put([self.price(21, d1, "4")]))  # Merchant set Store B
        self.login("STORE")
        grid = self.grid("store")
        self.assertEqual([s["id"] for s in grid["stores"]], [10])
        self.assertTrue(grid["can_edit"])
        saved = self.ok(self.put([self.price(10, d1, "3.50")], scope="store"))
        self.assertEqual(self.cell(saved, 10, d1)["price"], "3.50")
        for label, prices in (("another Store of its Merchant", [self.price(21, d1, "1", "x")]),
                              ("another Merchant's Store", [self.price(31, d1, "1")])):
            with self.subTest(label):
                self.assertEqual(self.put(prices, scope="store").status_code, 404)
        with self.factory() as db:
            self.assertEqual(db.query(StoreMdPrice).filter_by(i_store_id=21).one().n_price, 4)  # Store B unchanged
        # The common MD stays view only for a Store login (separate permission).
        common = self.client.put(self.url(f"md/{self.md['id']}", "store"), json=self.md_payload(self._cat, "Changed"),
                                 headers=ORIGIN)
        self.assertEqual(common.status_code, 403, common.text)
        self.assertEqual(self.row(self.md["id"]).c_item_name, "Coffee")

    def test_store_user_without_store_admin_role_is_refused(self):
        # Store login alone gives no Store Settings right: the common AppScope check needs a permitted Store admin role
        # (TENANT_ROLES). A user with another role cannot sign in to the Store Portal at all ...
        with self.factory() as db:
            role = Role(c_name="Employee", c_status="active")
            db.add(role)
            db.flush()
            user = db.query(PlatformUser).filter(PlatformUser.i_store_id == 10).one()
            admin_role, user.i_role_id = user.i_role_id, role.i_role_id
            db.commit()
        response = self.client.post("/auth/login", data={"store_code": "STR26ABCD", "email": "primary@example.test",
                                                         "password": "Awsum@5858"}, follow_redirects=False)
        self.assertEqual(response.status_code, 401, response.text)
        with self.factory() as db:
            db.query(PlatformUser).filter(PlatformUser.i_store_id == 10).one().i_role_id = admin_role
            db.commit()
        # ... and a signed-in Store whose scope is not permitted is refused by the route (403), nothing saved.
        self.login("STORE")
        real = master_group.resolve_app_scope

        def not_permitted(request, db, user):
            return replace(real(request, db, user), permitted=False)
        with patch.object(master_group, "resolve_app_scope", not_permitted):
            self.assertEqual(self.client.get(self.settings_url("store")).status_code, 403)
            response = self.put([self.price(10, self.default["id"], "1")], scope="store")
        self.assertEqual(response.status_code, 403, response.text)
        with self.factory() as db:
            self.assertEqual(db.query(StoreMdPrice).count(), 0)

    # ---- Platform / Store Merchant change ----
    def test_platform_cannot_use_store_settings_and_merchant_change_is_blocked(self):
        self.ok(self.put([self.price(10, self.default["id"], "4")]))
        response = self.client.post("/platform-admin/login", data={
            "login_mode": "platform_admin", "access_code": "ADMIN", "email": "admin@example.test",
            "password": self.password, "replace_active_session": "true"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        platform = self.client.get(f"/platform/api/master-group/md/{self.md['id']}/store-settings")
        self.assertEqual(platform.status_code, 403, platform.text)
        blocked = self.client.put("/platform/store/10", json={"merchant_id": 11})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("Store prices or POS settings", blocked.json()["detail"])
        renamed = self.client.put("/platform/store/10", json={"merchant_id": 10, "store_name": "Store Renamed"})
        self.assertEqual(renamed.status_code, 200, renamed.text)  # same Merchant: general edits are not blocked
        moved = self.client.put("/platform/store/23", json={"merchant_id": 11})  # no overrides: allowed as before
        self.assertEqual(moved.status_code, 200, moved.text)
        with self.factory() as db:
            self.assertEqual((db.get(Store, 10).i_merchant_id, db.get(Store, 10).c_store_name, db.get(Store, 23).i_merchant_id),
                             (10, "Store Renamed", 11))
            self.assertEqual(db.query(StoreMdPrice).filter_by(i_store_id=10).count(), 1)  # never removed automatically


if __name__ == "__main__":
    unittest.main()
