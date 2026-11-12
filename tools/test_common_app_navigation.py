"""Common shell and scoped Master endpoints against an isolated database."""
import unittest

from backend.database.master_scope_migration import migrate_master_scopes
from backend.models_admin.merchant import Merchant
from backend.models_admin.role import Role
from backend.routers import master_group, auth, platform_store
from backend.services import master_group as svc
from backend.services.app_navigation import resolve_app_scope
from backend.services.organization_users import organization_admin_query
from tools import test_registration_email


class CommonAppNavigationTests(unittest.TestCase):
    def setUp(self):
        test_registration_email.RegistrationEmailTests.setUp(self)
        self.app.include_router(master_group.router)
        self.app.dependency_overrides[master_group.get_db] = self.app.dependency_overrides[platform_store.get_db]
        self.activate = lambda owner: test_registration_email.RegistrationEmailTests.activate(self, owner)
        for owner in ("MERCHANT", "STORE"):
            self.activate(owner)
        with self.factory() as db:
            db.add(Merchant(i_merchant_id=11, c_merchant_code="MER26EFGH", c_merchant_name="Other Merchant", c_status="active"))
            db.commit()
            self.platform_category = svc.create_item(db, "category1", {"name": "Platform only"}, 1)["id"]
            self.other_category = svc.create_item(db, "category1", {"name": "Other only"}, 1, merchant_id=11)["id"]

    def login(self, scope):
        codes = {"MERCHANT": "MER26ABCD", "STORE": "STR26ABCD"}
        response = self.client.post("/auth/login", data={
            "store_code": codes[scope], "email": "primary@example.test", "password": "Awsum@5858",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)

    def test_tenant_dashboards_and_all_four_master_pages_share_shell(self):
        for scope in ("MERCHANT", "STORE"):
            self.login(scope)
            prefix = "/" + scope.lower()
            for path in (*(f"/merchant/dashboard/{kind}" for kind in ("standard", "restaurant", "deli", "tuxedo")), *(
                f"{prefix}/master-group/{slug}" for slug in ("tax", "category-1", "category-2", "category-3")
            )):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200, page.text[:300])
                self.assertIn('class="platform-shell"', page.text)
                self.assertIn('class="platform-navigation"', page.text)
                self.assertNotIn('<aside class="sidebar">', page.text)
                self.assertEqual(page.text.count('id="nav-master-group"'), 1)
                for slug in ("tax", "category-1", "category-2", "category-3"):
                    self.assertIn(f'href="{prefix}/master-group/{slug}"', page.text)
                self.assertNotIn('href="/platform/master-group/tax"', page.text)
                if "/master-group/" in path:
                    self.assertIn(f'data-api-base="{prefix}/api/master-group"', page.text)
                    for control in ("mgSearch", "mgActiveFilter", "mgPageSize", "mgForm"):
                        self.assertEqual(page.text.count(f'id="{control}"'), 1)

    def test_store_and_merchant_share_data_not_platform_or_other_tenant(self):
        self.login("MERCHANT")
        created = self.client.post("/merchant/api/master-group/category1", json={"name": "Shared Merchant"})
        self.assertEqual(created.status_code, 201, created.text)
        identity = created.json()["item"]["id"]
        self.login("STORE")
        rows = self.client.get("/store/api/master-group/category1").json()["items"]
        self.assertEqual([row["id"] for row in rows], [identity])
        options = self.client.get("/store/api/master-group/options").json()
        self.assertEqual([row["id"] for row in options["category1"]], [identity])
        for foreign_id in (self.platform_category, self.other_category):
            update = self.client.put(f"/store/api/master-group/category1/{foreign_id}", json={"name": "Forbidden"})
            self.assertEqual(update.status_code, 404, update.text)
            deleted = self.client.delete(f"/store/api/master-group/category1/{foreign_id}")
            self.assertEqual(deleted.status_code, 404, deleted.text)
            child = self.client.post("/store/api/master-group/category2", json={
                "name": "Forbidden child", "category1_id": foreign_id,
            })
            self.assertEqual(child.status_code, 422, child.text)
        child = self.client.post("/store/api/master-group/category2", json={
            "name": "Shared child", "category1_id": identity,
        })
        self.assertEqual(child.status_code, 201, child.text)
        child_id = child.json()["item"]["id"]
        leaf = self.client.post("/store/api/master-group/category3", json={
            "name": "Shared leaf", "category1_id": identity, "category2_id": child_id,
        })
        self.assertEqual(leaf.status_code, 201, leaf.text)
        tax = self.client.post("/store/api/master-group/tax", json={"name": "Local tax", "rate": "5"})
        self.assertEqual(tax.status_code, 201, tax.text)
        for entity in ("tax", "category1", "category2", "category3"):
            report = self.client.get(f"/store/api/master-group/{entity}/export?format=csv")
            self.assertEqual(report.status_code, 200, report.text)
            self.assertNotIn("Platform only", report.text)
            self.assertNotIn("Other only", report.text)
        # STEP 2: a Store login views its Merchant's MD Master (none here; Platform MD never shows).
        self.assertEqual(self.client.get("/store/api/master-group/md").json()["total"], 0)
        self.assertEqual(self.client.get("/store/master-group/md-master").status_code, 200)
        self.assertEqual(self.client.get("/merchant/api/master-group/category1").status_code, 403)
        self.assertEqual(self.client.get("/platform/api/master-group/category1").status_code, 403)
        removed = self.client.delete(f"/store/api/master-group/tax/{tax.json()['item']['id']}")
        self.assertEqual(removed.status_code, 200, removed.text)

    def test_permissions_deny_direct_api_and_hide_menu(self):
        self.login("STORE")
        with self.factory() as db:
            db.add(Role(i_role_id=500, c_name="Staff", c_status="active"))
            user = organization_admin_query(db, "STORE", 10).one()
            user.i_role_id = 500
            db.commit()
            from starlette.requests import Request
            req = Request({"type": "http", "method": "GET", "path": "/store/api/master-group/tax",
                           "headers": [(b"cookie", ("access_token=" + self.client.cookies.get("access_token")).encode())],
                           "query_string": b"", "server": ("testserver", 80)})
            scope = resolve_app_scope(req, db, user)
            self.assertEqual(scope.navigation()["master_items"], [])
            with self.assertRaises(auth.HTTPException):
                scope.require("read")
            from backend.services.web_sessions import create_session
            from tools.test_web_sessions import request
            token = create_session(db, user, request(client=self.client.cookies.get("awsum_web_client")),
                                   {"scope_type": "store", "access_code": "STR26ABCD"})
        self.client.cookies.clear()
        self.client.cookies.set("access_token", token)
        self.assertEqual(self.client.get("/store/api/master-group/tax").status_code, 403)

    def test_existing_platform_and_agency_shells_and_platform_scope_preserved(self):
        page = self.client.get("/platform/master-group/category-1")
        self.assertEqual(page.status_code, 200, page.text[:300])
        self.assertIn('href="/platform/master-group/tax"', page.text)
        rows = self.client.get("/platform/api/master-group/category1").json()["items"]
        self.assertEqual([row["id"] for row in rows], [self.platform_category])
        activation = self.client.put("/platform/agency/1", json={"status": "active"})
        self.assertEqual(activation.status_code, 200, activation.text)
        self.client.post("/auth/login", data={"store_code": "AGT00001", "email": "jane@example.test",
                                               "password": "Awsum@0100"}, follow_redirects=False)
        page = self.client.get("/agency/dashboard")
        self.assertEqual(page.status_code, 200, page.text[:300])
        self.assertIn('class="platform-shell"', page.text)
        self.assertIn('href="/agency/merchants"', page.text)
        self.assertNotIn('href="/store/master-group/tax"', page.text)

    def test_scope_migration_preserves_existing_rows(self):
        with self.factory() as db:
            before = [(row["id"], row["name"]) for row in svc.list_items(db, "category1")["items"]]
        migrate_master_scopes(self.engine)
        migrate_master_scopes(self.engine)
        with self.factory() as db:
            self.assertEqual(before, [(row["id"], row["name"]) for row in svc.list_items(db, "category1")["items"]])
