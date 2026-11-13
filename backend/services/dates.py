"""AWSUM Common Date standard (server side of static/js/date.js, AwsumDateInput).

Screens, exports and messages show MM-DD-YYYY (04-22-1961); the DB keeps DATE and the API keeps ISO YYYY-MM-DD.
parse_date accepts ISO (the API contract), MM-DD-YYYY and the earlier MM/DD/YYYY (older clients / screens).
Screens not moved to the standard yet keep their own MM/DD/YYYY output until their own step.
"""
from __future__ import annotations

from datetime import date, datetime

DATE_FORMAT = "MM-DD-YYYY"
_PARSE_FORMATS = ("%Y-%m-%d", "%m-%d-%Y", "%m/%d/%Y")


def format_date(value: date | datetime | None) -> str:
    """MM-DD-YYYY for screens / exports / messages; '' for None."""
    return value.strftime("%m-%d-%Y") if value else ""


def parse_date(value) -> date | None:
    """A date from the API / screen text; None when empty. Raises ValueError for anything else."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _PARSE_FORMATS:  # ISO may carry a time part (only its date is read, as before)
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Enter the date as {DATE_FORMAT}.")
