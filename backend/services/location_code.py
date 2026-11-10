"""Common Location Code rule (Sales Floor / Warehouse Location).

One rule for every screen and API that takes a Location Code: Category III and MD Master today, and later
Receiving / Transfer / Issue / POS. The browser applies the same rule while typing (static/js/code-input.js,
AwsumCodeInput); the server is the authority and always applies it again when saving.

- Leading / trailing spaces are removed; blank means "not set" (None -> stored as NULL).
- Letters are upper-cased (k01-s01 -> K01-S01). Digits, hyphens and every other character allowed so far
  are kept as they are (no new character restriction).
- At most LOCATION_CODE_MAX characters (the size of the c_*_location columns).
"""

LOCATION_CODE_MAX = 50


def normalize(value) -> str | None:
    """Trimmed, upper-cased Location Code; None when blank."""
    text_value = "" if value is None else str(value).strip().upper()
    return text_value or None


def length_error(value: str | None) -> str | None:
    """Validation message when a normalized code is too long, else None."""
    if value is not None and len(value) > LOCATION_CODE_MAX:
        return f"Must be {LOCATION_CODE_MAX} characters or fewer."
    return None
