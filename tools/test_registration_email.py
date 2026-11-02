"""Focused registration coverage; isolated DB, no actual SMTP or storage writes."""
import unittest
import re
import subprocess
from unittest.mock import patch

import bcrypt
from sqlalchemy import inspect, text

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.database.registration_user_migration import migrate_registration_users
from backend.models_admin.business_contact import BusinessContact
from backend.models_admin.merchant import Merchant
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.session import SessionTbl
from backend.models_admin.store import Store
from backend.routers import dashboard, platform_store
from backend.services.organization_users import organization_admin_query
from tools import test_agency_activation


class RegistrationEmailTests(unittest.TestCase):
    def setUp(self):
        test_agency_activation.AgencyActivationTests.setUp(self)
        migrate_registration_users(self.engine)
        self.app.include_router(dashboard.router)
        self.app.dependency_overrides[dashboard.get_db] = self.app.dependency_overrides[platform_store.get_db]
        with self.factory() as db:
            db.add(Merchant(i_merchant_id=10, c_merchant_code="MER26ABCD",
                            c_merchant_name="Merchant <One>", c_status="inactive", i_agency_id=1))
            db.flush()
            db.add(Store(i_store_id=10, i_merchant_id=10, c_store_code="STR26ABCD",
                         c_store_name="Store <One>", c_status="inactive"))
            for owner_type in ("MERCHANT", "STORE"):
                db.add(BusinessContact(
                    c_owner_type=owner_type, i_owner_id=10, c_contact_role="PRIMARY",
                    c_first_name="Pat", c_last_name=owner_type.title(),
                    c_email="primary@example.test", c_phone="703-855-5858",
                    b_is_primary=True, c_status="active",
                ))
            db.commit()

    def activate(self, owner_type):
        path = f"/platform/{owner_type.lower()}/10"
        status_key = "store_status" if owner_type == "STORE" else "status"
        response = self.client.put(path, json={status_key: "active"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_active_saves_create_scoped_admin_once_without_sending(self):
        with patch("backend.routers.platform_store.send_configured_email") as smtp:
            for owner_type in ("MERCHANT", "STORE"):
                with self.subTest(owner_type=owner_type):
                    result = self.activate(owner_type)
                    self.assertTrue(result[f"{owner_type.lower()}_user_created"])
                    self.assertEqual(result["temporary_password"], "Awsum@5858")
                    with self.factory() as db:
                        user = organization_admin_query(db, owner_type, 10).one()
                        self.assertEqual(user.c_email, "primary@example.test")
                        self.assertEqual(user.i_store_id, 10 if owner_type == "STORE" else ADMIN_STORE_ID)
                        self.assertEqual(user.i_must_change_password, 0)
                        self.assertTrue(bcrypt.checkpw(b"Awsum@5858", user.c_password.encode()))
                        original = user.c_password
                        contact = db.query(BusinessContact).filter_by(c_owner_type=owner_type).one()
                        contact.c_phone = "555-123-0000"
                        db.commit()
                    again = self.activate(owner_type)
                    self.assertFalse(again[f"{owner_type.lower()}_user_created"])
                    with self.factory() as db:
                        self.assertEqual(organization_admin_query(db, owner_type, 10).one().c_password, original)
            smtp.assert_not_called()

    def test_shared_preview_send_recipient_is_ephemeral_and_resend_supported(self):
        for owner_type in ("MERCHANT", "STORE"):
            with self.subTest(owner_type=owner_type):
                self.activate(owner_type)
                path = f"/platform/registration-email/{owner_type}/10"
                preview = self.client.post(path + "/preview", json={})
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertEqual(preview.json()["recipient"], "primary@example.test")
                self.assertIn(f"/login/{owner_type.lower()}/", preview.json()["html"])
                self.assertIn("Awsum@5858", preview.json()["html"])
                sent = []
                def send(db, message):
                    sent.append(message)
                    return "test-message-id", "accepted"
                with patch("backend.routers.platform_store.send_configured_email", side_effect=send):
                    for _ in range(2):
                        response = self.client.post(path, json={"recipient": "alternate@example.test"})
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertTrue(response.json()["accepted"])
                self.assertEqual(len(sent), 2)
                self.assertEqual(str(sent[0]["To"]), "alternate@example.test")
                with self.factory() as db:
                    self.assertEqual(organization_admin_query(db, owner_type, 10).one().c_email, "primary@example.test")
                    self.assertEqual(db.query(BusinessContact).filter_by(c_owner_type=owner_type).one().c_email,
                                     "primary@example.test")

    def test_preview_never_creates_or_resets_users(self):
        for owner_type in ("MERCHANT", "STORE"):
            response = self.client.post(f"/platform/registration-email/{owner_type}/10/preview", json={})
            self.assertEqual(response.status_code, 400)
            with self.factory() as db:
                self.assertEqual(organization_admin_query(db, owner_type, 10).count(), 0)

    def test_existing_login_sessions_dashboard_password_change_and_isolation(self):
        for owner_type in ("MERCHANT", "STORE"):
            self.activate(owner_type)
        self.client.cookies.clear()
        for owner_type, code in (("MERCHANT", "MER26ABCD"), ("STORE", "STR26ABCD")):
            with self.subTest(owner_type=owner_type):
                response = self.client.post("/auth/login", data={
                    "store_code": code, "email": "primary@example.test", "password": "Awsum@5858",
                }, follow_redirects=False)
                self.assertEqual(response.status_code, 303, response.text)
                self.assertEqual(response.headers["location"], "/merchant/dashboard/standard")
                with self.factory() as db:
                    session = db.query(SessionTbl).filter_by(c_status="ACTIVE", c_login_context=owner_type).one()
                    self.assertEqual(session.c_login_context, owner_type)
                    self.assertEqual(session.i_merchant_id, 10)
                page = self.client.get(response.headers["location"], follow_redirects=False)
                self.assertEqual(page.status_code, 200, page.text[:500])
                blocked = self.client.get("/platform/users")
                self.assertEqual(blocked.status_code, 403, blocked.text)
                changed = self.client.post("/auth/change-password", data={
                    "current_password": "Awsum@5858", "new_password": "Changed!5858",
                    "confirm_password": "Changed!5858",
                }, follow_redirects=False)
                self.assertEqual(changed.status_code, 303, changed.text)
                self.assertEqual(changed.headers["location"], "/merchant/dashboard/standard")
                self.client.get("/auth/logout", follow_redirects=False)
                self.client.cookies.clear()

    def test_invalid_owner_and_admin_store_are_rejected(self):
        for path, expected in (("CUSTOMER/10", 400), (f"STORE/{ADMIN_STORE_ID}", 403)):
            response = self.client.post(f"/platform/registration-email/{path}/preview", json={})
            self.assertEqual(response.status_code, expected, response.text)

    def test_platform_user_listing_excludes_new_scopes(self):
        for owner_type in ("MERCHANT", "STORE"):
            self.activate(owner_type)
        response = self.client.get("/platform/users")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("primary@example.test", response.text)

    def test_common_contact_actions_and_page_javascript(self):
        for module in ("agency", "merchant", "store"):
            response = self.client.get(f"/platform/master/{module}")
            self.assertEqual(response.status_code, 200, response.text[:500])
            self.assertIn("function openRegistrationEmail(ownerType, ownerId)", response.text)
            if module == "agency":
                self.assertIn('id="btnAgencyRegistrationEmail"', response.text)
            self.assertIn('id="btnContactRegistrationEmail"', response.text)
            for attrs, source in re.findall(r"<script\b([^>]*)>(.*?)</script>", response.text, re.S | re.I):
                if "src=" in attrs or "application/json" in attrs:
                    continue
                result = subprocess.run(["node", "--check"], input=source, text=True,
                                        encoding="utf-8", capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_migration_preserves_existing_users_and_is_repeatable(self):
        with self.engine.connect() as conn:
            before = conn.execute(text(
                "SELECT i_platform_user_id, c_email, c_password FROM tb_platform_user ORDER BY i_platform_user_id"
            )).all()
        migrate_registration_users(self.engine)
        migrate_registration_users(self.engine)
        with self.engine.connect() as conn:
            self.assertEqual(before, conn.execute(text(
                "SELECT i_platform_user_id, c_email, c_password FROM tb_platform_user ORDER BY i_platform_user_id"
            )).all())
            self.assertIn("i_merchant_id", {column["name"] for column in inspect(conn).get_columns("tb_platform_user")})
