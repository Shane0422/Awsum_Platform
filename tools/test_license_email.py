import os
import smtplib
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.mail_common import (
    EmailConfigurationError,
    MailDeliveryError,
    validate_email_address,
)
from backend.services.license_email import (
    build_license_email,
    build_system_test_email,
    send_license_email,
    validate_email_content,
)


class LicenseEmailTests(unittest.TestCase):
    def test_validates_single_email_address(self):
        self.assertEqual(validate_email_address(" agency@example.com "), "agency@example.com")
        for invalid in ("", "a@example", "a@example.com\r\nBcc:other@example.com", "a@example.com,b@example.com"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_email_address(invalid)

    def test_rejects_subject_header_injection_and_oversized_content(self):
        with self.assertRaises(ValueError):
            validate_email_content("License\r\nBcc:bad@example.com", "Hello")
        with self.assertRaises(ValueError):
            validate_email_content("Subject", "x" * 10001)

    def test_builds_plain_and_escaped_html_with_selected_full_keys(self):
        message = build_license_email(
            recipient="agency@example.com",
            subject="License information",
            custom_message="Keep <this> safe",
            store_info={"merchant_name": "Merchant <One>", "store_name": "Store", "agency_name": "Agency"},
            licenses=[{
                "name": "POS <01>",
                "type": "POS",
                "license_key": "AWS-POS-FULL-KEY",
            }],
            sender="",
        )
        self.assertEqual(message["To"], "agency@example.com")
        self.assertIn("AWS-POS-FULL-KEY", message.get_body(preferencelist=("plain",)).get_content())
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Merchant &lt;One&gt;", html_body)
        self.assertIn("Keep &lt;this&gt; safe", html_body)
        self.assertNotIn("<script>", html_body)

    def test_branded_html_uses_cid_logo_tables_and_no_local_urls(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        message = build_license_email(
            recipient="agency@example.com",
            subject="License information",
            custom_message="Line1\nLine2",
            store_info={"merchant_name": "M", "store_name": "S", "agency_name": "A"},
            licenses=[{"name": "<b>POS</b>", "type": "POS", "license_key": "AWS-POS-" + "K" * 80}],
            sender="",
            company={"name": "Awsum <Co>", "website": "https://awsumsolution.com",
                     "contact_email": "info@awsumsolution.com", "logo_bytes": png, "logo_subtype": "png"},
        )
        html_part = message.get_body(preferencelist=("html",))
        html_body = html_part.get_content()
        self.assertIn("src=\"cid:", html_body)
        self.assertNotIn("localhost", html_body)
        self.assertNotIn("127.0.0.1", html_body)
        self.assertNotIn("<script", html_body.lower())
        self.assertIn("Your License Information", html_body)
        self.assertIn("#EAF2FF", html_body)
        self.assertIn("&lt;b&gt;POS&lt;/b&gt;", html_body)
        self.assertIn("AWS-POS-" + "K" * 80, html_body)
        self.assertIn("Awsum &lt;Co&gt;", html_body)
        self.assertEqual(len(list(message.walk())), 5)
        images = [part for part in message.walk() if part.get_content_maintype() == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].get_content(), png)
        self.assertIn("AWS-POS-" + "K" * 80, message.get_body(preferencelist=("plain",)).get_content())

    def test_preview_matches_send_template_without_logo(self):
        from backend.services.license_email import render_license_email_html, render_license_email_preview

        kwargs = dict(
            store_info={"merchant_name": "M", "store_name": "S", "agency_name": "A"},
            licenses=[{"name": "POS 1", "type": "KIOSK", "license_key": "KEY-1"}],
            custom_message="Hi",
            company={"name": "Awsum", "website": "", "contact_email": "", "logo_bytes": None, "logo_subtype": None},
        )
        self.assertEqual(render_license_email_preview(**kwargs), render_license_email_html(logo_src=None, **kwargs))

    def test_builds_company_branded_test_email_with_plain_text_and_escaped_html(self):
        message = build_system_test_email(
            recipient="admin@example.com",
            subject="[AwsumSolution] Test Email",
            custom_message="Line one\n<b>Line two</b>",
            company_name="Awsum <Company>",
            logo_url="https://example.com/static/images/brands/platform/logo.png",
        )
        plain_body = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertEqual(message["To"], "admin@example.com")
        self.assertEqual(message["Subject"], "[AwsumSolution] Test Email")
        self.assertIn("Awsum <Company>", plain_body)
        self.assertIn("Line one\n<b>Line two</b>", plain_body)
        self.assertIn("Awsum &lt;Company&gt;", html_body)
        self.assertIn("src=\"https://example.com/static/images/brands/platform/logo.png\"", html_body)
        self.assertIn("&lt;b&gt;Line two&lt;/b&gt;", html_body)
        self.assertNotIn("<b>Line two</b>", html_body)

    def test_missing_smtp_configuration_is_an_explicit_failure(self):
        db = unittest.mock.MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(EmailConfigurationError, "Email delivery is not ready"):
                send_license_email(EmailMessage(), db)

    def test_smtp_acceptance_returns_message_id(self):
        message = EmailMessage()
        message["Message-ID"] = "<test-message@example.com>"
        message["To"] = "agency@example.com"
        message.set_content("Test")
        db = unittest.mock.MagicMock()
        with patch.dict(os.environ, {
            "AWSUM_SMTP_HOST": "smtp.example.com",
            "AWSUM_SMTP_PORT": "587",
            "AWSUM_SMTP_FROM_EMAIL": "sender@example.com",
            "AWSUM_SMTP_STARTTLS": "false",
            "AWSUM_SMTP_USE_SSL": "false",
            "AWSUM_SMTP_AUTHENTICATION": "false",
        }, clear=True), patch("backend.services.system_settings.smtplib.SMTP") as smtp_class:
            smtp = smtp_class.return_value.__enter__.return_value
            smtp.send_message.return_value = {}
            message_id, result = send_license_email(message, db)
        self.assertEqual(message_id, "<test-message@example.com>")
        self.assertEqual(result, "Accepted by SMTP server.")
        smtp.send_message.assert_called_once_with(message)

    def test_smtp_rejection_is_reported_as_failure(self):
        message = EmailMessage()
        message["Message-ID"] = "<rejected-message@example.com>"
        message["To"] = "agency@example.com"
        message.set_content("Test")
        db = unittest.mock.MagicMock()
        with patch.dict(os.environ, {
            "AWSUM_SMTP_HOST": "smtp.example.com",
            "AWSUM_SMTP_PORT": "587",
            "AWSUM_SMTP_FROM_EMAIL": "sender@example.com",
            "AWSUM_SMTP_STARTTLS": "false",
            "AWSUM_SMTP_USE_SSL": "false",
            "AWSUM_SMTP_AUTHENTICATION": "false",
        }, clear=True), patch("backend.services.system_settings.smtplib.SMTP") as smtp_class:
            smtp = smtp_class.return_value.__enter__.return_value
            smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused({
                "agency@example.com": (550, b"recipient rejected")
            })
            with self.assertRaisesRegex(MailDeliveryError, "Mail server refused the recipient"):
                send_license_email(message, db)


if __name__ == "__main__":
    unittest.main()
