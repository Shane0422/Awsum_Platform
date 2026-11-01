import re


EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class EmailConfigurationError(Exception):
    pass


class MailDeliveryError(Exception):
    pass


def validate_email_address(value: object) -> str:
    address = str(value or "").strip()
    if len(address) > 254 or not EMAIL_RE.fullmatch(address):
        raise ValueError("Enter a valid email address.")
    return address
