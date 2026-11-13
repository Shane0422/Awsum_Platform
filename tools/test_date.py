"""AWSUM Common Date standard: server helpers (backend.services.dates) and the browser component (static/js/date.js,
node tests in tools/test_date.cjs). Screens show MM-DD-YYYY; the DB keeps DATE and the API keeps ISO."""
import shutil
import subprocess
import unittest
from datetime import date, datetime
from pathlib import Path

from backend.services.dates import DATE_FORMAT, format_date, parse_date

ROOT = Path(__file__).resolve().parents[1]


class CommonDateTests(unittest.TestCase):
    def test_format_is_mm_dd_yyyy(self):
        self.assertEqual(DATE_FORMAT, "MM-DD-YYYY")
        self.assertEqual(format_date(date(1961, 4, 22)), "04-22-1961")
        self.assertEqual(format_date(datetime(2026, 11, 13, 21, 6)), "11-13-2026")
        self.assertEqual(format_date(None), "")

    def test_parse_accepts_iso_mm_dd_yyyy_and_the_earlier_slashes(self):
        for text in ("1961-04-22", "04-22-1961", "4-22-1961", "04/22/1961", "4/22/1961", "1961-04-22T10:00:00",
                     date(1961, 4, 22), datetime(1961, 4, 22, 8, 0)):
            with self.subTest(text=text):
                self.assertEqual(parse_date(text), date(1961, 4, 22))
        for empty in ("", None, "   "):
            self.assertIsNone(parse_date(empty))
        for bad in ("02-30-2024", "13-01-2020", "1961/04/22", "22-04-1961", "abc", "04-22-61"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_date(bad)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_browser_component(self):
        result = subprocess.run(["node", "--test", str(ROOT / "tools" / "test_date.cjs")], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-2000:])

    def test_component_is_loaded_once_by_the_platform_shell(self):
        base = (ROOT / "templates" / "platform" / "platform_base.html").read_text(encoding="utf-8")
        self.assertEqual(base.count('src="/static/js/date.js'), 1)
        self.assertEqual(base.count('href="/static/css/awsum-date.css'), 1)
        js = (ROOT / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
        self.assertNotIn('type="date"', js)  # Employee dates use the common component, never the native input
        self.assertNotRegex(js, r"input\('[a-z_]+', 'date'")  # no input(name, 'date') native control helper call


if __name__ == "__main__":
    unittest.main()
