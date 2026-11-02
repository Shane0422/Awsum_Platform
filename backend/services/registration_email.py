"""Common organization registration content; presentation belongs to AwsumEmailLayout."""
from email.message import EmailMessage
from typing import Any

from backend.services.email_layout import AwsumEmailLayout


PASSWORD_GUIDANCE = "You may continue using the initial password or change it later from your account settings."


def build_registration_email(
    *, recipient: str, company: dict[str, Any], owner_type: str, name: str, code: str,
    login_email: str, login_url: str, initial_password: str | None,
    must_change_password: bool = False,
) -> EmailMessage:
    if owner_type not in {"AGENCY", "MERCHANT", "STORE"}:
        raise ValueError("A valid registration owner type is required.")
    owner_label = owner_type.title()
    layout = AwsumEmailLayout(company)
    owner_information = [
        (f"{owner_label} Name", name), (f"{owner_label} Code", code), ("Login Email", login_email),
    ]
    account_information = [("Login URL", login_url)]
    if initial_password:
        account_information.append(("Initial Password", initial_password))
    guidance = PASSWORD_GUIDANCE if not must_change_password else \
        "This existing account requires a password change at login. Your password has not been reset."
    lines = [
        "Welcome to AWSUM Platform", f"{owner_label} Registration", "Registration completed", "",
        *(f"{label}: {value}" for label, value in owner_information), "",
        *(f"{label}: {value}" for label, value in account_information), "", guidance,
    ]
    content = [
        layout.paragraph("Registration completed"),
        layout.information_box(f"{owner_type} INFORMATION", owner_information),
        layout.information_box("ACCOUNT INFORMATION", account_information, monospace_labels=("Initial Password",)),
    ]
    if not initial_password:
        instructions = "First-login information: Use your existing password. If unavailable, contact the Platform administrator."
        lines.extend(("", instructions))
        content.append(layout.paragraph(instructions))
    content.extend([layout.paragraph(guidance), layout.button("Sign In", login_url)])
    return layout.build_message(
        recipient=recipient, subject=f"[{company['name']}] {owner_label} Registration completed",
        plain_text="\n".join(lines), title="Welcome to AWSUM Platform", context=f"{owner_label} Registration",
        content=content,
    )
