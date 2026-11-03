import asyncio
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from PIL import Image
from backend.models_admin.system_settings import SystemSettings
from backend.routers import system_settings as system_settings_router
from backend.routers.system_settings import _require_super_admin
from backend.config import templates as template_config
from backend.services import images

from backend.services.mail_common import EmailConfigurationError
from backend.services.system_settings import (
    _smtp_from_environment,
    _system_settings_smtp_configuration,
    get_smtp_configuration,
    require_encryption_key,
    smtp_environment_mode,
    smtp_public_status,
)


_env_patch = None


def setUpModule():
    global _env_patch
    from backend.core import local_env

    # Keep key-missing tests independent of the real server-only .env.
    _env_patch = patch.object(local_env, "LOCAL_ENV_PATH", Path(tempfile.gettempdir()) / "awsum-missing.env")
    _env_patch.start()


def tearDownModule():
    _env_patch.stop()


class LocalEnvKeyTests(unittest.TestCase):
    def test_key_is_read_lazily_from_env_file_and_environment_wins(self):
        from backend.core import local_env
        from backend.services.system_settings import ENCRYPTION_KEY_ENV, encryption_key_value

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(f"{ENCRYPTION_KEY_ENV}=from-file\n", encoding="utf-8")
            with patch.object(local_env, "LOCAL_ENV_PATH", env_file):
                with patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(encryption_key_value(), "from-file")
                with patch.dict(os.environ, {ENCRYPTION_KEY_ENV: "from-env"}, clear=True):
                    self.assertEqual(encryption_key_value(), "from-env")

class SecuritySettingsBrowserTests(unittest.TestCase):
    def test_security_uses_common_dirty_and_save_flow(self):
        import re
        from tools import test_image_components as browser

        if not browser.BROWSER.is_file():
            self.skipTest("Set AWSUM_REPORT_CHROME to Chrome/Edge.")
        html = (browser.ROOT / "templates" / "platform" / "system_settings.html").read_text(encoding="utf-8")
        pane = re.search(r'<section class="tab-pane fade" id="security-pane".*?</section>', html, re.S).group()
        source = (browser.ROOT / "static" / "js" / "system_settings.js").read_text(encoding="utf-8")
        values = source[source.index("  function valuesFor("):source.index("  function setMessage(")]
        save = source[source.index("  async function saveSection("):source.index("  function setEmailTestResult(")]
        script = "document.body.insertAdjacentHTML('beforeend'," + json.dumps(pane) + ");\n" + r"""
const $=id=>document.getElementById(id);
let settings={revision:3},snapshots={security:{web_session_idle_minutes:'30'}};
const sections=['security'],API='/platform/api/system-settings',pendingLogoFile=null;
let calls=[],message='';
function updateSettings(response){settings=response;$('web_session_idle_minutes').value=response.security.web_session_idle_minutes;}
function markSaved(section){snapshots[section]=valuesFor(section);}
function setMessage(text){message=text;}
async function api(path,options){
  const payload=JSON.parse(options.body);calls.push({path,method:options.method,payload});
  if(Number(payload.web_session_idle_minutes)<5)throw Error('Web Session Idle Timeout must be an integer from 5 to 480 minutes.');
  return {revision:4,security:{web_session_idle_minutes:payload.web_session_idle_minutes}};
}
""" + values + save + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
(async()=>{
 const input=$('web_session_idle_minutes');assert(input.min==='5'&&input.max==='480'&&input.step==='1','input range');
 input.value='30';assert(!isDirty('security'),'initial snapshot');
 input.value='45';assert(isDirty('security'),'unsaved Security setting detected');
 await saveSection('security');
 assert(calls[0].path===API+'/security'&&calls[0].method==='PUT','common settings endpoint');
 assert(calls[0].payload.revision===3&&calls[0].payload.web_session_idle_minutes==='45','revision and setting posted');
 assert(!isDirty('security')&&message==='Security settings saved.','saved snapshot');
 input.value='4';await saveSection('security');
 assert(document.querySelector('[data-save-result="security"]').textContent.includes('Save failed'),'validation surfaced');
 assert(isDirty('security')&&!document.querySelector('[type="submit"]').disabled,'failed save keeps changes and enables retry');
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack});});
"""
        browser.SharedImageComponentTests.run_component_script(self, script)


class SmtpPasswordPersistenceTests(unittest.TestCase):
    def test_security_idle_timeout_precedence_validation_and_audit(self):
        from backend.services.system_settings import web_session_idle_minutes
        from backend.models_admin.system_settings_audit import SystemSettingsAudit

        request = SimpleNamespace(headers={}, url=SimpleNamespace(netloc="x"))
        user = SimpleNamespace(i_platform_user_id=1)
        with self.Session() as db, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(web_session_idle_minutes(db), 30)
            with patch.dict(os.environ, {"AWSUM_WEB_SESSION_IDLE_MINUTES": "45"}):
                self.assertEqual(web_session_idle_minutes(db), 45)
            for value in (5, "480", 30):
                row = system_settings_router.get_or_create_settings(db)
                revision = row.i_revision if row else 0
                with patch.object(system_settings_router, "_require_super_admin", return_value=user), \
                     patch.object(system_settings_router, "_response_settings", return_value={}):
                    system_settings_router.save_system_settings("security", {
                        "revision": revision, "web_session_idle_minutes": value,
                    }, request, db)
                self.assertEqual(web_session_idle_minutes(db), int(value))
                with patch.dict(os.environ, {"AWSUM_WEB_SESSION_IDLE_MINUTES": "invalid"}):
                    self.assertEqual(web_session_idle_minutes(db), int(value))
            audit_count = db.query(SystemSettingsAudit).count()
            self.assertEqual(audit_count, 3)
            self.assertEqual(db.query(SystemSettingsAudit).first().c_changed_fields, "web_session_idle_minutes")
            for invalid in (4, 481, 30.5, True, None, "30.5", "", "invalid"):
                with patch.object(system_settings_router, "_require_super_admin", return_value=user):
                    with self.assertRaises(HTTPException) as exc:
                        system_settings_router.save_system_settings("security", {
                            "revision": db.get(SystemSettings, 1).i_revision,
                            "web_session_idle_minutes": invalid,
                        }, request, db)
                self.assertEqual(exc.exception.status_code, 400)
                self.assertEqual(web_session_idle_minutes(db), 30)
                self.assertEqual(db.query(SystemSettingsAudit).count(), audit_count)
            db.get(SystemSettings, 1).i_web_session_idle_minutes = None
            db.commit()
            for invalid in ("4", "481", "invalid"):
                with patch.dict(os.environ, {"AWSUM_WEB_SESSION_IDLE_MINUTES": invalid}):
                    with self.assertRaises(ValueError):
                        web_session_idle_minutes(db)

    def test_website_normalization(self):
        from backend.routers.system_settings import _normalize_website

        self.assertEqual(_normalize_website("awsumsolution.com"), "https://awsumsolution.com")
        self.assertEqual(_normalize_website("http://awsumsolution.com/a"), "http://awsumsolution.com/a")
        self.assertEqual(_normalize_website("https://awsumsolution.com"), "https://awsumsolution.com")
        self.assertEqual(_normalize_website(""), "")
        for bad in ("ftp://a.com", "not a url", "https://", "abc", "a b.com"):
            with self.assertRaises(HTTPException, msg=bad):
                _normalize_website(bad)

    def test_email_save_ignores_invalid_stored_website(self):
        with self.Session() as db:
            row = system_settings_router.get_or_create_settings(db, create=True)
            row.c_website = "broken value"
            db.commit()
        response = self.save({"smtp_password": "pw-x"})
        self.assertTrue(response["email"]["password_configured"])
        with self.Session() as db:
            self.assertEqual(db.query(SystemSettings).first().c_website, "broken value")

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.database.pg_platform import PlatformBase
        from backend.models_admin.system_settings_audit import SystemSettingsAudit

        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'settings.db'}")
        PlatformBase.metadata.create_all(engine, tables=[SystemSettings.__table__, SystemSettingsAudit.__table__])
        self.engine = engine
        self.Session = sessionmaker(bind=engine)
        self.key = Fernet.generate_key().decode()

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def save(self, payload, key=True):
        env = {"AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY": self.key} if key else {}
        with self.Session() as db:
            row = system_settings_router.get_or_create_settings(db)
            full = {
                "sender_name": "AwsumSolution", "from_email": "info@awsumsolution.com",
                "reply_to_email": "", "smtp_host": "smtp.gmail.com", "smtp_port": 465,
                "smtp_port_is_default": True, "smtp_username": "info@awsumsolution.com",
                "encryption": "SSL-TLS", "authentication": True, "test_recipient_email": "",
                "smtp_password": "", "clear_smtp_password": False,
            }
            payload = {"revision": row.i_revision if row else 0, **full, **payload}
            request = SimpleNamespace(headers={}, url=SimpleNamespace(netloc="x"))
            user = SimpleNamespace(i_platform_user_id=1)
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(images, "image_storage_root", return_value=Path(self.tmp.name) / "media"), \
                    patch.object(system_settings_router, "_require_super_admin", return_value=user):
                return system_settings_router._save_section("email", payload, request, db)

    def stored(self):
        with self.Session() as db:
            row = db.query(SystemSettings).first()
            return row.c_smtp_password_encrypted if row else None

    def test_password_new_keep_replace_clear_and_decrypt(self):
        secret = "app-pass-1"
        response = self.save({"smtp_password": secret})
        self.assertNotIn(secret, json.dumps(response, default=str))
        cipher = self.stored()
        self.assertTrue(cipher)
        self.assertNotIn(secret, cipher)
        self.assertEqual(Fernet(self.key.encode()).decrypt(cipher.encode()).decode(), secret)
        self.assertTrue(response["email"]["password_configured"])

        self.save({"smtp_password": ""})
        self.assertEqual(self.stored(), cipher)

        self.save({"smtp_password": "app-pass-2"})
        self.assertEqual(Fernet(self.key.encode()).decrypt(self.stored().encode()).decode(), "app-pass-2")

        response = self.save({"clear_smtp_password": True})
        self.assertIsNone(self.stored())
        self.assertFalse(response["email"]["password_configured"])

    def test_missing_key_blocks_save_and_writes_nothing(self):
        with self.assertRaises(HTTPException) as ctx:
            self.save({"smtp_password": "never-stored"}, key=False)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertNotIn("never-stored", str(ctx.exception.detail))
        self.assertIsNone(self.stored())

    def test_stored_password_is_used_for_smtp_login(self):
        self.save({"smtp_password": "app-pass-3", "smtp_host": "smtp.example.com", "smtp_username": "u@example.com"})
        with self.Session() as db, \
                patch.dict(os.environ, {"AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY": self.key}, clear=True), \
                patch("backend.services.system_settings.smtplib.SMTP_SSL") as smtp_ssl:
            from backend.services.system_settings import open_smtp_connection

            open_smtp_connection(get_smtp_configuration(db))
        smtp_ssl.return_value.login.assert_called_once_with("u@example.com", "app-pass-3")


class SystemSettingsServiceTests(unittest.TestCase):
    @staticmethod
    def logo_bytes():
        output = BytesIO()
        Image.new("RGB", (20, 10), "blue").save(output, "PNG")
        return output.getvalue()

    def test_partial_environment_configuration_does_not_fall_back_to_database(self):
        db = MagicMock()
        with patch.dict(os.environ, {
            "AWSUM_SMTP_HOST": "smtp.example.com",
        }, clear=True):
            self.assertTrue(smtp_environment_mode())
            with self.assertRaisesRegex(EmailConfigurationError, "SMTP authentication credentials not configured"):
                get_smtp_configuration(db)
        db.query.assert_not_called()

    def test_environment_configuration_uses_default_port_and_sender(self):
        db = MagicMock()
        with patch.dict(os.environ, {
            "AWSUM_SMTP_HOST": "smtp.example.com",
            "AWSUM_SMTP_FROM_EMAIL": "sender@example.com",
            "AWSUM_SMTP_PORT": "",
            "AWSUM_SMTP_STARTTLS": "false",
            "AWSUM_SMTP_USE_SSL": "false",
        }, clear=True):
            config = _smtp_from_environment()
        self.assertEqual(config.port, 587)
        self.assertEqual(config.sender_name, "AwsumSolution")
        self.assertEqual(config.source, "environment")
        db.query.assert_not_called()

    def test_environment_configuration_defaults_and_ssl_port(self):
        db = MagicMock()
        with patch.dict(os.environ, {
        }, clear=True):
            config = _smtp_from_environment()
        self.assertEqual(config.from_email, "info@awsumsolution.com")
        self.assertEqual(config.username, "info@awsumsolution.com")
        self.assertEqual(config.host, "smtp.gmail.com")
        self.assertEqual(config.port, 465)
        self.assertTrue(config.authentication)
        self.assertEqual(config.encryption, "SSL-TLS")

    def test_environment_authentication_can_be_explicitly_disabled(self):
        db = MagicMock()
        with patch.dict(os.environ, {
            "AWSUM_SMTP_HOST": "smtp.example.com",
            "AWSUM_SMTP_AUTHENTICATION": "false",
        }, clear=True):
            config = get_smtp_configuration(db)
        self.assertFalse(config.authentication)
        self.assertEqual(config.port, 465)
        self.assertEqual(config.encryption, "SSL-TLS")

    def test_encryption_key_is_required_and_validated(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(EmailConfigurationError, "AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY"):
                require_encryption_key()

        with patch.dict(os.environ, {"AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY": "not-a-fernet-key"}, clear=True):
            with self.assertRaisesRegex(EmailConfigurationError, "not a valid Fernet key"):
                require_encryption_key()

        key = Fernet.generate_key().decode("ascii")
        with patch.dict(os.environ, {"AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY": key}, clear=True):
            self.assertIsInstance(require_encryption_key(), Fernet)

    def test_database_smtp_configuration_decrypts_password_only_with_the_saved_key(self):
        key = Fernet.generate_key()
        cipher = Fernet(key)
        row = SystemSettings(
            i_settings_id=1,
            c_sender_name="Awsum",
            c_from_email="sender@example.com",
            c_smtp_host="smtp.example.com",
            i_smtp_port=465,
            c_smtp_username="smtp-user",
            c_smtp_password_encrypted=cipher.encrypt(b"secret-password").decode("ascii"),
            c_smtp_encryption="SSL-TLS",
            b_smtp_authentication=1,
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch.dict(os.environ, {
            "AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY": key.decode("ascii"),
        }, clear=True):
            config = _system_settings_smtp_configuration(db)

        self.assertEqual(config.source, "system_settings")
        self.assertEqual(config.password, "secret-password")
        self.assertEqual(config.encryption, "SSL-TLS")

        with patch.dict(os.environ, {
            "AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        }, clear=True):
            with self.assertRaisesRegex(EmailConfigurationError, "SMTP encryption key cannot decrypt"):
                get_smtp_configuration(db)

    def test_settings_access_requires_the_super_admin_role(self):
        user = SimpleNamespace(i_role_id=2)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(c_name="Merchant")
        with patch("backend.routers.system_settings.get_current_user", return_value=user):
            with self.assertRaises(HTTPException) as error:
                _require_super_admin(object(), db)
        self.assertEqual(error.exception.status_code, 403)

        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(c_name="SuperAdmin")
        with patch("backend.routers.system_settings.get_current_user", return_value=user):
            self.assertIs(_require_super_admin(object(), db), user)

    def test_blank_password_preserves_saved_secret_and_new_password_is_encrypted(self):
        key = Fernet.generate_key()
        existing_ciphertext = Fernet(key).encrypt(b"old-secret").decode("ascii")
        row = SystemSettings(
            i_settings_id=1,
            i_revision=4,
            c_sender_name="AwsumSolution",
            c_from_email="info@awsumsolution.com",
            c_smtp_host="smtp.gmail.com",
            i_smtp_port=465,
            c_smtp_username="info@awsumsolution.com",
            c_smtp_encryption="SSL-TLS",
            b_smtp_authentication=1,
            c_smtp_password_encrypted=existing_ciphertext,
        )
        user = SimpleNamespace(i_platform_user_id=9)
        request = SimpleNamespace(headers={}, url=SimpleNamespace(netloc="platform.example.com"))

        def payload(password):
            return {
                "revision": 4,
                "sender_name": "AwsumSolution",
                "from_email": "info@awsumsolution.com",
                "reply_to_email": "",
                "smtp_host": "smtp.gmail.com",
                "smtp_username": "info@awsumsolution.com",
                "test_recipient_email": "",
                "smtp_port": 465,
                "smtp_port_is_default": False,
                "encryption": "SSL-TLS",
                "authentication": True,
                "smtp_password": password,
                "clear_smtp_password": False,
            }

        with patch.dict(os.environ, {"AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY": key.decode("ascii")}, clear=True):
            db = MagicMock()
            db.refresh.side_effect = lambda instance, **kwargs: None
            db.query.return_value.filter.return_value.first.return_value = row
            with (
                patch.object(system_settings_router, "_require_super_admin", return_value=user),
                patch.object(system_settings_router, "get_or_create_settings", return_value=row),
                patch.object(system_settings_router, "_response_settings", return_value={}),
            ):
                system_settings_router._save_section("email", payload(""), request, db)
                self.assertEqual(row.c_smtp_password_encrypted, existing_ciphertext)

                row.i_revision = 5
                changed_payload = payload("new-secret")
                changed_payload["revision"] = 5
                system_settings_router._save_section("email", changed_payload, request, db)
                self.assertNotEqual(row.c_smtp_password_encrypted, existing_ciphertext)
                self.assertNotIn("new-secret", row.c_smtp_password_encrypted)
                self.assertEqual(
                    Fernet(key).decrypt(row.c_smtp_password_encrypted.encode("ascii")).decode(),
                    "new-secret",
                )
                row.i_revision = 6
                clear_payload = payload("")
                clear_payload["revision"] = 6
                clear_payload["clear_smtp_password"] = True
                system_settings_router._save_section("email", clear_payload, request, db)
                self.assertIsNone(row.c_smtp_password_encrypted)

    def test_test_email_endpoint_sends_submitted_content_with_saved_company_branding(self):
        row = SimpleNamespace(
            c_company_name="Example Company",
            c_logo_url="/static/images/brands/platform/company.png",
            c_email_test_status=None,
            c_email_test_result=None,
            dt_email_tested_at=None,
        )
        db = MagicMock()
        request = SimpleNamespace(
            json=AsyncMock(return_value={
                "recipient": "admin@example.com",
                "subject": "[AwsumSolution] Test Email",
                "message": "Custom test content",
            }),
            base_url="https://platform.example.com/",
        )
        with (
            patch.object(system_settings_router, "_validate_origin"),
            patch.object(system_settings_router, "_require_super_admin"),
            patch.object(system_settings_router, "get_or_create_settings", return_value=row),
            patch.object(system_settings_router, "_response_settings", return_value={"email": {}}),
            patch.object(system_settings_router, "_set_no_cache_headers", side_effect=lambda response: response),
            patch.object(system_settings_router, "send_configured_email", return_value=("<id@example.com>", "Accepted")) as send,
            patch.object(images, "image_url", return_value="/platform-admin/images/logo/company.png?v=123"),
        ):
            response = asyncio.run(system_settings_router.send_test_email(request, db))

        sent_email = send.call_args.args[1]
        self.assertEqual(sent_email["To"], "admin@example.com")
        self.assertEqual(sent_email["Subject"], "[AwsumSolution] Test Email")
        self.assertIn("Custom test content", sent_email.get_body(preferencelist=("plain",)).get_content())
        self.assertIn(
            "https://platform.example.com/platform-admin/images/logo/company.png?v=123",
            sent_email.get_body(preferencelist=("html",)).get_content(),
        )
        self.assertTrue(json.loads(response.body)["accepted"])

    def test_test_email_endpoint_rejects_invalid_recipient_before_sending(self):
        row = SimpleNamespace(c_company_name="Example", c_logo_url=None)
        db = MagicMock()
        request = SimpleNamespace(
            json=AsyncMock(return_value={
                "recipient": "not-an-email",
                "subject": "Test",
                "message": "Body",
            }),
            base_url="https://platform.example.com/",
        )
        with (
            patch.object(system_settings_router, "_validate_origin"),
            patch.object(system_settings_router, "_require_super_admin"),
            patch.object(system_settings_router, "get_or_create_settings", return_value=row),
            patch.object(system_settings_router, "send_configured_email") as send,
        ):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(system_settings_router.send_test_email(request, db))
        self.assertEqual(error.exception.status_code, 400)
        send.assert_not_called()

    def test_null_smtp_values_use_defaults_and_explicit_false_is_preserved(self):
        row = SystemSettings(i_settings_id=1, b_smtp_authentication=None, c_smtp_encryption=None)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = row
        with patch.dict(os.environ, {}, clear=True):
            config = _system_settings_smtp_configuration(db)
        self.assertEqual(config.sender_name, "AwsumSolution")
        self.assertEqual(config.from_email, "info@awsumsolution.com")
        self.assertEqual(config.host, "smtp.gmail.com")
        self.assertEqual(config.host, "smtp.gmail.com")
        self.assertEqual(config.port, 465)
        self.assertEqual(config.encryption, "SSL-TLS")
        self.assertTrue(config.authentication)
        self.assertEqual(config.username, "info@awsumsolution.com")

        row.b_smtp_authentication = 0
        row.c_smtp_host = "smtp.example.com"
        with patch.dict(os.environ, {}, clear=True):
            config = get_smtp_configuration(db)
        self.assertFalse(config.authentication)

    def test_explicit_smtp_port_is_not_replaced_by_encryption_default(self):
        row = SystemSettings(
            i_settings_id=1,
            c_smtp_host="smtp.example.com",
            c_smtp_encryption="SSL-TLS",
            i_smtp_port=587,
            b_smtp_authentication=0,
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = row
        with patch.dict(os.environ, {}, clear=True):
            config = get_smtp_configuration(db)
        self.assertEqual(config.port, 587)
        self.assertEqual(config.encryption, "SSL-TLS")

    def test_smtp_status_lists_missing_host_credentials_and_encryption_key(self):
        row = SystemSettings(i_settings_id=1)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = row
        with patch.dict(os.environ, {}, clear=True):
            status = smtp_public_status(db)
        self.assertFalse(status["ready"])
        self.assertEqual(status["sender_name"], "AwsumSolution")
        self.assertEqual(status["from_email"], "info@awsumsolution.com")
        self.assertEqual(status["port"], 465)
        self.assertEqual(status["encryption"], "SSL-TLS")
        self.assertTrue(status["authentication"])
        self.assertEqual(status["host"], "smtp.gmail.com")
        self.assertEqual(status["port"], 465)
        self.assertEqual(status["encryption"], "SSL-TLS")
        self.assertIn("SMTP authentication credentials not configured", status["missing"])
        self.assertIn("SMTP encryption key not configured", status["missing"])

    def test_settings_response_exposes_shared_smtp_defaults_without_creating_a_row(self):
        status = {
            "source": "system_settings",
            "ready": False,
            "status": "Not configured",
            "host": "smtp.gmail.com",
            "port": 465,
            "from_email": "info@awsumsolution.com",
            "sender_name": "AwsumSolution",
            "reply_to_email": None,
            "username": "info@awsumsolution.com",
            "encryption": "SSL-TLS",
            "authentication": True,
            "missing": ["SMTP authentication credentials not configured"],
            "error": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(system_settings_router, "_row_or_defaults", return_value=None) as read_row,
                patch("backend.services.system_settings.get_or_create_settings", return_value=None),
                patch.object(system_settings_router, "get_storage_root", return_value=(Path(directory), "default")),
                patch.object(system_settings_router, "smtp_public_status", return_value=status),
                patch.object(system_settings_router, "smtp_environment_mode", return_value=False),
            ):
                response = system_settings_router._response_settings(MagicMock())
        self.assertEqual(response["email"]["sender_name"], "AwsumSolution")
        self.assertEqual(response["email"]["from_email"], "info@awsumsolution.com")
        self.assertEqual(response["email"]["smtp_host"], "smtp.gmail.com")
        self.assertEqual(response["email"]["smtp_username"], "info@awsumsolution.com")
        self.assertEqual(response["email"]["smtp_port"], 465)
        self.assertEqual(response["email"]["encryption"], "SSL-TLS")
        self.assertTrue(response["email"]["authentication"])
        self.assertFalse(response["email"]["ready"])
        read_row.assert_called_once()

    def test_company_logo_accepts_real_png_jpeg_and_webp_images(self):
        for image_format, content_type in (
            ("PNG", "image/png"),
            ("JPEG", "image/jpeg"),
            ("WEBP", "image/webp"),
        ):
            content = BytesIO()
            Image.new("RGB", (2, 2), "white").save(content, format=image_format)
            extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image_format]
            upload = UploadFile(BytesIO(content.getvalue()), filename=f"logo.{extension}",
                                headers=Headers({"content-type": content_type}))
            normalized = images.normalize_image(upload)
            with Image.open(BytesIO(normalized)) as image:
                self.assertEqual((image.format, image.size), ("WEBP", (512, 512)))

    def test_company_logo_rejects_mismatched_and_invalid_images(self):
        upload = UploadFile(BytesIO(b"not an image"), filename="logo.png", headers=Headers({"content-type": "image/png"}))
        with self.assertRaises(HTTPException) as error:
            images.normalize_image(upload)
        self.assertEqual(error.exception.status_code, 400)

    def test_company_save_updates_logo_reference_only_after_new_file_is_staged(self):
        with tempfile.TemporaryDirectory() as directory:
            logo_dir = Path(directory) / "platform-admin" / "images" / "logo"
            logo_dir.mkdir(parents=True)
            old_logo = logo_dir / "company-logo-old.png"
            old_logo.write_bytes(b"prior logo")
            row = SystemSettings(
                i_settings_id=1,
                i_revision=3,
                c_logo_url="/platform-admin/images/logo/company-logo-old.png",
            )
            db = MagicMock()
            request = SimpleNamespace(headers={}, url=SimpleNamespace(netloc="platform.example.com"))
            user = SimpleNamespace(i_platform_user_id=9)
            payload = {
                "revision": 3,
                "company_name": "",
                "display_name": "",
                "website": "",
                "contact_email": "",
                "phone": "",
                "address": "",
            }
            file = SimpleNamespace()
            with (
                patch.object(images, "image_storage_root", return_value=Path(directory)),
                patch.object(system_settings_router, "_require_super_admin", return_value=user),
                patch.object(system_settings_router, "get_or_create_settings", return_value=row),
                patch.object(images, "read_image_upload", return_value=self.logo_bytes()),
                patch.object(system_settings_router, "record_settings_change"),
                patch.object(system_settings_router, "_response_settings", return_value={"revision": 4}),
            ):
                response = system_settings_router._save_section("company", payload, request, db, logo_upload=file)
            saved_path = logo_dir / Path(row.c_logo_url).name
            self.assertEqual(response, {"revision": 4})
            with Image.open(saved_path) as saved:
                self.assertEqual((saved.format, saved.size), ("WEBP", (512, 512)))
            self.assertFalse(old_logo.exists())
            db.commit.assert_called_once()

    def test_common_platform_brand_uses_shared_logo_and_cache_busting(self):
        with tempfile.TemporaryDirectory() as directory:
            static_root = Path(directory)
            shared_brand = static_root / "images" / "brands" / "platform"
            shared_brand.mkdir(parents=True)
            (shared_brand / "logo.png").write_bytes(b"logo")
            (shared_brand / "icon.png").write_bytes(b"icon")
            factory = MagicMock()
            factory.kw = {"bind": object()}
            factory.return_value.__enter__.return_value.get.return_value = None
            with (
                patch.object(images, "STATIC_ROOT", static_root),
                patch.object(images, "image_storage_root", return_value=static_root / "media"),
                patch.object(template_config, "PlatformSessionLocal", factory),
                patch("backend.config.templates.inspect", return_value=SimpleNamespace(has_table=lambda _: True)),
            ):
                brand = template_config.get_brand_context(context_type="platform")
        self.assertIn("/images/platform/logo/logo_full.png?v=", brand["brand_logo_path"])
        self.assertIn("/images/platform/favicon/favicon.png?v=", brand["brand_icon_path"])

    def test_common_brand_uses_saved_logo_reference_after_database_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            static_root = Path(directory)
            saved_logo = static_root / "images" / "brands" / "platform" / "company-logo-saved.png"
            saved_logo.parent.mkdir(parents=True)
            saved_logo.write_bytes(b"saved")
            engine = object()
            session_factory = MagicMock()
            session_factory.kw = {"bind": engine}
            session = session_factory.return_value.__enter__.return_value
            session.get.return_value = SystemSettings(
                i_settings_id=1, c_logo_url="/static/images/brands/platform/company-logo-saved.png",
            )
            with (
                patch.object(images, "STATIC_ROOT", static_root),
                patch.object(images, "image_storage_root", return_value=static_root / "media"),
                patch.object(template_config, "PlatformSessionLocal", session_factory),
                patch("backend.config.templates.inspect", return_value=SimpleNamespace(has_table=lambda _: True)),
            ):
                brand = template_config.get_brand_context(context_type="platform")
        self.assertIn("company-logo-saved.png?v=", brand["brand_logo_path"])
        self.assertIn("company-logo-saved.png?v=", brand["brand_icon_path"])

    def test_company_logo_keeps_previous_file_when_settings_database_commit_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            logo_dir = Path(directory) / "platform-admin" / "images" / "logo"
            logo_dir.mkdir(parents=True)
            old_logo = logo_dir / "company-logo-old.png"
            old_logo.write_bytes(b"prior logo")
            row = SystemSettings(
                i_settings_id=1,
                i_revision=3,
                c_logo_url="/platform-admin/images/logo/company-logo-old.png",
            )
            db = MagicMock()
            db.commit.side_effect = RuntimeError("database unavailable")
            request = SimpleNamespace(headers={}, url=SimpleNamespace(netloc="platform.example.com"))
            user = SimpleNamespace(i_platform_user_id=9)
            payload = {
                "revision": 3,
                "company_name": "",
                "display_name": "",
                "website": "",
                "contact_email": "",
                "phone": "",
                "address": "",
            }
            file = SimpleNamespace()
            with (
                patch.object(images, "image_storage_root", return_value=Path(directory)),
                patch.object(system_settings_router, "_require_super_admin", return_value=user),
                patch.object(system_settings_router, "get_or_create_settings", return_value=row),
                patch.object(images, "read_image_upload", return_value=self.logo_bytes()),
                patch.object(system_settings_router, "record_settings_change"),
            ):
                with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                    system_settings_router._save_section("company", payload, request, db, logo_upload=file)
            self.assertGreaterEqual(db.rollback.call_count, 1)
            self.assertEqual(old_logo.read_bytes(), b"prior logo")
            self.assertEqual(list(logo_dir.glob("*.png")), [old_logo])
            self.assertEqual(list(logo_dir.glob("*.webp")), [])


if __name__ == "__main__":
    unittest.main()
