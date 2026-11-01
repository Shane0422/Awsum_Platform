"""Verify real Chromium pagination (not DOM/CSS presence alone).

Run with AWSUM_REPORT_CHROME pointing to Chrome/Edge when not installed at the
standard Windows Chrome path. Uses an isolated temporary browser profile.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from backend.services.reporting import ReportPrintContext, apply_print_layout


ROOT = Path(__file__).resolve().parents[1]
BROWSER = Path(os.environ.get(
    "AWSUM_REPORT_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe",
))


@unittest.skipUnless(BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to a Chrome/Edge executable.")
class BrowserFooterTests(unittest.TestCase):
    def generate(self, directory: Path, company="Company LLC", user="Shane Lee", landscape=False, paper="A4"):
        sections = "".join(
            f'<section style="break-after:{"page" if n < 3 else "auto"}">Page content {n}</section>'
            for n in range(1, 4)
        )
        content = (
            '<html><head><meta charset="UTF-8">'
            f'<style>@page {{size: {paper} {"landscape" if landscape else "portrait"};}}</style>'
            f"</head><body>{sections}</body></html>"
        )
        html = apply_print_layout(content, ReportPrintContext(company, user, "2026-10-29 06:00 UTC"))
        html = html.replace("/static/css/report-print.css?v=2", (ROOT / "static" / "css" / "report-print.css").as_uri())
        html = html.replace('<script src="/static/js/report-print.js?v=2" defer></script>', "")
        source, target = directory / "report.html", directory / "report.pdf"
        source.write_text(html, encoding="utf-8")
        result = subprocess.run(
            [str(BROWSER), "--headless", "--disable-gpu", "--no-first-run",
             f"--user-data-dir={directory / 'profile'}", "--no-pdf-header-footer",
             f"--print-to-pdf={target}", source.as_uri()],
            capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace")[-2000:])
        self.assertTrue(target.is_file(), result.stderr.decode("utf-8", "replace")[-2000:])
        return PdfReader(target)

    def test_every_printed_page_has_accurate_total_in_both_orientations(self):
        for paper in ("A4", "Letter"):
            for landscape in (False, True):
                with self.subTest(paper=paper, landscape=landscape), tempfile.TemporaryDirectory(prefix="awsum-report-") as temp:
                    pdf = self.generate(Path(temp), landscape=landscape, paper=paper)
                    self.assertEqual(len(pdf.pages), 3)
                    for n, page in enumerate(pdf.pages, 1):
                        self.assertEqual(float(page.mediabox.width) > float(page.mediabox.height), landscape)
                        text = page.extract_text()
                        self.assertIn("Company LLC", text)
                        self.assertIn(f"Page {n} of 3", text)
                        self.assertIn("Printed by: Shane Lee", text)
                        self.assertIn("Printed: 2026-10-29 06:00 UTC", text)
                        self.assert_compact_footer(page)

    def assert_compact_footer(self, page):
        separator_edges = []
        text_bounds = []

        def rectangle(operator, args, cm, tm):
            if operator == b"re":
                x, y, width, height = map(float, args)
                edge = cm[3] * y + cm[5]
                if edge < 40 and width * abs(cm[0]) > 100 and height * abs(cm[3]) < 1:
                    separator_edges.append(edge - height * abs(cm[3]))

        def text(value, cm, tm, font, size):
            baseline = cm[3] * tm[5] + cm[5]
            if not value.strip() or baseline >= 40:
                return
            descriptor_font = font["/DescendantFonts"][0].get_object() if "/DescendantFonts" in font else font
            descriptor = descriptor_font["/FontDescriptor"]
            actual_size = size * abs(cm[0])
            ascent = float(descriptor["/Ascent"]) * actual_size / 1000
            descent = abs(float(descriptor["/Descent"])) * actual_size / 1000
            text_bounds.append((baseline + ascent, baseline - descent))

        page.extract_text(visitor_text=text, visitor_operand_before=rectangle)
        self.assertEqual(len(separator_edges), 3)
        self.assertTrue(text_bounds)
        for top, bottom in text_bounds:
            gap = min(separator_edges) - top
            self.assertGreaterEqual(gap, 3)  # 4px at 96dpi
            self.assertLessEqual(gap, 4.5)  # 6px at 96dpi
            self.assertGreater(bottom, 10)
            self.assertLess(bottom, 14)

    def test_long_unicode_names_do_not_replace_page_counter(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from backend.services.report_pdf import report_font

        with tempfile.TemporaryDirectory(prefix="awsum-report-") as temp:
            pdf = self.generate(Path(temp), company="\ud55c\uae00 \ud68c\uc0ac \uae34 \uc774\ub984 " * 25, user="Long user name " * 30)
            for n, page in enumerate(pdf.pages, 1):
                text = page.extract_text()
                self.assertIn(f"Page {n} of 3", text)
                self.assertIn("Printed by:", text)
                self.assertIn("\ud55c\uae00", text)
                self.assertGreaterEqual(text.count("..."), 2)
                footer_parts = []

                def collect(text, cm, tm, font, size):
                    x = cm[0] * tm[4] + cm[4]
                    y = cm[3] * tm[5] + cm[5]
                    if text.strip() and y < 51:  # inside the reserved 18mm footer
                        actual_size = size * abs(cm[0])
                        footer_parts.append((text, x, y, stringWidth(text, report_font(), actual_size)))

                page.extract_text(visitor_text=collect)
                company = next(p for p in footer_parts if "\ud55c\uae00" in p[0])
                user = next(p for p in footer_parts if "Printed by:" in p[0])
                counter = [p for p in footer_parts if p not in (company, user)]
                self.assertTrue(counter)
                counter_left = min(p[1] for p in counter)
                counter_right = max(p[1] + p[3] for p in counter)
                self.assertLess(company[1] + company[3], counter_left - 8)
                self.assertGreater(user[1], counter_right + 8)
                for _, x, y, width in footer_parts:
                    self.assertGreaterEqual(x, 30)
                    self.assertGreater(y, 10)
                    self.assertLessEqual(x + width, float(page.mediabox.width) - 30)


if __name__ == "__main__":
    unittest.main()
