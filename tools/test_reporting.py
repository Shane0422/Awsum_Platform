import asyncio
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, landscape

from backend.config.settings import APP_NAME
from backend.database.pg_platform import ADMIN_STORE_ID
from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.models_admin.system_settings import SystemSettings
from backend.services.report_pdf import GlobalReportCanvas, fit_footer_text, report_font
from backend.services.reporting import (
    GlobalReportMiddleware, ReportPrintContext, apply_print_layout,
    report_context, request_report_context, table_report_html,
)


class ReportContextTests(unittest.TestCase):
    def user(self, **kwargs):
        return SimpleNamespace(
            **({"c_first_name": "Shane", "c_last_name": "Lee", "c_email": "user@example.test",
                "i_store_id": ADMIN_STORE_ID} | kwargs)
        )

    def test_headquarters_uses_settings_and_authenticated_name(self):
        db = Mock()
        db.get.return_value = SimpleNamespace(c_company_name=" Company LLC ", c_display_name="Brand")
        ctx = report_context(db, self.user())
        self.assertEqual((ctx.company, ctx.printed_by), ("Company LLC", "Shane Lee"))
        db.get.assert_called_once_with(SystemSettings, 1)
        self.assertTrue(ctx.printed_at.endswith(" UTC"))

    def test_tenant_company_comes_from_users_store_not_report_target(self):
        db = Mock()
        store = SimpleNamespace(i_merchant_id=9, c_store_name="Tenant Store")
        merchant = SimpleNamespace(c_merchant_name="Tenant Company")
        db.get.side_effect = lambda model, key: store if model is Store else merchant if model is Merchant else None
        self.assertEqual(report_context(db, self.user(i_store_id=100)).company, "Tenant Company")
        db.get.assert_any_call(Store, 100)

    def test_missing_company_name_and_user_fields_are_safe(self):
        db = Mock()
        db.get.return_value = None
        ctx = report_context(db, self.user(c_first_name=None, c_last_name=None, c_email=None))
        self.assertEqual((ctx.company, ctx.printed_by), (APP_NAME, "Unknown user"))
        self.assertEqual(report_context(db).printed_by, "Unknown user")

    def test_anonymous_context_does_not_read_db(self):
        request = SimpleNamespace(cookies={})
        with patch("backend.services.reporting.PlatformSessionLocal") as factory:
            self.assertEqual(request_report_context(request).printed_by, "Unknown user")
            factory.assert_not_called()

    def test_context_is_escaped_and_applied_only_once(self):
        context = ReportPrintContext('</style>"', 'User " \\ Name', "UTC")
        html = apply_print_layout("<html><head></head><body>Report</body></html>", context)
        self.assertNotIn(context.company, html)
        self.assertIn(r"\3c /style>", html)
        self.assertEqual(html.count('id="awsum-report-context"'), 1)
        self.assertEqual(apply_print_layout(html, context), html)
        self.assertIn('counter(page) " of " counter(pages)', html)

    def test_table_renderer_has_no_local_footer_or_raw_user_html(self):
        html = table_report_html("<Report>", ["Name"], [['<img src=x onerror="bad">']])
        self.assertIn("&lt;Report&gt;", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("counter(page)", html)
        self.assertNotIn("Page 1", html)


class ReportMiddlewareTests(unittest.TestCase):
    def response(self, content_type, body, *, status=200, path="/future/sales/report"):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": status, "headers": [
                (b"content-type", content_type), (b"content-length", str(len(body)).encode()),
            ]})
            split = len(body) // 2
            await send({"type": "http.response.body", "body": body[:split], "more_body": True})
            await send({"type": "http.response.body", "body": body[split:]})

        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        ctx = ReportPrintContext("Company", "User", "2026-10-29 UTC")
        with patch("backend.services.reporting.request_report_context", return_value=ctx) as resolver:
            asyncio.run(GlobalReportMiddleware(app)(
                {"type": "http", "path": path, "headers": [], "method": "GET",
                 "scheme": "http", "server": ("localhost", 8000), "query_string": b""},
                receive, send,
            ))
        return messages, resolver

    def test_future_raw_html_automatically_inherits_layout(self):
        msgs, resolver = self.response(b"text/html; charset=utf-8", b"<html><head></head><body>Report</body></html>")
        self.assertEqual(resolver.call_count, 1)
        self.assertIn(b'awsum-report-context', msgs[1]["body"])
        self.assertEqual(int(dict(msgs[0]["headers"])[b"content-length"]), len(msgs[1]["body"]))
        self.assertIn(b"no-store", dict(msgs[0]["headers"])[b"cache-control"])
        self.assertIn(b"Cookie", dict(msgs[0]["headers"])[b"vary"])

    def test_binary_csv_json_errors_and_static_files_unchanged(self):
        for content_type, path, status in (
            (b"application/pdf", "/pdf", 200), (b"text/csv", "/csv", 200),
            (b"application/json", "/api", 200), (b"text/html", "/error", 403),
            (b"text/html", "/static/report.html", 200),
            (b"application/vnd.ms-excel", "/binary.xls", 200),
        ):
            with self.subTest(content_type=content_type, path=path):
                msgs, resolver = self.response(content_type, b"unchanged", path=path, status=status)
                self.assertEqual(b"".join(m.get("body", b"") for m in msgs), b"unchanged")
                if content_type != b"application/vnd.ms-excel":
                    resolver.assert_not_called()

    def test_html_excel_gets_native_office_page_counter_not_browser_scripts(self):
        msgs, _ = self.response(b"application/vnd.ms-excel", b"<html><head></head><body>Sheet</body></html>")
        self.assertIn(b"mso-footer-data", msgs[1]["body"])
        self.assertIn(b"Page &P of &N", msgs[1]["body"])
        self.assertNotIn(b"<script", msgs[1]["body"])


class ReportPdfTests(unittest.TestCase):
    def test_existing_contract_generator_uses_global_context(self):
        from backend.routers.platform_store import _build_contract_pdf_bytes

        contract = SimpleNamespace(
            i_contract_id=1, i_subscription_id=2, i_merchant_id=3, i_store_id=4,
            c_status="active", dt_contract_start_date=None, dt_contract_end_date=None,
            n_setup_fee=0, n_monthly_base_fee=0, n_monthly_device_fee=0, n_monthly_user_fee=0,
            n_monthly_total_fee=0, n_tax_rate=0, n_tax_amount=0, n_total_monthly_fee=0,
        )
        data = _build_contract_pdf_bytes(contract, ReportPrintContext("Context Company", "Context User", "UTC"))
        text = PdfReader(BytesIO(data)).pages[0].extract_text()
        self.assertIn("Context Company", text)
        self.assertIn("Printed by: Context User", text)
        self.assertIn("Page 1 of 1", text)

    def test_all_pages_have_real_total_and_authenticated_footer(self):
        for pagesize in (A4, landscape(A4)):
            with self.subTest(pagesize=pagesize):
                output = BytesIO()
                ctx = ReportPrintContext("Company LLC", "Shane Lee", "2026-10-29 06:00 UTC")
                canvas = GlobalReportCanvas(output, pagesize=pagesize, report_context=ctx)
                for n in range(3):
                    canvas.drawString(40, 100, f"Report content {n + 1}")
                    if n < 2:
                        canvas.showPage()
                canvas.save()
                pdf = PdfReader(output)
                self.assertEqual(len(pdf.pages), 3)
                for n, page in enumerate(pdf.pages, 1):
                    text = page.extract_text()
                    self.assertIn("Company LLC", text)
                    self.assertIn(f"Page {n} of 3", text)
                    self.assertIn("Printed by: Shane Lee", text)
                    self.assertIn("Printed: 2026-10-29 06:00 UTC", text)
                    separator = []
                    footer_bounds = []

                    def collect_line(operator, args, cm, tm):
                        if operator == b"m" and float(args[1]) < 40:
                            separator.append(float(args[1]))

                    def collect_text(value, cm, tm, font, size):
                        if value.strip() and tm[5] < 40:
                            from reportlab.pdfbase import pdfmetrics
                            ascent = pdfmetrics.getAscent(report_font()) * size / 1000
                            descent = pdfmetrics.getDescent(report_font()) * size / 1000
                            footer_bounds.append((tm[5] + ascent, tm[5] + descent))

                    page.extract_text(visitor_operand_before=collect_line, visitor_text=collect_text)
                    self.assertEqual(len(separator), 1)
                    self.assertEqual(len(footer_bounds), 3)
                    for top, bottom in footer_bounds:
                        self.assertAlmostEqual(separator[0] - top, 3.75, places=3)
                        self.assertGreater(bottom, 10)
                        self.assertLess(bottom, 14)

    def test_unicode_long_names_are_clipped_to_their_own_column(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        font = report_font()
        for name in ("An extremely long organization " * 20, "\ud68c\uc0ac \ud55c\uae00 " * 30):
            short = fit_footer_text(name, font, 8, 150)
            self.assertLessEqual(stringWidth(short, font, 8), 150)
            self.assertTrue(short.endswith("..."))

    def test_explicit_final_showpage_does_not_add_blank_page(self):
        output = BytesIO()
        canvas = GlobalReportCanvas(output, report_context=ReportPrintContext(APP_NAME, "Unknown user", "UTC"))
        canvas.drawString(40, 100, "Content")
        canvas.showPage()
        canvas.save()
        self.assertEqual(len(PdfReader(output).pages), 1)


if __name__ == "__main__":
    unittest.main()
