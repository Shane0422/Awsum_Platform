"""Agency portal tests with real auth/session checks against an isolated database."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

import bcrypt
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.pg_platform import ADMIN_STORE_ID, PlatformBase
from backend.models_admin.merchant import Merchant
from backend.models_admin.agency import Agency
from backend.models_admin.device import Device
from backend.models_admin.platform_user import PlatformUser, AGENCY_ADMIN_ROLE_NAME
from backend.models_admin.role import Role
from backend.models_admin.session import SessionTbl
from backend.models_admin.store import Store
from backend.models_admin.system_settings import SystemSettings
from backend.routers import agency_portal, auth, dashboard, images as image_routes, platform_store, platform_store_license
from backend.services.agency_permissions import AgencyPortalPermissions, PortalPermission
from backend.services import images, web_sessions


class AgencyPortalTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        PlatformBase.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        self.factory = sessionmaker(bind=engine)
        self.password = "PortalTest!42"
        self.password_hash = bcrypt.hashpw(self.password.encode(), bcrypt.gensalt(rounds=4)).decode()
        with self.factory() as db:
            db.add_all([
                Role(i_role_id=1, c_name="SuperAdmin", c_status="active"),
                Role(i_role_id=99, c_name=AGENCY_ADMIN_ROLE_NAME, c_status="active"),
                Agency(i_agency_id=1, c_agency_code="AGT00001", c_agency_name="One", c_status="active"),
                Agency(i_agency_id=2, c_agency_code="AGT00002", c_agency_name="Two", c_status="active"),
                SystemSettings(i_settings_id=1, c_company_name="Test Platform"),
                Merchant(i_merchant_id=1, c_merchant_code="M00001", c_merchant_name="Merchant One", i_agency_id=1),
                Merchant(i_merchant_id=2, c_merchant_code="M00002", c_merchant_name="Merchant Two", i_agency_id=2),
            ])
            db.flush()
            db.add_all([
                Store(i_store_id=ADMIN_STORE_ID, c_store_name="HQ", c_store_code="HQ"),
                Store(i_store_id=1, c_store_name="Direct One", c_store_code="S00001",
                      i_merchant_id=2, i_installed_by_agency_id=1),
                Store(i_store_id=2, c_store_name="Excluded Store", c_store_code="S00002",
                      i_merchant_id=1, i_installed_by_agency_id=2),
                Store(i_store_id=3, c_store_name="Unassigned Store", c_store_code="S00003", i_merchant_id=1),
            ])
            db.flush()
            db.add_all([
                PlatformUser(i_platform_user_id=1, i_store_id=ADMIN_STORE_ID, i_role_id=99,
                             i_agency_id=1, c_email="same@example.test", c_first_name="One",
                             c_password=self.password_hash, c_status="active"),
                PlatformUser(i_platform_user_id=2, i_store_id=ADMIN_STORE_ID, i_role_id=99,
                             i_agency_id=2, c_email="same@example.test", c_first_name="Two",
                             c_password=self.password_hash, c_status="active"),
                PlatformUser(i_platform_user_id=3, i_store_id=ADMIN_STORE_ID, i_role_id=1,
                             c_email="platform@example.test", c_password=self.password_hash, c_status="active"),
                Device(i_device_id=1, i_store_id=1, c_device_uuid="device-one", c_device_name="Visible POS",
                       c_activation_code="SECRET-NOT-FOR-PORTAL", c_bound_hardware_id="PRIVATE",
                       c_status="active"),
                Device(i_device_id=2, i_store_id=2, c_device_uuid="device-two",
                       c_device_name="Hidden POS", c_status="active"),
            ])
            db.commit()
        app = FastAPI()
        app.include_router(auth.router, prefix="/auth")
        app.include_router(auth.platform_admin_router)
        app.include_router(agency_portal.router)
        app.include_router(image_routes.router)
        app.include_router(platform_store.router)
        app.include_router(platform_store_license.router)
        app.include_router(dashboard.router)
        from backend.main import protect_platform_admin_routes
        app.middleware("http")(protect_platform_admin_routes)

        def get_db():
            with self.factory() as db:
                yield db

        for dependency in (auth.get_db, agency_portal.get_db, platform_store.get_db, dashboard.get_db, image_routes.get_db):
            app.dependency_overrides[dependency] = get_db
        image_directory = tempfile.TemporaryDirectory(prefix="awsum-agency-user-test-")
        self.addCleanup(image_directory.cleanup)
        storage = patch.object(images, "image_storage_root", return_value=Path(image_directory.name))
        storage.start()
        self.addCleanup(storage.stop)
        self.app = app
        for target, value in (
            ("backend.services.web_sessions.collect_session_metadata", {}),
            ("backend.routers.platform_store.load_company_branding", {"name": "Test Platform"}),
            ("backend.routers.agency_portal.get_brand_context", {
                "APP_NAME": "Test Platform", "company_name": "Test Platform", "company_logo_path": "",
                "brand_icon_path": "",
            }),
            ("backend.routers.dashboard.get_brand_context", {
                "APP_NAME": "Test Platform", "company_name": "Test Platform", "company_logo_path": "",
                "brand_icon_path": "",
            }),
        ):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        branding = patch("backend.config.templates.PlatformSessionLocal", self.factory)
        branding.start()
        self.addCleanup(branding.stop)
        platform_db = patch("backend.main.PlatformSessionLocal", self.factory)
        platform_db.start()
        self.addCleanup(platform_db.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.login(self.client)

    def login(self, client, code="AGT00001", email="same@example.test", password=None):
        return client.post("/auth/login", data={"store_code": code, "email": email,
                                               "password": password or self.password}, follow_redirects=False)

    def test_login_targets_dashboard_and_same_email_other_agency_is_isolated(self):
        self.assertEqual(self.login(self.client).headers["location"], "/agency/dashboard")
        page = self.client.get("/agency/dashboard")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("Agency Overview", page.text)
        self.assertNotIn('href="/platform/', page.text.split('<nav')[1].split('</nav>')[0])
        with TestClient(self.app) as second:
            self.assertEqual(self.login(second, code="AGT00002").status_code, 303)
            self.assertIn("Merchant Two", second.get("/agency/merchants").text)
            self.assertNotIn("Merchant One", second.get("/agency/merchants").text)
        with self.factory() as db:
            self.assertEqual(db.query(SessionTbl).filter_by(c_status="ACTIVE").count(), 2)

    def test_common_browser_relogin_other_browser_block_and_timeout(self):
        def sign_in(client, agency, password=None):
            if agency:
                return self.login(client, password=password)
            return client.post("/platform-admin/login", data={
                "login_mode": "platform_admin", "access_code": "ADMIN",
                "email": "platform@example.test", "password": password or self.password,
            }, follow_redirects=False)

        for agency, user_id, page in ((True, 1, "/agency/dashboard"),
                                     (False, 3, "/platform/dashboard")):
            with self.subTest(agency=agency), TestClient(self.app) as browser, TestClient(self.app) as other:
                if agency:
                    self.client.get("/auth/logout")
                self.assertEqual(sign_in(browser, agency).status_code, 303)
                old_token = browser.cookies.get("access_token")
                client_id = browser.cookies.get("awsum_web_client")
                self.assertTrue(client_id)
                browser.cookies.delete("access_token")  # Restart without logout; persistent browser cookie survives.
                self.assertEqual(sign_in(browser, agency).status_code, 303)
                self.assertEqual(browser.cookies.get("awsum_web_client"), client_id)
                new_token = browser.cookies.get("access_token")
                self.assertNotEqual(old_token, new_token)
                with self.factory() as db:
                    self.assertEqual(db.query(SessionTbl).filter_by(
                        i_platform_user_id=user_id, c_status="TERMINATED").count(), 1)
                    active_key = db.query(SessionTbl).filter_by(
                        i_platform_user_id=user_id, c_status="ACTIVE").one().c_session_key
                other.cookies.set("access_token", old_token)
                self.assertIn(other.get(page, follow_redirects=False).status_code, (401, 303))
                other.cookies.clear()
                self.assertEqual(sign_in(other, agency).status_code, 409)
                self.assertEqual(sign_in(browser, agency, "WrongPassword!42").status_code, 401)
                self.assertEqual(browser.cookies.get("access_token"), new_token)
                with self.factory() as db:
                    self.assertEqual(db.query(SessionTbl).filter_by(
                        c_session_key=active_key).one().c_status, "ACTIVE")
                for timeout in ("idle", "absolute"):
                    with self.factory() as db:
                        active = db.query(SessionTbl).filter_by(
                            i_platform_user_id=user_id, c_status="ACTIVE").one()
                        expired_key = active.c_session_key
                        if timeout == "idle":
                            active.dt_last_active = web_sessions.utcnow() - web_sessions.idle_timeout(db)
                        else:
                            active.dt_expires = web_sessions.utcnow() - timedelta(seconds=1)
                        db.commit()
                    self.assertEqual(sign_in(other, agency).status_code, 303)
                    self.assertEqual(other.get(page).status_code, 200)
                    with self.factory() as db:
                        self.assertEqual(db.query(SessionTbl).filter_by(
                            c_session_key=expired_key).one().c_status, "EXPIRED")
                    browser.cookies.clear()
                    browser.cookies.update(other.cookies)
                    other.cookies.clear()
                browser.get("/auth/logout")

    def test_platform_admin_alias_dashboard_and_agency_permissions_are_separate(self):
        for role_id in (1, 7):
            with self.subTest(role_id=role_id):
                if role_id != 1:
                    with self.factory() as db:
                        db.get(Role, 1).c_name = "FormerAdmin"
                        db.flush()
                        db.add(Role(i_role_id=role_id, c_name="SuperAdmin", c_status="active"))
                        db.get(PlatformUser, 3).i_role_id = role_id
                        db.commit()
                with TestClient(self.app) as platform:
                    login = platform.post("/platform-admin/login", data={
                        "login_mode": "platform_admin", "access_code": "ADMIN",
                        "email": "platform@example.test", "password": self.password,
                    }, follow_redirects=False)
                    self.assertEqual(login.status_code, 303, login.text)
                    try:
                        with patch.object(AgencyPortalPermissions, "authorize_request",
                                          side_effect=AssertionError("Agency policy applied to Platform")):
                            page = platform.get("/platform/dashboard")
                            self.assertEqual(page.status_code, 200, page.text)
                            alias = platform.get("/platform/admin", follow_redirects=False)
                            self.assertEqual(alias.status_code, 303, alias.text)
                            self.assertEqual(alias.headers["location"], "/platform/dashboard")
                    finally:
                        platform.get("/auth/logout")
        self.assertEqual(self.client.get("/agency/dashboard").status_code, 200)
        self.assertEqual(self.client.get("/platform/admin").status_code, 403)
        self.assertEqual(self.client.get("/platform/dashboard").status_code, 403)

    def test_forbidden_diagnostics_identify_scope_without_logging_credentials(self):
        with self.assertLogs(auth.logger, level="WARNING") as captured:
            response = self.client.get("/platform/master/store", headers={"Accept": "text/html"})
        # Still refused (403); this browser's login is the Agency one, so the page explains it (no bare "Forbidden").
        self.assertEqual(response.status_code, 403)
        self.assertIn("Your Platform session has ended or changed. Please sign in again as Platform Admin.", response.text)
        message = captured.records[0].getMessage()
        details = json.loads(message.split("PLATFORM_AUTH_FORBIDDEN ", 1)[1])
        self.assertEqual(details, {
            "path": "/platform/master/store", "location": "get_platform_admin_session",
            "reason": "scope_type_mismatch", "session_exists": True,
            "login_context": "AGENCY", "principal_type": "platform_user", "scope_type": "agency",
            "role_id": 99, "token_role_id": 99, "user_status": "active",
            "role_status": "active", "access_code_matches": False,
        })
        for secret in (self.client.cookies.get("access_token"), self.password,
                       self.password_hash, "same@example.test"):
            self.assertNotIn(secret, message)

    def test_replaced_platform_session_is_refused_with_a_clear_message_and_keeps_the_other_login(self):
        # This browser holds the Agency login (setUp). A Platform API call is still refused with 403, now explained.
        with self.assertLogs(auth.logger, level="WARNING"):
            response = self.client.put("/platform/merchant/1", json={"business_options": {"accounting": True}},
                                       headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {
            "detail": "Your Platform session has ended or changed. Please sign in again as Platform Admin.",
            "code": "SESSION_CONTEXT_CHANGED", "login_url": "/platform-admin"})
        self.assertNotIn("access_token", response.headers.get("set-cookie", ""))  # the other login's cookie is kept
        self.assertEqual(self.client.get("/agency/dashboard").status_code, 200)    # and keeps working

    def test_platform_user_without_admin_role_still_gets_plain_forbidden(self):
        with TestClient(self.app) as platform:
            login = platform.post("/platform-admin/login", data={
                "login_mode": "platform_admin", "access_code": "ADMIN",
                "email": "platform@example.test", "password": self.password,
            }, follow_redirects=False)
            self.assertEqual(login.status_code, 303, login.text)
            with self.factory() as db:
                db.get(Role, 1).c_name = "Staff"  # a real permission problem, same Platform login
                db.commit()
            with self.assertLogs(auth.logger, level="WARNING"):
                api = platform.put("/platform/merchant/1", json={}, headers={"Accept": "application/json"})
                page = platform.get("/platform/master/merchant", headers={"Accept": "text/html"})
            self.assertEqual((api.status_code, api.json()), (403, {"detail": "Forbidden"}))
            self.assertEqual((page.status_code, page.text), (403, "Forbidden"))

    def test_session_changed_only_for_other_login_scopes(self):
        from types import SimpleNamespace
        from backend.services.web_session_http import platform_session_changed
        for scope, expected in (("agency", True), ("merchant", True), ("store", True), ("platform", False), (None, False)):
            request = SimpleNamespace(state=SimpleNamespace(platform_auth_diagnostics={"scope_type": scope}))
            self.assertIs(platform_session_changed(request), expected, scope)
        self.assertFalse(platform_session_changed(SimpleNamespace(state=SimpleNamespace())))

    def test_master_role_diagnostic_preserves_403_and_expired_redirect(self):
        with self.factory() as db:
            db.get(Role, 1).c_name = "FormerAdmin"
            db.add(Role(i_role_id=7, c_name="SuperAdmin", c_status="active"))
            db.get(PlatformUser, 3).i_role_id = 7
            db.commit()
        with TestClient(self.app) as platform:
            login = platform.post("/platform-admin/login", data={
                "login_mode": "platform_admin", "access_code": "ADMIN",
                "email": "platform@example.test", "password": self.password,
            }, follow_redirects=False)
            self.assertEqual(login.status_code, 303, login.text)
            with self.assertLogs(auth.logger, level="WARNING") as captured:
                forbidden = platform.get("/platform/master/store", headers={"Accept": "text/html"})
            self.assertEqual(forbidden.status_code, 403, forbidden.text)
            details = json.loads(captured.records[0].getMessage().split("PLATFORM_AUTH_FORBIDDEN ", 1)[1])
            self.assertEqual((details["location"], details["reason"], details["role_id"]),
                             ("master_management", "login_role_not_1", 7))
            self.assertEqual((details["session_exists"], details["login_context"],
                              details["access_code_matches"]), (True, "PLATFORM", True))
            with self.factory() as db:
                db.query(SessionTbl).filter_by(i_platform_user_id=3, c_status="ACTIVE").one().dt_expires = (
                    web_sessions.utcnow() - timedelta(seconds=1)
                )
                db.commit()
            with patch.object(auth.logger, "warning") as warning:
                expired = platform.get("/platform/master/store", headers={"Accept": "text/html"},
                                       follow_redirects=False)
                self.assertEqual(expired.status_code, 303, expired.text)
                self.assertIn("reason=session_expired", expired.headers["location"])
                warning.assert_not_called()

    def test_scope_applies_to_lists_filters_counts_and_devices(self):
        merchants = self.client.get("/agency/merchants")
        self.assertIn("Merchant One", merchants.text)
        self.assertNotIn("Merchant Two", merchants.text)
        stores = self.client.get("/agency/stores")
        self.assertIn("Direct One", stores.text)
        self.assertNotIn("Excluded Store", stores.text)
        self.assertNotIn("Unassigned Store", stores.text)
        self.assertNotIn("Excluded Store", self.client.get("/agency/stores?q=Excluded").text)
        device_page = self.client.get("/agency/stores/1/devices")
        self.assertEqual(device_page.status_code, 200, device_page.text)
        self.assertIn("Visible POS", device_page.text)
        self.assertNotIn("Hidden POS", device_page.text)
        self.assertNotIn("SECRET-NOT-FOR-PORTAL", device_page.text)
        self.assertNotIn("PRIVATE", device_page.text)
        self.assertNotIn('data-bs-target="#edit-', device_page.text)
        self.assertEqual(self.client.get("/agency/stores/2/devices").status_code, 404)
        with self.factory() as db:
            user = db.get(PlatformUser, 1)
            self.assertEqual(agency_portal.summary(db, user), {"Merchants": 1, "Stores": 1, "Devices": 1, "Users": 1})
        self.assertEqual(self.client.get("/agency/stores?size=101").status_code, 422)

    def test_merchant_store_mutations_are_denied_and_records_unchanged(self):
        payload = {"name": "Edited", "email": "info@example.test", "phone": "703-555-1111"}
        for resource in ("merchants", "stores"):
            for record_id in (1, 2):
                self.assertEqual(self.client.put(f"/agency/{resource}/{record_id}", json=payload).status_code, 403)
            for method in ("post", "patch", "delete"):
                path = f"/agency/{resource}" if method == "post" else f"/agency/{resource}/1"
                self.assertIn(self.client.request(method, path, json=payload).status_code, (403, 405))
            page = self.client.get(f"/agency/{resource}")
            self.assertIn("Read Only.", page.text)
            self.assertNotIn('data-awsum-form', page.text)
        with self.factory() as db:
            self.assertEqual(db.get(Store, 1).i_installed_by_agency_id, 1)
            self.assertEqual(db.get(Store, 1).c_store_name, "Direct One")
            self.assertEqual(db.get(Merchant, 1).i_agency_id, 1)
            self.assertEqual(db.get(Merchant, 1).c_merchant_name, "Merchant One")
            self.assertEqual(db.get(Store, 2).c_store_name, "Excluded Store")

    def test_common_policy_controls_backend_and_page_actions(self):
        with patch.dict(AgencyPortalPermissions.POLICY, users=PortalPermission()):
            page = self.client.get("/agency/users")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Read Only.", page.text)
            self.assertNotIn('data-awsum-form', page.text)
            for method, path in (("POST", "/agency/users"), ("PUT", "/agency/users/1"),
                                 ("POST", "/agency/users/with-image"),
                                 ("PUT", "/agency/users/1/with-image"),
                                 ("POST", "/agency/users/1/reset-password"),
                                 ("DELETE", "/agency/users/1")):
                self.assertEqual(self.client.request(method, path, json={}).status_code, 403)
            self.assertIn('"reset": false', page.text)
        # Reset is an update action, not account creation.
        with patch.dict(AgencyPortalPermissions.POLICY, users=PortalPermission(create=True)):
            self.assertEqual(self.client.post("/agency/users/1/reset-password", json={}).status_code, 403)

    def test_user_delete_is_scoped_soft_delete_and_revokes_sessions(self):
        self.assertEqual(self.client.delete("/agency/users/1").status_code, 409)
        for target in (2, 3, 999):
            self.assertEqual(self.client.delete(f"/agency/users/{target}").status_code, 404)
        created = self.client.post("/agency/users", json={
            "name": "Delete Admin", "email": "delete@example.test", "phone": "",
            "password": "Hello!42", "confirm_password": "Hello!42",
        })
        self.assertEqual(created.status_code, 200, created.text)
        target = created.json()["id"]
        with TestClient(self.app) as other:
            self.assertEqual(self.login(other, email="delete@example.test", password="Hello!42").status_code, 303)
            with self.factory() as db:
                old_hash = db.get(PlatformUser, target).c_password
            deleted = self.client.delete(f"/agency/users/{target}")
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(other.get("/agency/dashboard").status_code, 401)
            self.assertEqual(self.login(other, email="delete@example.test", password="Hello!42").status_code, 401)
            with self.factory() as db:
                record = db.get(PlatformUser, target)
                self.assertEqual((record.c_status, record.c_password, record.i_agency_id), ("inactive", old_hash, 1))
                self.assertEqual(db.query(SessionTbl).filter_by(i_platform_user_id=target).one().c_status, "TERMINATED")
                self.assertEqual(db.get(PlatformUser, 2).c_status, "active")

    def test_common_user_list_json_is_scoped_and_excludes_credentials(self):
        response = self.client.get("/agency/users?agency_id=2&scope=PLATFORM",
                                   headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["pages"], 1)
        self.assertEqual([row["id"] for row in body["users"]], [1])
        self.assertEqual(body["users"][0]["role"], "Agency Admin")
        for forbidden in ("c_password", "password", "session", "token"):
            self.assertNotIn(forbidden, body["users"][0])
        self.assertEqual(self.client.get("/agency/users?q=NoMatch", headers={
            "Accept": "application/json",
        }).json()["users"], [])
        self.assertEqual(self.client.get("/agency/users?size=101", headers={
            "Accept": "application/json",
        }).status_code, 422)

    def test_users_create_scoped_identity_and_existing_auth(self):
        payload = {"name": "New Admin", "email": "platform@example.test", "phone": "555-1111",
                   "password": " NewPassword!42 ", "confirm_password": " NewPassword!42 "}
        response = self.client.post("/agency/users", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        with self.factory() as db:
            record = db.get(PlatformUser, response.json()["id"])
            self.assertEqual((record.i_agency_id, record.i_role_id, record.i_must_change_password), (1, 99, 0))
            self.assertTrue(bcrypt.checkpw(payload["password"].encode(), record.c_password.encode()))
        self.assertEqual(self.client.post("/agency/users", json=payload).status_code, 409)
        self.assertEqual(self.client.post("/agency/users", json={**payload, "i_role_id": 1}).status_code, 422)
        with TestClient(self.app) as other:
            login = self.login(other, email=payload["email"], password=payload["password"])
            self.assertEqual(login.headers.get("location"), "/agency/dashboard", login.text)
            self.assertEqual(other.get("/agency/dashboard").status_code, 200)

    def test_common_user_photo_and_must_change_flag_preserve_scope_phone_and_password(self):
        payload = {"name": "Photo Admin", "email": "photo@example.test", "phone": "555-1111",
                   "password": "Hello!42", "confirm_password": "Hello!42", "must_change_password": True}
        buffer = io.BytesIO()
        Image.new("RGB", (25, 30), "blue").save(buffer, "PNG")
        photo = buffer.getvalue()
        created = self.client.post("/agency/users/with-image", data={"payload": json.dumps(payload)},
                                   files={"image": ("personal.png", photo, "image/png")})
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()
        target = item["id"]
        self.assertEqual(item["image_path"], f"/agency/AGT00001/images/users/{target}.webp")
        downloaded = self.client.get(item["image_url"])
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.headers["cache-control"], "private, no-store")
        with Image.open(io.BytesIO(downloaded.content)) as saved:
            self.assertEqual((saved.format, saved.size), ("WEBP", (512, 512)))
        with TestClient(self.app) as anonymous:
            self.assertEqual(anonymous.get(item["image_url"]).status_code, 401)
        listed = self.client.get("/agency/users", headers={"Accept": "application/json"}).json()["users"]
        self.assertEqual(next(row for row in listed if row["id"] == target)["image_url"], item["image_url"])
        with self.factory() as db:
            record = db.get(PlatformUser, target)
            old_hash = record.c_password
            self.assertEqual((record.i_agency_id, record.i_role_id, record.i_must_change_password), (1, 99, 1))
            self.assertTrue(bcrypt.checkpw(payload["password"].encode(), old_hash.encode()))
        update = {"name": payload["name"], "email": payload["email"], "phone": "555-2222",
                  "must_change_password": False, "status": "active"}
        response = self.client.put(f"/agency/users/{target}/with-image",
                                   data={"payload": json.dumps(update)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["image_path"], item["image_path"])
        # A status-only common action must not accidentally clear this setting.
        self.assertEqual(self.client.put(f"/agency/users/{target}", json={
            **update, "must_change_password": True,
        }).status_code, 200)
        self.assertEqual(self.client.put(f"/agency/users/{target}", json={
            key: value for key, value in update.items() if key != "must_change_password"
        }).status_code, 200)
        with self.factory() as db:
            record = db.get(PlatformUser, target)
            self.assertEqual((record.c_phone, record.c_password, record.i_must_change_password),
                             ("555-2222", old_hash, 1))
        invalid = self.client.put(f"/agency/users/{target}/with-image",
                                 data={"payload": json.dumps({**update, "phone": "not saved"})},
                                 files={"image": ("broken.png", b"invalid image", "image/png")})
        self.assertEqual(invalid.status_code, 400, invalid.text)
        with self.factory() as db:
            self.assertEqual(db.get(PlatformUser, target).c_phone, "555-2222")
        with TestClient(self.app) as other:
            self.assertEqual(self.login(other, code="AGT00002").status_code, 303)
            self.assertEqual(other.get(item["image_url"]).status_code, 404)
            self.assertEqual(other.put(f"/agency/users/{target}/with-image",
                                      data={"payload": json.dumps(update)}).status_code, 404)
        for foreign in (2, 3):
            self.assertEqual(self.client.put(f"/agency/users/{foreign}/with-image",
                                            data={"payload": json.dumps(update)}).status_code, 404)
        for forbidden in ({"role_id": 1}, {"agency_id": 2}, {"image_path": "/arbitrary.png"}):
            rejected = self.client.put(f"/agency/users/{target}/with-image",
                                      data={"payload": json.dumps({**update, **forbidden})})
            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertEqual(rejected.json()["detail"][0]["loc"][0], "body")
        self.assertEqual(self.client.post("/agency/users/with-image?record_id=1",
                                         data={"payload": json.dumps(payload)}).status_code, 422)
        cross_origin = self.client.put(
            f"/agency/users/{target}/with-image", data={"payload": json.dumps(update)},
            headers={"Origin": "https://foreign.example.test"},
        )
        self.assertEqual(cross_origin.status_code, 403, cross_origin.text)
        removed = self.client.put(f"/agency/users/{target}/with-image",
                                  data={"payload": json.dumps(update), "remove_image": "true"})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["image_path"])
        self.assertEqual(self.client.get(item["image_url"]).status_code, 404)

    def test_common_reset_password_validation_and_agency_scope(self):
        created = self.client.post("/agency/users", json={
            "name": "Reset Admin", "email": "reset@example.test",
            "password": "Hello!42", "confirm_password": "Hello!42",
        })
        self.assertEqual(created.status_code, 200, created.text)
        target = created.json()["id"]
        with self.factory() as db:
            original_hash = db.get(PlatformUser, target).c_password
        reset_payload = {"password": "Replacement!42", "confirm_password": "Replacement!42"}
        for foreign in (2, 3, 999):
            self.assertEqual(self.client.post(f"/agency/users/{foreign}/reset-password",
                                             json=reset_payload).status_code, 404)
        mismatch = self.client.post(f"/agency/users/{target}/reset-password",
                                    json={**reset_payload, "confirm_password": "mismatch"})
        self.assertEqual(mismatch.status_code, 400, mismatch.text)
        with self.factory() as db:
            self.assertEqual(db.get(PlatformUser, target).c_password, original_hash)
        with patch.object(platform_store, "_validate_platform_user_password",
                          wraps=platform_store._validate_platform_user_password) as validator:
            reset = self.client.post(f"/agency/users/{target}/reset-password", json=reset_payload)
            self.assertEqual(reset.status_code, 200, reset.text)
            self.assertEqual(validator.call_count, 1)
        self.assertTrue(reset.json()["must_change_password"])
        with self.factory() as db:
            record = db.get(PlatformUser, target)
            self.assertTrue(bcrypt.checkpw(reset_payload["password"].encode(), record.c_password.encode()))
            self.assertEqual((record.i_must_change_password, record.i_agency_id, record.i_role_id), (1, 1, 99))
            self.assertEqual(db.get(PlatformUser, 2).c_password, self.password_hash)
        with TestClient(self.app) as other:
            response = self.login(other, email="reset@example.test", password=reset_payload["password"])
            self.assertEqual(response.status_code, 303, response.text)
            self.assertIn("change-password", response.headers["location"])

    def test_user_status_changes_are_scoped_and_self_deactivation_is_rejected(self):
        payload = {"name": "Updated", "email": "same@example.test", "phone": "", "status": "inactive"}
        self.assertEqual(self.client.put("/agency/users/1", json=payload).status_code, 409)
        self.assertEqual(self.client.put("/agency/users/2", json=payload).status_code, 404)
        create = self.client.post("/agency/users", json={**payload, "email": "new@example.test",
                                  "status": "active", "password": "Hello!42", "confirm_password": "Hello!42"})
        target = create.json()["id"]
        with TestClient(self.app) as other:
            self.assertEqual(self.login(other, email="new@example.test", password="Hello!42").status_code, 303)
            self.assertEqual(self.client.put(f"/agency/users/{target}", json={**payload, "email": "new@example.test"}).status_code, 200)
            self.assertEqual(other.get("/agency/dashboard").status_code, 401)
            self.assertEqual(self.client.put(f"/agency/users/{target}", json={
                **payload, "email": "new@example.test", "status": "active",
            }).status_code, 200)
            self.assertEqual(other.get("/agency/dashboard").status_code, 401)
            with self.factory() as db:
                self.assertEqual(db.query(SessionTbl).filter_by(i_platform_user_id=target).one().c_status, "TERMINATED")
        with self.factory() as db:
            self.assertEqual(db.get(PlatformUser, 2).c_password, self.password_hash)

    def test_reports_export_is_read_only_and_contains_only_scoped_counts(self):
        response = self.client.get("/agency/reports/export")
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows, [["Metric", "Count"], ["Merchants", "1"], ["Stores", "1"], ["Devices", "1"]])
        self.assertEqual(self.client.put("/agency/stores/1/devices", json={}).status_code, 405)
        self.assertEqual(self.client.put("/platform/merchant/2/stores/1/devices/1", json={}).status_code, 403)
        self.assertEqual(self.client.post("/platform/store/1/licenses/issue", json={}).status_code, 403)
        self.assertEqual(self.client.get("/platform/users").status_code, 403)

    def test_settings_keeps_login_email_password_and_legacy_completion_redirect(self):
        response = self.client.put("/agency/settings", json={"name": "Display Name",
                                  "email": "same@example.test", "phone": "123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.put("/agency/settings", json={"name": "Name",
                         "email": "changed@example.test", "phone": ""}).status_code, 400)
        completed = self.client.get("/auth/registration-completed", follow_redirects=False)
        self.assertEqual(completed.headers["location"], "/agency/dashboard")
        with self.factory() as db:
            self.assertEqual(db.get(PlatformUser, 1).c_password, self.password_hash)

    def test_anonymous_platform_and_wrong_password_cannot_access_portal(self):
        with TestClient(self.app) as anonymous:
            self.assertEqual(anonymous.get("/agency/dashboard").status_code, 401)
            self.assertEqual(self.login(anonymous, password="wrong").status_code, 401)
            self.assertEqual(anonymous.get("/agency/stores").status_code, 401)
        with TestClient(self.app) as platform:
            login = platform.post("/platform-admin/login", data={"login_mode": "platform_admin",
                                  "access_code": "ADMIN", "email": "platform@example.test",
                                  "password": self.password}, follow_redirects=False)
            self.assertEqual(login.headers["location"], "/platform/dashboard")
            self.assertEqual(platform.get("/agency/dashboard").status_code, 403)


if __name__ == "__main__":
    unittest.main()
