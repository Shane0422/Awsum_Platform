import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import bcrypt
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend.main import app
from backend.database.pg_platform import ADMIN_STORE_ID, PlatformBase, platform_engine
from backend.database.web_session_migration import migrate_web_sessions
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.role import Role
from backend.models_admin.session import SessionTbl
from backend.models_admin.store import Store
from backend.models_admin.merchant import Merchant
from backend.models_admin.agency import Agency
from backend.models_admin.system_settings import SystemSettings
from backend.services.session_context import session_organization
from backend.models_admin.device import Device
from backend.models_admin.license import License
from backend.models_admin.license_device_link import LicenseDeviceLink
from backend.routers import auth, dashboard, platform_store, store
from backend.services import web_sessions as svc
from backend.utils.jwt_handler import create_access_token, ALGORITHM, SECRET_KEY
from jose import jwt


PASSWORD = "TestOnly!Session42"


def request(token=None, path="/platform-admin/login", client="device-A"):
    headers = [(b"user-agent", b"Test Browser")]
    cookie = "awsum_web_client=" + client
    if token:
        cookie += "; access_token=" + token
    headers.append((b"cookie", cookie.encode()))
    return Request({
        "type": "http", "method": "POST", "scheme": "http", "path": path,
        "headers": headers, "client": ("192.0.2.10", 1234), "server": ("testserver", 80),
        "query_string": b"",
    })


def seed(factory):
    with factory() as db:
        db.add(Role(i_role_id=1, c_name="SuperAdmin", c_status="active"))
        db.add(Role(i_role_id=2, c_name="StoreAdmin", c_status="active"))
        db.add(Store(i_store_id=ADMIN_STORE_ID, c_store_name="HQ", c_store_code="HQ", c_status="active"))
        db.add(Store(i_store_id=2, c_store_name="Test Store", c_store_code="STR00002", c_status="active"))
        db.flush()
        db.add(Agency(i_agency_id=1, c_agency_code="AGT00001", c_agency_name="Test Agency"))
        db.add(Merchant(i_merchant_id=1, c_merchant_code="CLT11001", c_merchant_name="Customer Merchant"))
        db.add(SystemSettings(i_settings_id=1, c_company_name="Configured Platform"))
        db.flush()
        for store_id in (ADMIN_STORE_ID, 2):
            db.get(Store, store_id).i_merchant_id = 1
        hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
        for i in (1, 2):
            db.add(PlatformUser(
                i_platform_user_id=i, i_store_id=ADMIN_STORE_ID, i_role_id=1,
                c_email=f"user{i}@example.test", c_password=hashed,
                c_first_name="Test", c_last_name=f"User {i}", c_status="active", i_must_change_password=0,
            ))
        db.add(PlatformUser(
            i_platform_user_id=3, i_store_id=2, i_role_id=2, c_email="store@example.test",
            c_password=hashed, c_first_name="Store", c_last_name="User", c_status="active", i_must_change_password=0,
        ))
        db.commit()


def login_token(factory, user_id=1, client="device-A"):
    with factory() as db:
        user = db.get(PlatformUser, user_id)
        claims = {
            "uid": user_id, "user_id": user_id, "store": user.i_store_id, "role_id": user.i_role_id,
            "sub": user.c_email, "principal_type": "platform_user",
            "scope_type": "platform" if user.i_store_id == ADMIN_STORE_ID else "store",
            "access_code": "ADMIN" if user.i_store_id == ADMIN_STORE_ID else "STR00002",
        }
        return svc.create_session(db, user, request(client=client), claims)


class WebSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        PlatformBase.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        seed(self.factory)

    def active_count(self):
        with self.factory() as db:
            return db.query(SessionTbl).filter_by(c_status="ACTIVE").count()

    def test_explicit_contexts_do_not_inherit_legacy_admin_customer(self):
        token = login_token(self.factory)
        with self.factory() as db:
            row = db.query(SessionTbl).one()
            self.assertEqual(row.c_login_context, "PLATFORM")
            self.assertIsNone(row.i_store_id)
            self.assertIsNone(row.i_merchant_id)
            self.assertIsNone(row.i_agency_id)
            self.assertEqual(session_organization(db, row), ("Configured Platform", "Platform Admin"))
            self.assertEqual(db.get(PlatformUser, 1).i_store_id, ADMIN_STORE_ID)
            svc.authenticate(request(token), db)
            row.c_login_context = "STORE"
            db.commit()
            with self.assertRaises(HTTPException):
                svc.authenticate(request(token), db)
        for context, extra, expected in (
            ("store", {}, ("Customer Merchant", "Test Store")),
            ("merchant", {"merchant_id": 1}, ("Customer Merchant", "Merchant")),
            ("agency", {"agency_id": 1}, ("Test Agency", "Agency")),
        ):
            with self.subTest(context=context), self.factory() as db:
                if context == "agency":
                    db.add(Role(i_role_id=99, c_name="Agency Admin", c_status="active"))
                    db.get(Agency, 1).c_status = "active"
                    user = db.get(PlatformUser, 3)
                    user.i_role_id, user.i_agency_id = 99, 1
                    db.commit()
                token = svc.create_session(db, db.get(PlatformUser, 3), request(), {"scope_type": context, **extra})
                row = db.query(SessionTbl).filter_by(i_platform_user_id=3, c_status="ACTIVE").one()
                self.assertEqual(session_organization(db, row), expected)
                self.assertEqual(row.i_store_id, 2 if context == "store" else None)
                svc.authenticate(request(token), db)
                svc.logout_session(request(token), db)

    def test_missing_context_or_organization_is_rejected(self):
        for claims in ({}, {"scope_type": "agency"}, {"scope_type": "merchant", "merchant_id": 999}):
            with self.subTest(claims=claims), self.factory() as db:
                with self.assertRaises(HTTPException):
                    svc.create_session(db, db.get(PlatformUser, 1), request(), claims)
                self.assertEqual(db.query(SessionTbl).count(), 0)
        with self.factory() as db:
            row = SessionTbl(c_login_context="PLATFORM")
            settings = db.get(SystemSettings, 1)
            settings.c_company_name = ""
            settings.c_display_name = "Display Platform"
            db.flush()
            self.assertEqual(session_organization(db, row), ("Display Platform", "Platform Admin"))
            settings.c_display_name = ""
            db.flush()
            self.assertEqual(session_organization(db, row), ("AWSUM PLATFORM", "Platform Admin"))
            self.assertEqual(session_organization(db, SessionTbl(i_store_id=ADMIN_STORE_ID)),
                             ("Unknown organization", "Unknown context"))

    def test_second_login_denied_without_revoking_first(self):
        token = login_token(self.factory)
        with self.assertRaises(svc.ActiveSessionExists):
            login_token(self.factory, client="device-B")
        self.assertEqual(self.active_count(), 1)
        with self.factory() as db:
            self.assertEqual(svc.authenticate(request(token), db).i_platform_user_id, 1)
            row = db.query(SessionTbl).one()
            self.assertEqual(row.c_jwt_token, "")
            self.assertEqual(row.c_ip_address, "192.0.2.10")
            self.assertEqual(row.c_user_agent, "Test Browser")
            self.assertEqual(row.c_client_id, "device-A")

    def test_logout_immediately_allows_other_device_and_old_jwt_is_denied(self):
        token = login_token(self.factory)
        with self.factory() as db:
            svc.logout_session(request(token), db)
            row = db.query(SessionTbl).one()
            self.assertEqual(row.c_status, "LOGGED_OUT")
            self.assertIsNotNone(row.dt_logout)
            with self.assertRaises(HTTPException) as exc:
                svc.authenticate(request(token), db)
            self.assertEqual(exc.exception.status_code, 401)
        self.assertTrue(login_token(self.factory, client="device-B"))

    def test_termination_revokes_old_jwt_and_records_actor(self):
        token = login_token(self.factory)
        with self.factory() as db:
            row = db.query(SessionTbl).one()
            svc.terminate_session(db, row.i_session_id, 2)
            db.refresh(row)
            self.assertEqual(row.c_status, "TERMINATED")
            self.assertEqual(row.i_terminated_by, 2)
            self.assertIsNotNone(row.dt_terminated)
            with self.assertRaises(HTTPException):
                svc.authenticate(request(token), db)
        self.assertTrue(login_token(self.factory, client="device-B"))

    def test_idle_timeout_and_absolute_expiry_allow_next_login(self):
        for field in ("dt_last_active", "dt_expires"):
            with self.subTest(field=field):
                token = login_token(self.factory)
                with self.factory() as db:
                    row = db.query(SessionTbl).filter_by(c_status="ACTIVE").one()
                    setattr(row, field, svc.utcnow() - timedelta(days=1))
                    db.commit()
                    with self.assertRaises(HTTPException):
                        svc.authenticate(request(token), db)
                    db.refresh(row)
                    self.assertEqual(row.c_status, "EXPIRED")

    def test_expire_on_login_without_request_from_abandoned_merchant(self):
        login_token(self.factory)
        with self.factory() as db:
            db.query(SessionTbl).update({"dt_last_active": svc.utcnow() - timedelta(days=1)})
            db.commit()
        self.assertTrue(login_token(self.factory, client="device-B"))
        self.assertEqual(self.active_count(), 1)

    def test_two_different_users_and_store_scope_are_independent(self):
        tokens = [login_token(self.factory, i) for i in (1, 2, 3)]
        self.assertEqual(self.active_count(), 3)
        for i, token in enumerate(tokens, 1):
            with self.factory() as db:
                self.assertEqual(svc.authenticate(request(token), db).i_platform_user_id, i)

    def test_token_without_session_and_token_for_other_user_are_rejected(self):
        token = login_token(self.factory)
        payload = svc.decode_web_token(token)
        invalid = [
            create_access_token({k: v for k, v in payload.items() if k != "sid"}),
            create_access_token({**payload, "uid": 2}),
            create_access_token({**payload, "store": 2}),
            create_access_token({**payload, "sid": str(uuid4())}),
            jwt.encode({**payload, "exp": 1}, SECRET_KEY, algorithm=ALGORITHM),
        ]
        for value in invalid:
            with self.subTest(value=invalid.index(value)), self.factory() as db:
                with self.assertRaises(HTTPException):
                    svc.authenticate(request(value), db)

    def test_activity_timestamp_updates(self):
        token = login_token(self.factory)
        earlier = svc.utcnow() - timedelta(minutes=5)
        with self.factory() as db:
            db.query(SessionTbl).update({"dt_last_active": earlier})
            db.commit()
            svc.authenticate(request(token), db)
            self.assertGreater(db.query(SessionTbl).one().dt_last_active, earlier)

    def test_unique_active_constraint_protects_direct_database_writers(self):
        login_token(self.factory)
        with self.factory() as db:
            db.add(SessionTbl(i_platform_user_id=1, c_jwt_token="", c_status="ACTIVE"))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_multiple_licensed_devices_ignore_web_session_revocation(self):
        token = login_token(self.factory)
        with self.factory() as db:
            svc.terminate_session(db, db.query(SessionTbl).one().i_session_id, 2)
            for i, kind in ((1, "POS"), (2, "KIOSK")):
                lic = License(i_license_id=i, i_store_id=2, c_license_key=f"TEST-LICENSE-{i}", c_license_type=kind, c_status="active")
                dev = Device(
                    i_device_id=i, i_store_id=2, i_license_id=i, c_device_uuid=f"device-{i}",
                    c_device_type=kind, c_bound_hardware_id=f"fingerprint-{i}", c_status="activated",
                )
                db.add_all([lic, dev])
                db.flush()
                db.add(LicenseDeviceLink(i_license_id=i, i_device_id=i, i_store_id=2))
            db.commit()
        def dependency():
            with self.factory() as db:
                yield db
        with patch.dict(app.dependency_overrides, {platform_store.get_db: dependency}), \
             patch("backend.services.web_session_http._validate") as web_check:
            client = TestClient(app)
            self.addCleanup(client.close)
            client.cookies.set("access_token", token, domain="testserver.local", path="/")
            for i in (1, 2):
                result = client.post("/platform/device/validate", json={"device_code": f"device-{i}", "hardware_id": f"fingerprint-{i}"})
                self.assertEqual(result.status_code, 200, result.text)
            web_check.assert_not_called()

    def test_http_login_sessions_ui_logout_and_revocation(self):
        def dependency():
            with self.factory() as db:
                yield db
        overrides = {module.get_db: dependency for module in (auth, platform_store, dashboard, store)}
        with patch.dict(app.dependency_overrides, overrides), \
             patch("backend.main.PlatformSessionLocal", self.factory), \
             patch("backend.services.web_session_http.PlatformSessionLocal", self.factory), \
             patch("backend.services.reporting.PlatformSessionLocal", self.factory):
            # Do not enter TestClient lifespan: tests must never run production DB seed/startup.
            first = TestClient(app, follow_redirects=False)
            second = TestClient(app, follow_redirects=False)
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            data = {"access_code": "ADMIN", "login_mode": "platform_admin", "email": "user1@example.test", "password": PASSWORD}
            response = first.post("/platform-admin/login", data=data)
            self.assertEqual(response.status_code, 303, response.text)
            old_token = first.cookies.get("access_token")
            invalid_credentials = second.post("/platform-admin/login", data={**data, "password": "wrong"})
            self.assertEqual(invalid_credentials.status_code, 401)
            rejected = second.post("/platform-admin/login", data=data)
            self.assertEqual(rejected.status_code, 409, rejected.text)
            self.assertIn("already signed in", rejected.json()["message"])
            self.assertNotIn("access_token", second.cookies)
            listing = first.get("/platform/sessions").json()
            self.assertEqual(len(listing), 1)
            self.assertTrue(listing[0]["current"])
            self.assertEqual(listing[0]["login_context"], "PLATFORM")
            self.assertEqual(listing[0]["organization_name"], "Configured Platform")
            self.assertEqual(listing[0]["context_label"], "Platform Admin")
            self.assertIsNone(listing[0]["store_id"])
            self.assertEqual(len(first.get("/platform/sessions?q=Configured").json()), 1)
            self.assertEqual(first.get("/platform/sessions?q=Customer").json(), [])
            self.assertNotIn("c_jwt_token", listing[0])
            ui = first.get("/platform/master/session", headers={"Accept": "text/html"})
            self.assertEqual(ui.status_code, 200, ui.text[:200])
            self.assertIn("sessionRefresh", ui.text)
            self.assertNotIn("Add Session", ui.text)
            self.assertNotIn("btnListEdit", ui.text)
            with patch("backend.routers.dashboard._is_super_admin", return_value=False):
                forbidden = first.post(f'/platform/session/{listing[0]["id"]}/terminate', headers={"Origin": "http://testserver"})
                self.assertEqual(forbidden.status_code, 403)
            blocked = first.post(f'/platform/session/{listing[0]["id"]}/terminate', headers={"Origin": "https://other.example.test"})
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(first.put(f'/platform/session/{listing[0]["id"]}', json={"status": "ACTIVE"}).status_code, 405)
            result = first.post(f'/platform/session/{listing[0]["id"]}/terminate', headers={"Origin": "http://testserver"})
            self.assertEqual(result.status_code, 200, result.text)
            denied = first.get("/platform/sessions")
            self.assertEqual(denied.status_code, 401, denied.text)
            self.assertIn("/platform-admin", denied.headers["X-Awsum-Login"])
            self.assertEqual(second.post("/platform-admin/login", data=data).status_code, 303)
            logout = second.get("/auth/logout")
            self.assertEqual(logout.headers["location"], "/platform-admin")
            self.assertEqual(first.post("/platform-admin/login", data=data).status_code, 303)
            # A deliberately replayed JWT from the first session remains denied.
            with self.factory() as db, self.assertRaises(HTTPException):
                svc.authenticate(request(old_token), db)

    def test_http_expired_session_redirects_and_generic_helpers_reject_old_token(self):
        token = login_token(self.factory)
        with self.factory() as db:
            row = db.query(SessionTbl).one()
            svc.terminate_session(db, row.i_session_id, 2)
        for module in (auth, platform_store, dashboard, store):
            with self.subTest(module=module.__name__), self.factory() as db:
                if module is auth:
                    self.assertIsNone(module._get_authenticated_user(request(token), db))
                else:
                    with self.assertRaises(HTTPException) as exc:
                        module.get_current_user(request(token), db)
                    self.assertEqual(exc.exception.status_code, 401)
        with patch("backend.services.web_session_http.PlatformSessionLocal", self.factory):
            client = TestClient(app, follow_redirects=False)
            self.addCleanup(client.close)
            client.cookies.set("access_token", token, domain="testserver.local", path="/")
            html = client.get("/platform/dashboard", headers={"Accept": "text/html"})
            self.assertEqual(html.status_code, 303)
            self.assertEqual(html.headers["location"], "/platform-admin?reason=session_expired")
            self.assertNotIn("access_token", client.cookies)


@unittest.skipUnless(os.environ.get("AWSUM_TEST_POSTGRES_SESSIONS") == "1", "Opt-in isolated PostgreSQL integration test.")
class PostgresSessionTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_web_sessions_" + uuid4().hex
        with platform_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(
            platform_engine.url, connect_args={"options": f"-csearch_path={self.schema}"},
        )
        self.factory = sessionmaker(bind=self.engine)
        self.addCleanup(self.cleanup_schema)
        PlatformBase.metadata.create_all(self.engine)
        seed(self.factory)

    def cleanup_schema(self):
        self.engine.dispose()
        with platform_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))

    def test_simultaneous_logins_only_one_succeeds(self):
        barrier = Barrier(2)
        def attempt(client):
            barrier.wait(timeout=10)
            try:
                login_token(self.factory, client=client)
                return "accepted"
            except svc.ActiveSessionExists:
                return "denied"
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(attempt, ("A", "B")))
        self.assertCountEqual(results, ["accepted", "denied"])
        with self.factory() as db:
            self.assertEqual(db.query(SessionTbl).filter_by(c_status="ACTIVE").count(), 1)

    def test_migration_is_repeatable_and_preserves_history(self):
        token = login_token(self.factory)
        payload = svc.decode_web_token(token)
        payload.pop("login_context")
        legacy_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        with self.factory() as db:
            row = db.query(SessionTbl).one()
            original_id, original_login = row.i_session_id, row.dt_login
            row.c_login_context = None
            row.i_store_id = ADMIN_STORE_ID
            db.commit()
        migrate_web_sessions(self.engine)
        migrate_web_sessions(self.engine)
        with self.factory() as db:
            self.assertEqual(db.query(SessionTbl).count(), 1)
            row = db.query(SessionTbl).one()
            self.assertEqual(row.c_status, "ACTIVE")
            self.assertEqual(row.c_login_context, "PLATFORM")
            self.assertIsNone(row.i_store_id)
            self.assertEqual((row.i_session_id, row.dt_login), (original_id, original_login))
            self.assertEqual(svc.authenticate(request(legacy_token), db).i_platform_user_id, 1)

    def test_database_unique_index_rejects_bypassing_login_lock(self):
        login_token(self.factory)
        with self.factory() as db:
            db.add(SessionTbl(i_platform_user_id=1, c_jwt_token="", c_status="ACTIVE"))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_metadata_migration_preserves_existing_session_and_parses_historical_ua(self):
        login_token(self.factory)
        raw = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155.0.0.0 Safari/537.36"
        with self.factory() as db:
            row = db.query(SessionTbl).one()
            row.c_user_agent = raw
            row.c_ip_address = "127.0.0.1"
            original = (row.i_session_id, row.dt_login, row.c_session_key, row.c_status)
            db.commit()
        # Reproduce the real pre-metadata schema, inside this isolated test schema only.
        with self.engine.begin() as conn:
            for name in (
                "c_local_ip", "c_country_code", "c_region", "c_city", "c_browser_name",
                "c_browser_version", "c_os_name", "c_os_version", "c_device_name",
                "c_device_uuid", "c_app_version",
                "c_transport_peer_ip", "c_client_ip_source", "c_connection_type",
            ):
                conn.execute(text(f"ALTER TABLE tb_session DROP COLUMN {name}"))
        with patch("backend.services.session_metadata.locate_ip") as geo:
            migrate_web_sessions(self.engine)
            migrate_web_sessions(self.engine)
            geo.assert_not_called()
        with self.factory() as db:
            row = db.query(SessionTbl).one()
            self.assertEqual((row.i_session_id, row.dt_login, row.c_session_key, row.c_status), original)
            self.assertEqual(row.c_user_agent, raw)
            self.assertEqual(row.c_ip_address, "127.0.0.1")
            self.assertEqual((row.c_browser_name, row.c_os_name, row.c_os_version), ("Chrome", "Windows", "10"))
            for field in ("c_local_ip", "c_city", "c_country_code", "c_device_uuid", "c_device_name",
                          "c_transport_peer_ip", "c_client_ip_source", "c_connection_type"):
                self.assertIsNone(getattr(row, field))

    def test_branding_migration_preserves_logo_files_and_session_identity(self):
        import tempfile
        from pathlib import Path
        from backend.database.branding_migration import migrate_branding
        from backend.database.image_storage_migration import migrate_image_storage
        from backend.models_admin.merchant import Merchant
        token = login_token(self.factory, user_id=3)
        with self.factory() as db:
            row = db.query(SessionTbl).one()
            original = (row.i_session_id, row.c_status, row.c_session_key, row.dt_login)
            row.c_login_path = "/"
            db.commit()
        with self.engine.begin() as conn:
            for table in ("tb_merchant", "tb_store", "tb_agency"):
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN c_logo_url"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logo = root / "images" / "brands" / "clients" / "CLT11001" / "logo.png"
            logo.parent.mkdir(parents=True)
            logo.write_bytes(b"existing merchant logo")
            store_logo = logo.parent / "stores" / "STR00002" / "logo.png"
            store_logo.parent.mkdir(parents=True)
            store_logo.write_bytes(b"existing store logo")
            with patch("backend.services.images.STATIC_ROOT", root), patch("backend.services.images.image_storage_root", return_value=root / "media"):
                migrate_branding(self.engine)
                migrate_branding(self.engine)
                with self.factory() as db:
                    self.assertEqual(db.get(Merchant, 1).c_logo_url, "/static/images/brands/clients/CLT11001/logo.png")
                    self.assertEqual(db.get(Store, 2).c_logo_url, "/static/images/brands/clients/CLT11001/stores/STR00002/logo.png")
                migrated = migrate_image_storage(self.engine)
                self.assertEqual(len(migrated["references"]), 2)
                self.assertEqual(migrate_image_storage(self.engine)["status"], "UNCHANGED")
                with self.factory() as db:
                    self.assertEqual(db.get(Store, 2).c_logo_url, "/merchant/CLT11001/store/STR00002/images/logo/logo.png")
                    db.get(Store, 2).c_logo_url = None
                    db.commit()
                migrate_branding(self.engine)
                migrate_image_storage(self.engine)
            self.assertEqual(logo.read_bytes(), b"existing merchant logo")
            self.assertEqual(store_logo.read_bytes(), b"existing store logo")
        with self.factory() as db:
            self.assertEqual(db.get(Merchant, 1).c_logo_url, "/merchant/CLT11001/images/logo/logo.png")
            self.assertIsNone(db.get(Store, 2).c_logo_url)
            row = db.query(SessionTbl).one()
            self.assertEqual((row.i_session_id, row.c_status, row.c_session_key, row.dt_login), original)
            self.assertEqual(row.c_login_path, "/login/store/STR00002")
            self.assertEqual(svc.authenticate(request(token), db).i_platform_user_id, 3)


if __name__ == "__main__":
    unittest.main()
