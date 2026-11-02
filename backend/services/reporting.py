"""Global authenticated print context and HTML/PDF layout infrastructure."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from sqlalchemy.orm import Session

from backend.config.settings import APP_NAME
from backend.database.pg_platform import ADMIN_STORE_ID, PlatformSessionLocal
from backend.models_admin.merchant import Merchant
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.store import Store
from backend.models_admin.system_settings import SystemSettings


def _name(value: str | None) -> str:
    return " ".join((value or "").split())


@dataclass(frozen=True)
class ReportPrintContext:
    company: str
    printed_by: str
    printed_at: str


def report_context(db: Session, user: PlatformUser | None = None) -> ReportPrintContext:
    company = APP_NAME
    printed_by = "Unknown user"
    if user is not None:
        printed_by = (
            _name(f"{user.c_first_name or ''} {user.c_last_name or ''}")
            or _name(user.c_email)
            or printed_by
        )
        if user.i_store_id == ADMIN_STORE_ID:
            settings = db.get(SystemSettings, 1)
            if settings:
                company = _name(settings.c_company_name) or _name(settings.c_display_name) or company
        else:
            store = db.get(Store, user.i_store_id)
            if store:
                merchant = db.get(Merchant, store.i_merchant_id) if store.i_merchant_id else None
                company = (
                    _name(merchant.c_merchant_name if merchant else None)
                    or _name(store.c_store_name)
                    or company
                )
    return ReportPrintContext(company, printed_by, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


def request_report_context(request: Request) -> ReportPrintContext:
    # Reuse signature/expiry validation and load the user from the DB, not display
    # names or tenant IDs supplied by a report payload.
    from backend.routers.auth import _get_authenticated_user

    if not request.cookies.get("access_token"):
        return ReportPrintContext(APP_NAME, "Unknown user", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    with PlatformSessionLocal() as db:
        user = _get_authenticated_user(request, db)
        if user is not None and (user.c_status or "").lower() != "active":
            user = None
        return report_context(db, user)


def _css_string(value: str) -> str:
    # JSON strings are valid CSS strings after escaping '<' to protect </style>.
    return json.dumps(value, ensure_ascii=False).replace("<", r"\3c ")


def print_assets(context: ReportPrintContext) -> str:
    from backend.services.report_pdf import fit_footer_text, report_font

    # Chromium page-margin boxes do not reliably implement text-overflow.
    # Bound text before rendering; 42mm fits each side column on A4/Letter.
    font = report_font()
    company = fit_footer_text(context.company, font, 8, 119)
    printed_by = fit_footer_text("Printed by: " + context.printed_by, font, 8, 119)
    return (
        '<link rel="stylesheet" href="/static/css/report-print.css?v=2">'
        '<style id="awsum-report-context">'
        '@page {'
        f' @bottom-left {{ content: {_css_string(company)}; }}'
        ' @bottom-center { content: "Page " counter(page) " of " counter(pages); }'
        f' @bottom-right {{ content: {_css_string(printed_by)}; }}'
        f' @top-right {{ content: {_css_string("Printed: " + context.printed_at)}; }}'
        '}</style>'
        f'{spreadsheet_print_style(context)}'
        '<script src="/static/js/report-print.js?v=2" defer></script>'
    )


def spreadsheet_print_style(context: ReportPrintContext) -> str:
    from backend.services.report_pdf import fit_footer_text, report_font

    font = report_font()
    company = fit_footer_text(context.company, font, 8, 119).replace("&", "&&")
    user = fit_footer_text("Printed by: " + context.printed_by, font, 8, 119).replace("&", "&&")
    footer = f"&L{company}&CPage &P of &N&R{user}"
    return (
        '<style id="awsum-spreadsheet-print">@page {'
        f"mso-footer-data: {_css_string(footer)};"
        f"mso-header-data: {_css_string('&RPrinted: ' + context.printed_at)};"
        "margin: 14mm 12mm 18mm; mso-footer-margin: 6mm;"
        "}</style>"
    )


def apply_print_layout(html: str, context: ReportPrintContext) -> str:
    if 'id="awsum-report-context"' in html:
        return html
    assets = print_assets(context)
    if re.search(r"</head\s*>", html, re.IGNORECASE):
        return re.sub(r"</head\s*>", lambda _: assets + "</head>", html, count=1, flags=re.IGNORECASE)
    return assets + html


def table_report_html(title: str, headers: list[str], rows: list[list[str]]) -> str:
    """Content only: global middleware supplies identity, margins and footers."""
    head = "".join(f"<th>{escape(str(value))}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        '<!doctype html><html><head><meta charset="UTF-8">'
        f"<title>{escape(title)}</title>"
        '<style>@page { size: landscape; }</style></head>'
        '<body class="awsum-report" data-report-auto-print>'
        f"<h1>{escape(title)}</h1><p>Total Records: {len(rows)}</p>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></body></html>"
    )


class GlobalReportMiddleware:
    """All current/future HTML responses inherit the print layout, even raw HTML."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith("/static/"):
            await self.app(scope, receive, send)
            return
        start: Message | None = None
        chunks: list[bytes] = []

        async def send_with_layout(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                headers = dict(message["headers"])
                if (
                    message["status"] == 200
                    and headers.get(b"content-type", b"").startswith((b"text/html", b"application/vnd.ms-excel"))
                    and b"content-encoding" not in headers
                ):
                    start = message
                    return
            if start is not None and message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                context = await run_in_threadpool(request_report_context, Request(scope))
                raw = b"".join(chunks)
                is_excel = dict(start["headers"]).get(b"content-type", b"").startswith(b"application/vnd.ms-excel")
                if is_excel:
                    if raw.lstrip().startswith(b"<"):
                        html = raw.decode("utf-8")
                        assets = spreadsheet_print_style(context)
                        html = re.sub(r"</head\s*>", lambda _: assets + "</head>", html, count=1, flags=re.IGNORECASE)
                        raw = html.encode("utf-8")
                else:
                    raw = apply_print_layout(raw.decode("utf-8"), context).encode("utf-8")
                vary = dict(start["headers"]).get(b"vary", b"")
                if b"cookie" not in [part.strip().lower() for part in vary.split(b",")]:
                    vary = vary + b", Cookie" if vary else b"Cookie"
                start["headers"] = [
                    (key, value) for key, value in start["headers"]
                    if key.lower() not in (
                        b"content-length", b"etag", b"cache-control", b"pragma", b"expires", b"vary",
                    )
                ]
                start["headers"].extend([
                    (b"content-length", str(len(raw)).encode("ascii")),
                    (b"cache-control", b"private, no-store, no-cache, must-revalidate, max-age=0"),
                    (b"pragma", b"no-cache"), (b"expires", b"0"), (b"vary", vary),
                ])
                await send(start)
                await send({**message, "body": raw})
                return
            await send(message)

        await self.app(scope, receive, send_with_layout)
