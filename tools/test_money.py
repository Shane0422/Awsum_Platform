from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backend.services.money import format_money


class MoneyPresentationTests(unittest.TestCase):
    def test_examples_rounding_and_exact_decimal_values(self):
        examples = [
            (2500, "2,500.00"), (2500.5, "2,500.50"), (1000000, "1,000,000.00"),
            (Decimal("9007199254740993.99"), "9,007,199,254,740,993.99"),
            (Decimal("1.005"), "1.01"), (Decimal("-1.005"), "-1.01"),
            (Decimal("-0.001"), "0.00"), (0, "0.00"), (None, ""), ("", ""),
        ]
        for value, expected in examples:
            with self.subTest(value=value):
                self.assertEqual(format_money(value), expected)

    def test_invalid_values_do_not_become_zero(self):
        for value in [True, "NaN", "Infinity", "invalid", "2,500.00"]:
            with self.subTest(value=value), self.assertRaises((ValueError, ArithmeticError)):
                format_money(value)

    def test_master_export_uses_common_presentation_not_api_format(self):
        from backend.services.master_group import _money

        self.assertEqual(_money(Decimal("2500.5")), "2,500.50")
        self.assertEqual(_money(None), "Not set")

    def test_contract_html_and_pdf_use_grouping_without_changing_rate_precision(self):
        from pypdf import PdfReader
        from backend.routers.platform_store import _build_contract_pdf_bytes, _contract_html
        from backend.services.reporting import ReportPrintContext

        contract = SimpleNamespace(
            i_contract_id=1, i_subscription_id=2, i_account_id=3, i_store_id=4,
            i_pricing_plan_id=1, i_contract_term_month=12, c_status="active",
            dt_contract_start_date=None, dt_contract_end_date=None,
            n_setup_fee=Decimal("2500.50"), n_monthly_base_fee=Decimal("1000000"),
            n_monthly_device_fee=0, n_monthly_user_fee=0, n_monthly_total_fee=0,
            n_tax_rate=Decimal("8.1234"), n_tax_amount=0, n_total_monthly_fee=0,
        )
        html = _contract_html(contract)
        pdf = _build_contract_pdf_bytes(contract, ReportPrintContext("Test Company", "Test User", "UTC"))
        for content in [html, PdfReader(BytesIO(pdf)).pages[0].extract_text()]:
            self.assertIn("2,500.50", content)
            self.assertIn("1,000,000.00", content)
            self.assertIn("8.1234", content)

    def test_invoice_html_uses_money_for_amounts_not_quantities(self):
        from backend.routers import platform_store as router
        from backend.models_admin.invoice import Invoice

        invoice = SimpleNamespace(
            i_invoice_id=1, c_invoice_no="TEST", dt_invoice_date=None, dt_due_date=None,
            c_status="issued", i_subscription_id=1, i_account_id=2, i_store_id=3,
            c_currency="USD", n_subtotal=Decimal("2500.50"), n_tax=Decimal("1000"),
            n_total=Decimal("3500.50"),
        )
        line = SimpleNamespace(c_line_type="ITEM", c_description="Test", n_quantity=Decimal("1.2345"),
                               n_unit_price=Decimal("2500.50"), n_amount=Decimal("2500.50"))
        db = Mock()
        invoices, lines = Mock(), Mock()
        invoices.filter.return_value.first.return_value = invoice
        lines.filter.return_value.order_by.return_value.all.return_value = [line]
        db.query.side_effect = lambda model: invoices if model is Invoice else lines
        with patch.object(router, "get_current_user"), patch.object(router, "require_platform_admin"):
            response = router.download_invoice_html(1, Mock(), db)
        html = response.body.decode("utf-8")
        for expected in ["2,500.50", "1,000.00", "3,500.50", "1.2345"]:
            self.assertIn(expected, html)
