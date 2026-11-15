"""Common Store Management Tree API (S2-a): GET /platform/api/store-tree and GET /merchant/api/store-tree.

Isolated SQLite app (tools.test_common_app_navigation fixture): Agency 1 (AGT00001), Merchant 10 (MER26ABCD, Agency 1)
with Store 10, Merchant 11 (MER26EFGH, no Agency). Added here: Agency 2, Merchant 12 (Agency 2, no Store), Store 10
installed by Agency 2 (the installing Agency must not move it), an Inactive Store 12 of Merchant 10, 120 Stores of
Merchant 11 (more than the 100-row list page) and a Store without a Merchant.
"""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.models_admin.agency import Agency
from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.routers import merchant_store, platform_store
from tools import test_agency_activation, test_common_app_navigation

SECRETS = ("TREE-SECRET-NOTE", "TREE-SECRET-MEMO", "TREE-SECRET-REMARK", "TREE-LICENSE-KEY")
MANY = 120
STORE_KEYS = {"store_id", "store_code", "store_name", "status"}
MERCHANT_KEYS = {"merchant_id", "merchant_code", "merchant_name", "merchant_status", "stores"}
AGENCY_KEYS = {"agency_id", "agency_code", "agency_name", "merchants"}


class StoreTreeApiTests(unittest.TestCase):
    def setUp(self):
        test_common_app_navigation.CommonAppNavigationTests.setUp(self)
        self.app.include_router(merchant_store.router)
        with self.factory() as db:
            db.add(Agency(i_agency_id=2, i_agency_type_id=1, c_agency_code="AGT00002", c_agency_name="Beta Agency",
                          c_company_name="Beta Agency", c_status="active"))
            db.add(Merchant(i_merchant_id=12, c_merchant_code="MER26EMPT", c_merchant_name="Empty Merchant",
                            c_status="active", i_agency_id=2))
            db.flush()
            store = db.get(Store, 10)
            store.i_installed_by_agency_id = 2  # installed by Agency 2; the Merchant's Agency is 1
            store.c_notes, store.c_memo, store.c_remark = SECRETS[:3]
            store.c_store_license_key = SECRETS[3]
            store.n_license_count, store.i_max_user_count = 7, 9
            db.add(Store(i_store_id=12, i_merchant_id=10, c_store_code="STR26INAC", c_store_name="Inactive One",
                         c_status="inactive"))
            db.add_all(Store(i_store_id=2000 + n, i_merchant_id=11, c_store_code=f"STR26M{n:03d}",
                             c_store_name=f"Bulk {n:03d}", c_status="active" if n % 3 else "inactive")
                       for n in range(MANY))
            db.add(Store(i_store_id=3000, i_merchant_id=None, c_store_code="STR26ORPH", c_store_name="Orphan Store",
                         c_status="active"))
            db.commit()

    def login(self, scope):
        test_common_app_navigation.CommonAppNavigationTests.login(self, scope)

    @staticmethod
    def store_ids(merchants):
        return sorted(store["store_id"] for merchant in merchants for store in merchant["stores"])

    def assert_no_secrets(self, response):
        for secret in SECRETS:
            self.assertNotIn(secret, response.text)
        for key in ("notes", "memo", "remark", "store_license_key", "license_count", "max_user_count",
                    "contract_end_date", "installed_by_agency_id"):
            self.assertNotIn(f'"{key}"', response.text)

    # ---- Platform ----
    def test_platform_tree_groups_by_the_merchant_agency_without_paging(self):
        response = self.client.get("/platform/api/store-tree")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("no-store", response.headers.get("cache-control", ""))
        body = response.json()
        agencies = body["agencies"]
        self.assertEqual([a["agency_name"] for a in agencies], ["Agency <One>", "Beta Agency", "No Agency"])
        by_name = {a["agency_name"]: a for a in agencies}
        for agency in agencies:
            self.assertEqual(set(agency), AGENCY_KEYS)
            for merchant in agency["merchants"]:
                self.assertEqual(set(merchant), MERCHANT_KEYS)
                for store in merchant["stores"]:
                    self.assertEqual(set(store), STORE_KEYS)
        # Merchant 10 sits under its own Agency 1, although Store 10 was installed by Agency 2.
        agency_one = by_name["Agency <One>"]
        self.assertEqual((agency_one["agency_id"], agency_one["agency_code"]), (1, "AGT00001"))
        self.assertEqual([m["merchant_id"] for m in agency_one["merchants"]], [10])
        self.assertEqual([(s["store_id"], s["status"]) for s in agency_one["merchants"][0]["stores"]],
                         [(12, "inactive"), (10, "active")])  # name order, Inactive included
        # A Merchant without Stores is listed (Beta Agency has only the installation of Store 10, not the Store).
        self.assertEqual(by_name["Beta Agency"]["merchants"],
                         [{"merchant_id": 12, "merchant_code": "MER26EMPT", "merchant_name": "Empty Merchant",
                           "merchant_status": "active", "stores": []}])
        # No Agency: Merchant 11 with all 120 Stores (more than the 100-row list page), then the Store without Merchant.
        no_agency = by_name["No Agency"]
        self.assertEqual((no_agency["agency_id"], no_agency["agency_code"]), (None, None))
        self.assertEqual([m["merchant_name"] for m in no_agency["merchants"]], ["Other Merchant", "No Merchant"])
        bulk = no_agency["merchants"][0]["stores"]
        self.assertEqual(len(bulk), MANY)
        self.assertEqual([s["store_name"] for s in bulk], sorted(s["store_name"] for s in bulk))
        self.assertEqual(no_agency["merchants"][1]["merchant_id"], None)
        self.assertEqual([s["store_id"] for s in no_agency["merchants"][1]["stores"]], [3000])
        # Every Store except the Platform Admin store, exactly once.
        all_merchants = [m for a in agencies for m in a["merchants"]]
        ids = self.store_ids(all_merchants)
        self.assertEqual(ids, sorted([10, 12, 3000, *range(2000, 2000 + MANY)]))
        self.assertNotIn(ADMIN_STORE_ID, ids)
        self.assertEqual(body["counts"], {"agencies": 2, "merchants": 3, "stores": MANY + 3})
        self.assert_no_secrets(response)

    def test_tree_query_count_does_not_grow_with_stores(self):
        statements = []
        listener = lambda *args: statements.append(args[2])
        event.listen(self.engine, "before_cursor_execute", listener)
        try:
            with self.factory() as db:
                nodes = platform_store._query_store_tree(db)
                scoped = platform_store._query_store_tree(db, merchant_id=11)
        finally:
            event.remove(self.engine, "before_cursor_execute", listener)
        self.assertEqual(len(statements), 4, statements)  # 2 per tree (Merchants + Agency, Stores), no N+1
        self.assertEqual(sum(len(node["stores"]) for node in nodes), MANY + 3)
        self.assertEqual([(n["merchant_id"], len(n["stores"])) for n in scoped], [(11, MANY)])
        self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in statements))  # read only

    def test_tree_is_read_only(self):
        with self.factory() as db:
            before = [(s.i_store_id, s.i_merchant_id, s.c_status, s.dt_updated) for s in db.query(Store).all()]
        self.assertEqual(self.client.get("/platform/api/store-tree").status_code, 200)
        for method in ("post", "put", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)("/platform/api/store-tree").status_code, 405, method)
        self.login("MERCHANT")
        self.assertEqual(self.client.get("/merchant/api/store-tree").status_code, 200)
        self.assertEqual(self.client.post("/merchant/api/store-tree").status_code, 405)
        with self.factory() as db:
            after = [(s.i_store_id, s.i_merchant_id, s.c_status, s.dt_updated) for s in db.query(Store).all()]
        self.assertEqual(after, before)

    # ---- Merchant scope ----
    def test_merchant_tree_is_fixed_to_the_login_merchant(self):
        self.login("MERCHANT")
        for query in ("", "?merchant_id=11", "?merchant_id=11&agency_id=2", "?merchant=12&scope=PLATFORM"):
            response = self.client.get(f"/merchant/api/store-tree{query}")
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(set(body), {"merchants", "counts"})  # no Agency level
            self.assertEqual([(m["merchant_id"], m["merchant_code"]) for m in body["merchants"]],
                             [(10, "MER26ABCD")], query)
            self.assertEqual(set(body["merchants"][0]), MERCHANT_KEYS)
            self.assertEqual([s["store_id"] for s in body["merchants"][0]["stores"]], [12, 10])  # Inactive included
            self.assertEqual(body["counts"], {"merchants": 1, "stores": 2})
            for foreign in ("MER26EFGH", "Other Merchant", "Bulk 0", "STR26M", "Orphan", "Empty Merchant",
                            "Beta Agency", "Agency <One>", "AGT0000"):
                self.assertNotIn(foreign, response.text, query)
            self.assert_no_secrets(response)
        # The Merchant login cannot read the Platform tree.
        self.assertEqual(self.client.get("/platform/api/store-tree").status_code, 403)

    # ---- access control ----
    def test_anonymous_store_and_agency_logins_are_refused(self):
        # Agency login: activate Agency 1 with this (Platform Admin) session first.
        activated = self.client.put("/platform/agency/1", json={"status": "active"})
        self.assertEqual(activated.status_code, 200, activated.text)
        password = activated.json()["temporary_password"]
        with TestClient(self.app) as anonymous:
            for path in ("/platform/api/store-tree", "/merchant/api/store-tree"):
                response = anonymous.get(path)
                self.assertIn(response.status_code, (401, 403), path)
                self.assertNotIn("Store <One>", response.text)
        self.login("STORE")
        for path in ("/platform/api/store-tree", "/merchant/api/store-tree"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 403, path)
            self.assertNotIn("Store <One>", response.text)
        with TestClient(self.app) as agency:
            login = test_agency_activation.AgencyActivationTests.login_agency(self, agency, password)
            self.assertEqual(login.status_code, 303, login.text)
            for path in ("/platform/api/store-tree", "/merchant/api/store-tree"):
                response = agency.get(path, follow_redirects=False)
                self.assertIn(response.status_code, (401, 403), path)
                self.assertNotIn("Store <One>", response.text)

    # ---- existing Store APIs unchanged ----
    def test_existing_store_list_apis_are_unchanged(self):
        first = self.client.get("/platform/stores?page=1&page_size=100")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual((len(first.json()["items"]), first.json()["total"], first.json()["total_pages"]),
                         (100, MANY + 3, 2))  # the list keeps its 100-row page
        self.assertEqual(self.client.get("/platform/stores?page=1&page_size=101").status_code, 422)
        # The list Agency filter still means the installing Agency (the Tree uses the Merchant's Agency).
        installed = self.client.get("/platform/stores?page=1&agency_id=2").json()["items"]
        self.assertEqual([row["store_id"] for row in installed], [10])
        self.assertEqual(self.client.get("/platform/store/10").json()["notes"], SECRETS[0])
        self.login("MERCHANT")
        rows = self.client.get("/merchant/api/stores?page=1").json()
        self.assertEqual(sorted(row["store_id"] for row in rows["items"]), [10, 12])
        self.assertEqual(self.client.get("/merchant/api/store/10").status_code, 200)


if __name__ == "__main__":
    unittest.main()
