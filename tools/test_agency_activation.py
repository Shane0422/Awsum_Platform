"""Focused Agency flow tests using an isolated in-memory database and mocked SMTP."""
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from datetime import datetime
import bcrypt
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.agency_user_migration import migrate_agency_users
from backend.database.pg_platform import ADMIN_STORE_ID, PlatformBase, platform_engine
from backend.models_admin.agency import Agency
from backend.models_admin.agency_type import AgencyType
from backend.models_admin.business_contact import BusinessContact
from backend.models_admin.platform_user import AGENCY_ADMIN_ROLE_NAME, PlatformUser
from backend.models_admin.role import Role
from backend.models_admin.session import SessionTbl
from backend.models_admin.store import Store
from backend.models_admin.system_settings import SystemSettings
from backend.routers import auth, platform_store, agency_portal
from backend.services.business_contact import BusinessContactService
from backend.services.mail_common import EmailConfigurationError, MailDeliveryError


class AgencyActivationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        PlatformBase.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.password = "TestOnly!Admin42"
        with self.factory() as db:
            db.add_all([
                Role(i_role_id=1, c_name="SuperAdmin", c_status="active"),
                Role(i_role_id=99, c_name=AGENCY_ADMIN_ROLE_NAME, c_status="active"),
                Store(i_store_id=ADMIN_STORE_ID, c_store_code="HQ", c_store_name="HQ"),
                AgencyType(i_agency_type_id=1, c_agency_type_code="DEALER", c_agency_type_name="Dealer"),
                Agency(i_agency_id=1, i_agency_type_id=1, c_agency_code="AGT00001",
                       c_agency_name="Agency <One>", c_company_name="Agency <One>", c_status="inactive"),
                SystemSettings(i_settings_id=1, c_company_name="Test Platform"),
            ])
            db.flush()
            db.add(PlatformUser(
                i_platform_user_id=1, i_store_id=ADMIN_STORE_ID, i_role_id=1,
                c_email="admin@example.test", c_password=bcrypt.hashpw(
                    self.password.encode(), bcrypt.gensalt(rounds=4)).decode(),
                c_status="active", i_must_change_password=0,
            ))
            db.add(BusinessContact(
                c_owner_type="AGENCY", i_owner_id=1, c_contact_role="PRIMARY",
                c_first_name="Jane", c_last_name="Contact", c_email="Jane@example.test",
                c_phone="555-0100", b_is_primary=True, c_status="active",
            ))
            db.commit()
        app = FastAPI()
        app.include_router(auth.router, prefix="/auth")
        app.include_router(auth.platform_admin_router)
        app.include_router(platform_store.router)
        app.include_router(agency_portal.router)

        def get_db():
            with self.factory() as db:
                yield db

        app.dependency_overrides[auth.get_db] = get_db
        app.dependency_overrides[platform_store.get_db] = get_db
        app.dependency_overrides[agency_portal.get_db] = get_db
        self.app = app
        self.metadata = patch("backend.services.web_sessions.collect_session_metadata", return_value={})
        self.metadata.start()
        self.addCleanup(self.metadata.stop)
        self.branding_db = patch("backend.config.templates.PlatformSessionLocal", self.factory)
        self.branding_db.start()
        self.addCleanup(self.branding_db.stop)
        self.branding = patch("backend.routers.platform_store.load_company_branding",
                              return_value={"name": "Test Platform"})
        self.branding.start()
        self.addCleanup(self.branding.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        response = self.client.post("/platform-admin/login", data={
            "login_mode": "platform_admin", "access_code": "ADMIN",
            "email": "admin@example.test", "password": self.password,
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)

    def activate(self):
        response = self.client.put("/platform/agency/1", json={"status": "active"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_activation_creates_contact_admin_once_without_sending_email(self):
        with patch("backend.routers.platform_store.send_configured_email") as smtp:
            result = self.activate()
            smtp.assert_not_called()
        self.assertTrue(result["agency_user_created"])
        with self.factory() as db:
            user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
            self.assertEqual((user.c_first_name, user.c_last_name, user.c_phone),
                             ("Jane", "Contact", "555-0100"))
            self.assertEqual(user.c_email, "jane@example.test")
            self.assertEqual(user.i_role_id, 99)
            self.assertEqual(user.c_status, "active")
            self.assertEqual(user.i_must_change_password, 0)
            self.assertEqual(result["temporary_password"], "Awsum@0100")
            self.assertNotEqual(user.c_password, result["temporary_password"])
            self.assertTrue(bcrypt.checkpw(result["temporary_password"].encode(), user.c_password.encode()))
            password_hash = user.c_password
            contact = db.query(BusinessContact).one()
            contact.c_email = "new-contact@example.test"
            contact.c_phone = "703-855-5858"
            db.commit()
        again = self.activate()
        self.assertFalse(again["agency_user_created"])
        self.assertNotIn("temporary_password", again)
        self.assertEqual(again["login_email"], "jane@example.test")
        with self.factory() as db:
            self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).count(), 1)
            self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).one().c_password, password_hash)

    def test_phone_digits_generate_initial_password_without_random_fallback(self):
        for phone, expected in (
            ("703-855-5858", "Awsum@5858"),
            ("(703) 855-0007", "Awsum@0007"),
            ("12ab34", "Awsum@1234"),
        ):
            with self.subTest(phone=phone):
                with self.factory() as db:
                    db.query(PlatformUser).filter_by(i_agency_id=1).delete()
                    db.query(BusinessContact).one().c_phone = phone
                    db.commit()
                with patch("backend.routers.platform_store.secrets.token_urlsafe") as random:
                    result = self.activate()
                    random.assert_not_called()
                self.assertEqual(result["temporary_password"], expected)
                with self.factory() as db:
                    user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
                    self.assertTrue(bcrypt.checkpw(expected.encode(), user.c_password.encode()))
                    self.assertEqual(user.i_must_change_password, 0)

    def test_short_or_missing_phone_uses_existing_random_password(self):
        for phone in (None, "", "12-3", "no digits"):
            with self.subTest(phone=phone):
                with self.factory() as db:
                    db.query(PlatformUser).filter_by(i_agency_id=1).delete()
                    db.query(BusinessContact).one().c_phone = phone
                    db.commit()
                with patch("backend.routers.platform_store.secrets.token_urlsafe", return_value="RandomTest!42") as random:
                    result = self.activate()
                    random.assert_called_once_with(18)
                self.assertEqual(result["temporary_password"], "RandomTest!42")
                preview = self.client.post("/platform/agency/1/registration-email/preview", json={
                    "temporary_password": result["temporary_password"],
                })
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertIn("Initial Password: RandomTest!42", preview.json()["message"])
                with self.factory() as db:
                    user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
                    self.assertNotEqual(user.c_password, result["temporary_password"])
                    self.assertTrue(bcrypt.checkpw(result["temporary_password"].encode(), user.c_password.encode()))
                    self.assertEqual(user.i_must_change_password, 0)

    def test_saved_agency_lists_admin_presence_for_email_button(self):
        self.assertFalse(self.client.get("/platform/agencies").json()[0]["has_agency_admin"])
        self.activate()
        self.assertTrue(self.client.get("/platform/agencies").json()[0]["has_agency_admin"])

    def test_existing_user_and_password_survive_save_without_primary_contact(self):
        self.activate()
        with self.factory() as db:
            user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
            password_hash = user.c_password
            user.i_must_change_password = 1
            db.query(BusinessContact).delete()
            db.commit()
        result = self.activate()
        self.assertFalse(result["agency_user_created"])
        with self.factory() as db:
            user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
            self.assertEqual(user.c_password, password_hash)
            self.assertEqual(user.i_must_change_password, 1)

    def test_inactive_save_does_not_create_user(self):
        response = self.client.put("/platform/agency/1", json={"status": "inactive"})
        self.assertEqual(response.status_code, 200)
        with self.factory() as db:
            self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).count(), 0)

    def test_new_agency_defaults_inactive_and_uses_existing_contact_service(self):
        with patch("backend.routers.platform_store.AwsumStorageService") as storage:
            storage.return_value.initialize_after_registration.return_value = {}
            response = self.client.post("/platform/agency", json={"company_name": "New Agency", "agency_type_id": 1})
        self.assertEqual(response.status_code, 200, response.text)
        agency_id = response.json()["id"]
        with self.factory() as db:
            self.assertEqual(db.get(Agency, agency_id).c_status, "inactive")
            BusinessContactService(db).create_contact("AGENCY", agency_id, {
                "first_name": "New", "last_name": "Primary", "email": "new@example.test",
                "phone": "555-0101", "is_primary": True,
            })
        response = self.client.put(f"/platform/agency/{agency_id}", json={"status": "active"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["agency_user_created"])

    def test_missing_primary_rolls_back_active_save(self):
        with self.factory() as db:
            db.query(BusinessContact).delete()
            db.commit()
        response = self.client.put("/platform/agency/1", json={"status": "active"})
        self.assertEqual(response.status_code, 400)
        with self.factory() as db:
            self.assertEqual(db.get(Agency, 1).c_status, "inactive")
            self.assertEqual(db.query(PlatformUser).count(), 1)

    def test_invalid_primary_name_or_email_blocks_creation(self):
        for values in ({"c_email": ""}, {"c_first_name": "", "c_last_name": ""}):
            with self.subTest(values=values):
                with self.factory() as db:
                    contact = db.query(BusinessContact).one()
                    contact.c_email, contact.c_first_name, contact.c_last_name = "Jane@example.test", "Jane", "Contact"
                    for name, value in values.items():
                        setattr(contact, name, value)
                    db.commit()
                response = self.client.put("/platform/agency/1", json={"status": "active"})
                self.assertEqual(response.status_code, 400, response.text)
                with self.factory() as db:
                    self.assertEqual(db.query(PlatformUser).count(), 1)
                    self.assertEqual(db.get(Agency, 1).c_status, "inactive")

    def test_platform_email_is_allowed_without_changing_platform_user(self):
        with self.factory() as db:
            db.query(BusinessContact).one().c_email = "ADMIN@example.test"
            original_hash = db.get(PlatformUser, 1).c_password
            db.commit()
        response = self.client.put("/platform/agency/1", json={"status": "active"})
        self.assertEqual(response.status_code, 200, response.text)
        with self.factory() as db:
            self.assertIsNone(db.get(PlatformUser, 1).i_agency_id)
            self.assertEqual(db.get(PlatformUser, 1).c_password, original_hash)
            self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).one().c_email, "admin@example.test")

    def test_same_email_across_agencies_login_is_resolved_by_agency_code(self):
        first = self.activate()
        with self.factory() as db:
            db.add(Agency(i_agency_id=2, c_agency_code="AGT00002", c_agency_name="Second", c_status="inactive"))
            db.flush()
            BusinessContactService(db).create_contact("AGENCY", 2, {
                "first_name": "Second", "email": "JANE@example.test", "phone": "703-855-5858", "is_primary": True,
            })
        response = self.client.put("/platform/agency/2", json={"status": "active"})
        self.assertEqual(response.status_code, 200, response.text)
        second = response.json()
        self.assertEqual(first["login_email"], second["login_email"])
        with self.factory() as db:
            first_user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
            first_id, first_hash = first_user.i_platform_user_id, first_user.c_password
            second_id = db.query(PlatformUser).filter_by(i_agency_id=2).one().i_platform_user_id
        browser_ids = {}
        for agency_id, code, password, expected_id in (
            (1, "AGT00001", first["temporary_password"], first_id),
            (2, "AGT00002", second["temporary_password"], second_id),
        ):
            with TestClient(self.app) as client:
                login = client.post("/auth/login", data={
                    "store_code": code, "email": "JANE@example.test", "password": password,
                }, follow_redirects=False)
                self.assertEqual(login.status_code, 303, login.text)
                browser_ids[agency_id] = client.cookies.get("awsum_web_client")
                with self.factory() as db:
                    row = db.query(SessionTbl).filter_by(c_status="ACTIVE", i_agency_id=agency_id).one()
                    self.assertEqual(row.i_platform_user_id, expected_id)
        with TestClient(self.app) as client:
            client.cookies.set("awsum_web_client", browser_ids[1])
            response = self.login_agency(client, first["temporary_password"])
            self.assertEqual(response.status_code, 303, response.text)
            with self.factory() as db:
                self.assertEqual(db.query(SessionTbl).filter_by(c_status="ACTIVE", i_agency_id=1).count(), 1)
                self.assertEqual(db.query(SessionTbl).filter_by(c_status="TERMINATED", i_agency_id=1).count(), 1)
                other = db.query(SessionTbl).filter_by(c_status="ACTIVE", i_agency_id=2).one()
                self.assertEqual(other.i_platform_user_id, second_id)
        again = self.activate()
        self.assertFalse(again["agency_user_created"])
        with self.factory() as db:
            self.assertEqual(db.get(PlatformUser, first_id).c_password, first_hash)

    def test_scope_uniqueness_guards_case_insensitive_duplicates(self):
        self.activate()
        with self.factory() as db:
            with self.assertRaises(HTTPException) as error:
                platform_store._require_unique_platform_user_email(db, "jane@example.test", agency_id=1)
            self.assertEqual(error.exception.status_code, 409)
            platform_store._require_unique_platform_user_email(db, "jane@example.test", agency_id=2)
            platform_store._require_unique_platform_user_email(db, "jane@example.test")
            db.add(PlatformUser(
                i_store_id=ADMIN_STORE_ID, i_role_id=99, i_agency_id=1,
                c_email=" JANE@example.test ", c_password="unchanged-test-hash",
            ))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).count(), 1)
            db.add(PlatformUser(i_store_id=ADMIN_STORE_ID, i_role_id=1,
                                c_email="ADMIN@example.test", c_password="unchanged-test-hash"))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

    def test_preview_send_recipient_is_separate_and_templates_match(self):
        result = self.activate()
        payload = {"recipient": "alternate@example.test"}
        with patch("backend.routers.platform_store.send_configured_email", return_value=("<mock@test>", "accepted")) as smtp:
            preview = self.client.post("/platform/agency/1/registration-email/preview", json=payload)
            smtp.assert_not_called()
            self.assertEqual(preview.status_code, 200, preview.text)
            body = preview.json()
            for required in ("Registration completed", "Agency Name", "Agency Code", "Login Email",
                             "Initial Password", "Login URL", "You may continue using the initial password"):
                self.assertIn(required, body["message"])
            self.assertIn("Agency &lt;One&gt;", body["html"])
            self.assertIn("http://testserver/login/agency/AGT00001", body["message"])
            self.assertIn(result["temporary_password"], body["message"])
            sent = self.client.post("/platform/agency/1/registration-email", json=payload)
            self.assertEqual(sent.status_code, 200, sent.text)
            self.assertEqual(sent.json()["recipient"], "alternate@example.test")
            message = smtp.call_args.args[1]
            self.assertEqual(str(message["To"]), "alternate@example.test")
            self.assertEqual(message.get_body(preferencelist=("html",)).get_content(), body["html"])
            resent = self.client.post("/platform/agency/1/registration-email", json={})
            self.assertEqual(resent.status_code, 200, resent.text)
            self.assertEqual(smtp.call_count, 2)
            self.assertEqual(resent.json()["recipient"], "Jane@example.test")
        self.assertIn("no-store", preview.headers["Cache-Control"])
        with self.factory() as db:
            self.assertEqual(db.query(BusinessContact).one().c_email, "Jane@example.test")
            self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).one().c_email, "jane@example.test")

    def test_registration_uses_only_current_primary_without_changing_login(self):
        self.allow_multiple_contacts_in_sqlite()
        self.activate()
        with self.factory() as db:
            original_user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
            original_identity = (original_user.c_email, original_user.c_password)
            service = BusinessContactService(db)
            other = service.create_contact("AGENCY", 1, {
                "first_name": "Other", "email": "other@example.test", "phone": "1234567890",
            })
        preview = self.client.post("/platform/agency/1/registration-email/preview", json={})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["recipient"], "Jane@example.test")
        with self.factory() as db:
            BusinessContactService(db).set_primary(other["contact_id"])
        with patch("backend.routers.platform_store.send_configured_email", return_value=("<mock@test>", "accepted")) as smtp:
            preview = self.client.post("/platform/agency/1/registration-email/preview", json={})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["recipient"], "other@example.test")
            self.assertIn("jane@example.test", preview.json()["message"])
            sent = self.client.post("/platform/agency/1/registration-email", json={})
            self.assertEqual(sent.status_code, 200, sent.text)
            self.assertEqual(str(smtp.call_args.args[1]["To"]), "other@example.test")
        with self.factory() as db:
            user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
            self.assertEqual((user.c_email, user.c_password), original_identity)

    def test_registration_requires_primary_and_email_even_with_recipient_override(self):
        self.allow_multiple_contacts_in_sqlite()
        self.activate()
        with self.factory() as db:
            BusinessContactService(db).create_contact("AGENCY", 1, {
                "first_name": "Other", "email": "other@example.test", "phone": "1234567890",
            })
        for state, expected in (
            ("no_primary", "Please designate a Primary Contact first."),
            ("no_email", "Please enter the Primary Contact Email first."),
            ("invalid_email", "Please correct it first."),
        ):
            with self.subTest(state=state):
                with self.factory() as db:
                    contact = db.query(BusinessContact).filter_by(c_first_name="Jane").one()
                    contact.b_is_primary = state != "no_primary"
                    contact.c_email = "" if state == "no_email" else "invalid" if state == "invalid_email" else "Jane@example.test"
                    db.commit()
                with patch("backend.routers.platform_store.send_configured_email") as smtp:
                    for suffix in ("/preview", ""):
                        for payload in ({}, {"recipient": "override@example.test"}):
                            response = self.client.post("/platform/agency/1/registration-email" + suffix, json=payload)
                            self.assertEqual(response.status_code, 400, response.text)
                            self.assertIn(expected, response.json()["detail"])
                    smtp.assert_not_called()

    def allow_multiple_contacts_in_sqlite(self):
        # Model index has a PostgreSQL-only predicate; mirror it in this isolated SQLite fixture.
        with self.engine.begin() as connection:
            connection.execute(text("DROP INDEX ux_business_contact_primary"))
            connection.execute(text(
                "CREATE UNIQUE INDEX ux_business_contact_primary ON tb_business_contact "
                "(c_owner_type, i_owner_id) WHERE b_is_primary = 1"
            ))

    def test_registration_requires_activation_and_real_admin_before_rendering(self):
        with patch("backend.routers.platform_store.send_configured_email") as smtp:
            for suffix in ("/preview", ""):
                response = self.client.post("/platform/agency/1/registration-email" + suffix, json={})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "An active Agency is required.")
            with self.factory() as db:
                db.get(Agency, 1).c_status = "active"
                db.commit()
            for suffix in ("/preview", ""):
                response = self.client.post("/platform/agency/1/registration-email" + suffix, json={})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "An active Agency User is required.")
                self.assertNotIn("Not registered", response.text)
            smtp.assert_not_called()
        with self.factory() as db:
            self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).count(), 0)
        activation = self.activate()
        preview = self.client.post("/platform/agency/1/registration-email/preview", json={})
        self.assertEqual(preview.status_code, 200, preview.text)
        for expected in ("Agency Name", "Agency Code", "Login Email", "Login URL",
                         "Initial Password", "Sign In", activation["temporary_password"], activation["login_email"]):
            self.assertIn(expected, preview.json()["html"])
        with self.factory() as db:
            db.query(PlatformUser).filter_by(i_agency_id=1).one().c_status = "inactive"
            db.commit()
        with patch("backend.routers.platform_store.send_configured_email") as smtp:
            for suffix in ("/preview", ""):
                response = self.client.post("/platform/agency/1/registration-email" + suffix, json={})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "An active Agency User is required.")
            smtp.assert_not_called()

    def test_resend_after_password_change_omits_password_without_modifying_merchant(self):
        self.activate()
        changed_hash = bcrypt.hashpw(b"ChangedPassword!42", bcrypt.gensalt(rounds=4)).decode()
        with self.factory() as db:
            user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
            user.c_password = changed_hash
            user.dt_password_changed = datetime.now()
            db.commit()
        with patch("backend.routers.platform_store.send_configured_email", return_value=("<mock@test>", "accepted")):
            preview = self.client.post("/platform/agency/1/registration-email/preview", json={})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertNotIn("Initial Password:", preview.json()["message"])
            self.assertNotIn("ChangedPassword!42", preview.text)
            self.assertEqual(self.client.post("/platform/agency/1/registration-email", json={}).status_code, 200)
        with self.factory() as db:
            self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).one().c_password, changed_hash)

    def test_resend_uses_creation_phone_even_after_contact_phone_changes(self):
        self.activate()
        with self.factory() as db:
            db.query(BusinessContact).one().c_phone = "703-855-5858"
            db.commit()
        response = self.client.post("/platform/agency/1/registration-email/preview", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["recipient"], "Jane@example.test")
        self.assertIn("Initial Password: Awsum@0100", response.json()["message"])
        self.assertNotIn("Awsum@5858", response.json()["message"])
        with self.factory() as db:
            self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).count(), 1)

    def test_invalid_recipient_or_temporary_password_is_rejected(self):
        self.activate()
        for payload in ({"recipient": "x@example.test\r\nBcc:y@example.test"}, {"temporary_password": "wrong"}):
            with patch("backend.routers.platform_store.send_configured_email") as smtp:
                response = self.client.post("/platform/agency/1/registration-email", json=payload)
                self.assertEqual(response.status_code, 400, response.text)
                smtp.assert_not_called()

    def test_smtp_failure_is_explicit_and_does_not_undo_activation(self):
        self.activate()
        for error, status in ((EmailConfigurationError("SMTP unavailable"), 503), (MailDeliveryError("SMTP refused"), 502)):
            with patch("backend.routers.platform_store.send_configured_email", side_effect=error):
                response = self.client.post("/platform/agency/1/registration-email", json={})
                self.assertEqual(response.status_code, status, response.text)
                self.assertIn("SMTP", response.json()["detail"])
            with self.factory() as db:
                self.assertEqual(db.get(Agency, 1).c_status, "active")
                self.assertEqual(db.query(PlatformUser).filter_by(i_agency_id=1).count(), 1)

    def login_agency(self, client, password):
        return client.post("/auth/login", data={
            "store_code": "agt00001", "email": "JANE@example.test", "password": password,
        }, follow_redirects=False)

    def test_agency_relogin_replaces_session_and_revokes_old_token(self):
        result = self.activate()
        with TestClient(self.app) as first, TestClient(self.app) as second:
            login = self.login_agency(first, result["temporary_password"])
            self.assertEqual(login.status_code, 303, login.text)
            self.assertEqual(first.get("/auth/registration-completed").status_code, 200)
            original_token = first.cookies.get("access_token")
            second.cookies.set("awsum_web_client", first.cookies.get("awsum_web_client"))
            with self.factory() as db:
                user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
                user_id, original_hash = user.i_platform_user_id, user.c_password
                original_session_id = db.query(SessionTbl).filter_by(i_platform_user_id=user_id).one().i_session_id
            replacement = self.login_agency(second, result["temporary_password"])
            self.assertEqual(replacement.status_code, 303, replacement.text)
            self.assertNotEqual(original_token, second.cookies.get("access_token"))
            self.assertEqual(first.get("/auth/registration-completed").status_code, 401)
            self.assertEqual(second.get("/auth/registration-completed").status_code, 200)
            with self.factory() as db:
                previous = db.get(SessionTbl, original_session_id)
                self.assertEqual(previous.c_status, "TERMINATED")
                self.assertIsNotNone(previous.dt_terminated)
                self.assertEqual(previous.i_terminated_by, user_id)
                active = db.query(SessionTbl).filter_by(i_platform_user_id=user_id, c_status="ACTIVE").one()
                self.assertNotEqual(active.i_session_id, original_session_id)
                self.assertEqual(active.i_agency_id, 1)
                self.assertEqual(db.get(PlatformUser, user_id).c_password, original_hash)
            # Repeat in the same browser, then log out with the stale browser's token.
            latest = self.login_agency(second, result["temporary_password"])
            self.assertEqual(latest.status_code, 303, latest.text)
            first.get("/auth/logout", follow_redirects=False)
            self.assertEqual(second.get("/auth/registration-completed").status_code, 200)
            with self.factory() as db:
                self.assertEqual(db.query(SessionTbl).filter_by(i_platform_user_id=user_id, c_status="ACTIVE").count(), 1)
                self.assertEqual(db.query(SessionTbl).filter_by(i_platform_user_id=user_id, c_status="TERMINATED").count(), 2)

    def test_wrong_agency_password_does_not_replace_active_session(self):
        result = self.activate()
        with TestClient(self.app) as first, TestClient(self.app) as second:
            self.assertEqual(self.login_agency(first, result["temporary_password"]).status_code, 303)
            with self.factory() as db:
                user_id = db.query(PlatformUser).filter_by(i_agency_id=1).one().i_platform_user_id
                session_key = db.query(SessionTbl).filter_by(i_platform_user_id=user_id).one().c_session_key
            response = self.login_agency(second, "WrongPassword!42")
            self.assertEqual(response.status_code, 401, response.text)
            self.assertIsNone(second.cookies.get("access_token"))
            self.assertEqual(first.get("/auth/registration-completed").status_code, 200)
            with self.factory() as db:
                row = db.query(SessionTbl).filter_by(i_platform_user_id=user_id).one()
                self.assertEqual(row.c_status, "ACTIVE")
                self.assertEqual(row.c_session_key, session_key)
                self.assertIsNone(row.dt_terminated)

    def test_platform_admin_relogin_still_rejects_without_revoking_session(self):
        original_token = self.client.cookies.get("access_token")
        with TestClient(self.app) as second:
            response = second.post("/platform-admin/login", data={
                "login_mode": "platform_admin", "access_code": "ADMIN",
                "email": "admin@example.test", "password": self.password,
            }, follow_redirects=False)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIsNone(second.cookies.get("access_token"))
        self.assertEqual(self.client.cookies.get("access_token"), original_token)
        self.assertEqual(self.client.get("/platform/users").status_code, 200)
        with self.factory() as db:
            self.assertEqual(db.query(SessionTbl).filter_by(i_platform_user_id=1, c_status="ACTIVE").count(), 1)
            self.assertEqual(db.query(SessionTbl).filter_by(i_platform_user_id=1).count(), 1)

    def test_failed_agency_session_replacement_preserves_original_session(self):
        result = self.activate()
        with TestClient(self.app) as first, TestClient(self.app) as second:
            self.assertEqual(self.login_agency(first, result["temporary_password"]).status_code, 303)
            second.cookies.set("awsum_web_client", first.cookies.get("awsum_web_client"))
            with self.factory() as db:
                user_id = db.query(PlatformUser).filter_by(i_agency_id=1).one().i_platform_user_id
                original_key = db.query(SessionTbl).filter_by(i_platform_user_id=user_id).one().c_session_key
            with patch("backend.services.web_sessions.collect_session_metadata",
                       side_effect=SQLAlchemyError("Test replacement failure")):
                with self.assertLogs("backend.routers.auth", level="ERROR"):
                    response = self.login_agency(second, result["temporary_password"])
            self.assertEqual(response.status_code, 500, response.text)
            self.assertIsNone(second.cookies.get("access_token"))
            self.assertEqual(first.get("/auth/registration-completed").status_code, 200)
            with self.factory() as db:
                row = db.query(SessionTbl).filter_by(i_platform_user_id=user_id).one()
                self.assertEqual(row.c_session_key, original_key)
                self.assertEqual(row.c_status, "ACTIVE")
                self.assertIsNone(row.dt_terminated)

    def test_initial_password_can_be_reused_and_changed_voluntarily(self):
        result = self.activate()
        with TestClient(self.app) as client:
            login = self.login_agency(client, result["temporary_password"])
            self.assertEqual(login.status_code, 303, login.text)
            self.assertEqual(login.headers["location"], "/agency/dashboard")
            completed = client.get("/auth/registration-completed")
            self.assertEqual(completed.status_code, 200)
            self.assertIn('href="/auth/change-password"', client.get("/agency/settings").text)
            self.assertNotIn("Your password has been changed", completed.text)
            client.get("/auth/logout", follow_redirects=False)
            self.assertEqual(self.login_agency(client, result["temporary_password"]).status_code, 303)
            with self.factory() as db:
                session = db.query(SessionTbl).filter_by(c_login_context="AGENCY", c_status="ACTIVE").one()
                self.assertEqual(session.i_agency_id, 1)
                self.assertIsNone(session.i_store_id)
                self.assertIsNone(session.i_merchant_id)
            page = client.get("/auth/change-password")
            self.assertEqual(page.status_code, 200, page.text)
            new_password = "TestOnly!Changed42"
            changed = client.post("/auth/change-password", data={
                "current_password": result["temporary_password"], "new_password": new_password,
                "confirm_password": new_password,
            }, follow_redirects=False)
            self.assertEqual(changed.status_code, 303, changed.text)
            self.assertEqual(changed.headers["location"], "/agency/dashboard")
            self.assertEqual(client.get(changed.headers["location"]).status_code, 200)
            stale = client.post("/platform/agency/1/registration-email", json={
                "temporary_password": result["temporary_password"],
            })
            self.assertEqual(stale.status_code, 403)
            client.get("/auth/logout", follow_redirects=False)
            self.assertEqual(self.login_agency(client, result["temporary_password"]).status_code, 401)
            self.assertEqual(self.login_agency(client, new_password).headers["location"], "/agency/dashboard")
        stale_preview = self.client.post("/platform/agency/1/registration-email/preview", json={
            "temporary_password": result["temporary_password"],
        })
        self.assertEqual(stale_preview.status_code, 400)
        existing_preview = self.client.post("/platform/agency/1/registration-email/preview", json={})
        self.assertEqual(existing_preview.status_code, 200, existing_preview.text)
        self.assertIn("First-login information", existing_preview.json()["message"])
        self.assertNotIn("Initial Password:", existing_preview.json()["message"])
        with self.factory() as db:
            user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
            self.assertEqual(user.i_must_change_password, 0)
            self.assertIsNotNone(user.dt_password_changed)

    def test_agency_cannot_use_platform_login_or_another_agency(self):
        result = self.activate()
        with self.factory() as db:
            db.add(Agency(i_agency_id=2, c_agency_code="AGT00002", c_agency_name="Other", c_status="active"))
            db.commit()
        with TestClient(self.app) as client:
            platform = client.post("/platform-admin/login", data={
                "login_mode": "platform_admin", "access_code": "ADMIN",
                "email": result["login_email"], "password": result["temporary_password"],
            }, follow_redirects=False)
            self.assertEqual(platform.status_code, 401)
            other = client.post("/auth/login", data={
                "store_code": "AGT00002", "email": result["login_email"], "password": result["temporary_password"],
            }, follow_redirects=False)
            self.assertEqual(other.status_code, 401)
        store = self.client.post("/auth/login", data={"store_code": "ST020001", "email": "x@example.test", "password": "x"})
        self.assertEqual(store.status_code, 400)

    def test_inactive_agency_rejects_login_and_existing_session(self):
        result = self.activate()
        with TestClient(self.app) as client:
            self.assertEqual(self.login_agency(client, result["temporary_password"]).status_code, 303)
            self.client.put("/platform/agency/1", json={"status": "inactive"})
            self.assertEqual(client.get("/auth/registration-completed").status_code, 401)
            self.assertNotEqual(self.login_agency(client, result["temporary_password"]).status_code, 303)

    def test_agency_users_are_not_managed_as_platform_users(self):
        self.activate()
        response = self.client.get("/platform/users")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        with self.factory() as db:
            user = db.query(PlatformUser).filter_by(i_agency_id=1).one()
            user_id = user.i_platform_user_id
        response = self.client.post(f"/platform/user/{user_id}/reset-password", json={
            "password": "TestOnly!Reset42", "confirm_password": "TestOnly!Reset42",
        })
        self.assertEqual(response.status_code, 404)


class AgencyUserMigrationTests(unittest.TestCase):
    def test_existing_user_data_survives_idempotent_extension(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_agency (i_agency_id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE tb_platform_user (i_platform_user_id INTEGER PRIMARY KEY, i_store_id INTEGER, c_email TEXT)"))
            conn.execute(text("INSERT INTO tb_platform_user VALUES (1, 1000001, 'existing@example.test')"))
        migrate_agency_users(engine)
        migrate_agency_users(engine)
        self.assertIn("i_agency_id", {col["name"] for col in inspect(engine).get_columns("tb_platform_user")})
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT c_email, i_agency_id FROM tb_platform_user")).one(),
                             ("existing@example.test", None))

    def test_postgresql_legacy_constraints_are_replaced_atomically_without_user_changes(self):
        schema = "agency_scope_test_" + uuid4().hex
        with platform_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            conn.execute(text("CREATE TABLE tb_agency (i_agency_id INTEGER PRIMARY KEY)"))
            conn.execute(text(
                "CREATE TABLE tb_platform_user (i_platform_user_id INTEGER PRIMARY KEY, "
                "i_store_id INTEGER NOT NULL, c_email TEXT NOT NULL, c_password TEXT, "
                "CONSTRAINT uq_platform_store_email UNIQUE(i_store_id, c_email), "
                "CONSTRAINT legacy_email_only UNIQUE(c_email))"
            ))
            conn.execute(text("INSERT INTO tb_agency VALUES (1), (2)"))
            conn.execute(text(
                "INSERT INTO tb_platform_user VALUES (1, 1000001, 'shared@example.test', 'preserved-hash')"
            ))
            isolated_engine = SimpleNamespace(begin=lambda: nullcontext(conn))
            migrate_agency_users(isolated_engine)
            migrate_agency_users(isolated_engine)
            conn.execute(text(
                "INSERT INTO tb_platform_user (i_platform_user_id, i_store_id, i_agency_id, c_email, c_password) "
                "VALUES (2, 1000001, 1, 'shared@example.test', 'agency-one-hash'), "
                "(3, 1000001, 2, 'shared@example.test', 'agency-two-hash')"
            ))
            for scope in ("1", "NULL"):
                with self.assertRaises(IntegrityError):
                    with conn.begin_nested():
                        conn.execute(text(
                            "INSERT INTO tb_platform_user (i_platform_user_id, i_store_id, i_agency_id, c_email) "
                            f"VALUES (4, 1000001, {scope}, ' SHARED@example.test ')"
                        ))
            self.assertEqual(conn.execute(text(
                "SELECT c_password, i_agency_id FROM tb_platform_user WHERE i_platform_user_id=1"
            )).one(), ("preserved-hash", None))
            self.assertEqual(conn.execute(text("SELECT count(*) FROM tb_platform_user")).scalar(), 3)
            conn.execute(text('SET LOCAL search_path TO public'))
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

if __name__ == "__main__":
    unittest.main()
