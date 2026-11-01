"""AWSUM standard email presentation, shared by preview and MIME delivery."""
import base64
import html
import re
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any


DEFAULT_COMPANY_NAME = "AwsumSolution"
PRIMARY_BLUE = "#1263EB"
NAVY = "#102A43"
PAGE_BG = "#F4F7FB"
BORDER = "#E2E8F0"
FONT_STACK = "Arial,Helvetica,sans-serif"
MONO_STACK = "Consolas,'Courier New',monospace"


class AwsumEmailLayout:
    def __init__(self, company: dict[str, Any] | None = None):
        self.company = company or {}

    def information(self, values: list[tuple[str, str]]) -> str:
        cells = "".join(
            f'<div style="display:inline-block;vertical-align:top;padding:0 20px 8px 0;font-family:{FONT_STACK};'
            f'font-size:13px;color:#64748B;">{html.escape(label)}<br>'
            f'<span style="font-size:15px;font-weight:bold;color:{NAVY};">{html.escape(value)}</span></div>'
            for label, value in values
        )
        return f'<tr><td style="padding:12px 32px 4px 32px;">{cells}</td></tr>'

    def paragraph(self, text: str, *, title: str | None = None) -> str:
        value = html.escape(text).replace("\r\n", "\n").replace("\n", "<br>")
        if title is not None:
            return (
                f'<tr><td style="padding:8px 32px 28px 32px;font-family:{FONT_STACK};font-size:14px;line-height:1.6;color:#334155;">'
                f'<strong style="color:{NAVY};">{html.escape(title)}</strong><br>{value}</td></tr>'
            )
        return (
            f'<tr><td style="padding:8px 32px 16px 32px;font-family:{FONT_STACK};font-size:15px;line-height:1.6;color:#334155;">'
            f'{value}</td></tr>'
        )

    def information_box(
        self, title: str, values: list[tuple[str, str]], *, monospace_labels: tuple[str, ...] = (),
    ) -> str:
        if not values:
            return ""
        body = "".join(
            f'<tr><td style="padding:4px 0;font-family:{FONT_STACK};font-size:13px;color:#64748B;width:150px;">{html.escape(label)}</td>'
            f'<td style="padding:4px 0;font-family:{MONO_STACK if label in monospace_labels else FONT_STACK};font-size:14px;'
            f'font-weight:bold;color:{NAVY};word-break:break-all;">{html.escape(value)}</td></tr>'
            for label, value in values
        )
        return (
            f'<tr><td style="padding:8px 32px 8px 32px;">'
            f'<div style="font-family:{FONT_STACK};font-size:12px;font-weight:bold;letter-spacing:0.8px;color:{PRIMARY_BLUE};padding:0 0 6px 0;">{html.escape(title)}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border:1px solid {BORDER};border-radius:8px;background:#F8FAFC;">'
            f'<tr><td style="padding:10px 16px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'{body}</table></td></tr></table></td></tr>'
        )

    def section_title(self, title: str) -> str:
        return (
            f'<tr><td style="padding:8px 32px 6px 32px;font-family:{FONT_STACK};font-size:12px;font-weight:bold;'
            f'letter-spacing:0.8px;color:{PRIMARY_BLUE};">{html.escape(title)}</td></tr>'
        )

    def key_cards(self, values: list[tuple[str, str, str]]) -> str:
        cards = []
        for name, tag, value in values:
            cards.append(
                f'<tr><td style="padding:0 0 12px 0;">'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                f'style="border:1px solid {BORDER};border-radius:8px;background:#FFFFFF;">'
                f'<tr><td style="padding:14px 16px 6px 16px;font-family:{FONT_STACK};font-size:15px;font-weight:bold;color:{NAVY};">'
                f'{html.escape(name)}'
                f'&nbsp;&nbsp;<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:#E7F0FE;'
                f'color:{PRIMARY_BLUE};font-size:11px;font-weight:bold;letter-spacing:0.5px;">{html.escape(tag)}</span></td></tr>'
                f'<tr><td style="padding:0 16px 16px 16px;">'
                f'<div style="background:#EAF2FF;border:1px solid #C9DDFB;border-radius:6px;padding:10px 12px;'
                f'font-family:{MONO_STACK};font-size:14px;line-height:1.5;color:{NAVY};word-break:break-all;'
                f'word-wrap:break-word;">{html.escape(value)}</div></td></tr>'
                f'</table></td></tr>'
            )
        return (
            f'<tr><td style="padding:0 32px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'{"".join(cards)}</table></td></tr>'
        )

    def button(self, label: str, url: str) -> str:
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise ValueError("Email button URL must use HTTP or HTTPS.")
        return (
            '<tr><td style="padding:16px 32px 24px 32px;">'
            f'<a href="{html.escape(url, quote=True)}" style="display:inline-block;background:{PRIMARY_BLUE};'
            f'color:#FFFFFF;font-family:{FONT_STACK};font-size:15px;font-weight:bold;text-decoration:none;'
            f'padding:12px 24px;border-radius:8px;">{html.escape(label)}</a></td></tr>'
        )

    def render(self, *, title: str, content: list[str], context: str | None = None, logo_src: str | None = None) -> str:
        name = html.escape(self.company.get("name") or DEFAULT_COMPANY_NAME)
        if logo_src:
            header = (
                f'<img src="{html.escape(logo_src, quote=True)}" alt="{name}" width="160" '
                'style="display:block;border:0;outline:none;height:auto;max-width:160px;">'
            )
        else:
            header = f'<span style="font-family:{FONT_STACK};font-size:20px;font-weight:bold;color:{NAVY};">{name}</span>'
        footer_bits = [f'<strong style="color:{NAVY};">{name}</strong>']
        website = self.company.get("website") or ""
        if website:
            footer_bits.append(
                f'<a href="{html.escape(website, quote=True)}" style="color:{PRIMARY_BLUE};text-decoration:none;">{html.escape(website)}</a>'
                if re.match(r"^https?://", website, re.IGNORECASE) else html.escape(website)
            )
        contact = self.company.get("contact_email") or ""
        if contact:
            footer_bits.append(
                f'<a href="mailto:{html.escape(contact, quote=True)}" style="color:{PRIMARY_BLUE};text-decoration:none;">{html.escape(contact)}</a>'
            )
        subtitle = (
            f'<tr><td style="padding:8px 32px;font-family:{FONT_STACK};font-size:14px;color:#64748B;">'
            f'{html.escape(context)}</td></tr>'
        ) if context else ""
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{name}</title></head>'
            f'<body style="margin:0;padding:0;background:{PAGE_BG};">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE_BG};">'
            '<tr><td align="center" style="padding:32px 12px;">'
            f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;max-width:600px;background:#FFFFFF;border:1px solid {BORDER};border-radius:12px;">'
            f'<tr><td style="padding:28px 32px 8px 32px;">{header}</td></tr>'
            f'<tr><td style="padding:12px 32px 4px 32px;font-family:{FONT_STACK};font-size:24px;font-weight:bold;color:{NAVY};">'
            f'{html.escape(title)}</td></tr>{subtitle}{"".join(content)}'
            f'<tr><td style="padding:18px 32px;border-top:1px solid {BORDER};font-family:{FONT_STACK};font-size:12px;'
            f'line-height:1.6;color:#64748B;">{" &nbsp;·&nbsp; ".join(footer_bits)}</td></tr>'
            '</table></td></tr></table></body></html>'
        )

    def preview(self, *, title: str, content: list[str], context: str | None = None) -> str:
        logo_src = None
        if self.company.get("logo_bytes") and self.company.get("logo_subtype"):
            logo_src = f"data:image/{self.company['logo_subtype']};base64," + base64.b64encode(
                self.company["logo_bytes"]
            ).decode("ascii")
        return self.render(title=title, content=content, context=context, logo_src=logo_src)

    def build_message(
        self, *, recipient: str, subject: str, plain_text: str, title: str, content: list[str],
        context: str | None = None, sender: str | None = None,
    ) -> EmailMessage:
        message = EmailMessage()
        if sender:
            message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = make_msgid()
        message.set_content(plain_text)
        logo_bytes = self.company.get("logo_bytes")
        logo_cid = make_msgid(domain="logo.awsumsolution") if logo_bytes and self.company.get("logo_subtype") else None
        message.add_alternative(self.render(
            title=title, content=content, context=context,
            logo_src=f"cid:{logo_cid[1:-1]}" if logo_cid else None,
        ), subtype="html")
        if logo_cid:
            message.get_payload()[1].add_related(
                logo_bytes, "image", self.company["logo_subtype"], cid=logo_cid,
                filename="logo." + self.company["logo_subtype"], disposition="inline",
            )
        return message

    @staticmethod
    def preview_message(message: EmailMessage) -> str:
        """Use the actual MIME HTML; only embedded image transport differs in a browser."""
        body = message.get_body(preferencelist=("html",)).get_content()
        for part in message.walk():
            if part.get_content_maintype() == "image" and part["Content-ID"]:
                cid = str(part["Content-ID"]).strip("<>")
                source = f"data:{part.get_content_type()};base64," + base64.b64encode(part.get_content()).decode("ascii")
                body = body.replace(f"cid:{cid}", source)
        return body
