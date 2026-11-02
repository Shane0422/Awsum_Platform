"""Rendering-only coverage for AWSUM common email presentation."""
import hashlib
import inspect
import unittest
from unittest.mock import patch

from backend.services.registration_email import PASSWORD_GUIDANCE, build_registration_email
from backend.services.email_layout import AwsumEmailLayout
from backend.services.license_email import (
    _license_content, build_license_email, render_license_email_html, render_license_email_preview,
)


class EmailLayoutTests(unittest.TestCase):
    def setUp(self):
        self.company = {
            "name": "Awsum <Co>", "website": "https://awsumsolution.com",
            "contact_email": "info@awsumsolution.com",
        }
        self.license_values = {
            "store_info": {"merchant_name": "Merchant <One>", "store_name": "Store", "agency_name": "Agency"},
            "licenses": [{"name": "POS <01>", "type": "POS", "license_key": "AWS-POS-FULL-KEY"}],
            "custom_message": "Line1\nKeep <this> safe", "company": self.company,
        }
        self.agency_values = {
            "recipient": "recipient@example.com", "company": self.company,
            "owner_type": "AGENCY", "name": "Agency <One>", "code": "AGT00001",
            "login_email": "login@example.com", "login_url": "https://platform.example.com/login/agency/AGT00001",
            "initial_password": "Awsum@5858",
        }

    def test_license_layout_matches_pre_extraction_html_exactly(self):
        # Fingerprints captured from the existing renderer before extracting its layout.
        fixtures = (
            (None, "212145d5a05c23d9fbad1014529511369ea1d0fda391299fe555fa9459bcf8b0"),
            ({"key": "STORE-KEY", "status": "ACTIVE", "valid_from": "11/01/2026",
              "valid_until": "11/01/2027", "device_licenses": "2"},
             "c725315738ca22dc6b8f738cc742a5a465bee92d9d3f5483ad34784648c1177e"),
        )
        for store_license, expected in fixtures:
            with self.subTest(store_license=store_license):
                values = dict(self.license_values)
                values["store_info"] = dict(values["store_info"], store_license=store_license)
                result = render_license_email_html(**values, logo_src=None)
                self.assertEqual(hashlib.sha256(result.encode()).hexdigest(), expected)

    def test_agency_uses_standard_branding_sections_and_sign_in_button(self):
        message = build_registration_email(**self.agency_values)
        body = AwsumEmailLayout.preview_message(message)
        for value in (
            "Welcome to AWSUM Platform", "Agency Registration", "AGENCY INFORMATION", "ACCOUNT INFORMATION",
            "Agency Name", "Agency Code", "Login Email", "Login URL", "Initial Password", "Awsum@5858",
            PASSWORD_GUIDANCE, ">Sign In</a>", 'max-width:600px', "background:#F4F7FB", "border-radius:12px",
            "Awsum &lt;Co&gt;", "info@awsumsolution.com",
        ):
            self.assertIn(value, body)
        self.assertIn('href="https://platform.example.com/login/agency/AGT00001"', body)
        self.assertIn("Agency &lt;One&gt;", body)
        self.assertNotIn("DEVICE LICENSES", body)
        self.assertEqual(str(message["To"]), "recipient@example.com")
        self.assertIn("Login Email: login@example.com", message.get_body(preferencelist=("plain",)).get_content())

    def test_resend_without_initial_password_omits_entire_password_row(self):
        message = build_registration_email(**dict(self.agency_values, initial_password=None))
        body = AwsumEmailLayout.preview_message(message)
        self.assertNotIn("Initial Password", body)
        self.assertNotIn("Awsum@5858", body)
        self.assertIn("First-login information", body)
        self.assertIn(">Sign In</a>", body)
        self.assertIn(PASSWORD_GUIDANCE, body)

    def test_both_email_types_use_one_render_method_for_preview_and_send(self):
        original = AwsumEmailLayout.render
        with patch.object(AwsumEmailLayout, "render", autospec=True, side_effect=original) as renderer:
            license_preview = render_license_email_preview(**self.license_values)
            license_message = build_license_email(
                **self.license_values, recipient="license@example.com", subject="License", sender=None,
            )
            agency_message = build_registration_email(**self.agency_values)
            agency_preview = AwsumEmailLayout.preview_message(agency_message)
            self.assertEqual(renderer.call_count, 3)
            self.assertEqual(
                [call.kwargs["title"] for call in renderer.call_args_list],
                ["Your License Information", "Your License Information", "Welcome to AWSUM Platform"],
            )
        self.assertEqual(license_preview, license_message.get_body(preferencelist=("html",)).get_content().rstrip("\n"))
        self.assertEqual(agency_preview, agency_message.get_body(preferencelist=("html",)).get_content())
        self.assertNotIn("style=", inspect.getsource(_license_content))
        self.assertNotIn("<table", inspect.getsource(_license_content))
        self.assertNotIn("style=", inspect.getsource(build_registration_email))
        self.assertNotIn("<table", inspect.getsource(build_registration_email))

    def test_preview_and_mime_share_html_with_only_logo_transport_difference(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        company = dict(self.company, logo_bytes=png, logo_subtype="png")
        for kind in ("license", "agency"):
            with self.subTest(kind=kind):
                if kind == "license":
                    values = dict(self.license_values, company=company)
                    message = build_license_email(**values, recipient="to@example.com", subject="License", sender=None)
                    preview = render_license_email_preview(**values)
                    self.assertEqual(preview, AwsumEmailLayout.preview_message(message).rstrip("\n"))
                else:
                    message = build_registration_email(**dict(self.agency_values, company=company))
                    preview = AwsumEmailLayout.preview_message(message)
                sent = message.get_body(preferencelist=("html",)).get_content()
                self.assertIn('src="cid:', sent)
                self.assertIn('src="data:image/png;base64,', preview)
                image = next(part for part in message.walk() if part.get_content_maintype() == "image")
                cid = str(image["Content-ID"]).strip("<>")
                image_src = preview.split('src="', 1)[1].split('"', 1)[0]
                self.assertEqual(sent.replace(f"cid:{cid}", image_src), preview if kind == "agency" else preview + "\n")
                self.assertEqual(image.get_content(), png)
                self.assertNotIn("localhost", sent)

    def test_content_escaping_and_button_url_validation_are_common(self):
        message = build_registration_email(**dict(
            self.agency_values, name="<script>bad</script>",
            initial_password='Password<&">', login_url='https://example.com/?next="quoted"&a=1',
        ))
        body = AwsumEmailLayout.preview_message(message)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", body)
        self.assertIn("Password&lt;&amp;&quot;&gt;", body)
        self.assertIn("?next=&quot;quoted&quot;&amp;a=1", body)
        with self.assertRaises(ValueError):
            AwsumEmailLayout().button("Sign In", "javascript:alert(1)")


if __name__ == "__main__":
    unittest.main()
