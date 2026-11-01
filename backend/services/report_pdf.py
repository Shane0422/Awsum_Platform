"""Use GlobalReportCanvas for every server-generated PDF."""
import os
from functools import lru_cache
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from backend.services.reporting import ReportPrintContext


@lru_cache(maxsize=1)
def report_font() -> str:
    windows_font = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "malgun.ttf"
    if windows_font.is_file():
        pdfmetrics.registerFont(TTFont("AwsumReport", str(windows_font)))
        return "AwsumReport"
    # Built-in Korean CID font, without downloading fonts or touching data folders.
    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    return "HYSMyeongJo-Medium"


def fit_footer_text(text: str, font: str, size: int, width: float) -> str:
    if pdfmetrics.stringWidth(text, font, size) <= width:
        return text
    while text and pdfmetrics.stringWidth(text + "...", font, size) > width:
        text = text[:-1]
    return text + "..."


class GlobalReportCanvas(Canvas):
    """Two-pass canvas: footer totals are real, not a guessed '1 / 1'."""

    def __init__(self, *args, report_context: ReportPrintContext, **kwargs):
        super().__init__(*args, **kwargs)
        self.report_context = report_context
        self._report_pages = []

    def showPage(self):
        self._report_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        if self._code:
            self.showPage()
        pages = self._report_pages
        for state in pages:
            self.__dict__.update(state)
            self._draw_report_footer(len(pages))
            super().showPage()
        super().save()

    def _draw_report_footer(self, total: int) -> None:
        width, height = self._pagesize
        margin = 36
        usable = width - 2 * margin
        font = report_font()
        baseline = 14
        separator = baseline + pdfmetrics.getAscent(font) * 8 / 1000 + 3.75
        self.saveState()
        self.setStrokeColorRGB(0.82, 0.84, 0.86)
        self.setLineWidth(0.5)
        self.line(margin, separator, width - margin, separator)
        self.setFillColorRGB(0.28, 0.33, 0.41)
        self.setFont(font, 8)
        self.drawString(margin, baseline, fit_footer_text(self.report_context.company, font, 8, usable * .30))
        self.drawCentredString(width / 2, baseline, f"Page {self._pageNumber} of {total}")
        self.drawRightString(
            width - margin, baseline,
            fit_footer_text("Printed by: " + self.report_context.printed_by, font, 8, usable * .30),
        )
        self.setFont(font, 7)
        self.drawRightString(width - margin, height - 23, "Printed: " + self.report_context.printed_at)
        self.restoreState()
