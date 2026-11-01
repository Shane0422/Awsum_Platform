"""Shared monetary presentation; never use formatted strings as API/DB values."""
from decimal import Decimal, ROUND_HALF_UP


def format_money(value: Decimal | int | float | str | None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        raise ValueError("A monetary amount must be numeric.")
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("A monetary amount must be finite.")
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    return format(rounded, ",.2f")
