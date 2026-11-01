import html
import logging
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from sqlalchemy.orm import Session

from backend.services import images
from backend.services.email_layout import AwsumEmailLayout, DEFAULT_COMPANY_NAME
from backend.services.system_settings import get_or_create_settings, send_configured_email


MAX_LOGO_BYTES = 2 * 1024 * 1024
ACTIVATION_GUIDE = (
    "On site, install the AwsumSolution application on the assigned device and "
    "enter its license key in the activation screen."
)


def validate_email_content(subject: object, message: object) -> tuple[str, str]:
    clean_subject = str(subject or "").strip()
    clean_message = str(message or "").strip()
    if not clean_subject or len(clean_subject) > 255 or "\r" in clean_subject or "\n" in clean_subject:
        raise ValueError("Subject is required and must be 255 characters or fewer.")
    if not clean_message or len(clean_message) > 10000:
        raise ValueError("Message is required and must be 10,000 characters or fewer.")
    return clean_subject, clean_message


def _image_subtype(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def load_company_branding(db: Session) -> dict[str, Any]:
    """Resolve the same local image used by web branding; never fetch arbitrary URLs."""
    row = get_or_create_settings(db)
    name = ((row.c_display_name or row.c_company_name) if row else "") or DEFAULT_COMPANY_NAME
    branding: dict[str, Any] = {
        "name": name.strip() or DEFAULT_COMPANY_NAME,
        "website": (row.c_website or "").strip() if row else "",
        "contact_email": (row.c_contact_email or "").strip() if row else "",
        "logo_bytes": None,
        "logo_subtype": None,
    }
    logo_url = images.platform_logo_url(row.c_logo_url if row else None)
    try:
        candidate = images.resolve_image_file(logo_url)
        if candidate is not None and candidate.stat().st_size <= MAX_LOGO_BYTES:
            data = candidate.read_bytes()
            subtype = _image_subtype(data)
            if subtype:
                branding["logo_bytes"], branding["logo_subtype"] = data, subtype
    except OSError:
        logging.getLogger(__name__).exception("Configured company email logo could not be read.")
    return branding


def _store_license_rows(store_info: dict[str, Any]) -> list[tuple[str, str]]:
    store_license = store_info.get("store_license")
    if not store_license:
        return []
    return [
        ("Store License Key", str(store_license.get("key") or "\u2014")),
        ("Status", str(store_license.get("status") or "\u2014")),
        ("Valid From", str(store_license.get("valid_from") or "\u2014")),
        ("Valid Until", str(store_license.get("valid_until") or "\u2014")),
        ("Device Licenses", str(store_license.get("device_licenses") or "\u2014")),
    ]


def _message_text(
    store_info: dict[str, Any],
    licenses: list[dict[str, Any]],
    custom_message: str,
    company: dict[str, Any] | None = None,
) -> str:
    lines = [
        "Your License Information",
        "",
        f"Merchant Name: {store_info['merchant_name']}",
        f"Store Name: {store_info['store_name']}",
        f"Agency Name: {store_info['agency_name']}",
        "",
        custom_message,
        "",
    ]
    store_license_rows = _store_license_rows(store_info)
    if store_license_rows:
        lines.append("STORE LICENSE")
        lines.extend(f"{label + ':':<19}{value}" for label, value in store_license_rows)
        lines.append("")
    lines.append("Device licenses:")
    for license_info in licenses:
        lines.extend((
            f"- Name: {license_info['name']}",
            f"  Type: {license_info['type']}",
            f"  License Key: {license_info['license_key']}",
        ))
    lines.extend(("", ACTIVATION_GUIDE))
    if company:
        footer = [company.get("name") or DEFAULT_COMPANY_NAME]
        footer += [value for value in (company.get("website"), company.get("contact_email")) if value]
        lines.extend(("", "--", *footer))
    return "\n".join(lines)


def _license_content(
    layout: AwsumEmailLayout, store_info: dict[str, Any], licenses: list[dict[str, Any]], custom_message: str,
) -> list[str]:
    return [
        layout.information([
            ("Merchant", store_info["merchant_name"]), ("Store", store_info["store_name"]),
            ("Agency", store_info["agency_name"]),
        ]),
        layout.paragraph(custom_message),
        layout.information_box("STORE LICENSE", _store_license_rows(store_info), monospace_labels=("Store License Key",)),
        layout.section_title("DEVICE LICENSES"),
        layout.key_cards([(str(item["name"]), str(item["type"]), str(item["license_key"])) for item in licenses]),
        layout.paragraph(ACTIVATION_GUIDE, title="Activation"),
    ]


def render_license_email_html(
    *,
    store_info: dict[str, Any],
    licenses: list[dict[str, Any]],
    custom_message: str,
    company: dict[str, Any] | None,
    logo_src: str | None,
) -> str:
    layout = AwsumEmailLayout(company)
    return layout.render(
        title="Your License Information", content=_license_content(layout, store_info, licenses, custom_message),
        logo_src=logo_src,
    )


def render_license_email_preview(
    *,
    store_info: dict[str, Any],
    licenses: list[dict[str, Any]],
    custom_message: str,
    company: dict[str, Any] | None,
) -> str:
    layout = AwsumEmailLayout(company)
    return layout.preview(
        title="Your License Information", content=_license_content(layout, store_info, licenses, custom_message),
    )


def build_license_email(
    *,
    recipient: str,
    subject: str,
    custom_message: str,
    store_info: dict[str, Any],
    licenses: list[dict[str, Any]],
    sender: str | None,
    company: dict[str, Any] | None = None,
) -> EmailMessage:
    layout = AwsumEmailLayout(company)
    return layout.build_message(
        recipient=recipient, subject=subject, sender=sender,
        plain_text=_message_text(store_info, licenses, custom_message, company),
        title="Your License Information", content=_license_content(layout, store_info, licenses, custom_message),
    )


def build_company_email(
    *,
    recipient: str,
    subject: str,
    custom_message: str,
    company_name: str,
    logo_url: str | None,
) -> EmailMessage:
    clean_subject, clean_message = validate_email_content(subject, custom_message)
    safe_company = html.escape(company_name)
    text_body = f"{company_name}\n\n{clean_message}"
    logo_markup = (
        f'<img src="{html.escape(logo_url, quote=True)}" alt="{safe_company}" '
        'style="display:block;max-width:220px;max-height:80px;margin-bottom:20px">'
        if logo_url else ""
    )
    html_body = (
        "<html><body style=\"font-family:Arial,sans-serif;color:#222\">"
        f"{logo_markup}<h2>{safe_company}</h2>"
        f"<p>{html.escape(clean_message).replace(chr(10), '<br>')}</p>"
        "</body></html>"
    )
    email = EmailMessage()
    email["To"] = recipient
    email["Subject"] = clean_subject
    email["Message-ID"] = make_msgid()
    email.set_content(text_body)
    email.add_alternative(html_body, subtype="html")
    return email


build_system_test_email = build_company_email


def send_license_email(message: EmailMessage, db: Session) -> tuple[str, str]:
    return send_configured_email(db, message)
