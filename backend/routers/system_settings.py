import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import available_timezones

import smtplib
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from backend.config.settings import APP_NAME
from backend.config.templates import get_brand_context, templates
from backend.core.data_paths import PROJECT_ROOT
from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.role import Role
from backend.models_admin.system_settings import SystemSettings
from backend.routers.platform_store import (
    _set_no_cache_headers,
    get_current_user,
    get_db,
)
from backend.services.mail_common import EmailConfigurationError, MailDeliveryError, validate_email_address
from backend.services.system_settings import (
    DATE_FORMATS,
    DEFAULT_FROM_EMAIL,
    TIME_FORMATS,
    encryption_key_value,
    get_or_create_settings,
    get_smtp_configuration,
    get_storage_root,
    open_smtp_connection,
    record_settings_change,
    require_encryption_key,
    safe_mail_error,
    send_configured_email,
    smtp_environment_mode,
    smtp_public_status,
)
from backend.services.license_email import build_license_email, build_system_test_email, validate_email_content


router = APIRouter()
from backend.services import images

COMPANY_FIELDS = {
    "company_name": ("c_company_name", 255),
    "display_name": ("c_display_name", 255),
    "website": ("c_website", 1000),
    "contact_email": ("c_contact_email", 254),
    "phone": ("c_phone", 80),
    "address": ("c_address", 2000),
}
EMAIL_FIELDS = {
    "sender_name": ("c_sender_name", 255),
    "from_email": ("c_from_email", 254),
    "reply_to_email": ("c_reply_to_email", 254),
    "smtp_host": ("c_smtp_host", 255),
    "smtp_username": ("c_smtp_username", 255),
    "test_recipient_email": ("c_test_recipient_email", 254),
}
GENERAL_FIELDS = {
    "default_timezone": "c_default_timezone",
    "date_format": "c_date_format",
    "time_format": "c_time_format",
}


def _require_super_admin(request: Request, db: Session) -> PlatformUser:
    user = get_current_user(request, db)
    role = db.query(Role).filter(Role.i_role_id == user.i_role_id).first()
    if not role or (role.c_name or "").strip().casefold() != "superadmin":
        raise HTTPException(status_code=403, detail="Super Admin access is required.")
    return user


def _validate_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        if referer:
            origin = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"
    if origin and urlparse(origin).netloc.casefold() != request.url.netloc.casefold():
        raise HTTPException(status_code=403, detail="Cross-origin settings requests are not allowed.")


def _row_or_defaults(db: Session) -> SystemSettings | None:
    return get_or_create_settings(db)


def _path_status(path: Path, *, probe_write: bool = False) -> dict:
    exists = path.exists()
    is_directory = path.is_dir() if exists else False
    readable = bool(is_directory and os.access(path, os.R_OK))
    writable = bool(is_directory and os.access(path, os.W_OK))
    error = None
    if is_directory and probe_write:
        temp_path = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".awsum-path-check-", dir=path)
            os.close(descriptor)
            temp_path = Path(name)
            writable = True
        except OSError as exc:
            error = str(exc)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError as exc:
                    error = f"Temporary path-check file could not be removed: {exc}"
                    writable = False
    elif is_directory:
        error = None if readable and writable else "The configured path is not readable or writable."
    elif exists:
        error = "The configured path exists but is not a directory."
    else:
        error = "The configured path does not exist."
    return {
        "path": str(path),
        "exists": exists,
        "is_directory": is_directory,
        "readable": readable,
        "writable": writable,
        "ok": exists and is_directory and readable and writable,
        "error": error,
    }


def _response_settings(db: Session) -> dict:
    row = _row_or_defaults(db)
    effective_path, path_source = get_storage_root(db)
    saved_path = row.c_data_root_path if row else None
    environment_mode = smtp_environment_mode()
    email_status = smtp_public_status(db)
    try:
        data_exists = effective_path.is_dir() and next(effective_path.iterdir(), None) is not None
    except OSError:
        data_exists = False

    if environment_mode:
        email = {
            "sender_name": email_status["sender_name"],
            "from_email": email_status["from_email"],
            "reply_to_email": email_status["reply_to_email"] or "",
            "smtp_host": email_status["host"],
            "smtp_port": email_status["port"],
            "smtp_port_default": not bool(os.getenv("AWSUM_SMTP_PORT", "").strip()),
            "smtp_username": email_status["username"],
            "encryption": email_status["encryption"],
            "authentication": email_status["authentication"],
            "test_recipient_email": row.c_test_recipient_email if row else "",
            "password_configured": bool(os.getenv("AWSUM_SMTP_PASSWORD")),
            "source": "environment",
            "read_only": True,
            "ready": email_status["ready"],
            "status": email_status["status"],
            "missing": email_status["missing"],
            "configuration_error": email_status["error"],
        }
    else:
        email = {
            "sender_name": email_status["sender_name"],
            "from_email": email_status["from_email"],
            "reply_to_email": email_status["reply_to_email"] or "",
            "smtp_host": email_status["host"],
            "smtp_port": email_status["port"],
            "smtp_port_default": not bool(row and row.i_smtp_port is not None),
            "smtp_username": email_status["username"],
            "encryption": email_status["encryption"],
            "authentication": email_status["authentication"],
            "test_recipient_email": row.c_test_recipient_email if row else "",
            "password_configured": bool(row and row.c_smtp_password_encrypted),
            "source": "system_settings",
            "read_only": False,
            "ready": email_status["ready"],
            "status": email_status["status"],
            "missing": email_status["missing"],
            "configuration_error": email_status["error"],
        }
    email.update({
        "test_status": row.c_email_test_status if row else None,
        "test_result": row.c_email_test_result if row else None,
        "tested_at": (
            row.dt_email_tested_at.replace(tzinfo=timezone.utc).isoformat()
            if row and row.dt_email_tested_at else None
        ),
    })

    return {
        "revision": row.i_revision if row else 0,
        "company": {
            "company_name": row.c_company_name if row and row.c_company_name else APP_NAME,
            "display_name": row.c_display_name if row and row.c_display_name else APP_NAME,
            "logo_url": images.platform_logo_url(row.c_logo_url if row else None),
            "website": row.c_website if row else "",
            "contact_email": row.c_contact_email if row else "",
            "phone": row.c_phone if row else "",
            "address": row.c_address if row else "",
        },
        "storage": {
            "configured_path": str(effective_path) if path_source == "environment" else (saved_path or ""),
            "effective_path": str(effective_path),
            "source": path_source,
            "environment_override": path_source == "environment",
            "has_existing_data": data_exists,
            **_path_status(effective_path),
        },
        "email": email,
        "general": {
            "default_timezone": row.c_default_timezone if row and row.c_default_timezone else "UTC",
            "date_format": row.c_date_format if row and row.c_date_format else "YYYY-MM-DD",
            "time_format": row.c_time_format if row and row.c_time_format else "12h",
            "admin_timezone_note": "No per-admin timezone setting is currently implemented; this default applies to platform display settings.",
        },
        "timezones": sorted(available_timezones()),
        "apply_notice": {
            "company": "Company information and logo apply immediately.",
            "storage": "Storage path changes apply immediately. Existing owner data, images (media), and image/path recovery maps are not moved; copy or migrate them before switching roots.",
            "email": "Email settings apply immediately to new messages. Existing SMTP environment settings take precedence until removed from the server environment.",
            "general": "General defaults apply to future platform displays. Database timestamps remain stored in UTC.",
        },
        "encryption_key_configured": bool(encryption_key_value()),
    }


def _validate_revision(payload: dict, row: SystemSettings | None) -> None:
    expected = payload.get("revision")
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise HTTPException(status_code=400, detail="Settings revision is required.")
    current = row.i_revision if row else 0
    if expected != current:
        raise HTTPException(
            status_code=409,
            detail="Settings were changed by another administrator. Reload before saving.",
        )


def _validate_payload_fields(payload: dict, allowed: set[str]) -> None:
    unexpected = set(payload) - allowed - {"revision"}
    missing = allowed - set(payload)
    if unexpected:
        raise HTTPException(status_code=400, detail="Unsupported settings fields: " + ", ".join(sorted(unexpected)))
    if missing:
        raise HTTPException(status_code=400, detail="Missing settings fields: " + ", ".join(sorted(missing)))


def _normalize_website(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.IGNORECASE):
        if "://" in value:
            raise HTTPException(status_code=400, detail="website must be a valid http or https URL.")
        value = "https://" + value
    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        parsed.port
    except ValueError:
        raise HTTPException(status_code=400, detail="website must be a valid http or https URL.")
    valid_host = bool(re.fullmatch(
        r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}|localhost|\d{1,3}(\.\d{1,3}){3}",
        host, re.IGNORECASE,
    ))
    if not valid_host or re.search(r"\s", value):
        raise HTTPException(status_code=400, detail="website must be a valid http or https URL.")
    return value


def _clean_text(payload: dict, name: str, limit: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{name} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise HTTPException(status_code=400, detail=f"{name} must be {limit} characters or fewer.")
    return value


def _email_value(value: str, name: str, *, optional: bool = False) -> str | None:
    if not value and optional:
        return None
    try:
        return validate_email_address(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be a valid email address.") from exc


def _save_section(
    section: str,
    payload: dict,
    request: Request,
    db: Session,
    logo_upload: UploadFile | None = None,
) -> dict:
    _validate_origin(request)
    user = _require_super_admin(request, db)
    existing_row = get_or_create_settings(db)
    row = get_or_create_settings(db, create=True)
    db.refresh(row, with_for_update=True)
    # A row created by this very request counts as revision 0 for the client.
    _validate_revision(payload, row if existing_row is not None else None)
    changed_fields: list[str] = []
    logo_data = images.read_image_upload(logo_upload) if section == "company" and logo_upload else None

    if section == "company":
        _validate_payload_fields(payload, set(COMPANY_FIELDS))
        for name, (column, limit) in COMPANY_FIELDS.items():
            value = _clean_text(payload, name, limit)
            if name == "contact_email":
                value = _email_value(value, name, optional=True) or ""
            if name == "website" and value:
                value = _normalize_website(value)
            if getattr(row, column) != value:
                setattr(row, column, value)
                changed_fields.append(name)
        if logo_data is not None:
            changed_fields.append("company_logo")
    elif section == "storage":
        _validate_payload_fields(payload, {"data_root_path", "confirm_existing_data_path_change"})
        if os.getenv("AWSUM_STORAGE_ROOT", "").strip():
            raise HTTPException(status_code=409, detail="AWSUM_STORAGE_ROOT is set; the effective path is environment-managed.")
        value = _clean_text(payload, "data_root_path", 1000)
        confirm_change = payload.get("confirm_existing_data_path_change")
        if not isinstance(confirm_change, bool):
            raise HTTPException(status_code=400, detail="confirm_existing_data_path_change must be true or false.")
        if value:
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise HTTPException(status_code=400, detail="Data Root Path must be an absolute path.")
            value = str(path.resolve())
            from backend.services.system_settings import _storage_path
            try:
                _storage_path(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        current_path, _ = get_storage_root(db)
        try:
            current_has_data = current_path.is_dir() and next(current_path.iterdir(), None) is not None
        except OSError:
            current_has_data = False
        if value and Path(value) != current_path and current_has_data and not confirm_change:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Existing data is present at {current_path}. New path {value} will not receive "
                    "those files automatically. Confirm that a separate file migration is required."
                ),
            )
        if row.c_data_root_path != (value or None):
            row.c_data_root_path = value or None
            changed_fields.append("data_root_path")
    elif section == "email":
        allowed = set(EMAIL_FIELDS) | {
            "smtp_port",
            "smtp_port_is_default",
            "encryption",
            "authentication",
            "smtp_password",
            "clear_smtp_password",
        }
        environment_mode = smtp_environment_mode()
        if environment_mode:
            mutable = {"test_recipient_email"}
            _validate_payload_fields(payload, mutable)
            value = _clean_text(payload, "test_recipient_email", EMAIL_FIELDS["test_recipient_email"][1])
            value = _email_value(value, "test_recipient_email", optional=True) or ""
            if row.c_test_recipient_email != value:
                row.c_test_recipient_email = value
                changed_fields.append("test_recipient_email")
        else:
            _validate_payload_fields(payload, allowed)
            for name, (column, limit) in EMAIL_FIELDS.items():
                value = _clean_text(payload, name, limit)
                if name == "from_email" and not value:
                    value = DEFAULT_FROM_EMAIL
                if name in {"from_email", "reply_to_email", "test_recipient_email"}:
                    value = _email_value(value, name, optional=name != "from_email") or ""
                if getattr(row, column) != value:
                    setattr(row, column, value)
                    changed_fields.append(name)

            raw_port = payload.get("smtp_port")
            port_is_default = payload.get("smtp_port_is_default")
            if not isinstance(port_is_default, bool):
                raise HTTPException(status_code=400, detail="smtp_port_is_default must be true or false.")
            if isinstance(raw_port, bool):
                raise HTTPException(status_code=400, detail="smtp_port must be a port number.")
            try:
                port = int(raw_port) if str(raw_port).strip() else None
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="smtp_port must be a port number.") from exc
            if port is not None and not 1 <= port <= 65535:
                raise HTTPException(status_code=400, detail="smtp_port must be between 1 and 65535.")
            if port_is_default:
                port = None
            if row.i_smtp_port != port:
                row.i_smtp_port = port
                changed_fields.append("smtp_port")

            encryption = payload.get("encryption")
            if encryption not in {"STARTTLS", "SSL-TLS"}:
                raise HTTPException(status_code=400, detail="encryption must be STARTTLS or SSL-TLS.")
            if row.c_smtp_encryption != encryption:
                row.c_smtp_encryption = encryption
                changed_fields.append("encryption")

            authentication = payload.get("authentication")
            clear_password = payload.get("clear_smtp_password")
            if not isinstance(authentication, bool) or not isinstance(clear_password, bool):
                raise HTTPException(status_code=400, detail="authentication and clear_smtp_password must be true or false.")
            if row.b_smtp_authentication != int(authentication):
                row.b_smtp_authentication = int(authentication)
                changed_fields.append("authentication")

            password = payload.get("smtp_password")
            if not isinstance(password, str) or len(password) > 2000:
                raise HTTPException(status_code=400, detail="smtp_password must be 2,000 characters or fewer.")
            if clear_password and password:
                raise HTTPException(status_code=400, detail="Clear the current password or enter a new one, not both.")
            if clear_password:
                if row.c_smtp_password_encrypted:
                    row.c_smtp_password_encrypted = None
                    changed_fields.append("smtp_password")
            elif password:
                try:
                    encrypted = require_encryption_key().encrypt(password.encode("utf-8")).decode("ascii")
                except EmailConfigurationError as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
                if encrypted != row.c_smtp_password_encrypted:
                    row.c_smtp_password_encrypted = encrypted
                    changed_fields.append("smtp_password")

            if not row.c_from_email:
                row.c_from_email = DEFAULT_FROM_EMAIL
                changed_fields.append("from_email")
    elif section == "general":
        _validate_payload_fields(payload, set(GENERAL_FIELDS))
        timezone_name = _clean_text(payload, "default_timezone", 100)
        if timezone_name not in available_timezones():
            raise HTTPException(status_code=400, detail="default_timezone must be a valid IANA timezone.")
        date_format = _clean_text(payload, "date_format", 30)
        time_format = _clean_text(payload, "time_format", 10)
        if date_format not in DATE_FORMATS:
            raise HTTPException(status_code=400, detail="Unsupported date_format.")
        if time_format not in TIME_FORMATS:
            raise HTTPException(status_code=400, detail="time_format must be 12h or 24h.")
        values = {
            "default_timezone": timezone_name,
            "date_format": date_format,
            "time_format": time_format,
        }
        for name, value in values.items():
            column = GENERAL_FIELDS[name]
            if getattr(row, column) != value:
                setattr(row, column, value)
                changed_fields.append(name)
    else:
        raise HTTPException(status_code=404, detail="Unknown settings section.")

    try:
        if changed_fields:
            record_settings_change(db, row, user.i_platform_user_id, changed_fields)
        if logo_data is not None:
            from backend.services.image_service import commit_entity_image, image_operation_lock
            from backend.services.resource_storage import AwsumStorageService
            owner = AwsumStorageService(db).resolve_owner("PLATFORM")
            with image_operation_lock(owner, "logo"):
                warning = commit_entity_image(db, row, owner, "logo", category="logo",
                                              image_data=logo_data, reference_field="c_logo_url")
        else:
            warning = None
            db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    result = _response_settings(db)
    if warning:
        result["image_warning"] = warning
    return result


@router.get("/platform/system-settings", response_class=HTMLResponse)
def system_settings_page(request: Request, db: Session = Depends(get_db)):
    user = _require_super_admin(request, db)
    role = db.query(Role).filter(Role.i_role_id == user.i_role_id).first()
    return _set_no_cache_headers(templates.TemplateResponse(
        request,
        "platform/system_settings.html",
        {
            "request": request,
            "APP_NAME": APP_NAME,
            "user": user,
            "role": role.c_name if role else "SuperAdmin",
            "active_page": "system-settings",
            "active_module": "system-settings",
            "is_super_admin": True,
            **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
        },
    ))


@router.get("/platform/api/system-settings")
def get_system_settings(request: Request, db: Session = Depends(get_db)):
    _require_super_admin(request, db)
    return _set_no_cache_headers(JSONResponse(_response_settings(db)))


@router.put("/platform/api/system-settings/{section}")
def save_system_settings(section: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Settings payload must be a JSON object.")
    try:
        response = _save_section(section, payload, request, db)
    except HTTPException:
        db.rollback()
        raise
    return _set_no_cache_headers(JSONResponse(response))


@router.post("/platform/api/system-settings/storage/check")
def check_storage_path(payload: dict, request: Request, db: Session = Depends(get_db)):
    _validate_origin(request)
    _require_super_admin(request, db)
    if not isinstance(payload, dict) or set(payload) != {"path"} or not isinstance(payload["path"], str):
        raise HTTPException(status_code=400, detail="A path string is required.")
    raw_path = payload["path"].strip()
    if not raw_path or len(raw_path) > 1000:
        raise HTTPException(status_code=400, detail="Enter a path of 1,000 characters or fewer.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute.")
    return _set_no_cache_headers(JSONResponse(_path_status(path.resolve(), probe_write=True)))


@router.post("/platform/api/system-settings/company/save")
def save_company_settings(
    request: Request,
    settings: str = Form(...),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    try:
        payload = json.loads(settings)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Company settings payload must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Company settings payload must be a JSON object.")
    try:
        response = _save_section("company", payload, request, db, logo_upload=file)
    except HTTPException:
        db.rollback()
        raise
    return _set_no_cache_headers(JSONResponse(response))


@router.post("/platform/api/system-settings/email/test-connection")
def test_email_connection(request: Request, db: Session = Depends(get_db)):
    _validate_origin(request)
    _require_super_admin(request, db)
    row = get_or_create_settings(db, create=True)
    try:
        config = get_smtp_configuration(db)
        with open_smtp_connection(config):
            pass
        result = "SMTP connection, encryption, and authentication succeeded."
        status = "success"
    except EmailConfigurationError as exc:
        result = str(exc)
        status = "failed"
    except (smtplib.SMTPException, OSError) as exc:
        try:
            config = get_smtp_configuration(db)
            result = safe_mail_error(exc, config)
        except EmailConfigurationError:
            result = "SMTP configuration is incomplete."
        status = "failed"
    if row:
        row.c_email_test_status = f"connection_{status}"
        row.c_email_test_result = result[:1000]
        row.dt_email_tested_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    return _set_no_cache_headers(JSONResponse({
        "success": status == "success",
        "kind": "connection",
        "result": result,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "email": _response_settings(db)["email"],
    }))


@router.post("/platform/api/system-settings/email/send-test")
async def send_test_email(request: Request, db: Session = Depends(get_db)):
    _validate_origin(request)
    _require_super_admin(request, db)
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="A valid test email request is required.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="A valid test email request is required.")
    row = get_or_create_settings(db, create=True)
    try:
        recipient = validate_email_address(payload.get("recipient"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Enter a valid recipient email address.") from exc
    try:
        subject, custom_message = validate_email_content(
            payload.get("subject"),
            payload.get("message"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    company_name = row.c_company_name if row and row.c_company_name else APP_NAME
    logo_url = images.platform_logo_url(row.c_logo_url if row else None)
    absolute_logo_url = None
    if logo_url:
        absolute_logo_url = urljoin(str(request.base_url), logo_url.lstrip("/"))
    message = build_system_test_email(
        recipient=recipient,
        subject=subject,
        custom_message=custom_message,
        company_name=company_name,
        logo_url=absolute_logo_url,
    )
    try:
        message_id, result = send_configured_email(db, message)
    except EmailConfigurationError as exc:
        tested_at = datetime.now(timezone.utc).isoformat()
        if row:
            row.c_email_test_status = "send_failed"
            row.c_email_test_result = str(exc)[:1000]
            row.dt_email_tested_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        return _set_no_cache_headers(JSONResponse({
            "accepted": False,
            "detail": str(exc),
            "result": str(exc),
            "tested_at": tested_at,
            "email": _response_settings(db)["email"],
        }, status_code=503))
    except MailDeliveryError as exc:
        tested_at = datetime.now(timezone.utc).isoformat()
        if row:
            row.c_email_test_status = "send_failed"
            row.c_email_test_result = str(exc)[:1000]
            row.dt_email_tested_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        result_message = "SMTP server did not accept the test email: " + str(exc)
        return _set_no_cache_headers(JSONResponse({
            "accepted": False,
            "detail": result_message,
            "result": result_message,
            "tested_at": tested_at,
            "email": _response_settings(db)["email"],
        }, status_code=502))

    if row:
        row.c_email_test_status = "send_accepted"
        row.c_email_test_result = "Accepted by SMTP server; recipient delivery is not confirmed."
        row.dt_email_tested_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    return _set_no_cache_headers(JSONResponse({
        "accepted": True,
        "recipient": recipient,
        "message_id": message_id,
        "result": result + " Recipient delivery is not confirmed.",
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "email": _response_settings(db)["email"],
    }))
