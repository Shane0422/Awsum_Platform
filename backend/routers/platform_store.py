from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from collections import defaultdict
import re
import secrets
import bcrypt
import logging
import json

from fastapi import APIRouter, Request, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from sqlalchemy import String, cast, or_, text, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, aliased
from typing import Optional
import csv
import io

from backend.database.pg_platform import PlatformSessionLocal, get_next_merchant_store_id
from backend.core.data_paths import PROJECT_ROOT
from backend.models_admin.store import Store
from backend.models_admin.business_type import BusinessType
from backend.models_admin.role import Role
from backend.models_admin.platform_user import PlatformUser, AGENCY_ADMIN_ROLE_NAME
from backend.models_admin.merchant import Merchant
from backend.models_admin.session import SessionTbl
from backend.models_admin.device import Device
from backend.models_admin.device_log import DeviceLog
from backend.models_admin.license import License
from backend.models_admin.agency import Agency
from backend.models_admin.agency_type import AgencyType
from backend.models_admin.payment_method import PaymentMethod
from backend.models_admin.pricing_plan import PricingPlan
from backend.models_admin.invoice import Invoice, InvoiceLine
from backend.models_admin.contract import Contract
from backend.utils.zip_lookup import lookup_zip
from backend.config.templates import templates
from backend.config.settings import APP_NAME

from reportlab.lib.pagesizes import letter
from backend.services.report_pdf import GlobalReportCanvas
from backend.services.reporting import ReportPrintContext, report_context
from backend.services.resource_storage import AwsumStorageService
from backend.services.store_item_features import ITEM_FEATURES, store_option_default, store_option_enabled
from backend.services.images import image_url, image_mutation_form
from backend.services.image_service import commit_entity_image, image_operation_lock
from backend.services.paths import AwsumPathResolver
from backend.services.money import format_money
from backend.services.business_contact import BusinessContactService
from backend.services.business_code import generate_business_code
from backend.services.license_email import load_company_branding
from backend.services.registration_email import build_registration_email
from backend.services.organization_users import (
    get_registration_owner, initial_phone_password, organization_admin_query,
    organization_user_query, registration_owner_config,
)
from backend.services.email_layout import AwsumEmailLayout
from backend.services.mail_common import EmailConfigurationError, MailDeliveryError, validate_email_address
from backend.services.system_settings import send_configured_email

router = APIRouter()

# Platform admin store ID (fixed)
ADMIN_STORE_ID = 1000001
LEGACY_TEST_STORE_CODE = "CUST00001"
TEST_STORE_CODE = "CLT00001"
LEGACY_TEST_STORE_CODES = {"CLT_00001", "CUST00001", "CUST_00001"}
ALLOWED_DASHBOARD_TYPES = {"PLATFORM", "STANDARD", "RESTAURANT", "DELI", "TUXEDO_RENTAL"}
BASE_DIR = PROJECT_ROOT
DEVICE_ACTIVATION_TTL_MINUTES = 30
PLATFORM_USER_ROLE_NAMES = {"superadmin", "admin", "staff"}
MAX_BCRYPT_PASSWORD_BYTES = 72



def get_db():
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _set_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _parse_optional_iso_datetime(value: Optional[str]):
    if not value:
        return None
    return datetime.fromisoformat(value)


US_COUNTRY_NAME = "United States"
_US_COUNTRY_ALIASES = {"US", "USA", "U.S.", "U.S.A.", "UNITED STATES", "UNITED STATES OF AMERICA"}


def _normalize_country(value) -> str:
    # Saved Country is "United States" for US/empty input; other countries are kept as entered.
    text_value = str(value or "").strip()
    if not text_value or text_value.upper() in _US_COUNTRY_ALIASES:
        return US_COUNTRY_NAME
    return text_value


STORE_LICENSE_STATUSES = ("ACTIVE", "SUSPENDED", "EXPIRED", "CANCELLED")
STORE_OPTION_SCOPES = ("STORE", "MERCHANT")
# payload key -> (option column, scope payload key, scope column)
STORE_OPTION_FIELDS = {
    "opt_customer": ("b_opt_customer", "customer_scope", "c_customer_scope"),
    "opt_time_attendance": ("b_opt_time_attendance", None, None),
    "opt_inventory": ("b_opt_inventory", None, None),
    # Use Location Management (shown under Inventory Management, independent of it).
    "opt_location_management": ("b_opt_use_shelf_location", None, None),
    "opt_loyalty": ("b_opt_loyalty", "loyalty_scope", "c_loyalty_scope"),
    "opt_accounting": ("b_opt_accounting", None, None),
    # Store Options > Item Features (no scope).
    **{option_key: (column, None, None) for option_key, column, _default in ITEM_FEATURES.values()},
}
_LICENSE_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_store_license_key(db: Session) -> str:
    for _ in range(20):
        key = "SL-" + "".join(secrets.choice(_LICENSE_KEY_ALPHABET) for _ in range(8))
        if not db.query(Store.i_store_id).filter(Store.c_store_license_key == key).first():
            return key
    raise HTTPException(status_code=409, detail="Unable to generate a unique Store License Key.")


def _parse_option_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "on", "yes", "y"}


def _apply_store_license_and_options(db: Session, store: Store, payload: dict, creating: bool) -> None:
    if "license_count" in payload or creating:
        raw = payload.get("license_count")
        if raw is None or str(raw).strip() == "":
            store.n_license_count = 0 if creating else None
        else:
            try:
                qty = int(str(raw).strip())
            except ValueError:
                raise HTTPException(status_code=400, detail="Device License Quantity must be a whole number.")
            if qty < 0:
                raise HTTPException(status_code=400, detail="Device License Quantity cannot be negative.")
            store.n_license_count = qty

    if "store_license_status" in payload or creating:
        status = str(payload.get("store_license_status") or "").strip().upper() or ("ACTIVE" if creating else None)
        if status is not None and status not in STORE_LICENSE_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid Store License Status.")
        store.c_store_license_status = status
    if "store_license_start_date" in payload:
        store.dt_store_license_start = _parse_optional_iso_datetime(payload.get("store_license_start_date"))
    if not store.c_store_license_key:
        store.c_store_license_key = generate_store_license_key(db)

    for option_key, (option_col, scope_key, scope_col) in STORE_OPTION_FIELDS.items():
        if option_key in payload:
            setattr(store, option_col, _parse_option_flag(payload.get(option_key)))
        elif creating:
            setattr(store, option_col, store_option_default(option_key))
        if not scope_col:
            continue
        if not getattr(store, option_col):
            setattr(store, scope_col, None)
            continue
        if scope_key in payload or not getattr(store, scope_col):
            scope = str(payload.get(scope_key) or "").strip().upper() or "STORE"
            if scope not in STORE_OPTION_SCOPES:
                raise HTTPException(status_code=400, detail=f"Invalid {scope_key.replace('_', ' ').title()}.")
            setattr(store, scope_col, scope)


def _store_license_and_options(store: Store) -> dict:
    data = {
        "store_license_key": store.c_store_license_key,
        "store_license_status": store.c_store_license_status,
        "store_license_start_date": store.dt_store_license_start.isoformat() if store.dt_store_license_start else None,
        "customer_scope": store.c_customer_scope or "",
        "loyalty_scope": store.c_loyalty_scope or "",
    }
    for option_key, (option_col, _, _) in STORE_OPTION_FIELDS.items():
        data[option_key] = "on" if store_option_enabled(store, option_key, option_col) else "off"
    return data


def _normalize_dashboard_type(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().upper()
    if not normalized:
        return None
    if normalized not in ALLOWED_DASHBOARD_TYPES:
        raise HTTPException(status_code=400, detail="Invalid dashboard_type.")
    return normalized


def _normalize_status(value: Optional[str], allowed_statuses: set[str], field_name: str = "status") -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}.")
    return normalized


def _parse_optional_int(value: Optional[object], field_name: str) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}.")


def _parse_optional_decimal(value: Optional[object], field_name: str) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}.")


def _parse_non_negative_decimal(value: Optional[object], field_name: str, default: Decimal = Decimal("0")) -> Decimal:
    parsed = _parse_optional_decimal(value, field_name)
    if parsed is None:
        return default
    if parsed < 0:
        raise HTTPException(status_code=400, detail=f"{field_name} cannot be negative")
    return parsed


def _parse_non_negative_int(value: Optional[object], field_name: str, default: int = 0) -> int:
    parsed = _parse_optional_int(value, field_name)
    if parsed is None:
        return default
    if parsed < 0:
        raise HTTPException(status_code=400, detail=f"{field_name} cannot be negative")
    return parsed


def _parse_bool(value: Optional[object], field_name: str, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise HTTPException(status_code=400, detail=f"Invalid {field_name}.")


def _parse_optional_iso_date(value: Optional[str], field_name: str = "date"):
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}.")


def _validate_invoice_status_transition(current_status: str, new_status: str) -> None:
    current = str(current_status or "issued").strip().lower()
    target = str(new_status or current).strip().lower()
    allowed = {
        "issued": {"issued", "paid", "void"},
        "paid": {"paid"},
        "void": {"void"},
    }
    if current not in allowed:
        raise HTTPException(status_code=400, detail="Invalid current invoice status")
    if target not in allowed[current]:
        raise HTTPException(status_code=409, detail=f"Invalid invoice status transition: {current} -> {target}")


def _serialize_invoice_line(line: InvoiceLine) -> dict:
    return {
        "line_id": line.i_invoice_line_id,
        "line_type": line.c_line_type,
        "description": line.c_description,
        "quantity": float(line.n_quantity or 0),
        "unit_price": float(line.n_unit_price or 0),
        "amount": float(line.n_amount or 0),
        "currency": line.c_currency,
    }


def _serialize_invoice(invoice: Invoice, line_count: Optional[int] = None, lines: Optional[list[InvoiceLine]] = None) -> dict:
    payload = {
        "id": invoice.i_invoice_id,
        "invoice_id": invoice.i_invoice_id,
        "invoice_no": invoice.c_invoice_no,
        "subscription_id": invoice.i_subscription_id,
        "merchant_id": invoice.i_merchant_id,
        "store_id": invoice.i_store_id,
        "invoice_date": str(invoice.dt_invoice_date) if invoice.dt_invoice_date else None,
        "due_date": str(invoice.dt_due_date) if invoice.dt_due_date else None,
        "currency": invoice.c_currency,
        "subtotal": float(invoice.n_subtotal or 0),
        "tax": float(invoice.n_tax or 0),
        "total": float(invoice.n_total or 0),
        "status": invoice.c_status,
        "memo": invoice.c_memo,
    }
    if line_count is not None:
        payload["line_count"] = int(line_count)
    if lines is not None:
        payload["lines"] = [_serialize_invoice_line(line) for line in lines]
    return payload


def _serialize_contract(contract: Contract) -> dict:
        return {
                "id": contract.i_contract_id,
                "contract_id": contract.i_contract_id,
                "subscription_id": contract.i_subscription_id,
                "merchant_id": contract.i_merchant_id,
                "store_id": contract.i_store_id,
                "pricing_plan_id": contract.i_pricing_plan_id,
                "contract_start_date": str(contract.dt_contract_start_date) if contract.dt_contract_start_date else None,
                "contract_end_date": str(contract.dt_contract_end_date) if contract.dt_contract_end_date else None,
                "contract_term_month": int(contract.i_contract_term_month or 1),
                "setup_fee": float(contract.n_setup_fee or 0),
                "monthly_base_fee": float(contract.n_monthly_base_fee or 0),
                "monthly_device_fee": float(contract.n_monthly_device_fee or 0),
                "monthly_user_fee": float(contract.n_monthly_user_fee or 0),
                "monthly_total_fee": float(contract.n_monthly_total_fee or 0),
                "tax_rate": float(contract.n_tax_rate or 0),
                "tax_amount": float(contract.n_tax_amount or 0),
                "total_monthly_fee": float(contract.n_total_monthly_fee or 0),
                "status": contract.c_status,
                "contract_pdf_path": contract.c_contract_pdf_path,
                "created_at": contract.dt_created_at.isoformat() if contract.dt_created_at else None,
        }


def _contract_html(contract: Contract) -> str:
        return f"""
        <html>
            <head>
                <meta charset='UTF-8' />
                <title>Contract {contract.i_contract_id}</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 24px; color: #0f172a; }}
                    h2 {{ margin: 0 0 8px 0; }}
                    .meta {{ margin-bottom: 14px; font-size: 13px; }}
                    table {{ width: 100%; border-collapse: collapse; }}
                    th, td {{ border: 1px solid #cbd5e1; padding: 8px; font-size: 12px; }}
                    th {{ background: #f8fafc; text-align: left; width: 32%; }}
                    .fee {{ text-align: right; font-variant-numeric: tabular-nums; }}
                </style>
            </head>
            <body>
                <h2>Service Contract #{contract.i_contract_id}</h2>
                <div class='meta'>
                    Subscription: {contract.i_subscription_id} | Merchant: {contract.i_merchant_id} | Store: {contract.i_store_id} | Status: {contract.c_status}
                    <br />
                    Period: {contract.dt_contract_start_date or '-'} ~ {contract.dt_contract_end_date or '-'}
                </div>

                <table>
                    <tbody>
                        <tr><th>Pricing Plan ID</th><td>{contract.i_pricing_plan_id or '-'}</td></tr>
                        <tr><th>Contract Term (Month)</th><td>{int(contract.i_contract_term_month or 1)}</td></tr>
                        <tr><th>Setup Fee</th><td class='fee'>{format_money(contract.n_setup_fee or 0)}</td></tr>
                        <tr><th>Monthly Base Fee</th><td class='fee'>{format_money(contract.n_monthly_base_fee or 0)}</td></tr>
                        <tr><th>Monthly Device Fee</th><td class='fee'>{format_money(contract.n_monthly_device_fee or 0)}</td></tr>
                        <tr><th>Monthly User Fee</th><td class='fee'>{format_money(contract.n_monthly_user_fee or 0)}</td></tr>
                        <tr><th>Monthly Total Fee</th><td class='fee'>{format_money(contract.n_monthly_total_fee or 0)}</td></tr>
                        <tr><th>Tax Rate (%)</th><td class='fee'>{float(contract.n_tax_rate or 0):,.4f}</td></tr>
                        <tr><th>Tax Amount</th><td class='fee'>{format_money(contract.n_tax_amount or 0)}</td></tr>
                        <tr><th>Total Monthly Fee</th><td class='fee'><strong>{format_money(contract.n_total_monthly_fee or 0)}</strong></td></tr>
                    </tbody>
                </table>
            </body>
        </html>
        """


def _build_contract_pdf_bytes(contract: Contract, context: ReportPrintContext) -> bytes:
        buffer = BytesIO()
        pdf = GlobalReportCanvas(buffer, pagesize=letter, report_context=context)
        width, height = letter
        y = height - 48

        def write_line(text: str, *, bold: bool = False, step: int = 18) -> None:
                nonlocal y
                pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 11)
                pdf.drawString(48, y, text)
                y -= step

        write_line(f"Service Contract #{contract.i_contract_id}", bold=True, step=22)
        write_line(f"Subscription: {contract.i_subscription_id}   Merchant: {contract.i_merchant_id}   Store: {contract.i_store_id}")
        write_line(f"Status: {contract.c_status}")
        write_line(f"Period: {contract.dt_contract_start_date or '-'} ~ {contract.dt_contract_end_date or '-'}")
        y -= 8
        write_line("Monthly Fee Details", bold=True, step=20)
        write_line(f"Setup Fee: {format_money(contract.n_setup_fee or 0)}")
        write_line(f"Monthly Base Fee: {format_money(contract.n_monthly_base_fee or 0)}")
        write_line(f"Monthly Device Fee: {format_money(contract.n_monthly_device_fee or 0)}")
        write_line(f"Monthly User Fee: {format_money(contract.n_monthly_user_fee or 0)}")
        write_line(f"Monthly Total Fee: {format_money(contract.n_monthly_total_fee or 0)}")
        write_line(f"Tax Rate (%): {float(contract.n_tax_rate or 0):,.4f}")
        write_line(f"Tax Amount: {format_money(contract.n_tax_amount or 0)}")
        write_line(f"Total Monthly Fee: {format_money(contract.n_total_monthly_fee or 0)}", bold=True)

        pdf.showPage()
        pdf.save()
        buffer.seek(0)
        return buffer.read()


def _generate_merchant_store_code(db: Session) -> tuple[str, int]:
    code = generate_business_code(db, "STORE")
    max_seq = db.query(func.max(Store.i_store_seq)).scalar()
    return code, int(max_seq or 0) + 1


def _get_business_store(db: Session, store_id: int) -> Store:
    # The Platform Admin store (ADMIN_STORE_ID / "Admin") is a platform identity, not a business Store.
    if store_id == ADMIN_STORE_ID:
        raise HTTPException(status_code=403, detail="The Platform Admin store cannot be used as a business Store.")
    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")
    return store


def _generate_next_merchant_code(db: Session) -> str:
    return generate_business_code(db, "MERCHANT")


def _generate_agency_code(db: Session) -> str:
    return generate_business_code(db, "AGENCY")


def _save_organization_logo(db, organization, file, context, code, *, merchant_code=None):
    from backend.services import images
    identity_field = {"AGENCY": "i_agency_id", "MERCHANT": "i_merchant_id", "STORE": "i_store_id"}[context]
    owner = AwsumStorageService(db).resolve_owner(context, getattr(organization, identity_field))
    if owner != images.AwsumPathResolver.for_owner(context, code, merchant_code=merchant_code):
        raise HTTPException(409, "Logo owner does not match the saved organization relationship.")
    images.validate_owner(db, owner)
    data = images.read_image_upload(file)
    with image_operation_lock(owner, "logo"):
        db.refresh(organization, with_for_update=True)
        warning = commit_entity_image(db, organization, owner, "logo", category="logo",
                                      image_data=data, reference_field="c_logo_url")
    reference = organization.c_logo_url
    url = images.image_url(reference)
    return JSONResponse({"success": True, "image_path": reference, "logo_url": url, "icon_url": url,
                         "image_warning": warning})


def _remove_organization_logo(db, organization, context):
    identity_field = {"AGENCY": "i_agency_id", "MERCHANT": "i_merchant_id", "STORE": "i_store_id"}[context]
    owner = AwsumStorageService(db).resolve_owner(context, getattr(organization, identity_field))
    with image_operation_lock(owner, "logo"):
        db.refresh(organization, with_for_update=True)
        from backend.services import images
        previous = images.resolve_image_file(organization.c_logo_url)
        warning = commit_entity_image(db, organization, owner, "logo", category="logo",
                                      remove_image=True, reference_field="c_logo_url")
    return JSONResponse({"success": True, "deleted_files": int(previous is not None and not previous.exists()),
                         "image_warning": warning})


def _ensure_merchant_store(db: Session, merchant_id: int, store_id: int) -> tuple[Merchant, Store]:
    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found.")

    store = (
        db.query(Store)
        .filter(Store.i_store_id == store_id, Store.i_merchant_id == merchant_id)
        .first()
    )
    if not store:
        raise HTTPException(status_code=404, detail="Store not found for this merchant.")

    return merchant, store


def _generate_store_device_code(db: Session, store_id: int) -> str:
    prefix = f"DEV_{store_id}_"
    existing_codes = (
        db.query(Device.c_device_uuid)
        .filter(Device.i_store_id == store_id, Device.c_device_uuid.like(f"{prefix}%"))
        .all()
    )
    max_seq = 0
    for (raw_code,) in existing_codes:
        code = str(raw_code or "")
        if not code.startswith(prefix):
            continue
        suffix = code[len(prefix):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f"{prefix}{max_seq + 1:04d}"


def _generate_device_activation_code(db: Session) -> str:
    while True:
        token = secrets.token_urlsafe(12).replace("-", "").replace("_", "")
        code = f"ACT-{token[:10].upper()}"
        exists = db.query(Device.i_device_id).filter(Device.c_activation_code == code).first()
        if not exists:
            return code


def _resolve_license_status(device: Device, license_obj: License | None) -> str:
    if not device.i_license_id:
        return "Unassigned"
    if not license_obj:
        return "Unassigned"
    raw_status = str(license_obj.c_status or "active").strip().lower()
    if raw_status in {"inactive", "suspended"}:
        return "Suspended"
    if raw_status == "trial":
        return "Trial"
    if raw_status and raw_status != "active":
        return raw_status.replace("_", " ").title()
    if license_obj.dt_end and license_obj.dt_end < datetime.now():
        return "Expired"
    return "Active"


def _serialize_device(device: Device, license_obj: License | None, agency_obj: Agency | None = None) -> dict:
    return {
        "device_id": device.i_device_id,
        "store_id": device.i_store_id,
        "license_id": device.i_license_id,
        "installed_by_agency_id": device.i_installed_by_agency_id,
        "installed_by_agency_name": agency_obj.c_agency_name if agency_obj else None,
        "device_code": device.c_device_uuid,
        "device_name": device.c_device_name,
        "device_type": device.c_device_type,
        "device_type_id": device.i_device_type_id,
        "serial_no": device.c_serial_no,
        "installed_at": device.dt_installed_at.isoformat() if device.dt_installed_at else None,
        "memo": device.c_memo,
        "status": device.c_status,
        "activation_code": device.c_activation_code,
        "activation_expiry": device.dt_activation_expiry.isoformat() if device.dt_activation_expiry else None,
        "first_activated_at": device.dt_first_activated_at.isoformat() if device.dt_first_activated_at else None,
        "activated_by": device.c_activated_by,
        "bound_hardware_id": device.c_bound_hardware_id,
        "last_ip": device.c_last_ip,
        "last_seen": device.dt_last_seen.isoformat() if device.dt_last_seen else None,
        "os": device.c_os,
        "app_version": device.c_app_version,
        "note": device.c_note,
        "license_status": _resolve_license_status(device, license_obj),
    }


def _serialize_device_log(log: DeviceLog) -> dict:
    return {
        "log_id": log.i_log_id,
        "device_id": log.i_device_id,
        "event_type": log.c_event_type,
        "hardware_id": log.c_hardware_id,
        "ip_address": log.c_ip_address,
        "agent": log.c_agent,
        "os": log.c_os,
        "version": log.c_version,
        "action_by": log.c_action_by,
        "event_time": log.dt_event_time.isoformat() if log.dt_event_time else None,
        "note": log.c_note,
    }


def _actor_label(user) -> Optional[str]:
    if not user:
        return None
    email = getattr(user, "c_email", None)
    if email:
        return str(email)
    actor_id = _actor_id(user)
    return str(actor_id) if actor_id is not None else None


def _resolve_request_ip(request: Optional[Request], payload: Optional[dict] = None) -> Optional[str]:
    payload = payload or {}
    explicit_ip = str(payload.get("ip_address") or payload.get("last_ip") or "").strip()
    if explicit_ip:
        return explicit_ip
    merchant = getattr(request, "merchant", None)
    host = getattr(merchant, "host", None)
    host = str(host or "").strip()
    return host or None


def _log_device_event(
    db: Session,
    device: Device,
    event_type: str,
    *,
    request: Optional[Request] = None,
    payload: Optional[dict] = None,
    action_by: Optional[str] = None,
    note: Optional[str] = None,
) -> DeviceLog:
    payload = payload or {}
    event_log = DeviceLog(
        i_device_id=device.i_device_id,
        c_event_type=str(event_type or "").strip().upper() or "UPDATED",
        c_hardware_id=str(payload.get("hardware_id") or payload.get("bound_hardware_id") or device.c_bound_hardware_id or "").strip() or None,
        c_ip_address=_resolve_request_ip(request, payload),
        c_agent=str(payload.get("agency") or getattr(request, "headers", {}).get("user-agent") or "").strip() or None,
        c_os=str(payload.get("os") or device.c_os or "").strip() or None,
        c_version=str(payload.get("app_version") or payload.get("version") or device.c_app_version or "").strip() or None,
        c_action_by=str(action_by or payload.get("action_by") or payload.get("activated_by") or "").strip() or None,
        dt_event_time=datetime.now(),
        c_note=str(note or payload.get("note") or "").strip() or None,
    )
    db.add(event_log)
    return event_log


def _resolve_agency(agency_id: Optional[object], db: Session, *, required: bool = False) -> Optional[Agency]:
    normalized_id = _parse_optional_int(agency_id, "agency_id")
    if normalized_id is None:
        if required:
            raise HTTPException(status_code=400, detail="agency_id is required.")
        return None
    agency = db.query(Agency).filter(Agency.i_agency_id == normalized_id).first()
    if not agency:
        raise HTTPException(status_code=400, detail="Invalid agency_id.")
    return agency


# ---------------------------
# Auth Helpers
# ---------------------------

def get_current_user(request: Request, db: Session):
    from backend.services.web_sessions import authenticate
    return authenticate(request, db)


def require_platform_admin(user):
    if (user.login_role != 1 or getattr(user, "i_agency_id", None) is not None
            or getattr(user, "i_merchant_id", None) is not None):
        raise HTTPException(status_code=403, detail="Not authorized.")


def _actor_id(user):
    return getattr(user, "i_platform_user_id", None)


def _stamp_created(entity, user):
    actor_id = _actor_id(user)
    if hasattr(entity, "i_created_by") and getattr(entity, "i_created_by", None) is None:
        entity.i_created_by = actor_id


def _stamp_updated(entity, user):
    actor_id = _actor_id(user)
    if hasattr(entity, "dt_updated"):
        entity.dt_updated = datetime.now()
    if hasattr(entity, "i_updated_by"):
        entity.i_updated_by = actor_id


# ---------------------------
# Store API
# ---------------------------

@router.get("/platform/zip-lookup/{zip_code}")
def zip_lookup(zip_code: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    return JSONResponse(lookup_zip(zip_code))


STORE_LIST_SORT_OPTIONS = (
    "merchant_asc", "merchant_desc", "store_asc", "store_desc",
    "agency_asc", "agency_desc", "newest", "oldest",
)
STORE_LIST_MAX_PAGE_SIZE = 100


def _store_list_order_by(sort_key: str, merchant, agency):
    """Order-by clauses for the store list; Store ID is always the final tie-breaker."""
    merchant_name = func.lower(merchant.c_merchant_name)
    store_name = func.lower(Store.c_store_name)
    agency_name = func.lower(agency.c_agency_name)
    if sort_key == "merchant_asc":
        clauses = [merchant_name.asc().nulls_last(), store_name.asc()]
    elif sort_key == "merchant_desc":
        clauses = [merchant_name.desc().nulls_last(), store_name.asc()]
    elif sort_key == "store_asc":
        clauses = [store_name.asc(), merchant_name.asc().nulls_last()]
    elif sort_key == "store_desc":
        clauses = [store_name.desc(), merchant_name.asc().nulls_last()]
    elif sort_key == "agency_asc":
        clauses = [agency_name.asc().nulls_last(), merchant_name.asc().nulls_last(), store_name.asc()]
    elif sort_key == "agency_desc":
        clauses = [agency_name.desc().nulls_last(), merchant_name.asc().nulls_last(), store_name.asc()]
    elif sort_key == "newest":
        clauses = [Store.dt_created.desc().nulls_last()]
    else:  # oldest
        clauses = [Store.dt_created.asc().nulls_last()]
    return clauses + [Store.i_store_id.asc()]


@router.get("/platform/stores")
def list_stores(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
    business_type: Optional[str] = None,
    operation_type: Optional[str] = None,
    channel_type: Optional[str] = None,
    device_type: Optional[str] = None,
    merchant_id: Optional[int] = None,
    agency_id: Optional[int] = None,
    sort: Optional[str] = None,
    page: Optional[int] = Query(None, ge=1),
    page_size: int = Query(20, ge=1, le=STORE_LIST_MAX_PAGE_SIZE),
    locate_id: Optional[int] = None,
):
    """Returns a JSON list of stores, or renders the Store Management page for HTML requests."""
    user = get_current_user(request, db)
    require_platform_admin(user)

    # Render the page for HTML requests
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        roles = db.query(Role).all()
        business_types = db.query(BusinessType).filter(BusinessType.c_status == "active").all()
        stores = db.query(Store).filter(Store.i_store_id != ADMIN_STORE_ID).order_by(Store.i_store_id).all()
        users = db.query(PlatformUser).all()

        return _set_no_cache_headers(templates.TemplateResponse(
            request,
            "platform/platform_stores.html",
            {
                "request": request,
                "APP_NAME": APP_NAME,
                "user": user,
                "role": "Platform Admin",
                "active_page": "stores",
                "roles": roles,
                "business_types": business_types,
                "stores": stores,
                "users": users,
                "filter": {
                    "search": search or "",
                    "status": status or "",
                    "business_type": business_type or "",
                    "operation_type": operation_type or "",
                    "channel_type": channel_type or "",
                    "device_type": device_type or "",
                },
            },
        ))

    # JSON API request: return the filtered list
    # sort/page switch on the master-grid behavior; plain calls keep the legacy list behavior.
    grid_mode = sort is not None or page is not None
    sort_key = (sort or "merchant_asc").strip()
    if grid_mode and sort_key not in STORE_LIST_SORT_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid sort option.")

    merchant_alias = aliased(Merchant)
    agency_alias = aliased(Agency)
    query = (
        db.query(Store)
        .outerjoin(merchant_alias, merchant_alias.i_merchant_id == Store.i_merchant_id)
        .outerjoin(agency_alias, agency_alias.i_agency_id == Store.i_installed_by_agency_id)
        .filter(Store.i_store_id != ADMIN_STORE_ID)
    )

    if status:
        query = query.filter(Store.c_status == status)

    if operation_type:
        query = query.filter(Store.c_operation_type.ilike(f"%{operation_type}%"))

    if channel_type:
        query = query.filter(Store.c_channel_type.ilike(f"%{channel_type}%"))

    if device_type:
        query = query.filter(Store.c_device_type.ilike(f"%{device_type}%"))

    if search and search.strip():
        term = f"%{search.strip()}%"
        code_term = f"%{search.strip().replace('_', '')}%"
        if grid_mode:
            query = query.filter(
                or_(
                    agency_alias.c_agency_name.ilike(term),
                    agency_alias.c_agency_code.ilike(term),
                    agency_alias.c_agency_code.ilike(code_term),
                    merchant_alias.c_merchant_name.ilike(term),
                    merchant_alias.c_merchant_code.ilike(term),
                    merchant_alias.c_merchant_code.ilike(code_term),
                    Store.c_store_name.ilike(term),
                    Store.c_store_code.ilike(term),
                    Store.c_store_code.ilike(code_term),
                )
            )
        else:
            query = query.filter(
                or_(
                    Store.c_store_name.ilike(term),
                    Store.c_store_code.ilike(term),
                    Store.c_store_code.ilike(code_term),
                    Store.c_email.ilike(term),
                    Store.c_phone.ilike(term),
                )
            )

    if merchant_id is not None:
        query = query.filter(Store.i_merchant_id == merchant_id)

    if agency_id is not None:
        query = query.filter(Store.i_installed_by_agency_id == agency_id)

    if business_type:
        query = query.join(BusinessType, Store.i_business_type == BusinessType.i_business_type_id)
        query = query.filter(
            BusinessType.c_name.ilike(f"%{business_type}%")
            | BusinessType.c_business_code.ilike(f"%{business_type}%")
        )

    pagination = None
    if grid_mode:
        order_by = _store_list_order_by(sort_key, merchant_alias, agency_alias)
        query = query.order_by(*order_by)
        if page is not None:
            total = query.order_by(None).with_entities(func.count(Store.i_store_id)).scalar() or 0
            total_pages = max(1, -(-total // page_size))
            current_page = page
            if locate_id is not None:
                ids = [row[0] for row in query.with_entities(Store.i_store_id).all()]
                if locate_id in ids:
                    current_page = ids.index(locate_id) // page_size + 1
            current_page = min(current_page, total_pages)
            stores = query.offset((current_page - 1) * page_size).limit(page_size).all()
            pagination = {
                "total": total,
                "page": current_page,
                "page_size": page_size,
                "total_pages": total_pages,
                "sort": sort_key,
            }
        else:
            stores = query.all()
    else:
        stores = query.order_by(Store.i_store_id).all()

    results = []
    for s in stores:
        bt = db.query(BusinessType).filter(BusinessType.i_business_type_id == s.i_business_type).first()
        merchant = db.query(Merchant).filter(Merchant.i_merchant_id == s.i_merchant_id).first() if s.i_merchant_id else None
        store_agency = db.query(Agency).filter(Agency.i_agency_id == s.i_installed_by_agency_id).first() if s.i_installed_by_agency_id else None
        results.append({
            "store_id": s.i_store_id,
            "store_seq": s.i_store_seq,
            "store_code": s.c_store_code,
            "image_path": s.c_logo_url,
            "image_url": image_url(s.c_logo_url),
            "store_name": s.c_store_name,
            "merchant_id": s.i_merchant_id,
            "merchant_name": merchant.c_merchant_name if merchant else None,
            "merchant_code": merchant.c_merchant_code if merchant else None,
            "is_test": s.c_is_test,
            "dashboard_type": s.c_dashboard_type,
            "business_type": bt.c_name if bt else None,
            "operation_type": s.c_operation_type,
            "store_purpose": s.c_operation_type,
            "channel_type": s.c_channel_type,
            "device_type": s.c_device_type,
            "contact_name": s.c_contact_name,
            "owner_name": s.c_owner_name,
            "status": s.c_status,
            "phone": s.c_phone,
            "email": s.c_email,
            "zip": s.c_zip,
            "address_line1": s.c_address_line1,
            "address_line2": s.c_address_line2,
            "city": s.c_city,
            "state": s.c_state,
            "country": s.c_country,
            "default_tax_rate": s.c_default_tax_rate,
            "timezone": s.c_timezone,
            "tax_source": s.c_tax_source,
            "receipt_store_name": s.c_receipt_store_name,
            "receipt_phone": s.c_receipt_phone,
            "receipt_email": s.c_receipt_email,
            "receipt_website_url": s.c_receipt_website_url,
            "receipt_message": s.c_receipt_message,
            "contract_date": s.dt_contract_date.isoformat() if s.dt_contract_date else None,
            "contract_start_date": s.dt_contract_start.isoformat() if s.dt_contract_start else None,
            "contract_end_date": s.dt_contract_end.isoformat() if s.dt_contract_end else None,
            "license_type": s.c_license_type,
            "license_count": s.n_license_count,
            "max_user_count": s.i_max_user_count,
            "max_terminal_count": s.i_max_terminal_count,
            "license_expire_date": s.dt_license_expire.isoformat() if s.dt_license_expire else None,
            "memo": s.c_memo,
            "notes": s.c_notes,
            "installed_by_agency_id": s.i_installed_by_agency_id,
            "installed_by_agency_name": store_agency.c_agency_name if store_agency else None,
            "installed_by_agency_code": store_agency.c_agency_code if store_agency else None,
            "created_at": s.dt_created.isoformat() if s.dt_created else None,
            **_store_license_and_options(s),
        })

    if pagination is not None:
        return _set_no_cache_headers(JSONResponse({"items": results, **pagination}))
    return _set_no_cache_headers(JSONResponse(results))


@router.get("/platform/store/{store_id}")
def get_store(request: Request, store_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store = _get_business_store(db, store_id)

    bt = db.query(BusinessType).filter(BusinessType.i_business_type_id == store.i_business_type).first()
    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == store.i_merchant_id).first() if store.i_merchant_id else None
    store_agency = db.query(Agency).filter(Agency.i_agency_id == store.i_installed_by_agency_id).first() if store.i_installed_by_agency_id else None

    data = {
        "store_id": store.i_store_id,
        "store_seq": store.i_store_seq,
        "store_code": store.c_store_code,
        "image_path": store.c_logo_url,
        "image_url": image_url(store.c_logo_url),
        "store_name": store.c_store_name,
        "merchant_id": store.i_merchant_id,
        "merchant_name": merchant.c_merchant_name if merchant else None,
        "merchant_code": merchant.c_merchant_code if merchant else None,
        "is_test": store.c_is_test,
        "dashboard_type": store.c_dashboard_type,
        "business_type": bt.c_name if bt else None,
        "operation_type": store.c_operation_type,
        "store_purpose": store.c_operation_type,
        "channel_type": store.c_channel_type,
        "device_type": store.c_device_type,
        "contact_name": store.c_contact_name,
        "owner_name": store.c_owner_name,
        "address": store.c_address_line1,
        "zip": store.c_zip,
        "address_line1": store.c_address_line1,
        "address_line2": store.c_address_line2,
        "city": store.c_city,
        "state": store.c_state,
        "country": store.c_country,
        "default_tax_rate": store.c_default_tax_rate,
        "timezone": store.c_timezone,
        "tax_source": store.c_tax_source,
        "phone": store.c_phone,
        "email": store.c_email,
        "receipt_store_name": store.c_receipt_store_name,
        "receipt_phone": store.c_receipt_phone,
        "receipt_email": store.c_receipt_email,
        "receipt_website_url": store.c_receipt_website_url,
        "receipt_message": store.c_receipt_message,
        "contract_date": store.dt_contract_date.isoformat() if store.dt_contract_date else None,
        "contract_start_date": store.dt_contract_start.isoformat() if store.dt_contract_start else None,
        "contract_end_date": store.dt_contract_end.isoformat() if store.dt_contract_end else None,
        "license_type": store.c_license_type,
        "license_count": store.n_license_count,
        "max_user_count": store.i_max_user_count,
        "max_terminal_count": store.i_max_terminal_count,
        "store_status": store.c_status,
        "license_expire_date": store.dt_license_expire.isoformat() if store.dt_license_expire else None,
        "created_at": store.dt_created.isoformat() if store.dt_created else None,
        "memo": store.c_memo,
        "remark": store.c_remark,
        "notes": store.c_notes,
        "installed_by_agency_id": store.i_installed_by_agency_id,
        "installed_by_agency_name": store_agency.c_agency_name if store_agency else None,
        **_store_license_and_options(store),
    }

    return _set_no_cache_headers(JSONResponse(data))


@router.post("/platform/merchant/{merchant_id}/logo")
def upload_merchant_logo(
    merchant_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found.")

    from backend.routers.system_settings import _validate_origin
    _validate_origin(request)
    return _save_organization_logo(db, merchant, file, "MERCHANT", merchant.c_merchant_code)


@router.post("/platform/store/{store_id}/logo")
def upload_store_logo(
    store_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")
    if not store.i_merchant_id:
        raise HTTPException(status_code=400, detail="Store must be linked to a Merchant before uploading a logo.")

    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == store.i_merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=400, detail="Linked Merchant not found.")

    from backend.routers.system_settings import _validate_origin
    _validate_origin(request)
    return _save_organization_logo(db, store, file, "STORE", store.c_store_code, merchant_code=merchant.c_merchant_code)


@router.delete("/platform/merchant/{merchant_id}/logo")
def reset_merchant_logo(merchant_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    from backend.routers.system_settings import _validate_origin
    _validate_origin(request)
    return _remove_organization_logo(db, merchant, "MERCHANT")


@router.delete("/platform/store/{store_id}/logo")
def reset_store_logo(store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")
    if not store.i_merchant_id:
        raise HTTPException(status_code=400, detail="Store must be linked to a Merchant before resetting a logo.")

    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == store.i_merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=400, detail="Linked Merchant not found.")

    from backend.routers.system_settings import _validate_origin
    _validate_origin(request)
    return _remove_organization_logo(db, store, "STORE")


@router.post("/platform/agency/{agency_id}/logo")
def upload_agency_logo(
    agency_id: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    require_platform_admin(user)
    from backend.routers.system_settings import _validate_origin
    _validate_origin(request)
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(404, "Agency not found.")
    return _save_organization_logo(db, agency, file, "AGENCY", agency.c_agency_code)


@router.delete("/platform/agency/{agency_id}/logo")
def reset_agency_logo(agency_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    from backend.routers.system_settings import _validate_origin
    _validate_origin(request)
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(404, "Agency not found.")
    return _remove_organization_logo(db, agency, "AGENCY")


# Business Contacts: one shared API for AGENCY / MERCHANT / STORE (owner_type + owner_id).
def _contact_service(request: Request, db: Session, mutate: bool = False):
    from backend.services.business_contact import BusinessContactService
    require_platform_admin(get_current_user(request, db))
    if mutate:
        from backend.routers.system_settings import _validate_origin
        _validate_origin(request)
    return BusinessContactService(db)


@router.get("/platform/business-contacts/{owner_type}/{owner_id}")
def list_business_contacts(owner_type: str, owner_id: int, request: Request, db: Session = Depends(get_db)):
    return _contact_service(request, db).get_contacts(owner_type, owner_id)


@router.post("/platform/business-contacts/{owner_type}/{owner_id}")
def create_business_contact(owner_type: str, owner_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return _contact_service(request, db, True).create_contact(owner_type, owner_id, payload)


@router.put("/platform/business-contacts/{owner_type}/{owner_id}/order")
def reorder_business_contacts(owner_type: str, owner_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return _contact_service(request, db, True).reorder(owner_type, owner_id, list(payload.get("contact_ids") or []))


@router.put("/platform/business-contact/{contact_id}")
def update_business_contact(contact_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return _contact_service(request, db, True).update_contact(contact_id, payload)


@router.post("/platform/business-contact/{contact_id}/primary")
def set_primary_business_contact(contact_id: int, request: Request, db: Session = Depends(get_db)):
    return _contact_service(request, db, True).set_primary(contact_id)


@router.delete("/platform/business-contact/{contact_id}")
def delete_business_contact(contact_id: int, request: Request, db: Session = Depends(get_db)):
    _contact_service(request, db, True).delete_contact(contact_id)
    return {"deleted": True}


@router.post("/platform/store")
def create_store(request: Request, payload: dict, db: Session = Depends(get_db)):
    return _create_store(request, payload, db)


def _commit_store_image(db, store, *, image_data=None, remove_image=False):
    if image_data is None and not remove_image:
        db.commit()
        return None
    db.flush()
    owner = AwsumStorageService(db).resolve_owner("STORE", store.i_store_id)
    with image_operation_lock(owner, "logo"):
        return commit_entity_image(db, store, owner, "logo", category="logo",
                                   image_data=image_data, remove_image=remove_image,
                                   reference_field="c_logo_url")


@router.post("/platform/store/with-image")
@router.put("/platform/store/{store_id}/with-image")
def save_store_with_image(
    request: Request, payload: str = Form(...), image: UploadFile | None = File(None),
    remove_image: bool = Form(False), store_id: int | None = None,
    db: Session = Depends(get_db),
):
    from backend.routers.system_settings import _validate_origin
    user = get_current_user(request, db)
    require_platform_admin(user)
    _validate_origin(request)
    if request.method == "POST" and store_id is not None:
        raise HTTPException(422, "Store ID cannot be supplied when creating a Store.")
    if store_id is not None:
        _get_business_store(db, store_id)
    values, data = image_mutation_form(payload, remove_image, image)
    if store_id is None:
        return _create_store(request, values, db, image_data=data)
    return _update_store(store_id, values, request, db, image_data=data, remove_image=remove_image)


def _create_store(request: Request, payload: dict, db: Session, *, image_data: bytes | None = None):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store_name = payload.get("store_name")

    if not store_name:
        raise HTTPException(status_code=400, detail="Store name is required.")

    merchant_id_raw = payload.get("merchant_id")
    merchant_id = _parse_optional_int(merchant_id_raw, "merchant_id")
    if merchant_id is None:
        raise HTTPException(status_code=400, detail="Merchant is required.")

    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=400, detail="Selected Merchant does not exist.")

    agency_id_new = _parse_optional_int(payload.get("installed_by_agency_id"), "installed_by_agency_id")
    # The Merchant > Stores tab has no Agency field, so it stays optional there.
    if agency_id_new is None and not payload.get("_agency_optional"):
        raise HTTPException(status_code=400, detail="Agency is required.")
    if agency_id_new is not None and not db.query(Agency.i_agency_id).filter(Agency.i_agency_id == agency_id_new).first():
        raise HTTPException(status_code=400, detail="Selected Agency does not exist.")

    for _ in range(5):
        generated_store_code, generated_seq = _generate_merchant_store_code(db)

        next_id = get_next_merchant_store_id(db)

        store = Store(
            i_store_id=next_id,
            i_store_seq=generated_seq,
            c_store_code=generated_store_code,
            c_store_name=store_name,
            i_merchant_id=merchant_id,
            c_contact_name=payload.get("contact_name"),
            c_owner_name=payload.get("owner_name"),
            c_address_line1=payload.get("address_line1") or payload.get("address"),
            c_address_line2=payload.get("address_line2"),
            c_city=payload.get("city"),
            c_state=payload.get("state"),
            c_zip=payload.get("zip"),
            c_country=_normalize_country(payload.get("country")),
            c_default_tax_rate=payload.get("default_tax_rate"),
            c_timezone=payload.get("timezone"),
            c_tax_source=payload.get("tax_source") or "auto",
            c_phone=payload.get("phone"),
            c_email=payload.get("email"),
            c_receipt_store_name=payload.get("receipt_store_name"),
            c_receipt_phone=payload.get("receipt_phone"),
            c_receipt_email=payload.get("receipt_email"),
            c_receipt_website_url=payload.get("receipt_website_url"),
            c_receipt_message=payload.get("receipt_message"),
            dt_contract_date=_parse_optional_iso_datetime(payload.get("contract_date")),
            dt_contract_start=_parse_optional_iso_datetime(payload.get("contract_start_date")),
            dt_contract_end=_parse_optional_iso_datetime(payload.get("contract_end_date")),
            c_license_type=payload.get("license_type"),
            n_license_count=payload.get("license_count"),
            i_max_user_count=payload.get("max_user_count"),
            i_max_terminal_count=payload.get("max_terminal_count"),
            c_status=payload.get("store_status") or "active",
            c_is_test=0,
            c_dashboard_type=_normalize_dashboard_type(payload.get("dashboard_type")),
            c_operation_type=payload.get("operation_type") or payload.get("store_purpose"),
            c_channel_type=payload.get("channel_type"),
            c_device_type=payload.get("device_type"),
            dt_license_expire=_parse_optional_iso_datetime(payload.get("license_expire_date")),
            c_memo=payload.get("memo"),
            c_notes=(str(payload.get("notes")).strip()[:5000] or None) if payload.get("notes") is not None else None,
            c_remark=payload.get("remark"),
        )

        if payload.get("business_type"):
            bt_obj = db.query(BusinessType).filter(BusinessType.c_name == payload.get("business_type")).first()
            if bt_obj:
                store.i_business_type = bt_obj.i_business_type_id

        if agency_id_new is not None:
            store.i_installed_by_agency_id = agency_id_new

        _apply_store_license_and_options(db, store, payload, creating=True)
        _stamp_created(store, user)
        db.add(store)
        try:
            db.flush()
            activation = _activate_organization(db, "STORE", store)
            warning = _commit_store_image(db, store, image_data=image_data)
            storage = AwsumStorageService(db).initialize_after_registration("STORE", next_id)
            result = {"store_id": next_id, "store_code": generated_store_code, "store_seq": generated_seq,
                      **storage, **activation}
            if image_data is not None:
                result.update(image_path=store.c_logo_url, image_url=image_url(store.c_logo_url), image_warning=warning)
            return JSONResponse(result)
        except IntegrityError as exc:
            db.rollback()
            if (
                "store_code" in str(exc).lower()
                or "tb_store.c_store_code" in str(exc).lower()
                or "store_seq" in str(exc).lower()
                or "tb_store.i_store_seq" in str(exc).lower()
            ):
                continue
            raise

    raise HTTPException(status_code=409, detail="Unable to generate a unique store_code.")


@router.put("/platform/store/{store_id}")
def update_store(store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return _update_store(store_id, payload, request, db)


def _update_store(store_id: int, payload: dict, request: Request, db: Session,
                  *, image_data: bytes | None = None, remove_image: bool = False):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store = _get_business_store(db, store_id)
    db.refresh(store, with_for_update=True)

    # Bulk-update allowed fields
    for key, value in {
        "store_name": "c_store_name",
        "operation_type": "c_operation_type",
        "store_purpose": "c_operation_type",
        "channel_type": "c_channel_type",
        "device_type": "c_device_type",
        "contact_name": "c_contact_name",
        "owner_name": "c_owner_name",
        "phone": "c_phone",
        "email": "c_email",
        "zip": "c_zip",
        "address_line1": "c_address_line1",
        "address_line2": "c_address_line2",
        "city": "c_city",
        "state": "c_state",
        "default_tax_rate": "c_default_tax_rate",
        "timezone": "c_timezone",
        "tax_source": "c_tax_source",
        "receipt_store_name": "c_receipt_store_name",
        "receipt_phone": "c_receipt_phone",
        "receipt_email": "c_receipt_email",
        "receipt_website_url": "c_receipt_website_url",
        "receipt_message": "c_receipt_message",
        "license_type": "c_license_type",
        "license_count": "n_license_count",
        "max_user_count": "i_max_user_count",
        "max_terminal_count": "i_max_terminal_count",
        "memo": "c_memo",
        "store_status": "c_status",
        "remark": "c_remark",
    }.items():
        if key in payload:
            setattr(store, value, payload.get(key))

    if "address" in payload and "address_line1" not in payload:
        store.c_address_line1 = payload.get("address")
    if "country" in payload:
        store.c_country = _normalize_country(payload.get("country"))

    if "license_expire_date" in payload:
        store.dt_license_expire = _parse_optional_iso_datetime(payload.get("license_expire_date"))
    if "contract_date" in payload:
        store.dt_contract_date = _parse_optional_iso_datetime(payload.get("contract_date"))
    if "contract_start_date" in payload:
        store.dt_contract_start = _parse_optional_iso_datetime(payload.get("contract_start_date"))
    if "contract_end_date" in payload:
        store.dt_contract_end = _parse_optional_iso_datetime(payload.get("contract_end_date"))
    if "dashboard_type" in payload:
        store.c_dashboard_type = _normalize_dashboard_type(payload.get("dashboard_type"))

    if payload.get("business_type"):
        bt_obj = db.query(BusinessType).filter(BusinessType.c_name == payload.get("business_type")).first()
        if bt_obj:
            store.i_business_type = bt_obj.i_business_type_id

    if "installed_by_agency_id" in payload:
        agency_id = _parse_optional_int(payload.get("installed_by_agency_id"), "installed_by_agency_id")
        if agency_id is not None and not db.query(Agency.i_agency_id).filter(Agency.i_agency_id == agency_id).first():
            raise HTTPException(status_code=400, detail="Selected Agency does not exist.")
        store.i_installed_by_agency_id = agency_id

    if "notes" in payload:
        notes_val = str(payload.get("notes") or "").strip()
        if len(notes_val) > 5000:
            raise HTTPException(status_code=400, detail="Notes is too long.")
        store.c_notes = notes_val or None

    if "merchant_id" in payload:
        merchant_id_raw = payload.get("merchant_id")
        merchant_id = _parse_optional_int(merchant_id_raw, "merchant_id")
        if merchant_id is None:
            raise HTTPException(status_code=400, detail="Merchant cannot be empty.")
        merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
        if not merchant:
            raise HTTPException(status_code=400, detail="Selected Merchant does not exist.")
        if store.i_merchant_id != merchant.i_merchant_id:
            from backend.services.paths import AwsumPathResolver, resource_storage_path
            from backend.services import images
            previous_merchant = db.get(Merchant, store.i_merchant_id)
            if store.c_logo_url or store.c_db_path:
                raise HTTPException(409, "Store has owned resources. Use a reviewed resource transfer before changing its Merchant.")
            if previous_merchant:
                try:
                    old_owner = AwsumPathResolver("STORE", merchant_code=previous_merchant.c_merchant_code,
                                                  store_code=store.c_store_code)
                except ValueError as exc:
                    raise HTTPException(409, "Store owner requires review before transfer.") from exc
                roots = (
                    resource_storage_path(old_owner.get_data_path("common")).parent,
                    images.image_storage_root().joinpath(*old_owner.get_context_root().lstrip("/").split("/")),
                )
                if any(root.is_dir() and any(file.is_file() for file in root.rglob("*")) for root in roots):
                    raise HTTPException(409, "Store has image/data files. Transfer resources before changing its Merchant.")
        store.i_merchant_id = merchant.i_merchant_id

    _apply_store_license_and_options(db, store, payload, creating=False)
    _stamp_updated(store, user)
    activation = _activate_organization(db, "STORE", store)
    warning = _commit_store_image(db, store, image_data=image_data, remove_image=remove_image)
    return JSONResponse({"success": True, "image_path": store.c_logo_url,
                         "image_url": image_url(store.c_logo_url), "image_warning": warning, **activation})


@router.delete("/platform/store/{store_id}")
def delete_store(store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store = _get_business_store(db, store_id)

    if store.c_is_test == 1 or store.c_store_code in LEGACY_TEST_STORE_CODES | {TEST_STORE_CODE}:
        raise HTTPException(status_code=403, detail="Test Store cannot be deleted.")

    store.c_status = "inactive"
    _stamp_updated(store, user)
    db.commit()
    return JSONResponse({"success": True})


@router.get("/platform/merchant/{merchant_id}/stores")
def list_merchant_stores(merchant_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found.")

    stores = (
        db.query(Store)
        .filter(Store.i_merchant_id == merchant_id)
        .order_by(Store.i_store_id)
        .all()
    )

    results = []
    for s in stores:
        bt = db.query(BusinessType).filter(BusinessType.i_business_type_id == s.i_business_type).first()
        results.append({
            "store_id": s.i_store_id,
            "store_seq": s.i_store_seq,
            "store_code": s.c_store_code,
            "store_name": s.c_store_name,
            "merchant_id": s.i_merchant_id,
            "merchant_name": merchant.c_merchant_name,
            "merchant_code": merchant.c_merchant_code,
            "business_type": bt.c_name if bt else None,
            "operation_type": s.c_operation_type,
            "status": s.c_status,
            "phone": s.c_phone,
            "email": s.c_email,
            "address_line1": s.c_address_line1,
            "address_line2": s.c_address_line2,
            "city": s.c_city,
            "state": s.c_state,
            "country": s.c_country,
            "zip": s.c_zip,
            "receipt_store_name": s.c_receipt_store_name,
            "receipt_phone": s.c_receipt_phone,
            "receipt_email": s.c_receipt_email,
            "receipt_website_url": s.c_receipt_website_url,
            "receipt_message": s.c_receipt_message,
            "contact_name": s.c_contact_name,
            "default_tax_rate": s.c_default_tax_rate,
            "timezone": s.c_timezone,
            "tax_source": s.c_tax_source,
            "memo": s.c_memo,
            "installed_by_agency_id": s.i_installed_by_agency_id,
        })
    return _set_no_cache_headers(JSONResponse(results))


@router.get("/platform/merchant/{merchant_id}/stores/{store_id}")
def get_merchant_store(merchant_id: int, store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    merchant, store = _ensure_merchant_store(db, merchant_id, store_id)
    bt = db.query(BusinessType).filter(BusinessType.i_business_type_id == store.i_business_type).first()

    data = {
        "store_id": store.i_store_id,
        "store_seq": store.i_store_seq,
        "store_code": store.c_store_code,
        "image_path": store.c_logo_url,
        "image_url": image_url(store.c_logo_url),
        "store_name": store.c_store_name,
        "merchant_id": store.i_merchant_id,
        "merchant_name": merchant.c_merchant_name,
        "merchant_code": merchant.c_merchant_code,
        "business_type": bt.c_name if bt else None,
        "operation_type": store.c_operation_type,
        "store_purpose": store.c_operation_type,
        "channel_type": store.c_channel_type,
        "device_type": store.c_device_type,
        "contact_name": store.c_contact_name,
        "owner_name": store.c_owner_name,
        "address": store.c_address_line1,
        "zip": store.c_zip,
        "address_line1": store.c_address_line1,
        "address_line2": store.c_address_line2,
        "city": store.c_city,
        "state": store.c_state,
        "country": store.c_country,
        "default_tax_rate": store.c_default_tax_rate,
        "timezone": store.c_timezone,
        "tax_source": store.c_tax_source,
        "phone": store.c_phone,
        "email": store.c_email,
        "receipt_store_name": store.c_receipt_store_name,
        "receipt_phone": store.c_receipt_phone,
        "receipt_email": store.c_receipt_email,
        "receipt_website_url": store.c_receipt_website_url,
        "receipt_message": store.c_receipt_message,
        "status": store.c_status,
        "store_status": store.c_status,
        "memo": store.c_memo,
        "installed_by_agency_id": store.i_installed_by_agency_id,
    }
    return _set_no_cache_headers(JSONResponse(data))


@router.post("/platform/merchant/{merchant_id}/stores")
def create_merchant_store(merchant_id: int, request: Request, payload: dict, db: Session = Depends(get_db)):
    payload = dict(payload or {})
    payload["merchant_id"] = merchant_id
    payload["_agency_optional"] = True
    return create_store(request, payload, db)


@router.put("/platform/merchant/{merchant_id}/stores/{store_id}")
def update_merchant_store(merchant_id: int, store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_merchant_store(db, merchant_id, store_id)

    payload = dict(payload or {})
    payload.pop("merchant_id", None)
    return update_store(store_id, payload, request, db)


@router.delete("/platform/merchant/{merchant_id}/stores/{store_id}")
def delete_merchant_store(merchant_id: int, store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_merchant_store(db, merchant_id, store_id)
    return delete_store(store_id, request, db)


@router.get("/platform/merchant/{merchant_id}/stores/{store_id}/devices")
def list_store_devices(merchant_id: int, store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_merchant_store(db, merchant_id, store_id)

    devices = (
        db.query(Device)
        .filter(Device.i_store_id == store_id)
        .order_by(Device.i_device_id)
        .all()
    )
    license_ids = [d.i_license_id for d in devices if d.i_license_id]
    license_map = {}
    if license_ids:
        for lic in db.query(License).filter(License.i_license_id.in_(license_ids)).all():
            license_map[lic.i_license_id] = lic

    agency_ids = [d.i_installed_by_agency_id for d in devices if d.i_installed_by_agency_id]
    agency_map = {}
    if agency_ids:
        for agency in db.query(Agency).filter(Agency.i_agency_id.in_(agency_ids)).all():
            agency_map[agency.i_agency_id] = agency

    return _set_no_cache_headers(JSONResponse([
        _serialize_device(device, license_map.get(device.i_license_id), agency_map.get(device.i_installed_by_agency_id)) for device in devices
    ]))


@router.get("/platform/merchant/{merchant_id}/stores/{store_id}/devices/{device_id}")
def get_store_device_detail(merchant_id: int, store_id: int, device_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_merchant_store(db, merchant_id, store_id)

    device = (
        db.query(Device)
        .filter(Device.i_device_id == device_id, Device.i_store_id == store_id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found for this store.")

    license_obj = db.query(License).filter(License.i_license_id == device.i_license_id).first() if device.i_license_id else None
    agency_obj = db.query(Agency).filter(Agency.i_agency_id == device.i_installed_by_agency_id).first() if device.i_installed_by_agency_id else None
    logs = (
        db.query(DeviceLog)
        .filter(DeviceLog.i_device_id == device.i_device_id)
        .order_by(DeviceLog.dt_event_time.desc(), DeviceLog.i_log_id.desc())
        .all()
    )

    return _set_no_cache_headers(JSONResponse({
        "device": _serialize_device(device, license_obj, agency_obj),
        "logs": [_serialize_device_log(log) for log in logs],
    }))


@router.post("/platform/merchant/{merchant_id}/stores/{store_id}/devices")
def create_store_device(merchant_id: int, store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_merchant_store(db, merchant_id, store_id)

    device_name = str(payload.get("device_name") or "").strip()
    if not device_name:
        raise HTTPException(status_code=400, detail="device_name is required.")

    license_id = _parse_optional_int(payload.get("license_id"), "license_id")
    license_obj = None
    if license_id is not None:
        license_obj = db.query(License).filter(License.i_license_id == license_id).first()
        if not license_obj:
            raise HTTPException(status_code=400, detail="Invalid license_id.")
        if license_obj.i_store_id != store_id:
            raise HTTPException(status_code=400, detail="License must belong to the same store.")

    installed_agent = _resolve_agency(payload.get("installed_by_agency_id"), db)

    device_type_id = _parse_optional_int(payload.get("device_type_id"), "device_type_id")
    serial_no = str(payload.get("serial_no") or "").strip() or None
    installed_at_raw = payload.get("installed_at")
    installed_at = None
    if installed_at_raw:
        try:
            from datetime import date
            installed_at = date.fromisoformat(str(installed_at_raw)[:10])
        except (ValueError, TypeError):
            installed_at = None
    memo = str(payload.get("memo") or "").strip() or None

    device = Device(
        i_store_id=store_id,
        i_license_id=license_id,
        i_installed_by_agency_id=installed_agent.i_agency_id if installed_agent else None,
        c_device_uuid=_generate_store_device_code(db, store_id),
        c_device_name=device_name,
        c_device_type=str(payload.get("device_type") or "").strip() or None,
        i_device_type_id=device_type_id,
        c_serial_no=serial_no,
        dt_installed_at=installed_at,
        c_memo=memo,
        c_activation_code=_generate_device_activation_code(db),
        dt_activation_expiry=datetime.now() + timedelta(minutes=DEVICE_ACTIVATION_TTL_MINUTES),
        c_status="inactive",
        c_note=str(payload.get("note") or "").strip() or None,
        c_last_ip=_resolve_request_ip(request, payload),
        dt_last_seen=None,
    )
    _stamp_created(device, user)
    db.add(device)
    db.flush()
    _log_device_event(
        db,
        device,
        "CREATED",
        request=request,
        payload=payload,
        action_by=_actor_label(user),
        note=f"Device created for store {store_id}.",
    )
    db.commit()
    db.refresh(device)

    return JSONResponse(_serialize_device(device, license_obj, installed_agent))


@router.put("/platform/merchant/{merchant_id}/stores/{store_id}/devices/{device_id}")
def update_store_device(merchant_id: int, store_id: int, device_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_merchant_store(db, merchant_id, store_id)

    device = (
        db.query(Device)
        .filter(Device.i_device_id == device_id, Device.i_store_id == store_id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found for this store.")

    if "device_name" in payload:
        device_name = str(payload.get("device_name") or "").strip()
        if not device_name:
            raise HTTPException(status_code=400, detail="device_name is required.")
        device.c_device_name = device_name

    if "device_type" in payload:
        device.c_device_type = str(payload.get("device_type") or "").strip() or None

    if "device_type_id" in payload:
        device.i_device_type_id = _parse_optional_int(payload.get("device_type_id"), "device_type_id")

    if "serial_no" in payload:
        device.c_serial_no = str(payload.get("serial_no") or "").strip() or None

    if "installed_at" in payload:
        raw_ia = payload.get("installed_at")
        if raw_ia:
            try:
                from datetime import date
                device.dt_installed_at = date.fromisoformat(str(raw_ia)[:10])
            except (ValueError, TypeError):
                device.dt_installed_at = None
        else:
            device.dt_installed_at = None

    if "memo" in payload:
        device.c_memo = str(payload.get("memo") or "").strip() or None

    if "status" in payload:
        normalized_status = str(payload.get("status") or "active").strip().lower() or "active"
        if normalized_status not in {"active", "inactive", "offline", "activated"}:
            raise HTTPException(status_code=400, detail="Invalid device status.")
        device.c_status = normalized_status

    if "last_seen" in payload:
        device.dt_last_seen = _parse_optional_iso_datetime(payload.get("last_seen"))

    if "note" in payload:
        device.c_note = str(payload.get("note") or "").strip() or None

    installed_agent = None
    if "installed_by_agency_id" in payload:
        installed_agent = _resolve_agency(payload.get("installed_by_agency_id"), db)
        device.i_installed_by_agency_id = installed_agent.i_agency_id if installed_agent else None
    elif device.i_installed_by_agency_id:
        installed_agent = db.query(Agency).filter(Agency.i_agency_id == device.i_installed_by_agency_id).first()

    license_obj = None
    if "license_id" in payload:
        license_id = _parse_optional_int(payload.get("license_id"), "license_id")
        if license_id is None:
            device.i_license_id = None
        else:
            license_obj = db.query(License).filter(License.i_license_id == license_id).first()
            if not license_obj:
                raise HTTPException(status_code=400, detail="Invalid license_id.")
            if license_obj.i_store_id != store_id:
                raise HTTPException(status_code=400, detail="License must belong to the same store.")
            device.i_license_id = license_id
    elif device.i_license_id:
        license_obj = db.query(License).filter(License.i_license_id == device.i_license_id).first()

    _stamp_updated(device, user)
    event_type = "UPDATED"
    if "status" in payload:
        if device.c_status == "offline":
            event_type = "OFFLINE"
        elif device.c_status == "activated":
            event_type = "ACTIVATED"
    _log_device_event(
        db,
        device,
        event_type,
        request=request,
        payload=payload,
        action_by=_actor_label(user),
        note=str(payload.get("note") or "").strip() or f"Device {event_type.lower()} via platform.",
    )
    db.commit()
    db.refresh(device)

    return JSONResponse(_serialize_device(device, license_obj, installed_agent))


@router.delete("/platform/merchant/{merchant_id}/stores/{store_id}/devices/{device_id}")
def delete_store_device(merchant_id: int, store_id: int, device_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_merchant_store(db, merchant_id, store_id)

    device = (
        db.query(Device)
        .filter(Device.i_device_id == device_id, Device.i_store_id == store_id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found for this store.")

    from backend.models_admin.license_device_link import LicenseDeviceLink
    if db.query(LicenseDeviceLink.i_link_id).filter(LicenseDeviceLink.i_device_id == device.i_device_id).first():
        raise HTTPException(status_code=409, detail="Device has license link history and cannot be deleted.")

    db.query(DeviceLog).filter(DeviceLog.i_device_id == device.i_device_id).delete(synchronize_session=False)
    db.delete(device)
    db.commit()
    return JSONResponse({"success": True})


@router.post("/platform/device/activate")
def activate_device(payload: dict, request: Request, db: Session = Depends(get_db)):
    activation_code = str(payload.get("activation_code") or "").strip().upper()
    hardware_id = str(payload.get("hardware_id") or "").strip()

    if not activation_code:
        raise HTTPException(status_code=400, detail="activation_code is required.")
    if not hardware_id:
        raise HTTPException(status_code=400, detail="hardware_id is required.")

    device = db.query(Device).filter(Device.c_activation_code == activation_code).first()
    if not device:
        raise HTTPException(status_code=404, detail="Invalid activation code.")

    if not device.dt_activation_expiry or device.dt_activation_expiry < datetime.now():
        raise HTTPException(status_code=410, detail="Activation code has expired.")

    if device.c_bound_hardware_id and device.c_bound_hardware_id != hardware_id:
        raise HTTPException(status_code=403, detail="Device is already bound to different hardware.")

    activated_at = datetime.now()
    device.c_bound_hardware_id = hardware_id
    device.c_status = "activated"
    device.dt_last_seen = activated_at
    device.dt_first_activated_at = device.dt_first_activated_at or activated_at
    device.c_activated_by = str(payload.get("activated_by") or payload.get("action_by") or hardware_id).strip() or device.c_activated_by
    device.c_last_ip = _resolve_request_ip(request, payload)
    device.c_os = str(payload.get("os") or device.c_os or "").strip() or device.c_os
    device.c_app_version = str(payload.get("app_version") or device.c_app_version or "").strip() or device.c_app_version
    if payload.get("note") is not None:
        device.c_note = str(payload.get("note") or "").strip() or device.c_note

    # One-time activation code: consume immediately.
    device.c_activation_code = None
    device.dt_activation_expiry = None

    _log_device_event(
        db,
        device,
        "ACTIVATED",
        request=request,
        payload=payload,
        action_by=device.c_activated_by,
        note=str(payload.get("note") or "").strip() or "Device activated.",
    )
    db.commit()
    db.refresh(device)

    return JSONResponse({
        "success": True,
        "device_id": device.i_device_id,
        "device_code": device.c_device_uuid,
        "store_id": device.i_store_id,
        "status": device.c_status,
        "bound_hardware_id": device.c_bound_hardware_id,
        "first_activated_at": device.dt_first_activated_at.isoformat() if device.dt_first_activated_at else None,
        "last_ip": device.c_last_ip,
    })


@router.post("/platform/device/validate")
def validate_device_binding(payload: dict, request: Request, db: Session = Depends(get_db)):
    device_code = str(payload.get("device_code") or "").strip()
    hardware_id = str(payload.get("hardware_id") or "").strip()

    if not device_code:
        raise HTTPException(status_code=400, detail="device_code is required.")
    if not hardware_id:
        raise HTTPException(status_code=400, detail="hardware_id is required.")

    device = db.query(Device).filter(Device.c_device_uuid == device_code).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    if str(device.c_status or "").lower() != "activated":
        raise HTTPException(status_code=403, detail="Device is not activated.")

    if not device.c_bound_hardware_id:
        raise HTTPException(status_code=403, detail="Device is not bound to hardware.")

    # License-based devices: authorized only while an active link and a valid license exist.
    from backend.models_admin.license_device_link import LicenseDeviceLink
    if db.query(LicenseDeviceLink.i_link_id).filter(LicenseDeviceLink.i_device_id == device.i_device_id).first():
        active_link = (
            db.query(LicenseDeviceLink)
            .filter(LicenseDeviceLink.i_device_id == device.i_device_id, LicenseDeviceLink.dt_unlinked_at.is_(None))
            .first()
        )
        if not active_link:
            raise HTTPException(status_code=403, detail="Device is no longer linked to a license.")
        linked_license = db.query(License).filter(License.i_license_id == active_link.i_license_id).first()
        license_status = str(getattr(linked_license, "c_status", "") or "").lower()
        if (
            not linked_license
            or license_status not in {"active", "trial"}
            or (linked_license.dt_end and linked_license.dt_end < datetime.now())
        ):
            raise HTTPException(status_code=403, detail="License is not active.")

    if device.c_bound_hardware_id != hardware_id:
        raise HTTPException(status_code=403, detail="Hardware mismatch.")

    device.dt_last_seen = datetime.now()
    device.c_last_ip = _resolve_request_ip(request, payload)
    if payload.get("os") is not None:
        device.c_os = str(payload.get("os") or "").strip() or device.c_os
    if payload.get("app_version") is not None:
        device.c_app_version = str(payload.get("app_version") or "").strip() or device.c_app_version
    if payload.get("note") is not None:
        device.c_note = str(payload.get("note") or "").strip() or device.c_note

    _log_device_event(
        db,
        device,
        "LOGIN",
        request=request,
        payload=payload,
        action_by=str(payload.get("action_by") or payload.get("hardware_id") or "").strip() or None,
        note=str(payload.get("note") or "").strip() or "Device validation/login.",
    )
    db.commit()

    return JSONResponse({
        "success": True,
        "device_id": device.i_device_id,
        "store_id": device.i_store_id,
        "status": device.c_status,
        "last_ip": device.c_last_ip,
        "last_seen": device.dt_last_seen.isoformat() if device.dt_last_seen else None,
    })


# Agencies

@router.get("/platform/agency-types")
def list_agency_types(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(AgencyType)

    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(AgencyType.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                AgencyType.c_agency_type_code.ilike(term),
                AgencyType.c_agency_type_name.ilike(term),
            )
        )

    items = query.order_by(AgencyType.i_agency_type_id).all()
    return JSONResponse([
        {
            "id": item.i_agency_type_id,
            "agency_type_code": item.c_agency_type_code,
            "agency_type_name": item.c_agency_type_name,
            "status": item.c_status,
        }
        for item in items
    ])


def _validate_agency_type_fields(type_code: str, type_name: str) -> None:
    if not type_code:
        raise HTTPException(status_code=400, detail="Agency Type Code is required.")
    if not type_name:
        raise HTTPException(status_code=400, detail="Agency Type Name is required.")
    if len(type_code) > 30:
        raise HTTPException(status_code=400, detail="Agency Type Code must be 30 characters or fewer.")
    if len(type_name) > 120:
        raise HTTPException(status_code=400, detail="Agency Type Name must be 120 characters or fewer.")


@router.post("/platform/agency-type")
def create_agency_type(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    type_code = str(payload.get("agency_type_code") or "").strip().upper()
    type_name = str(payload.get("agency_type_name") or "").strip()
    _validate_agency_type_fields(type_code, type_name)

    try:
        status_value = _normalize_status(payload.get("status"), {"active", "inactive"}) or "active"
    except HTTPException:
        raise HTTPException(status_code=400, detail="Status must be Active or Inactive.")

    item = AgencyType(
        c_agency_type_code=type_code,
        c_agency_type_name=type_name,
        c_status=status_value,
    )
    db.add(item)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Agency Type Code already exists.")
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to save the agency type. Please try again.")

    db.refresh(item)
    return JSONResponse({"id": item.i_agency_type_id})


@router.put("/platform/agency-type/{agency_type_id}")
def update_agency_type(agency_type_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(AgencyType).filter(AgencyType.i_agency_type_id == agency_type_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Agency type not found")

    type_code = str(payload.get("agency_type_code", item.c_agency_type_code) or "").strip().upper()
    type_name = str(payload.get("agency_type_name", item.c_agency_type_name) or "").strip()
    _validate_agency_type_fields(type_code, type_name)
    item.c_agency_type_code = type_code
    item.c_agency_type_name = type_name
    if "status" in payload:
        try:
            item.c_status = _normalize_status(payload.get("status"), {"active", "inactive"}) or "active"
        except HTTPException:
            raise HTTPException(status_code=400, detail="Status must be Active or Inactive.")

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Agency Type Code already exists.")
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to save the agency type. Please try again.")

    return JSONResponse({"success": True})


@router.delete("/platform/agency-type/{agency_type_id}")
def delete_agency_type(agency_type_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(AgencyType).filter(AgencyType.i_agency_type_id == agency_type_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Agency type not found")

    linked_agent_count = db.query(Agency.i_agency_id).filter(Agency.i_agency_type_id == agency_type_id).count()
    if linked_agent_count > 0:
        item.c_status = "inactive"
        db.commit()
        return JSONResponse({"success": True, "mode": "soft-delete", "status": "inactive"})

    db.delete(item)
    db.commit()
    return JSONResponse({"success": True, "mode": "hard-delete"})

@router.get("/platform/agencies")
def list_agents(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(Agency, AgencyType).outerjoin(AgencyType, Agency.i_agency_type_id == AgencyType.i_agency_type_id)

    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(Agency.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        code_term = f"%{search.strip().replace('_', '')}%"
        query = query.filter(
            or_(
                Agency.c_agency_code.ilike(term),
                Agency.c_agency_code.ilike(code_term),
                Agency.c_agency_name.ilike(term),
                Agency.c_company_name.ilike(term),
                Agency.c_contact_name.ilike(term),
                Agency.c_email.ilike(term),
                Agency.c_phone.ilike(term),
                AgencyType.c_agency_type_name.ilike(term),
            )
        )

    items = query.order_by(Agency.i_agency_id).all()
    from backend.services.business_contact import BusinessContactService
    primary_names = BusinessContactService(db).primary_names("AGENCY", [agency.i_agency_id for agency, _ in items])
    agency_admin_ids = {
        agency_id for (agency_id,) in db.query(PlatformUser.i_agency_id)
        .join(Role, Role.i_role_id == PlatformUser.i_role_id)
        .filter(
            PlatformUser.i_agency_id.in_([agency.i_agency_id for agency, _ in items]),
            Role.c_name == AGENCY_ADMIN_ROLE_NAME,
        ).all()
    }
    return JSONResponse([
        {
            "id": agency.i_agency_id,
            "image_path": agency.c_logo_url,
            "image_url": image_url(agency.c_logo_url),
            "has_agency_admin": agency.i_agency_id in agency_admin_ids,
            "agency_type_id": agency.i_agency_type_id,
            "agency_type_code": agency_type.c_agency_type_code if agency_type else None,
            "agency_type_name": agency_type.c_agency_type_name if agency_type else None,
            "agency_code": agency.c_agency_code,
            "agent_name": agency.c_contact_name or agency.c_agency_name,
            "company_name": agency.c_company_name,
            "contact_name": primary_names.get(agency.i_agency_id) or agency.c_contact_name or "",
            "phone": agency.c_phone,
            "email": agency.c_email,
            "address_line1": agency.c_address_line1,
            "address_line2": agency.c_address_line2,
            "city": agency.c_city,
            "state": agency.c_state,
            "zip": agency.c_zip,
            "country": agency.c_country,
            "commission_rate": float(agency.n_commission_rate) if agency.n_commission_rate is not None else None,
            "memo": agency.c_memo,
            "status": agency.c_status,
            "created_at": agency.dt_created_at.isoformat() if agency.dt_created_at else None,
        }
        for agency, agency_type in items
    ])


_AGENCY_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_agency_email(value: object) -> Optional[str]:
    email = str(value or "").strip()
    if not email:
        return None
    if len(email) > 255 or not _AGENCY_EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    return email


def _parse_agency_commission(value: object) -> Optional[Decimal]:
    try:
        rate = _parse_optional_decimal(value, "commission_rate")
    except HTTPException:
        raise HTTPException(status_code=400, detail="Commission Rate must be a number between 0 and 100.")
    if rate is not None and (not rate.is_finite() or rate < 0 or rate > 100):
        raise HTTPException(status_code=400, detail="Commission Rate must be a number between 0 and 100.")
    return rate


def _check_agency_lengths(payload: dict) -> None:
    limits = {
        "company_name": 255, "contact_name": 120, "phone": 50, "email": 255, "address_line1": 255,
        "address_line2": 255, "city": 120, "state": 120, "zip": 20, "country": 120, "memo": 4000,
    }
    for key, limit in limits.items():
        if len(str(payload.get(key) or "").strip()) > limit:
            raise HTTPException(status_code=400, detail=f"{key.replace('_', ' ').title()} must be {limit} characters or fewer.")

def _agency_phone_password(phone: object) -> str | None:
    return initial_phone_password(phone)


def _activate_agency(db: Session, agency: Agency) -> dict:
    return _activate_organization(db, "AGENCY", agency)


def _activate_organization(db: Session, owner_type: str, owner) -> dict:
    _, field, _, _, role_name = registration_owner_config(owner_type)
    owner_id = getattr(owner, field)
    label = owner_type.title()
    if owner.c_status != "active":
        return {}
    existing_query = organization_user_query(db, owner_type, owner_id) if owner_type == "AGENCY" else \
        organization_admin_query(db, owner_type, owner_id)
    existing = existing_query.first()
    if existing:
        return {f"{owner_type.lower()}_user_created": False, "login_email": existing.c_email}
    contact = BusinessContactService(db).get_primary_contact(owner_type, owner_id)
    if not contact:
        raise HTTPException(400, f"Save the {label} as Inactive, add an active Primary Contact, then activate it.")
    email = _platform_user_email(contact.c_email)
    first_name, last_name = _platform_user_name(
        {"first_name": contact.c_first_name, "last_name": contact.c_last_name}
    )
    if organization_user_query(db, owner_type, owner_id).filter(
        func.lower(func.trim(PlatformUser.c_email)) == email,
    ).first():
        raise HTTPException(409, f"A {label} User with the Primary Contact Email already exists. Review its Admin Role.")
    role = db.query(Role).filter(Role.c_name == role_name).first()
    if not role or (role.c_status or "").lower() != "active":
        raise HTTPException(400, f"An active {role_name} Role is required. Apply the registration user migration.")
    password = initial_phone_password(contact.c_phone) or secrets.token_urlsafe(18)
    scope = {field: owner_id}
    if owner_type != "STORE":
        scope["i_store_id"] = ADMIN_STORE_ID
    agency_user = PlatformUser(
        **scope, i_role_id=role.i_role_id,
        c_first_name=first_name, c_last_name=last_name, c_email=email, c_phone=contact.c_phone,
        c_password=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        c_status="active", i_must_change_password=0,
    )
    db.add(agency_user)
    db.flush()
    return {f"{owner_type.lower()}_user_created": True, "login_email": email,
            "temporary_password": password}


@router.post("/platform/agency")
def create_agent(payload: dict, request: Request, db: Session = Depends(get_db)):
    return _create_agent(payload, request, db)


def _commit_organization_image(
    db: Session, item: Agency | Merchant, context: str, *,
    image_data: bytes | None = None, remove_image: bool = False,
) -> str | None:
    if image_data is None and not remove_image:
        db.commit()
        return None
    db.flush()
    identity = item.i_agency_id if context == "AGENCY" else item.i_merchant_id
    owner = AwsumStorageService(db).resolve_owner(context, identity)
    with image_operation_lock(owner, "logo"):
        return commit_entity_image(db, item, owner, "logo", category="logo",
                                   image_data=image_data, remove_image=remove_image,
                                   reference_field="c_logo_url")


@router.post("/platform/agency/with-image")
@router.put("/platform/agency/{record_id}/with-image")
@router.post("/platform/merchant/with-image")
@router.put("/platform/merchant/{record_id}/with-image")
def save_organization_with_image(
    request: Request, payload: str = Form(...), image: UploadFile | None = File(None),
    remove_image: bool = Form(False), record_id: int | None = None,
    db: Session = Depends(get_db),
):
    from backend.routers.system_settings import _validate_origin
    require_platform_admin(get_current_user(request, db))
    _validate_origin(request)
    if request.method == "POST" and record_id is not None:
        raise HTTPException(422, "Record ID cannot be supplied when creating an organization.")
    agency = request.url.path.startswith("/platform/agency/")
    model, key = (Agency, Agency.i_agency_id) if agency else (Merchant, Merchant.i_merchant_id)
    if record_id is not None and not db.query(model).filter(key == record_id).first():
        raise HTTPException(404, "Organization not found.")
    values, data = image_mutation_form(payload, remove_image, image)
    if agency:
        response = (_create_agent(values, request, db, image_data=data) if record_id is None else
                    _update_agent(record_id, values, request, db, image_data=data, remove_image=remove_image))
    else:
        response = (_create_merchant(values, request, db, image_data=data) if record_id is None else
                    _update_merchant(record_id, values, request, db, image_data=data, remove_image=remove_image))
    result = json.loads(response.body)
    item = db.query(model).filter(key == (record_id or result["id"])).one()
    result.update(image_path=item.c_logo_url, image_url=image_url(item.c_logo_url))
    return _set_no_cache_headers(JSONResponse(result))


def _create_agent(payload: dict, request: Request, db: Session, *, image_data: bytes | None = None):
    user = get_current_user(request, db)
    require_platform_admin(user)

    _check_agency_lengths(payload)
    # Person names are managed by the common Business Contact (First/Last); legacy contact_name is optional.
    contact_name = str(payload.get("contact_name") or payload.get("agent_name") or "").strip()
    company_name = str(payload.get("company_name") or "").strip()
    if not company_name:
        raise HTTPException(status_code=400, detail="Company Name is required.")

    agency_type_id = _parse_optional_int(payload.get("agency_type_id"), "agency_type_id")
    if agency_type_id is None:
        raise HTTPException(status_code=400, detail="Agency Type is required.")
    agency_type = db.query(AgencyType).filter(AgencyType.i_agency_type_id == agency_type_id).first()
    if not agency_type:
        raise HTTPException(status_code=400, detail="Invalid Agency Type.")
    email = _validate_agency_email(payload.get("email"))
    commission_rate = _parse_agency_commission(payload.get("commission_rate"))

    item = Agency(
        i_agency_type_id=agency_type_id,
        c_agency_code=_generate_agency_code(db),
        c_agency_name=(contact_name or company_name)[:120],
        c_company_name=company_name,
        c_contact_name=contact_name or None,
        c_phone=str(payload.get("phone") or "").strip() or None,
        c_email=email,
        c_address_line1=str(payload.get("address_line1") or "").strip() or None,
        c_address_line2=str(payload.get("address_line2") or "").strip() or None,
        c_city=str(payload.get("city") or "").strip() or None,
        c_state=str(payload.get("state") or "").strip() or None,
        c_zip=str(payload.get("zip") or "").strip() or None,
        c_country=_normalize_country(payload.get("country")),
        n_commission_rate=commission_rate,
        c_memo=str(payload.get("memo") or "").strip() or None,
        c_status=_normalize_status(payload.get("status"), {"active", "inactive"}) or "inactive",
    )
    try:
        db.add(item)
        db.flush()
        activation = _activate_agency(db, item)
        warning = _commit_organization_image(db, item, "AGENCY", image_data=image_data)
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logging.getLogger(__name__).exception("Unable to create Agency")
        raise HTTPException(status_code=500, detail="Unable to create the agency. Please try again.")
    db.refresh(item)

    storage = AwsumStorageService(db).initialize_after_registration("AGENCY", item.i_agency_id)
    return _set_no_cache_headers(JSONResponse(
        {"id": item.i_agency_id, "agency_code": item.c_agency_code, **storage, **activation,
         **({"image_warning": warning} if image_data is not None else {})}
    ))


@router.put("/platform/agency/{agency_id}")
def update_agent(agency_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return _update_agent(agency_id, payload, request, db)


def _update_agent(agency_id: int, payload: dict, request: Request, db: Session,
                   *, image_data: bytes | None = None, remove_image: bool = False):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(Agency).filter(Agency.i_agency_id == agency_id).with_for_update().first()
    if not item:
        raise HTTPException(status_code=404, detail="Agency not found")

    _check_agency_lengths(payload)
    if "agency_type_id" in payload:
        agency_type_id = _parse_optional_int(payload.get("agency_type_id"), "agency_type_id")
        if agency_type_id is None:
            raise HTTPException(status_code=400, detail="Agency Type is required.")
        agency_type = db.query(AgencyType).filter(AgencyType.i_agency_type_id == agency_type_id).first()
        if not agency_type:
            raise HTTPException(status_code=400, detail="Invalid Agency Type.")
        item.i_agency_type_id = agency_type_id
    if "contact_name" in payload or "agent_name" in payload:
        contact_name = str(payload.get("contact_name") or payload.get("agent_name") or "").strip()
        if not contact_name:
            raise HTTPException(status_code=400, detail="Contact Name is required.")
        item.c_agency_name = contact_name
        item.c_contact_name = contact_name
    if "company_name" in payload:
        company_name = str(payload.get("company_name") or "").strip()
        if not company_name:
            raise HTTPException(status_code=400, detail="Company Name is required.")
        item.c_company_name = company_name
    if "phone" in payload:
        item.c_phone = str(payload.get("phone") or "").strip() or None
    if "email" in payload:
        item.c_email = _validate_agency_email(payload.get("email"))
    if "address_line1" in payload:
        item.c_address_line1 = str(payload.get("address_line1") or "").strip() or None
    if "address_line2" in payload:
        item.c_address_line2 = str(payload.get("address_line2") or "").strip() or None
    if "city" in payload:
        item.c_city = str(payload.get("city") or "").strip() or None
    if "state" in payload:
        item.c_state = str(payload.get("state") or "").strip() or None
    if "zip" in payload:
        item.c_zip = str(payload.get("zip") or "").strip() or None
    if "country" in payload:
        item.c_country = _normalize_country(payload.get("country"))
    if "commission_rate" in payload:
        item.n_commission_rate = _parse_agency_commission(payload.get("commission_rate"))
    if "memo" in payload:
        item.c_memo = str(payload.get("memo") or "").strip() or None
    if "status" in payload:
        item.c_status = _normalize_status(payload.get("status"), {"active", "inactive"}) or "active"

    try:
        activation = _activate_agency(db, item)
        warning = _commit_organization_image(db, item, "AGENCY", image_data=image_data, remove_image=remove_image)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A user with this login email already exists.")
    except SQLAlchemyError:
        db.rollback()
        logging.getLogger(__name__).exception("Unable to save Agency activation")
        raise HTTPException(status_code=500, detail="Unable to save the agency. Please try again.")
    return _set_no_cache_headers(JSONResponse({"success": True, "id": agency_id, **activation,
        **({"image_warning": warning} if image_data is not None or remove_image else {})}))


def _registration_email(db: Session, owner_type: str, owner_id: int, payload: dict, request: Request):
    from backend.routers.auth import password_matches

    _, _, code_field, name_field, _ = registration_owner_config(owner_type)
    owner = get_registration_owner(db, owner_type, owner_id)
    if owner.c_status != "active":
        raise HTTPException(400, f"An active {owner_type.title()} is required.")
    agency_user = organization_admin_query(db, owner_type, owner_id).order_by(
        (PlatformUser.c_status == "active").desc(), PlatformUser.i_platform_user_id,
    ).first()
    if not agency_user or agency_user.c_status != "active":
        raise HTTPException(400, f"An active {owner_type.title()} User is required.")
    contact = BusinessContactService(db).get_primary_contact(owner_type, owner_id)
    if not contact:
        raise HTTPException(400, "No active Primary Contact is registered. Please designate a Primary Contact first.")
    if not (contact.c_email or "").strip():
        raise HTTPException(400, "The Primary Contact has no Email. Please enter the Primary Contact Email first.")
    try:
        primary_email = validate_email_address(contact.c_email)
    except ValueError as exc:
        raise HTTPException(400, "The Primary Contact Email is invalid. Please correct it first.") from exc
    try:
        recipient = validate_email_address(payload.get("recipient", primary_email))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    password = str(payload.get("temporary_password") or "")
    if password and not password_matches(agency_user, password):
        raise HTTPException(400, "The temporary password is no longer valid. Reopen without it to send login information.")
    if not password and agency_user.dt_password_changed is None:
        # The User phone is the creation-time snapshot, not the current Contact phone.
        candidate = initial_phone_password(agency_user.c_phone)
        if candidate and password_matches(agency_user, candidate):
            password = candidate
    code = getattr(owner, code_field)
    login_url = str(request.base_url).rstrip("/") + f"/login/{owner_type.lower()}/{code}"
    company = load_company_branding(db)
    return build_registration_email(
        recipient=recipient, company=company, owner_type=owner_type,
        name=(owner.c_company_name or owner.c_agency_name) if owner_type == "AGENCY" else getattr(owner, name_field),
        code=code, login_email=agency_user.c_email,
        login_url=login_url, initial_password=password, must_change_password=agency_user.i_must_change_password == 1,
    )


@router.post("/platform/agency/{agency_id}/registration-email/preview")
def preview_agency_registration_email(agency_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return preview_registration_email("AGENCY", agency_id, payload, request, db)


@router.post("/platform/registration-email/{owner_type}/{owner_id}/preview")
def preview_registration_email(owner_type: str, owner_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    require_platform_admin(get_current_user(request, db))
    message = _registration_email(db, owner_type, owner_id, payload, request)
    return _set_no_cache_headers(JSONResponse({
        "html": AwsumEmailLayout.preview_message(message),
        "subject": str(message["Subject"]), "recipient": str(message["To"]),
        "message": message.get_body(preferencelist=("plain",)).get_content(),
    }))


@router.post("/platform/agency/{agency_id}/registration-email")
def send_agency_registration_email(agency_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return send_registration_email("AGENCY", agency_id, payload, request, db)


@router.post("/platform/registration-email/{owner_type}/{owner_id}")
def send_registration_email(owner_type: str, owner_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    require_platform_admin(get_current_user(request, db))
    from backend.routers.system_settings import _validate_origin
    _validate_origin(request)
    message = _registration_email(db, owner_type, owner_id, payload, request)
    try:
        message_id, _ = send_configured_email(db, message)
    except EmailConfigurationError as exc:
        logging.getLogger(__name__).warning("%s registration email configuration failed: %s", owner_type, exc)
        raise HTTPException(503, str(exc)) from exc
    except MailDeliveryError as exc:
        logging.getLogger(__name__).warning("%s registration email was not accepted: %s", owner_type, exc)
        raise HTTPException(502, str(exc)) from exc
    return _set_no_cache_headers(JSONResponse({
        "accepted": True, "recipient": str(message["To"]), "message_id": message_id,
        "delivery_note": "Accepted by the mail server; recipient delivery is not confirmed.",
    }))


@router.delete("/platform/agency/{agency_id}")
def delete_agent(agency_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(Agency).filter(Agency.i_agency_id == agency_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Agency not found")

    item.c_status = "inactive"
    db.commit()
    return JSONResponse({"success": True})


# Licenses

@router.get("/platform/licenses")
def list_licenses(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(License)

    normalized_status = _normalize_status(status, {"active", "inactive", "suspended", "trial", "expired"})
    if normalized_status:
        query = query.filter(License.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                License.c_license_key.ilike(term),
                License.c_plan_name.ilike(term),
                License.c_license_type.ilike(term),
            )
        )

    items = query.order_by(License.i_license_id.desc()).all()

    store_ids = [item.i_store_id for item in items if item.i_store_id is not None]
    merchant_ids = [item.i_merchant_id for item in items if item.i_merchant_id is not None]
    agency_ids = [item.i_agency_id for item in items if item.i_agency_id is not None]

    store_map = {
        row.i_store_id: row.c_store_name
        for row in db.query(Store).filter(Store.i_store_id.in_(store_ids)).all()
    } if store_ids else {}
    merchant_map = {
        row.i_merchant_id: row.c_merchant_name
        for row in db.query(Merchant).filter(Merchant.i_merchant_id.in_(merchant_ids)).all()
    } if merchant_ids else {}
    agency_map = {
        row.i_agency_id: row.c_agency_name
        for row in db.query(Agency).filter(Agency.i_agency_id.in_(agency_ids)).all()
    } if agency_ids else {}

    return JSONResponse([
        {
            "id": item.i_license_id,
            "license_key": item.c_license_key,
            "plan_name": item.c_plan_name,
            "license_type": item.c_license_type,
            "store_id": item.i_store_id,
            "store_name": store_map.get(item.i_store_id),
            "merchant_id": item.i_merchant_id,
            "merchant_name": merchant_map.get(item.i_merchant_id),
            "agency_id": item.i_agency_id,
            "agent_name": agency_map.get(item.i_agency_id),
            "max_devices": item.i_max_devices,
            "max_users": item.i_max_users,
            "start_date": item.dt_start.isoformat() if item.dt_start else None,
            "end_date": item.dt_end.isoformat() if item.dt_end else None,
            "monthly_fee": float(item.n_monthly_fee) if item.n_monthly_fee is not None else None,
            "agency_commission": float(item.n_agency_commission) if item.n_agency_commission is not None else None,
            "platform_fee": float(item.n_platform_fee) if item.n_platform_fee is not None else None,
            "status": item.c_status or "active",
        }
        for item in items
    ])


@router.post("/platform/license")
def create_license(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    license_key = str(payload.get("license_key") or "").strip()
    if not license_key:
        raise HTTPException(status_code=400, detail="license_key is required")

    store_id = _parse_optional_int(payload.get("store_id"), "store_id")
    if store_id is None:
        raise HTTPException(status_code=400, detail="store_id is required")

    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=400, detail="Invalid store_id")

    merchant_id = _parse_optional_int(payload.get("merchant_id"), "merchant_id")
    if merchant_id is not None:
        merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
        if not merchant:
            raise HTTPException(status_code=400, detail="Invalid merchant_id")

    agency_id = _parse_optional_int(payload.get("agency_id"), "agency_id")
    if agency_id is not None:
        agency = db.query(Agency).filter(Agency.i_agency_id == agency_id).first()
        if not agency:
            raise HTTPException(status_code=400, detail="Invalid agency_id")

    item = License(
        c_license_key=license_key,
        c_plan_name=str(payload.get("plan_name") or "").strip() or None,
        c_license_type=str(payload.get("license_type") or "").strip() or None,
        i_store_id=store_id,
        i_merchant_id=merchant_id,
        i_agency_id=agency_id,
        i_max_devices=_parse_optional_int(payload.get("max_devices"), "max_devices"),
        i_max_users=_parse_optional_int(payload.get("max_users"), "max_users"),
        dt_start=_parse_optional_iso_datetime(payload.get("start_date")),
        dt_end=_parse_optional_iso_datetime(payload.get("end_date")),
        n_monthly_fee=_parse_optional_decimal(payload.get("monthly_fee"), "monthly_fee"),
        n_agency_commission=_parse_optional_decimal(payload.get("agency_commission"), "agency_commission"),
        n_platform_fee=_parse_optional_decimal(payload.get("platform_fee"), "platform_fee"),
        c_status=_normalize_status(payload.get("status"), {"active", "inactive", "suspended", "trial", "expired"}) or "active",
    )
    _stamp_created(item, user)
    db.add(item)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="license_key already exists")

    db.refresh(item)
    return JSONResponse({"id": item.i_license_id})


@router.put("/platform/license/{license_id}")
def update_license(license_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(License).filter(License.i_license_id == license_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="License not found")

    if "license_key" in payload:
        license_key = str(payload.get("license_key") or "").strip()
        if not license_key:
            raise HTTPException(status_code=400, detail="license_key is required")
        item.c_license_key = license_key
    if "plan_name" in payload:
        item.c_plan_name = str(payload.get("plan_name") or "").strip() or None
    if "license_type" in payload:
        item.c_license_type = str(payload.get("license_type") or "").strip() or None
    if "store_id" in payload:
        store_id = _parse_optional_int(payload.get("store_id"), "store_id")
        if store_id is None:
            raise HTTPException(status_code=400, detail="store_id is required")
        store = db.query(Store).filter(Store.i_store_id == store_id).first()
        if not store:
            raise HTTPException(status_code=400, detail="Invalid store_id")
        item.i_store_id = store_id
    if "merchant_id" in payload:
        merchant_id = _parse_optional_int(payload.get("merchant_id"), "merchant_id")
        if merchant_id is not None:
            merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
            if not merchant:
                raise HTTPException(status_code=400, detail="Invalid merchant_id")
        item.i_merchant_id = merchant_id
    if "agency_id" in payload:
        agency_id = _parse_optional_int(payload.get("agency_id"), "agency_id")
        if agency_id is not None:
            agency = db.query(Agency).filter(Agency.i_agency_id == agency_id).first()
            if not agency:
                raise HTTPException(status_code=400, detail="Invalid agency_id")
        item.i_agency_id = agency_id
    if "max_devices" in payload:
        item.i_max_devices = _parse_optional_int(payload.get("max_devices"), "max_devices")
    if "max_users" in payload:
        item.i_max_users = _parse_optional_int(payload.get("max_users"), "max_users")
    if "start_date" in payload:
        item.dt_start = _parse_optional_iso_datetime(payload.get("start_date"))
    if "end_date" in payload:
        item.dt_end = _parse_optional_iso_datetime(payload.get("end_date"))
    if "monthly_fee" in payload:
        item.n_monthly_fee = _parse_optional_decimal(payload.get("monthly_fee"), "monthly_fee")
    if "agency_commission" in payload:
        item.n_agency_commission = _parse_optional_decimal(payload.get("agency_commission"), "agency_commission")
    if "platform_fee" in payload:
        item.n_platform_fee = _parse_optional_decimal(payload.get("platform_fee"), "platform_fee")
    if "status" in payload:
        item.c_status = _normalize_status(payload.get("status"), {"active", "inactive", "suspended", "trial", "expired"}) or "active"

    _stamp_updated(item, user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="license_key already exists")

    return JSONResponse({"success": True})


@router.delete("/platform/license/{license_id}")
def delete_license(license_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(License).filter(License.i_license_id == license_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="License not found")

    from backend.models_admin.license_device_link import LicenseDeviceLink
    if db.query(LicenseDeviceLink.i_link_id).filter(LicenseDeviceLink.i_license_id == license_id).first():
        raise HTTPException(status_code=409, detail="License has device link history; revoke it instead of deleting.")

    item.c_status = "inactive"
    _stamp_updated(item, user)
    db.commit()
    return JSONResponse({"success": True})


# Payment Methods

@router.get("/platform/payment-methods")
def list_payment_methods(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(PaymentMethod)

    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(PaymentMethod.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                PaymentMethod.c_payment_type.ilike(term),
                PaymentMethod.c_billing_cycle.ilike(term),
                PaymentMethod.c_card_token.ilike(term),
                PaymentMethod.c_bank_account.ilike(term),
            )
        )

    items = query.order_by(PaymentMethod.i_payment_id.desc()).all()
    merchant_ids = [item.i_merchant_id for item in items if item.i_merchant_id is not None]
    merchant_map = {
        row.i_merchant_id: row.c_merchant_name
        for row in db.query(Merchant).filter(Merchant.i_merchant_id.in_(merchant_ids)).all()
    } if merchant_ids else {}

    return JSONResponse([
        {
            "id": item.i_payment_id,
            "merchant_id": item.i_merchant_id,
            "merchant_name": merchant_map.get(item.i_merchant_id),
            "payment_type": item.c_payment_type,
            "card_token": item.c_card_token,
            "bank_account": item.c_bank_account,
            "billing_cycle": item.c_billing_cycle,
            "next_billing": item.dt_next_billing.isoformat() if item.dt_next_billing else None,
            "status": item.c_status or "active",
        }
        for item in items
    ])


@router.post("/platform/payment-method")
def create_payment_method(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    payment_type = str(payload.get("payment_type") or "").strip()
    if not payment_type:
        raise HTTPException(status_code=400, detail="payment_type is required")

    merchant_id = _parse_optional_int(payload.get("merchant_id"), "merchant_id")
    if merchant_id is not None:
        merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
        if not merchant:
            raise HTTPException(status_code=400, detail="Invalid merchant_id")

    item = PaymentMethod(
        i_merchant_id=merchant_id,
        c_payment_type=payment_type,
        c_card_token=str(payload.get("card_token") or "").strip() or None,
        c_bank_account=str(payload.get("bank_account") or "").strip() or None,
        c_billing_cycle=str(payload.get("billing_cycle") or "").strip() or None,
        dt_next_billing=_parse_optional_iso_datetime(payload.get("next_billing")),
        c_status=_normalize_status(payload.get("status"), {"active", "inactive"}) or "active",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return JSONResponse({"id": item.i_payment_id})


@router.put("/platform/payment-method/{payment_id}")
def update_payment_method(payment_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(PaymentMethod).filter(PaymentMethod.i_payment_id == payment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Payment method not found")

    if "merchant_id" in payload:
        merchant_id = _parse_optional_int(payload.get("merchant_id"), "merchant_id")
        if merchant_id is not None:
            merchant = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
            if not merchant:
                raise HTTPException(status_code=400, detail="Invalid merchant_id")
        item.i_merchant_id = merchant_id
    if "payment_type" in payload:
        payment_type = str(payload.get("payment_type") or "").strip()
        if not payment_type:
            raise HTTPException(status_code=400, detail="payment_type is required")
        item.c_payment_type = payment_type
    if "card_token" in payload:
        item.c_card_token = str(payload.get("card_token") or "").strip() or None
    if "bank_account" in payload:
        item.c_bank_account = str(payload.get("bank_account") or "").strip() or None
    if "billing_cycle" in payload:
        item.c_billing_cycle = str(payload.get("billing_cycle") or "").strip() or None
    if "next_billing" in payload:
        item.dt_next_billing = _parse_optional_iso_datetime(payload.get("next_billing"))
    if "status" in payload:
        item.c_status = _normalize_status(payload.get("status"), {"active", "inactive"}) or "active"

    db.commit()
    return JSONResponse({"success": True})


@router.delete("/platform/payment-method/{payment_id}")
def delete_payment_method(payment_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(PaymentMethod).filter(PaymentMethod.i_payment_id == payment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Payment method not found")

    item.c_status = "inactive"
    db.commit()
    return JSONResponse({"success": True})


@router.get("/platform/store-next-id")
def next_merchant_store_id(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    next_code, next_seq = _generate_merchant_store_code(db)
    return JSONResponse({"next_store_code": next_code, "next_store_seq": next_seq})


@router.get("/platform/store/export")
def export_stores(
    request: Request,
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    operation_type: Optional[str] = None,
    channel_type: Optional[str] = None,
    device_type: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(Store).filter(Store.i_store_id != ADMIN_STORE_ID)
    if status:
        query = query.filter(Store.c_status == status)

    if operation_type:
        query = query.filter(Store.c_operation_type.ilike(f"%{operation_type}%"))
    if channel_type:
        query = query.filter(Store.c_channel_type.ilike(f"%{channel_type}%"))
    if device_type:
        query = query.filter(Store.c_device_type.ilike(f"%{device_type}%"))

    stores = query.order_by(Store.i_store_id).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Store ID", "Store Code", "Store Name", "Business Type", "Operation Type", "Channel Type", "Device Type", "Status", "Email", "Phone", "Created"])

    for s in stores:
        bt = db.query(BusinessType).filter(BusinessType.i_business_type_id == s.i_business_type).first()
        writer.writerow([
            s.i_store_id,
            s.c_store_code,
            s.c_store_name,
            bt.c_name if bt else "",
            s.c_operation_type or "",
            s.c_channel_type or "",
            s.c_device_type or "",
            s.c_status,
            s.c_email,
            s.c_phone,
            s.dt_created.isoformat() if s.dt_created else "",
        ])

    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=stores.csv"})


# ---------------------------
# Role / Business Type / User API (master management)
# ---------------------------

# Roles

@router.get("/platform/roles")
def list_roles(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(Role)
    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(Role.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(Role.c_name.ilike(term), Role.c_description.ilike(term))
        )

    roles = query.order_by(Role.i_role_id).all()
    return JSONResponse([{
        "id": r.i_role_id,
        "name": r.c_name,
        "description": r.c_description,
        "status": r.c_status,
    } for r in roles])


@router.post("/platform/role")
def create_role(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    role = Role(c_name=name, c_description=payload.get("description"), c_status=payload.get("status") or "active")
    _stamp_created(role, user)
    db.add(role)
    db.commit()
    db.refresh(role)

    return JSONResponse({"id": role.i_role_id})


@router.put("/platform/role/{role_id}")
def update_role(role_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    role = db.query(Role).filter(Role.i_role_id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if "name" in payload:
        role.c_name = payload.get("name")
    if "description" in payload:
        role.c_description = payload.get("description")
    if "status" in payload:
        role.c_status = payload.get("status")

    _stamp_updated(role, user)
    db.commit()
    return JSONResponse({"success": True})


@router.delete("/platform/role/{role_id}")
def delete_role(role_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    role = db.query(Role).filter(Role.i_role_id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    role.c_status = "inactive"
    _stamp_updated(role, user)
    db.commit()
    return JSONResponse({"success": True})


# Business Types

@router.get("/platform/business-types")
def list_business_types(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(BusinessType)
    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(BusinessType.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(BusinessType.c_business_code.ilike(term), BusinessType.c_name.ilike(term))
        )

    items = query.order_by(BusinessType.i_business_type_id).all()
    return JSONResponse([{
        "id": i.i_business_type_id,
        "code": i.c_business_code,
        "name": i.c_name,
        "status": i.c_status,
    } for i in items])


@router.post("/platform/business-type")
def create_business_type(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    code = payload.get("code")
    name = payload.get("name")
    if not code or not name:
        raise HTTPException(status_code=400, detail="code and name are required")

    item = BusinessType(c_business_code=code, c_name=name, c_status=payload.get("status") or "active")
    _stamp_created(item, user)
    db.add(item)
    db.commit()
    db.refresh(item)

    return JSONResponse({"id": item.i_business_type_id})


@router.put("/platform/business-type/{type_id}")
def update_business_type(type_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(BusinessType).filter(BusinessType.i_business_type_id == type_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Business type not found")

    if "code" in payload:
        item.c_business_code = payload.get("code")
    if "name" in payload:
        item.c_name = payload.get("name")
    if "status" in payload:
        item.c_status = payload.get("status")

    _stamp_updated(item, user)
    db.commit()
    return JSONResponse({"success": True})


@router.delete("/platform/business-type/{type_id}")
def delete_business_type(type_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(BusinessType).filter(BusinessType.i_business_type_id == type_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Business type not found")

    # Business types referenced by a store cannot be hard-deleted
    linked_store_count = db.query(Store.i_store_id).filter(Store.i_business_type == type_id).count()

    # First deletion request: safe soft-delete
    if item.c_status != "inactive":
        item.c_status = "inactive"
        _stamp_updated(item, user)
        db.commit()
        return JSONResponse({"success": True, "mode": "soft-delete", "status": "inactive"})

    # Already inactive: allow hard-delete only if not referenced by any store
    if linked_store_count > 0:
        raise HTTPException(status_code=409, detail="business_type is in use by store(s); cannot hard-delete")

    db.delete(item)
    db.commit()
    return JSONResponse({"success": True, "mode": "hard-delete"})


# Pricing Plans


def _serialize_pricing_plan(plan: PricingPlan) -> dict:
    return {
        "id": plan.i_plan_id,
        "plan_code": plan.c_plan_code,
        "plan_name": plan.c_plan_name,
        "store_base_fee": float(plan.n_store_base_fee or 0),
        "included_pos_count": int(plan.i_included_pos_count or 0),
        "pos_fee": float(plan.n_pos_fee or 0),
        "included_kiosk_count": int(plan.i_included_kiosk_count or 0),
        "kiosk_fee": float(plan.n_kiosk_fee or 0),
        "included_mobile_order_count": int(plan.i_included_mobile_order_count or 0),
        "mobile_order_fee": float(plan.n_mobile_order_fee or 0),
        "included_user_count": int(plan.i_included_user_count or 0),
        "extra_user_fee": float(plan.n_extra_user_fee or 0),
        "setup_fee": float(plan.n_setup_fee or 0),
        "contract_term_month": int(plan.i_contract_term_month or 1),
        "transaction_fee_rate": float(plan.n_transaction_fee_rate or 0),
        "sort_order": int(plan.i_sort_order or 100),
        "is_default": bool(plan.b_is_default),
        "extra_device_fee": float(plan.n_extra_device_fee or 0),
        "currency": plan.c_currency,
        "status": plan.c_status,
        "memo": plan.c_memo,
    }

@router.get("/platform/pricing-plans")
def list_pricing_plans(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(PricingPlan)
    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(PricingPlan.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                PricingPlan.c_plan_code.ilike(term),
                PricingPlan.c_plan_name.ilike(term),
                PricingPlan.c_currency.ilike(term),
            )
        )

    plans = query.order_by(PricingPlan.i_sort_order.asc(), PricingPlan.i_plan_id.asc()).all()
    return JSONResponse([_serialize_pricing_plan(p) for p in plans])


@router.get("/platform/pricing-plan/{plan_id}")
def get_pricing_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    plan = db.query(PricingPlan).filter(PricingPlan.i_plan_id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Pricing plan not found")

    return JSONResponse(_serialize_pricing_plan(plan))


@router.post("/platform/pricing-plan")
def create_pricing_plan(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    plan_code = str(payload.get("plan_code") or "").strip().upper()
    plan_name = str(payload.get("plan_name") or "").strip()
    if not plan_code or not plan_name:
        raise HTTPException(status_code=400, detail="plan_code and plan_name are required")

    exists = db.query(PricingPlan).filter(PricingPlan.c_plan_code == plan_code).first()
    if exists:
        raise HTTPException(status_code=409, detail="plan_code already exists")

    contract_term_month = _parse_non_negative_int(payload.get("contract_term_month"), "contract_term_month", default=1)
    if contract_term_month <= 0:
        raise HTTPException(status_code=400, detail="contract_term_month must be greater than 0")

    plan = PricingPlan(
        c_plan_code=plan_code,
        c_plan_name=plan_name,
        n_store_base_fee=_parse_non_negative_decimal(payload.get("store_base_fee"), "store_base_fee"),
        i_included_pos_count=_parse_non_negative_int(payload.get("included_pos_count"), "included_pos_count"),
        n_pos_fee=_parse_non_negative_decimal(payload.get("pos_fee"), "pos_fee"),
        i_included_kiosk_count=_parse_non_negative_int(payload.get("included_kiosk_count"), "included_kiosk_count"),
        n_kiosk_fee=_parse_non_negative_decimal(payload.get("kiosk_fee"), "kiosk_fee"),
        i_included_mobile_order_count=_parse_non_negative_int(payload.get("included_mobile_order_count"), "included_mobile_order_count"),
        n_mobile_order_fee=_parse_non_negative_decimal(payload.get("mobile_order_fee"), "mobile_order_fee"),
        i_included_user_count=_parse_non_negative_int(payload.get("included_user_count"), "included_user_count"),
        n_extra_user_fee=_parse_non_negative_decimal(payload.get("extra_user_fee"), "extra_user_fee"),
        n_setup_fee=_parse_non_negative_decimal(payload.get("setup_fee"), "setup_fee"),
        i_contract_term_month=contract_term_month,
        n_transaction_fee_rate=_parse_non_negative_decimal(payload.get("transaction_fee_rate"), "transaction_fee_rate"),
        i_sort_order=_parse_non_negative_int(payload.get("sort_order"), "sort_order", default=100),
        b_is_default=_parse_bool(payload.get("is_default"), "is_default", default=False),
        n_extra_device_fee=_parse_non_negative_decimal(payload.get("extra_device_fee"), "extra_device_fee"),
        c_currency=(str(payload.get("currency") or "USD").strip().upper() or "USD"),
        c_status=(str(payload.get("status") or "active").strip().lower() or "active"),
        c_memo=payload.get("memo"),
    )

    if plan.b_is_default:
        db.query(PricingPlan).update({PricingPlan.b_is_default: False})

    _stamp_created(plan, user)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return JSONResponse({"id": plan.i_plan_id})


@router.put("/platform/pricing-plan/{plan_id}")
def update_pricing_plan(plan_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    plan = db.query(PricingPlan).filter(PricingPlan.i_plan_id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Pricing plan not found")

    if "plan_code" in payload:
        new_code = str(payload.get("plan_code") or "").strip().upper()
        if not new_code:
            raise HTTPException(status_code=400, detail="plan_code cannot be empty")
        duplicate = (
            db.query(PricingPlan)
            .filter(PricingPlan.c_plan_code == new_code, PricingPlan.i_plan_id != plan_id)
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="plan_code already exists")
        plan.c_plan_code = new_code

    if "plan_name" in payload:
        new_name = str(payload.get("plan_name") or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="plan_name cannot be empty")
        plan.c_plan_name = new_name
    if "store_base_fee" in payload:
        plan.n_store_base_fee = _parse_non_negative_decimal(payload.get("store_base_fee"), "store_base_fee")
    if "included_pos_count" in payload:
        plan.i_included_pos_count = _parse_non_negative_int(payload.get("included_pos_count"), "included_pos_count")
    if "pos_fee" in payload:
        plan.n_pos_fee = _parse_non_negative_decimal(payload.get("pos_fee"), "pos_fee")
    if "included_kiosk_count" in payload:
        plan.i_included_kiosk_count = _parse_non_negative_int(payload.get("included_kiosk_count"), "included_kiosk_count")
    if "kiosk_fee" in payload:
        plan.n_kiosk_fee = _parse_non_negative_decimal(payload.get("kiosk_fee"), "kiosk_fee")
    if "included_mobile_order_count" in payload:
        plan.i_included_mobile_order_count = _parse_non_negative_int(payload.get("included_mobile_order_count"), "included_mobile_order_count")
    if "mobile_order_fee" in payload:
        plan.n_mobile_order_fee = _parse_non_negative_decimal(payload.get("mobile_order_fee"), "mobile_order_fee")
    if "included_user_count" in payload:
        plan.i_included_user_count = _parse_non_negative_int(payload.get("included_user_count"), "included_user_count")
    if "extra_user_fee" in payload:
        plan.n_extra_user_fee = _parse_non_negative_decimal(payload.get("extra_user_fee"), "extra_user_fee")
    if "setup_fee" in payload:
        plan.n_setup_fee = _parse_non_negative_decimal(payload.get("setup_fee"), "setup_fee")
    if "contract_term_month" in payload:
        contract_term_month = _parse_non_negative_int(payload.get("contract_term_month"), "contract_term_month", default=1)
        if contract_term_month <= 0:
            raise HTTPException(status_code=400, detail="contract_term_month must be greater than 0")
        plan.i_contract_term_month = contract_term_month
    if "transaction_fee_rate" in payload:
        plan.n_transaction_fee_rate = _parse_non_negative_decimal(payload.get("transaction_fee_rate"), "transaction_fee_rate")
    if "sort_order" in payload:
        plan.i_sort_order = _parse_non_negative_int(payload.get("sort_order"), "sort_order", default=100)
    if "is_default" in payload:
        new_is_default = _parse_bool(payload.get("is_default"), "is_default", default=False)
        if new_is_default:
            db.query(PricingPlan).filter(PricingPlan.i_plan_id != plan_id).update({PricingPlan.b_is_default: False})
        plan.b_is_default = new_is_default
    if "extra_device_fee" in payload:
        plan.n_extra_device_fee = _parse_non_negative_decimal(payload.get("extra_device_fee"), "extra_device_fee")
    if "currency" in payload:
        plan.c_currency = str(payload.get("currency") or "").strip().upper() or "USD"
    if "status" in payload:
        plan.c_status = str(payload.get("status") or "").strip().lower() or "active"
    if "memo" in payload:
        plan.c_memo = payload.get("memo")

    _stamp_updated(plan, user)
    db.commit()
    return JSONResponse({"success": True})


@router.delete("/platform/pricing-plan/{plan_id}")
def delete_pricing_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    plan = db.query(PricingPlan).filter(PricingPlan.i_plan_id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Pricing plan not found")

    plan.c_status = "inactive"
    _stamp_updated(plan, user)
    db.commit()
    return JSONResponse({"success": True})


# Users


# Invoices


@router.get("/platform/contracts")
def list_contracts(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(Contract)
    normalized_status = _normalize_status(status, {"active", "terminated", "expired"})
    if normalized_status:
        query = query.filter(Contract.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                cast(Contract.i_contract_id, String).ilike(term),
                cast(Contract.i_subscription_id, String).ilike(term),
                cast(Contract.i_merchant_id, String).ilike(term),
                cast(Contract.i_store_id, String).ilike(term),
                Contract.c_status.ilike(term),
            )
        )

    contracts = query.order_by(Contract.i_contract_id.desc()).all()
    return JSONResponse([_serialize_contract(c) for c in contracts])


@router.get("/platform/contract/{contract_id}")
def get_contract(contract_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    contract = db.query(Contract).filter(Contract.i_contract_id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    return JSONResponse(_serialize_contract(contract))


@router.put("/platform/contract/{contract_id}")
def update_contract(contract_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    contract = db.query(Contract).filter(Contract.i_contract_id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    if "status" in payload:
        new_status = _normalize_status(payload.get("status"), {"active", "terminated", "expired"}, field_name="status")
        contract.c_status = new_status or contract.c_status

    if "contract_end_date" in payload:
        contract.dt_contract_end_date = _parse_optional_iso_date(payload.get("contract_end_date"), "contract_end_date")

    db.commit()
    return JSONResponse({"success": True, "status": contract.c_status})


@router.delete("/platform/contract/{contract_id}")
def terminate_contract(contract_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    contract = db.query(Contract).filter(Contract.i_contract_id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    contract.c_status = "terminated"
    db.commit()
    return JSONResponse({"success": True, "status": contract.c_status})


@router.get("/platform/contract/{contract_id}/download-html", response_class=HTMLResponse)
def download_contract_html(contract_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    contract = db.query(Contract).filter(Contract.i_contract_id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    return HTMLResponse(content=_contract_html(contract))


@router.get("/platform/contract/{contract_id}/download-pdf")
def download_contract_pdf(contract_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    contract = db.query(Contract).filter(Contract.i_contract_id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    pdf_bytes = _build_contract_pdf_bytes(contract, report_context(db, user))
    from uuid import uuid4
    from backend.services.paths import AwsumPathResolver, validate_owner
    from backend.services.resource_storage import write_data
    store = db.get(Store, contract.i_store_id)
    merchant = db.get(Merchant, contract.i_merchant_id)
    if store is None or merchant is None or store.i_merchant_id != merchant.i_merchant_id:
        raise HTTPException(409, "Contract Store does not belong to its Merchant.")
    owner = AwsumPathResolver("STORE", merchant_code=merchant.c_merchant_code, store_code=store.c_store_code)
    validate_owner(db, owner)
    file_name = f"contract_{contract.i_contract_id}.pdf"
    logical = owner.get_data_path("common", f"contract-{contract.i_contract_id}-{uuid4().hex}.pdf")
    target_path = write_data(request, db, logical, pdf_bytes)
    contract.c_contract_pdf_path = logical
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            target_path.unlink()
        except OSError as cleanup_error:
            exc.add_note(f"Unreferenced contract cleanup failed: {cleanup_error}")
        raise

    headers = {"Content-Disposition": f'attachment; filename="{file_name}"'}
    return _set_no_cache_headers(StreamingResponse(BytesIO(pdf_bytes), media_type="application/pdf", headers=headers))

@router.get("/platform/invoices")
def list_invoices(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(Invoice)
    normalized_status = _normalize_status(status, {"issued", "paid", "void"})
    if normalized_status:
        query = query.filter(Invoice.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Invoice.c_invoice_no.ilike(term),
                Invoice.c_currency.ilike(term),
                Invoice.c_status.ilike(term),
            )
        )

    invoices = query.order_by(Invoice.dt_invoice_date.desc(), Invoice.i_invoice_id.desc()).all()

    invoice_ids = [inv.i_invoice_id for inv in invoices]
    line_counts = {}
    if invoice_ids:
        rows = (
            db.query(InvoiceLine.i_invoice_id, func.count(InvoiceLine.i_invoice_line_id))
            .filter(InvoiceLine.i_invoice_id.in_(invoice_ids))
            .group_by(InvoiceLine.i_invoice_id)
            .all()
        )
        line_counts = {int(row[0]): int(row[1]) for row in rows}

    return JSONResponse([
        _serialize_invoice(inv, line_count=line_counts.get(inv.i_invoice_id, 0))
        for inv in invoices
    ])


@router.get("/platform/invoice/{invoice_id}")
def get_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    invoice = db.query(Invoice).filter(Invoice.i_invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    lines = (
        db.query(InvoiceLine)
        .filter(InvoiceLine.i_invoice_id == invoice.i_invoice_id)
        .order_by(InvoiceLine.i_invoice_line_id.asc())
        .all()
    )
    return JSONResponse(_serialize_invoice(invoice, line_count=len(lines), lines=lines))


@router.get("/platform/invoice/{invoice_id}/download-html", response_class=HTMLResponse)
def download_invoice_html(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    invoice = db.query(Invoice).filter(Invoice.i_invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    lines = (
        db.query(InvoiceLine)
        .filter(InvoiceLine.i_invoice_id == invoice.i_invoice_id)
        .order_by(InvoiceLine.i_invoice_line_id.asc())
        .all()
    )

    line_rows = "".join(
        f"<tr><td>{line.c_line_type}</td><td>{line.c_description}</td><td style='text-align:right'>{float(line.n_quantity or 0):,.4f}</td><td style='text-align:right'>{format_money(line.n_unit_price or 0)}</td><td style='text-align:right'>{format_money(line.n_amount or 0)}</td></tr>"
        for line in lines
    )

    html = f"""
    <html>
      <head>
        <meta charset='UTF-8' />
        <title>Invoice {invoice.c_invoice_no}</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 20px; color: #0f172a; }}
          h2 {{ margin: 0 0 8px 0; }}
          .meta {{ margin-bottom: 12px; font-size: 13px; }}
          table {{ width: 100%; border-collapse: collapse; }}
          th, td {{ border: 1px solid #cbd5e1; padding: 6px 8px; font-size: 12px; }}
          th {{ background: #f8fafc; text-align: left; }}
          .totals {{ margin-top: 12px; width: 320px; margin-left: auto; }}
          .totals td {{ border: none; padding: 3px 0; font-size: 13px; }}
          .totals .label {{ text-align: right; padding-right: 8px; color: #475569; }}
          .totals .value {{ text-align: right; font-weight: 600; }}
        </style>
      </head>
      <body>
        <h2>Invoice {invoice.c_invoice_no}</h2>
        <div class='meta'>
          Invoice Date: {invoice.dt_invoice_date} &nbsp;|&nbsp; Due Date: {invoice.dt_due_date or '-'} &nbsp;|&nbsp; Status: {invoice.c_status}
          <br/>
          Subscription: {invoice.i_subscription_id} &nbsp;|&nbsp; Merchant: {invoice.i_merchant_id} &nbsp;|&nbsp; Store: {invoice.i_store_id}
        </div>
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th>Description</th>
              <th style='text-align:right'>Qty</th>
              <th style='text-align:right'>Unit Price</th>
              <th style='text-align:right'>Amount</th>
            </tr>
          </thead>
          <tbody>{line_rows}</tbody>
        </table>
        <table class='totals'>
          <tr><td class='label'>Subtotal</td><td class='value'>{format_money(invoice.n_subtotal or 0)}</td></tr>
          <tr><td class='label'>Tax</td><td class='value'>{format_money(invoice.n_tax or 0)}</td></tr>
          <tr><td class='label'>Total ({invoice.c_currency})</td><td class='value'>{format_money(invoice.n_total or 0)}</td></tr>
        </table>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.put("/platform/invoice/{invoice_id}")
def update_invoice(invoice_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    invoice = db.query(Invoice).filter(Invoice.i_invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if "status" in payload:
        new_status = str(payload.get("status") or "").strip().lower()
        if new_status not in {"issued", "paid", "void"}:
            raise HTTPException(status_code=400, detail="Invalid status")
        _validate_invoice_status_transition(invoice.c_status, new_status)
        invoice.c_status = new_status

    if "due_date" in payload:
        invoice.dt_due_date = _parse_optional_iso_date(payload.get("due_date"), "due_date")

    if "memo" in payload:
        invoice.c_memo = payload.get("memo")

    _stamp_updated(invoice, user)
    db.commit()
    return JSONResponse({"success": True, "status": invoice.c_status})


@router.delete("/platform/invoice/{invoice_id}")
def void_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    invoice = db.query(Invoice).filter(Invoice.i_invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    _validate_invoice_status_transition(invoice.c_status, "void")
    invoice.c_status = "void"
    _stamp_updated(invoice, user)
    db.commit()
    return JSONResponse({"success": True, "status": "void"})


# Users

def _platform_user_email(value: object) -> str:
    email = str(value or "").strip().lower()
    if not email or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return email


def _platform_user_name(payload: dict) -> tuple[str, str]:
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name is required.")
        first_name, separator, last_name = name.partition(" ")
        return first_name, last_name.strip() if separator else ""

    first_name = str(payload.get("first_name") or "").strip()
    last_name = str(payload.get("last_name") or "").strip()
    if not first_name and not last_name:
        raise HTTPException(status_code=400, detail="Name is required.")
    return first_name, last_name


def _platform_user_bool(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(status_code=400, detail=f"{field_name} must be true or false.")


def _get_platform_user_role(db: Session, value: object) -> Role:
    try:
        role_id = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Select a valid Role.")

    role = db.query(Role).filter(Role.i_role_id == role_id).first()
    if (
        not role
        or (role.c_status or "").strip().lower() != "active"
        or (role.c_name or "").strip().casefold() not in PLATFORM_USER_ROLE_NAMES
    ):
        raise HTTPException(status_code=400, detail="Select an active Platform User Role.")
    return role


def _require_unique_platform_user_email(
    db: Session, email: str, *, exclude_user_id: int | None = None, agency_id: int | None = None,
) -> None:
    query = db.query(PlatformUser).filter(
        func.lower(func.trim(PlatformUser.c_email)) == email,
        PlatformUser.i_agency_id == agency_id if agency_id is not None else PlatformUser.i_agency_id.is_(None),
    )
    if agency_id is None:
        query = query.filter(PlatformUser.i_merchant_id.is_(None), PlatformUser.i_store_id == ADMIN_STORE_ID)
    if exclude_user_id is not None:
        query = query.filter(PlatformUser.i_platform_user_id != exclude_user_id)
    if query.first():
        detail = "An Agency User with this email already exists in this Agency." if agency_id is not None else \
            "A Platform User with this email already exists."
        raise HTTPException(status_code=409, detail=detail)


def _validate_platform_user_password(password: object, confirm_password: object) -> str:
    value = str(password or "")
    if not value:
        raise HTTPException(status_code=400, detail="Temporary password is required.")
    if value != str(confirm_password or ""):
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    if len(value.encode("utf-8")) > MAX_BCRYPT_PASSWORD_BYTES:
        raise HTTPException(status_code=400, detail=f"Password is too long (max {MAX_BCRYPT_PASSWORD_BYTES} bytes).")
    return value


def _get_platform_user(db: Session, user_id: int) -> PlatformUser:
    target = (
        db.query(PlatformUser)
        .filter(
            PlatformUser.i_platform_user_id == user_id,
            PlatformUser.i_store_id == ADMIN_STORE_ID,
            PlatformUser.i_agency_id.is_(None),
            PlatformUser.i_merchant_id.is_(None),
        )
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="Platform user not found.")
    return target


def _active_superadmin_count(db: Session) -> int:
    count = (
        db.query(func.count(PlatformUser.i_platform_user_id))
        .join(Role, Role.i_role_id == PlatformUser.i_role_id)
        .filter(
            PlatformUser.i_store_id == ADMIN_STORE_ID,
            func.lower(PlatformUser.c_status) == "active",
            func.lower(Role.c_name) == "superadmin",
            func.lower(Role.c_status) == "active",
        )
        .scalar()
    )
    return int(count or 0)


def _guard_platform_user_change(
    db: Session,
    actor: PlatformUser,
    target: PlatformUser,
    next_status: str,
    next_role: Role | None,
) -> None:
    if target.i_platform_user_id == actor.i_platform_user_id and next_status == "inactive":
        raise HTTPException(status_code=400, detail="You cannot deactivate your own Platform User merchant.")

    current_role = db.query(Role).filter(Role.i_role_id == target.i_role_id).first() if target.i_role_id else None
    is_active_superadmin = (
        (target.c_status or "").strip().lower() == "active"
        and current_role is not None
        and (current_role.c_name or "").strip().casefold() == "superadmin"
        and (current_role.c_status or "").strip().lower() == "active"
    )
    remains_active_superadmin = (
        next_status == "active"
        and next_role is not None
        and (next_role.c_name or "").strip().casefold() == "superadmin"
    )
    if is_active_superadmin and not remains_active_superadmin and _active_superadmin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="The last active SuperAdmin cannot be deactivated or demoted.")


@router.get("/platform/users")
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(PlatformUser).filter(
        PlatformUser.i_store_id == ADMIN_STORE_ID, PlatformUser.i_agency_id.is_(None),
        PlatformUser.i_merchant_id.is_(None),
    )
    normalized_status = _normalize_status(status, {"active", "inactive"})
    if status and not normalized_status:
        raise HTTPException(status_code=400, detail="Status must be active or inactive.")
    if normalized_status:
        query = query.filter(func.lower(PlatformUser.c_status) == normalized_status)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                PlatformUser.c_email.ilike(term),
                PlatformUser.c_first_name.ilike(term),
                PlatformUser.c_last_name.ilike(term),
            )
        )

    users = query.order_by(PlatformUser.i_platform_user_id).all()
    role_ids = {item.i_role_id for item in users if item.i_role_id is not None}
    role_map = {
        role.i_role_id: role.c_name
        for role in db.query(Role).filter(Role.i_role_id.in_(role_ids)).all()
    } if role_ids else {}
    return JSONResponse([
        {
            "id": item.i_platform_user_id,
            "name": " ".join(part for part in (item.c_first_name, item.c_last_name) if part).strip(),
            "first_name": item.c_first_name,
            "last_name": item.c_last_name,
            "email": item.c_email,
            "role_id": item.i_role_id,
            "role": role_map.get(item.i_role_id),
            "status": (item.c_status or "inactive").strip().lower(),
            "must_change_password": bool(item.i_must_change_password),
            "last_login": item.dt_last_login.isoformat() if item.dt_last_login else None,
            "created_at": item.dt_created.isoformat() if item.dt_created else None,
            "image_path": item.c_image_path,
            "image_url": image_url(item.c_image_path),
        }
        for item in users
    ])


@router.post("/platform/user")
def create_user(payload: dict, request: Request, db: Session = Depends(get_db)):
    return _save_user(None, payload, request, db)


@router.put("/platform/user/{user_id}")
def update_user(user_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return _save_user(user_id, payload, request, db)


def _save_user(user_id: int | None, payload: dict, request: Request, db: Session,
               *, image_data: bytes | None = None, remove_image: bool = False):
    actor = get_current_user(request, db)
    require_platform_admin(actor)
    with image_operation_lock(AwsumStorageService(db).resolve_owner("PLATFORM"), "users"):
        if user_id is None:
            return _create_user(payload, request, db, image_data=image_data)
        return _update_user(user_id, payload, request, db, image_data=image_data, remove_image=remove_image)


@router.post("/platform/user/with-image")
def create_user_with_image(
    request: Request, payload: str = Form(...), image: UploadFile | None = File(None),
    remove_image: bool = Form(False), db: Session = Depends(get_db),
):
    from backend.routers.system_settings import _validate_origin
    actor = get_current_user(request, db)
    require_platform_admin(actor)
    _validate_origin(request)
    values, data = image_mutation_form(payload, remove_image, image)
    return _save_user(None, values, request, db, image_data=data)


@router.put("/platform/user/{user_id}/with-image")
def update_user_with_image(
    user_id: int, request: Request, payload: str = Form(...), image: UploadFile | None = File(None),
    remove_image: bool = Form(False), db: Session = Depends(get_db),
):
    from backend.routers.system_settings import _validate_origin
    actor = get_current_user(request, db)
    require_platform_admin(actor)
    _validate_origin(request)
    values, data = image_mutation_form(payload, remove_image, image)
    return _save_user(user_id, values, request, db, image_data=data, remove_image=remove_image)


def _create_user(payload: dict, request: Request, db: Session, *, image_data: bytes | None = None):
    actor = get_current_user(request, db)
    require_platform_admin(actor)

    first_name, last_name = _platform_user_name(payload)
    email = _platform_user_email(payload.get("email"))
    _require_unique_platform_user_email(db, email)
    password = _validate_platform_user_password(payload.get("password"), payload.get("confirm_password"))
    role = _get_platform_user_role(db, payload.get("role_id"))
    status = str(payload.get("status") or "active").strip().lower()
    if status not in {"active", "inactive"}:
        raise HTTPException(status_code=400, detail="Status must be active or inactive.")
    must_change_password = _platform_user_bool(payload.get("must_change_password", True), "Must Change Password")

    new_user = PlatformUser(
        i_store_id=ADMIN_STORE_ID,
        i_role_id=role.i_role_id,
        c_first_name=first_name,
        c_last_name=last_name,
        c_email=email,
        c_password=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        c_status=status,
        i_must_change_password=int(must_change_password),
    )
    db.add(new_user)
    try:
        db.flush()
        warning = commit_entity_image(db, new_user, AwsumStorageService(db).resolve_owner("PLATFORM"), new_user.i_platform_user_id,
                                      category="users", image_data=image_data)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A Platform User with this email already exists.")
    db.refresh(new_user)
    return JSONResponse({"success": True, "id": new_user.i_platform_user_id,
                         "image_path": new_user.c_image_path, "image_url": image_url(new_user.c_image_path),
                         "image_warning": warning})


def _update_user(user_id: int, payload: dict, request: Request, db: Session,
                 *, image_data: bytes | None = None, remove_image: bool = False):
    actor = get_current_user(request, db)
    require_platform_admin(actor)
    target = _get_platform_user(db, user_id)
    db.refresh(target, with_for_update=True)

    if "email" in payload:
        email = _platform_user_email(payload.get("email"))
        _require_unique_platform_user_email(db, email, exclude_user_id=user_id)
        target.c_email = email
    if "name" in payload or "first_name" in payload or "last_name" in payload:
        target.c_first_name, target.c_last_name = _platform_user_name(payload)

    next_role = db.query(Role).filter(Role.i_role_id == target.i_role_id).first() if target.i_role_id else None
    if "role_id" in payload:
        next_role = _get_platform_user_role(db, payload.get("role_id"))

    next_status = (target.c_status or "inactive").strip().lower()
    if "status" in payload:
        next_status = str(payload.get("status") or "").strip().lower()
        if next_status not in {"active", "inactive"}:
            raise HTTPException(status_code=400, detail="Status must be active or inactive.")

    _guard_platform_user_change(db, actor, target, next_status, next_role)
    if "role_id" in payload:
        target.i_role_id = next_role.i_role_id
    target.c_status = next_status
    if "must_change_password" in payload:
        target.i_must_change_password = int(_platform_user_bool(payload["must_change_password"], "Must Change Password"))
    target.i_store_id = ADMIN_STORE_ID

    _stamp_updated(target, actor)
    try:
        warning = commit_entity_image(db, target, AwsumStorageService(db).resolve_owner("PLATFORM"), target.i_platform_user_id,
                                      category="users", image_data=image_data, remove_image=remove_image)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A Platform User with this email already exists.")
    return JSONResponse({"success": True, "id": target.i_platform_user_id,
                         "image_path": target.c_image_path, "image_url": image_url(target.c_image_path),
                         "image_warning": warning})


@router.post("/platform/user/{user_id}/reset-password")
def reset_user_password(user_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    actor = get_current_user(request, db)
    require_platform_admin(actor)
    target = _get_platform_user(db, user_id)
    return _reset_user_password(target, payload, actor, db)


def _reset_user_password(target: PlatformUser, payload: dict, actor: PlatformUser, db: Session):
    password = _validate_platform_user_password(payload.get("password"), payload.get("confirm_password"))

    target.c_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    target.i_must_change_password = 1
    _stamp_updated(target, actor)
    db.commit()
    return JSONResponse({"success": True, "must_change_password": True})


@router.patch("/platform/user/{user_id}/status")
def set_user_status(user_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    actor = get_current_user(request, db)
    require_platform_admin(actor)
    target = _get_platform_user(db, user_id)

    next_status = str(payload.get("status") or "").strip().lower()
    if next_status not in {"active", "inactive"}:
        raise HTTPException(status_code=400, detail="Status must be active or inactive.")
    role = db.query(Role).filter(Role.i_role_id == target.i_role_id).first() if target.i_role_id else None
    _guard_platform_user_change(db, actor, target, next_status, role)

    target.c_status = next_status
    target.i_store_id = ADMIN_STORE_ID
    _stamp_updated(target, actor)
    db.commit()
    return JSONResponse({"success": True, "status": target.c_status})


@router.delete("/platform/user/{user_id}")
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Legacy delete route; Platform Users are deactivated, never deleted."""
    actor = get_current_user(request, db)
    require_platform_admin(actor)
    target = _get_platform_user(db, user_id)
    role = db.query(Role).filter(Role.i_role_id == target.i_role_id).first() if target.i_role_id else None
    _guard_platform_user_change(db, actor, target, "inactive", role)

    target.c_status = "inactive"
    _stamp_updated(target, actor)
    db.commit()
    return JSONResponse({"success": True, "status": target.c_status})


# Merchants



@router.get("/platform/merchants-tree/data")
def list_merchants_tree_data(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    merchants = db.query(Merchant).order_by(Merchant.i_merchant_id.asc()).all()
    if not merchants:
        return _set_no_cache_headers(JSONResponse([]))

    business_type_map = {
        row.i_business_type_id: row.c_name
        for row in db.query(BusinessType).all()
    }

    agency_map = {
        row.i_agency_id: row
        for row in db.query(Agency).all()
    }

    merchant_ids = [merchant.i_merchant_id for merchant in merchants]
    stores = (
        db.query(Store)
        .filter(Store.i_merchant_id.in_(merchant_ids))
        .order_by(Store.i_merchant_id.asc(), Store.i_store_id.asc())
        .all()
    )

    stores_by_merchant_id: dict[int, list[dict]] = defaultdict(list)
    store_ids = [store.i_store_id for store in stores]

    devices_by_store_id: dict[int, list[dict]] = defaultdict(list)
    if store_ids:
        devices = (
            db.query(Device)
            .filter(Device.i_store_id.in_(store_ids))
            .order_by(Device.i_store_id.asc(), Device.i_device_id.asc())
            .all()
        )

        license_ids = [device.i_license_id for device in devices if device.i_license_id]
        license_map = {}
        if license_ids:
            for license_obj in db.query(License).filter(License.i_license_id.in_(license_ids)).all():
                license_map[license_obj.i_license_id] = license_obj

        for device in devices:
            devices_by_store_id[device.i_store_id].append(
                _serialize_device(
                    device,
                    license_map.get(device.i_license_id),
                    agency_map.get(device.i_installed_by_agency_id),
                )
            )

    for store in stores:
        store_agency = agency_map.get(store.i_installed_by_agency_id)
        stores_by_merchant_id[store.i_merchant_id].append({
            "store_id": store.i_store_id,
            "store_seq": store.i_store_seq,
            "store_code": store.c_store_code,
            "store_name": store.c_store_name,
            "merchant_id": store.i_merchant_id,
            "dashboard_type": store.c_dashboard_type,
            "business_type": business_type_map.get(store.i_business_type),
            "operation_type": store.c_operation_type,
            "channel_type": store.c_channel_type,
            "device_type": store.c_device_type,
            "contact_name": store.c_contact_name,
            "owner_name": store.c_owner_name,
            "status": store.c_status,
            "phone": store.c_phone,
            "email": store.c_email,
            "zip": store.c_zip,
            "address_line1": store.c_address_line1,
            "address_line2": store.c_address_line2,
            "city": store.c_city,
            "state": store.c_state,
            "country": store.c_country,
            "default_tax_rate": store.c_default_tax_rate,
            "timezone": store.c_timezone,
            "tax_source": store.c_tax_source,
            "memo": store.c_memo,
            "installed_by_agency_id": store.i_installed_by_agency_id,
            "installed_by_agency_name": store_agency.c_agency_name if store_agency else None,
            "devices": devices_by_store_id.get(store.i_store_id, []),
        })

    result = []
    for merchant in merchants:
        result.append({
            "id": merchant.i_merchant_id,
            "merchant_id": merchant.i_merchant_id,
            "merchant_code": merchant.c_merchant_code,
            "merchant_name": merchant.c_merchant_name,
            "business_type": business_type_map.get(merchant.i_business_type),
            "channel_type": merchant.c_channel_type,
            "i_agency_id": merchant.i_agency_id,
            "primary_agency_id": merchant.i_agency_id,
            "primary_agency_name": (agency_map.get(merchant.i_agency_id).c_contact_name or agency_map.get(merchant.i_agency_id).c_agency_name) if agency_map.get(merchant.i_agency_id) else None,
            "first_name": merchant.c_first_name,
            "last_name": merchant.c_last_name,
            "email": merchant.c_email,
            "phone": merchant.c_phone,
            "address": merchant.c_address_line1,
            "address_line1": merchant.c_address_line1,
            "address_line2": merchant.c_address_line2,
            "city": merchant.c_city,
            "state": merchant.c_state,
            "zip": merchant.c_zip,
            "country": merchant.c_country,
            "memo": merchant.c_memo,
            "status": merchant.c_status,
            "stores": stores_by_merchant_id.get(merchant.i_merchant_id, []),
        })

    return _set_no_cache_headers(JSONResponse(result))












# Merchants (backed by the existing tb_merchant table; no schema change)

MERCHANT_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
MERCHANT_TEXT_LIMITS = {
    "first_name": 120, "last_name": 120, "email": 255, "phone": 50,
    "address_line1": 255, "address_line2": 255, "city": 120, "state": 120, "zip": 20, "country": 120, "website": 1000, "notes": 5000, "timezone": 64,
    }
def _merchant_agency_label(agency: Optional[Agency]) -> str:
    if not agency:
        return ""
    return f"{agency.c_agency_code} - {agency.c_company_name or agency.c_agency_name}"


def _serialize_merchant(item: Merchant, agency_map: dict, bt_name_map: dict) -> dict:
    return {
        "id": item.i_merchant_id,
        "image_path": item.c_logo_url,
        "image_url": image_url(item.c_logo_url),
        "merchant_code": item.c_merchant_code,
        "merchant_name": item.c_merchant_name,
        "primary_agency_id": str(item.i_agency_id) if item.i_agency_id is not None else "",
        "primary_agency_name": _merchant_agency_label(agency_map.get(item.i_agency_id)),
        "business_type": bt_name_map.get(item.i_business_type) or "",
        "status": item.c_status,
        "created_date": item.dt_created.strftime("%Y-%m-%d") if item.dt_created else "",
        "first_name": item.c_first_name or "",
        "last_name": item.c_last_name or "",
        "email": item.c_email or "",
        "website": item.c_website or "",
        "notes": item.c_notes or "",
        "phone": item.c_phone or "",
        "address_line1": item.c_address_line1 or "",
        "address_line2": item.c_address_line2 or "",
        "city": item.c_city or "",
        "state": item.c_state or "",
        "zip": item.c_zip or "",
        "country": item.c_country or "",
        "timezone": item.c_timezone or "",
    }


def _merchant_clean_text(payload: dict, key: str) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        return None
    value = str(value).strip()
    if len(value) > MERCHANT_TEXT_LIMITS[key]:
        raise HTTPException(status_code=400, detail=f"{key} is too long.")
    return value or None


def _merchant_resolve_agency(db: Session, raw) -> int:
    agency_id = _parse_optional_int(raw, "primary_agency_id")
    if agency_id is None:
        raise HTTPException(status_code=400, detail="Primary Agency is required.")
    if not db.query(Agency.i_agency_id).filter(Agency.i_agency_id == agency_id).first():
        raise HTTPException(status_code=400, detail="Selected Primary Agency does not exist.")
    return agency_id


def _merchant_resolve_business_type(db: Session, raw) -> int:
    name = str(raw or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Business Type is required.")
    bt = db.query(BusinessType).filter(BusinessType.c_name == name).first()
    if not bt:
        raise HTTPException(status_code=400, detail="Selected Business Type does not exist.")
    return bt.i_business_type_id


def _merchant_validate_email(payload: dict) -> Optional[str]:
    email = _merchant_clean_text(payload, "email")
    if email and not MERCHANT_EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid contact email address.")
    return email


def _merchant_validate_website(payload: dict) -> Optional[str]:
    # Accepts "abcmarket.com" or "https://abcmarket.com"; always stored with a scheme.
    raw = _merchant_clean_text(payload, "website")
    if not raw:
        return None
    value = raw if re.match(r"^https?://", raw, re.I) else f"https://{raw}"
    value = re.sub(r"^https?://", lambda m: m.group(0).lower(), value, flags=re.I)
    if not re.fullmatch(r"https?://[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+(:\d{1,5})?(/\S*)?", value):
        raise HTTPException(status_code=400, detail="Please enter a valid website, e.g. abcmarket.com or https://abcmarket.com.")
    if len(value) > 255:
        raise HTTPException(status_code=400, detail="Website is too long.")
    return value


@router.get("/platform/merchants")
def list_merchants(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(Merchant)
    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(Merchant.c_status == normalized_status)
    if search and search.strip():
        term = f"%{search.strip()}%"
        code_term = f"%{search.strip().replace('_', '')}%"
        query = query.filter(
            or_(
                Merchant.c_merchant_code.ilike(term),
                Merchant.c_merchant_code.ilike(code_term),
                Merchant.c_merchant_name.ilike(term),
                Merchant.c_first_name.ilike(term),
                Merchant.c_last_name.ilike(term),
                Merchant.c_email.ilike(term),
                Merchant.c_phone.ilike(term),
            )
        )

    agency_map = {a.i_agency_id: a for a in db.query(Agency).all()}
    bt_name_map = {b.i_business_type_id: b.c_name for b in db.query(BusinessType).all()}
    items = query.order_by(Merchant.i_merchant_id).all()
    return _set_no_cache_headers(JSONResponse([_serialize_merchant(i, agency_map, bt_name_map) for i in items]))


@router.post("/platform/merchant")
def create_merchant(payload: dict, request: Request, db: Session = Depends(get_db)):
    return _create_merchant(payload, request, db)


def _create_merchant(payload: dict, request: Request, db: Session, *, image_data: bytes | None = None):
    user = get_current_user(request, db)
    require_platform_admin(user)

    name = str(payload.get("merchant_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Merchant Name is required.")
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="Merchant Name is too long.")
    agency_id = _merchant_resolve_agency(db, payload.get("primary_agency_id"))
    business_type_id = _merchant_resolve_business_type(db, payload.get("business_type"))
    email = _merchant_validate_email(payload)
    status_value = _normalize_status(payload.get("status"), {"active", "inactive"}) or "active"
    if db.query(Merchant.i_merchant_id).filter(func.lower(Merchant.c_merchant_name) == name.lower()).first():
        raise HTTPException(status_code=409, detail="A merchant with this name already exists.")

    # Merchant code is always server-generated; any merchant-supplied code is ignored.
    for _ in range(5):
        item = Merchant(
            c_merchant_code=_generate_next_merchant_code(db),
            c_merchant_name=name,
            i_business_type=business_type_id,
            i_agency_id=agency_id,
            c_first_name=_merchant_clean_text(payload, "first_name"),
            c_last_name=_merchant_clean_text(payload, "last_name"),
            c_email=email,
            c_website=_merchant_validate_website(payload),
            c_notes=_merchant_clean_text(payload, "notes"),
            c_phone=_merchant_clean_text(payload, "phone"),
            c_address_line1=_merchant_clean_text(payload, "address_line1"),
            c_address_line2=_merchant_clean_text(payload, "address_line2"),
            c_city=_merchant_clean_text(payload, "city"),
            c_state=_merchant_clean_text(payload, "state"),
            c_zip=_merchant_clean_text(payload, "zip"),
            c_country=_normalize_country(_merchant_clean_text(payload, "country")),
            c_timezone=_merchant_clean_text(payload, "timezone"),
            c_channel_type=payload.get("channel_type"),
            c_memo=payload.get("memo"),
            c_status=status_value,
        )
        _stamp_created(item, user)
        db.add(item)
        try:
            db.flush()
            activation = _activate_organization(db, "MERCHANT", item)
            warning = _commit_organization_image(db, item, "MERCHANT", image_data=image_data)
            db.refresh(item)
            storage = AwsumStorageService(db).initialize_after_registration("MERCHANT", item.i_merchant_id)
            return JSONResponse({"id": item.i_merchant_id, "merchant_code": item.c_merchant_code, **storage, **activation,
                                 **({"image_warning": warning} if image_data is not None else {})})
        except IntegrityError as exc:
            db.rollback()
            lowered = str(exc).lower()
            if "c_merchant_name" in lowered:
                raise HTTPException(status_code=409, detail="A merchant with this name already exists.")
            if "c_merchant_code" in lowered:
                continue
            raise HTTPException(status_code=400, detail="Unable to save the merchant. Please check the entered values.")
    raise HTTPException(status_code=409, detail="Could not generate a unique merchant code. Please try again.")


@router.put("/platform/merchant/{merchant_id}")
def update_merchant(merchant_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return _update_merchant(merchant_id, payload, request, db)


def _update_merchant(merchant_id: int, payload: dict, request: Request, db: Session,
                     *, image_data: bytes | None = None, remove_image: bool = False):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).with_for_update().first()
    if not item:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    if image_data is not None or remove_image:
        db.refresh(item, with_for_update=True)

    # Merchant code is immutable; Store agencies are intentionally never touched here.
    if "merchant_name" in payload:
        name = str(payload.get("merchant_name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Merchant Name is required.")
        if len(name) > 255:
            raise HTTPException(status_code=400, detail="Merchant Name is too long.")
        duplicate = (
            db.query(Merchant.i_merchant_id)
            .filter(func.lower(Merchant.c_merchant_name) == name.lower(), Merchant.i_merchant_id != merchant_id)
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A merchant with this name already exists.")
        item.c_merchant_name = name
    if "primary_agency_id" in payload:
        item.i_agency_id = _merchant_resolve_agency(db, payload.get("primary_agency_id"))
    if "business_type" in payload:
        item.i_business_type = _merchant_resolve_business_type(db, payload.get("business_type"))
    if "status" in payload:
        normalized = _normalize_status(payload.get("status"), {"active", "inactive"})
        if normalized:
            item.c_status = normalized
    if "email" in payload:
        item.c_email = _merchant_validate_email(payload)
    if "website" in payload:
        item.c_website = _merchant_validate_website(payload)
    if "notes" in payload:
        item.c_notes = _merchant_clean_text(payload, "notes")
    if "channel_type" in payload:
        item.c_channel_type = payload.get("channel_type") or None
    if "memo" in payload:
        item.c_memo = payload.get("memo")
    for key, column in (
        ("first_name", "c_first_name"), ("last_name", "c_last_name"), ("phone", "c_phone"),
        ("address_line1", "c_address_line1"), ("address_line2", "c_address_line2"),
        ("city", "c_city"), ("state", "c_state"),         ("zip", "c_zip"), ("timezone", "c_timezone"),
    ):
        if key in payload:
            setattr(item, column, _merchant_clean_text(payload, key))
    if "country" in payload:
        item.c_country = _normalize_country(_merchant_clean_text(payload, "country"))

    _stamp_updated(item, user)
    try:
        activation = _activate_organization(db, "MERCHANT", item)
        warning = _commit_organization_image(db, item, "MERCHANT", image_data=image_data, remove_image=remove_image)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A merchant with this name already exists.")
    return JSONResponse({"success": True, "id": merchant_id, **activation,
                         **({"image_warning": warning} if image_data is not None or remove_image else {})})


@router.delete("/platform/merchant/{merchant_id}")
def delete_merchant(merchant_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Merchant not found.")

    # Merchants referenced by stores, subscriptions, invoices, etc. are deactivated, never physically deleted.
    refs = db.execute(
        text(
            """
            SELECT c.conrelid::regclass::text AS tbl, a.attname AS col
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.contype = 'f' AND c.confrelid = 'tb_merchant'::regclass
            """
        )
    ).all()
    in_use = False
    for tbl, col in refs:
        if not re.fullmatch(r"[A-Za-z0-9_\.\"]+", tbl) or not re.fullmatch(r"[A-Za-z0-9_]+", col):
            continue
        count = db.execute(text(f'SELECT COUNT(*) FROM {tbl} WHERE "{col}" = :mid'), {"mid": merchant_id}).scalar()
        if count:
            in_use = True
            break

    if in_use:
        item.c_status = "inactive"
        _stamp_updated(item, user)
        db.commit()
        return JSONResponse({"success": True, "deactivated": True})

    db.delete(item)
    db.commit()
    return JSONResponse({"success": True, "deleted": True})


# Sessions

@router.get("/platform/sessions")
def list_sessions(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    from backend.services.web_sessions import expire_sessions
    from backend.services.session_context import session_organization
    from backend.services.session_metadata import session_client_network
    expire_sessions(db)
    db.commit()
    query = db.query(SessionTbl, PlatformUser).outerjoin(
        PlatformUser, SessionTbl.i_platform_user_id == PlatformUser.i_platform_user_id
    )

    normalized_status = (status or "").strip().upper()
    if normalized_status and normalized_status not in {"ACTIVE", "LOGGED_OUT", "EXPIRED", "TERMINATED", "REPLACED"}:
        raise HTTPException(status_code=422, detail="Invalid session status.")
    if normalized_status:
        query = query.filter(SessionTbl.c_status == normalized_status)

    rows = query.order_by(SessionTbl.i_session_id.desc()).all()
    results = []
    for sess, u in rows:
        organization, context_label = session_organization(db, sess)
        network = session_client_network(sess)
        if search and not any(
            search.strip().casefold() in str(value or "").casefold()
            for value in (organization, context_label, u.c_email if u else None,
                          u.c_first_name if u else None, u.c_last_name if u else None,
                          sess.c_client_id, sess.c_user_agent, sess.c_ip_address,
                          network["client_local_ip"], network["client_location"],
                          sess.c_browser_name, sess.c_browser_version, sess.c_os_name,
                          sess.c_os_version, sess.c_device_name, sess.c_device_uuid)
        ):
            continue
        results.append(
            {
                "id": sess.i_session_id,
                "user_id": sess.i_platform_user_id,
                "user_email": u.c_email if u else None,
                "user_name": f"{(u.c_first_name or '').strip()} {(u.c_last_name or '').strip()}".strip() if u else None,
                "store_id": sess.i_store_id,
                "login_context": sess.c_login_context,
                "organization_name": organization,
                "context_label": context_label,
                "store_name": context_label if sess.c_login_context == "STORE" else None,
                "merchant_id": sess.i_merchant_id,
                "merchant_name": organization,
                "device_id": sess.c_client_id,
                "browser_client_id": sess.c_client_id,
                "device_uuid": sess.c_device_uuid,
                "device_name": sess.c_device_name,
                "app_version": sess.c_app_version,
                "user_agent": sess.c_user_agent,
                **network,
                "ip_address": network["client_public_ip"],
                "public_ip": network["client_public_ip"],
                "local_ip": network["client_local_ip"],
                "country_code": network["client_country_code"],
                "region": network["client_region"],
                "city": network["client_city"],
                "browser_name": sess.c_browser_name,
                "browser_version": sess.c_browser_version,
                "os_name": sess.c_os_name,
                "os_version": sess.c_os_version,
                "current": sess.c_session_key == getattr(request.state, "web_session_key", None),
                "status": sess.c_status,
                "login_at": sess.dt_login.isoformat() if sess.dt_login else None,
                "last_active_at": sess.dt_last_active.isoformat() if sess.dt_last_active else None,
                "terminated_at": sess.dt_terminated.isoformat() if sess.dt_terminated else None,
                "logout_at": sess.dt_logout.isoformat() if sess.dt_logout else None,
                "expires_at": sess.dt_expires.isoformat() if sess.dt_expires else None,
                "terminated_by": sess.i_terminated_by,
                "created_at": sess.dt_created.isoformat() if sess.dt_created else None,
            }
        )

    return _set_no_cache_headers(JSONResponse(results))


@router.put("/platform/session/{session_id}")
def update_session(session_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    raise HTTPException(status_code=405, detail="Sessions are generated by authentication and cannot be edited.")


@router.delete("/platform/session/{session_id}")
@router.post("/platform/session/{session_id}/terminate")
def delete_session(session_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    from backend.routers.dashboard import _is_super_admin
    from backend.routers.system_settings import _validate_origin
    from backend.services.web_sessions import terminate_session
    if not _is_super_admin(db, user):
        raise HTTPException(status_code=403, detail="Only Super Admin can terminate sessions.")
    _validate_origin(request)
    terminate_session(db, session_id, user.i_platform_user_id)
    return JSONResponse({"success": True})
