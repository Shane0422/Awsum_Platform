from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import secrets

from fastapi import APIRouter, Request, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Optional
import csv
import io

from backend.database.pg_platform import PlatformSessionLocal, get_next_account_store_id
from backend.models_admin.store import Store
from backend.models_admin.business_type import BusinessType
from backend.models_admin.role import Role
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.account import Client
from backend.models_admin.session import SessionTbl
from backend.models_admin.device import Device
from backend.models_admin.device_log import DeviceLog
from backend.models_admin.license import License
from backend.models_admin.agent import Agent
from backend.models_admin.agent_type import AgentType
from backend.models_admin.payment_method import PaymentMethod
from backend.utils.jwt_handler import SECRET_KEY, ALGORITHM
from jose import jwt, JWTError
from backend.config.templates import templates
from backend.config.settings import APP_NAME

router = APIRouter()

# Platform admin store ID (fixed)
ADMIN_STORE_ID = 1000001
LEGACY_TEST_STORE_CODE = "CUST_00001"
TEST_STORE_CODE = "CLT_00001"  # Legacy code kept for backwards-compat filtering
CLIENT_STORE_PREFIX = "STR_"
CLIENT_STORE_SEQ_START = 20001
CLIENT_CODE_PREFIX = "CLT_"
CLIENT_CODE_SEQ_START = 11001
CLIENT_CODE_ADVISORY_LOCK_KEY = 11001001
AGENT_CODE_PREFIX = "AGT_"
ALLOWED_DASHBOARD_TYPES = {"PLATFORM", "STANDARD", "RESTAURANT", "DELI", "TUXEDO_RENTAL"}
BASE_DIR = Path(__file__).resolve().parents[2]
BRANDS_STATIC_ROOT = BASE_DIR / "static" / "images" / "brands"
DEVICE_ACTIVATION_TTL_MINUTES = 30

get_next_client_store_id = get_next_account_store_id


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


def _generate_client_store_code(db: Session) -> tuple[str, int]:
    max_seq = (
        db.query(Store.i_store_seq)
        .filter(
            Store.i_store_seq.is_not(None),
            Store.i_store_seq >= CLIENT_STORE_SEQ_START,
            Store.c_is_test != 1,
        )
        .order_by(Store.i_store_seq.desc())
        .limit(1)
        .scalar()
    )

    next_seq = CLIENT_STORE_SEQ_START if not max_seq or max_seq < CLIENT_STORE_SEQ_START else max_seq + 1

    while True:
        candidate_code = f"{CLIENT_STORE_PREFIX}{next_seq:05d}"
        exists = db.query(Store.i_store_id).filter(Store.c_store_code == candidate_code).first()
        if not exists:
            return candidate_code, next_seq
        next_seq += 1


def _generate_next_client_code(db: Session) -> str:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": CLIENT_CODE_ADVISORY_LOCK_KEY},
    )

    max_seq = db.execute(
        text(
            """
            SELECT COALESCE(
                MAX(CAST(SUBSTRING(c_client_code FROM '([0-9]+)$') AS INTEGER)),
                :seq_start - 1
            ) AS max_seq
            FROM tb_client
            WHERE c_client_code ~ '^CLT_[0-9]+$'
            """
        ),
        {"seq_start": CLIENT_CODE_SEQ_START},
    ).scalar()

    next_seq = CLIENT_CODE_SEQ_START if not max_seq or max_seq < CLIENT_CODE_SEQ_START else max_seq + 1
    return f"{CLIENT_CODE_PREFIX}{next_seq:05d}"


def _generate_agent_code(agent_id: int) -> str:
    return f"{AGENT_CODE_PREFIX}{int(agent_id):05d}"


def _sanitize_asset_code(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]", "", str(value or "").strip())
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid code for logo path.")
    return normalized.upper()


def _validate_logo_upload(file: UploadFile) -> None:
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Logo file is required.")
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Logo must be an image file.")


def _write_logo_assets(target_dir: Path, file: UploadFile) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Logo file is empty.")
    (target_dir / "logo.png").write_bytes(data)
    # Sync uploaded logo as the dashboard favicon/brand icon as well
    (target_dir / "icon.png").write_bytes(data)


def _delete_logo_assets(target_dir: Path) -> int:
    deleted = 0
    for file_name in ("logo.png", "icon.png"):
        path = target_dir / file_name
        if path.exists():
            path.unlink()
            deleted += 1
    return deleted


def _ensure_client_store(db: Session, client_id: int, store_id: int) -> tuple[Client, Store]:
    client = db.query(Client).filter(Client.i_account_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

    store = (
        db.query(Store)
        .filter(Store.i_store_id == store_id, Store.i_account_id == client_id)
        .first()
    )
    if not store:
        raise HTTPException(status_code=404, detail="Store not found for this client.")

    return client, store


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


def _serialize_device(device: Device, license_obj: License | None, agent_obj: Agent | None = None) -> dict:
    return {
        "device_id": device.i_device_id,
        "store_id": device.i_store_id,
        "license_id": device.i_license_id,
        "installed_by_agent_id": device.i_installed_by_agent_id,
        "installed_by_agent_name": agent_obj.c_agent_name if agent_obj else None,
        "device_code": device.c_device_uuid,
        "device_name": device.c_device_name,
        "device_type": device.c_device_type,
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
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
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
        c_agent=str(payload.get("agent") or getattr(request, "headers", {}).get("user-agent") or "").strip() or None,
        c_os=str(payload.get("os") or device.c_os or "").strip() or None,
        c_version=str(payload.get("app_version") or payload.get("version") or device.c_app_version or "").strip() or None,
        c_action_by=str(action_by or payload.get("action_by") or payload.get("activated_by") or "").strip() or None,
        dt_event_time=datetime.now(),
        c_note=str(note or payload.get("note") or "").strip() or None,
    )
    db.add(event_log)
    return event_log


def _resolve_agent(agent_id: Optional[object], db: Session, *, required: bool = False) -> Optional[Agent]:
    normalized_id = _parse_optional_int(agent_id, "agent_id")
    if normalized_id is None:
        if required:
            raise HTTPException(status_code=400, detail="agent_id is required.")
        return None
    agent = db.query(Agent).filter(Agent.i_agent_id == normalized_id).first()
    if not agent:
        raise HTTPException(status_code=400, detail="Invalid agent_id.")
    return agent


# ---------------------------
# Auth Helpers
# ---------------------------

def get_current_user(request: Request, db: Session):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid: int = payload.get("uid")
        store = payload.get("store")
        role: int = payload.get("role")
        principal_type = payload.get("principal_type", "platform_user")
        if uid is None:
            raise HTTPException(status_code=401, detail="Invalid token.")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token.")

    if principal_type != "platform_user":
        raise HTTPException(status_code=401, detail="Invalid token principal for platform DB.")

    user = db.query(PlatformUser).filter(PlatformUser.i_platform_user_id == uid).first()

    if not user:
        raise HTTPException(status_code=401, detail="User not found.")

    try:
        store = int(store)
    except (TypeError, ValueError):
        pass

    user.login_store = store
    user.login_role = role
    if user.i_must_change_password == 1:
        raise HTTPException(status_code=403, detail="Password change required.")
    return user


def require_platform_admin(user):
    if not (user.login_role == 1 or user.login_store == ADMIN_STORE_ID):
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
    client_id: Optional[int] = None,
):
    """Returns a JSON list of stores, or renders the Store Management page for HTML requests."""
    user = get_current_user(request, db)
    require_platform_admin(user)

    # Render the page for HTML requests
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        roles = db.query(Role).all()
        business_types = db.query(BusinessType).filter(BusinessType.c_status == "active").all()
        stores = db.query(Store).order_by(Store.i_store_id).all()
        users = db.query(PlatformUser).all()

        return _set_no_cache_headers(templates.TemplateResponse(
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
    query = db.query(Store)

    if status:
        query = query.filter(Store.c_status == status)

    if operation_type:
        query = query.filter(Store.c_operation_type.ilike(f"%{operation_type}%"))

    if channel_type:
        query = query.filter(Store.c_channel_type.ilike(f"%{channel_type}%"))

    if device_type:
        query = query.filter(Store.c_device_type.ilike(f"%{device_type}%"))

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Store.c_store_name.ilike(term),
                Store.c_store_code.ilike(term),
                Store.c_email.ilike(term),
                Store.c_phone.ilike(term),
            )
        )

    if client_id is not None:
        query = query.filter(Store.i_account_id == client_id)

    if business_type:
        query = query.join(BusinessType, Store.i_business_type == BusinessType.i_business_type_id)
        query = query.filter(
            BusinessType.c_name.ilike(f"%{business_type}%")
            | BusinessType.c_business_code.ilike(f"%{business_type}%")
        )

    stores = query.order_by(Store.i_store_id).all()

    results = []
    for s in stores:
        bt = db.query(BusinessType).filter(BusinessType.i_business_type_id == s.i_business_type).first()
        client = db.query(Client).filter(Client.i_account_id == s.i_account_id).first() if s.i_account_id else None
        store_agent = db.query(Agent).filter(Agent.i_agent_id == s.i_installed_by_agent_id).first() if s.i_installed_by_agent_id else None
        results.append({
            "store_id": s.i_store_id,
            "store_seq": s.i_store_seq,
            "store_code": s.c_store_code,
            "store_name": s.c_store_name,
            "client_id": s.i_account_id,
            "client_name": client.c_account_name if client else None,
            "client_code": client.c_client_code if client else None,
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
            "installed_by_agent_id": s.i_installed_by_agent_id,
            "installed_by_agent_name": store_agent.c_agent_name if store_agent else None,
            "created_at": s.dt_created.isoformat() if s.dt_created else None,
        })

    return _set_no_cache_headers(JSONResponse(results))


@router.get("/platform/store/{store_id}")
def get_store(request: Request, store_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")

    bt = db.query(BusinessType).filter(BusinessType.i_business_type_id == store.i_business_type).first()
    client = db.query(Client).filter(Client.i_account_id == store.i_account_id).first() if store.i_account_id else None
    store_agent = db.query(Agent).filter(Agent.i_agent_id == store.i_installed_by_agent_id).first() if store.i_installed_by_agent_id else None

    data = {
        "store_id": store.i_store_id,
        "store_seq": store.i_store_seq,
        "store_code": store.c_store_code,
        "store_name": store.c_store_name,
        "client_id": store.i_account_id,
        "client_name": client.c_account_name if client else None,
        "client_code": client.c_client_code if client else None,
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
        "installed_by_agent_id": store.i_installed_by_agent_id,
        "installed_by_agent_name": store_agent.c_agent_name if store_agent else None,
    }

    return _set_no_cache_headers(JSONResponse(data))


@router.post("/platform/client/{client_id}/logo")
def upload_client_logo(
    client_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    client = db.query(Client).filter(Client.i_account_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

    _validate_logo_upload(file)
    client_code = _sanitize_asset_code(client.c_client_code or "")
    target_dir = BRANDS_STATIC_ROOT / "clients" / client_code
    _write_logo_assets(target_dir, file)

    logo_url = f"/static/images/brands/clients/{client_code}/logo.png"
    icon_url = f"/static/images/brands/clients/{client_code}/icon.png"
    return JSONResponse({"success": True, "logo_url": logo_url, "icon_url": icon_url})


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
    if not store.i_account_id:
        raise HTTPException(status_code=400, detail="Store must be linked to a Client before uploading a logo.")

    client = db.query(Client).filter(Client.i_account_id == store.i_account_id).first()
    if not client:
        raise HTTPException(status_code=400, detail="Linked Client not found.")

    _validate_logo_upload(file)
    client_code = _sanitize_asset_code(client.c_client_code or "")
    store_code = _sanitize_asset_code(store.c_store_code or "")
    target_dir = BRANDS_STATIC_ROOT / "clients" / client_code / "stores" / store_code
    _write_logo_assets(target_dir, file)

    logo_url = f"/static/images/brands/clients/{client_code}/stores/{store_code}/logo.png"
    icon_url = f"/static/images/brands/clients/{client_code}/stores/{store_code}/icon.png"
    return JSONResponse({"success": True, "logo_url": logo_url, "icon_url": icon_url})


@router.delete("/platform/client/{client_id}/logo")
def reset_client_logo(client_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    client = db.query(Client).filter(Client.i_account_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    client_code = _sanitize_asset_code(client.c_client_code or "")
    target_dir = BRANDS_STATIC_ROOT / "clients" / client_code
    deleted = _delete_logo_assets(target_dir)
    return JSONResponse({"success": True, "deleted_files": deleted})


@router.delete("/platform/store/{store_id}/logo")
def reset_store_logo(store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")
    if not store.i_account_id:
        raise HTTPException(status_code=400, detail="Store must be linked to a Client before resetting a logo.")

    client = db.query(Client).filter(Client.i_account_id == store.i_account_id).first()
    if not client:
        raise HTTPException(status_code=400, detail="Linked Client not found.")

    client_code = _sanitize_asset_code(client.c_client_code or "")
    store_code = _sanitize_asset_code(store.c_store_code or "")
    target_dir = BRANDS_STATIC_ROOT / "clients" / client_code / "stores" / store_code
    deleted = _delete_logo_assets(target_dir)
    return JSONResponse({"success": True, "deleted_files": deleted})


@router.post("/platform/store")
def create_store(request: Request, payload: dict, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store_name = payload.get("store_name")

    if not store_name:
        raise HTTPException(status_code=400, detail="Store name is required.")

    client_id_raw = payload.get("client_id")
    client_id = _parse_optional_int(client_id_raw, "client_id")
    if client_id is None:
        raise HTTPException(status_code=400, detail="client_id is required.")

    client = db.query(Client).filter(Client.i_account_id == client_id).first()
    if not client:
        raise HTTPException(status_code=400, detail="Invalid client_id.")

    for _ in range(5):
        generated_store_code, generated_seq = _generate_client_store_code(db)

        next_id = get_next_client_store_id(db)

        store = Store(
            i_store_id=next_id,
            i_store_seq=generated_seq,
            c_store_code=generated_store_code,
            c_store_name=store_name,
            i_account_id=client_id,
            c_contact_name=payload.get("contact_name"),
            c_owner_name=payload.get("owner_name"),
            c_address_line1=payload.get("address_line1") or payload.get("address"),
            c_address_line2=payload.get("address_line2"),
            c_city=payload.get("city"),
            c_state=payload.get("state"),
            c_zip=payload.get("zip"),
            c_country=payload.get("country"),
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
            c_remark=payload.get("remark"),
        )

        if payload.get("business_type"):
            bt_obj = db.query(BusinessType).filter(BusinessType.c_name == payload.get("business_type")).first()
            if bt_obj:
                store.i_business_type = bt_obj.i_business_type_id

        if payload.get("installed_by_agent_id"):
            agent_id = _parse_optional_int(payload.get("installed_by_agent_id"), "installed_by_agent_id")
            store.i_installed_by_agent_id = agent_id

        _stamp_created(store, user)
        db.add(store)
        try:
            db.commit()
            return JSONResponse({"store_id": next_id, "store_code": generated_store_code, "store_seq": generated_seq})
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
    user = get_current_user(request, db)
    require_platform_admin(user)

    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")

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
        "country": "c_country",
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

    if "installed_by_agent_id" in payload:
        agent_id = _parse_optional_int(payload.get("installed_by_agent_id"), "installed_by_agent_id")
        store.i_installed_by_agent_id = agent_id

    if "client_id" in payload:
        client_id_raw = payload.get("client_id")
        client_id = _parse_optional_int(client_id_raw, "client_id")
        if client_id is None:
            raise HTTPException(status_code=400, detail="client_id cannot be empty.")
        client = db.query(Client).filter(Client.i_account_id == client_id).first()
        if not client:
            raise HTTPException(status_code=400, detail="Invalid client_id.")
        store.i_account_id = client.i_account_id

    _stamp_updated(store, user)
    db.commit()
    return JSONResponse({"success": True})


@router.delete("/platform/store/{store_id}")
def delete_store(store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")

    if store.c_is_test == 1 or store.c_store_code in {TEST_STORE_CODE, LEGACY_TEST_STORE_CODE}:
        raise HTTPException(status_code=403, detail="Test Store cannot be deleted.")

    store.c_status = "inactive"
    _stamp_updated(store, user)
    db.commit()
    return JSONResponse({"success": True})


@router.get("/platform/client/{client_id}/stores")
def list_client_stores(client_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    client = db.query(Client).filter(Client.i_account_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

    stores = (
        db.query(Store)
        .filter(Store.i_account_id == client_id)
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
            "client_id": s.i_account_id,
            "client_name": client.c_account_name,
            "client_code": client.c_client_code,
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
        })
    return _set_no_cache_headers(JSONResponse(results))


@router.get("/platform/client/{client_id}/stores/{store_id}")
def get_client_store(client_id: int, store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    client, store = _ensure_client_store(db, client_id, store_id)
    bt = db.query(BusinessType).filter(BusinessType.i_business_type_id == store.i_business_type).first()

    data = {
        "store_id": store.i_store_id,
        "store_seq": store.i_store_seq,
        "store_code": store.c_store_code,
        "store_name": store.c_store_name,
        "client_id": store.i_account_id,
        "client_name": client.c_account_name,
        "client_code": client.c_client_code,
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
        "store_status": store.c_status,
    }
    return _set_no_cache_headers(JSONResponse(data))


@router.post("/platform/client/{client_id}/stores")
def create_client_store(client_id: int, request: Request, payload: dict, db: Session = Depends(get_db)):
    payload = dict(payload or {})
    payload["client_id"] = client_id
    return create_store(request, payload, db)


@router.put("/platform/client/{client_id}/stores/{store_id}")
def update_client_store(client_id: int, store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_client_store(db, client_id, store_id)

    payload = dict(payload or {})
    payload.pop("client_id", None)
    return update_store(store_id, payload, request, db)


@router.delete("/platform/client/{client_id}/stores/{store_id}")
def delete_client_store(client_id: int, store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_client_store(db, client_id, store_id)
    return delete_store(store_id, request, db)


@router.get("/platform/client/{client_id}/stores/{store_id}/devices")
def list_store_devices(client_id: int, store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_client_store(db, client_id, store_id)

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

    agent_ids = [d.i_installed_by_agent_id for d in devices if d.i_installed_by_agent_id]
    agent_map = {}
    if agent_ids:
        for agent in db.query(Agent).filter(Agent.i_agent_id.in_(agent_ids)).all():
            agent_map[agent.i_agent_id] = agent

    return _set_no_cache_headers(JSONResponse([
        _serialize_device(device, license_map.get(device.i_license_id), agent_map.get(device.i_installed_by_agent_id)) for device in devices
    ]))


@router.get("/platform/client/{client_id}/stores/{store_id}/devices/{device_id}")
def get_store_device_detail(client_id: int, store_id: int, device_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_client_store(db, client_id, store_id)

    device = (
        db.query(Device)
        .filter(Device.i_device_id == device_id, Device.i_store_id == store_id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found for this store.")

    license_obj = db.query(License).filter(License.i_license_id == device.i_license_id).first() if device.i_license_id else None
    agent_obj = db.query(Agent).filter(Agent.i_agent_id == device.i_installed_by_agent_id).first() if device.i_installed_by_agent_id else None
    logs = (
        db.query(DeviceLog)
        .filter(DeviceLog.i_device_id == device.i_device_id)
        .order_by(DeviceLog.dt_event_time.desc(), DeviceLog.i_log_id.desc())
        .all()
    )

    return _set_no_cache_headers(JSONResponse({
        "device": _serialize_device(device, license_obj, agent_obj),
        "logs": [_serialize_device_log(log) for log in logs],
    }))


@router.post("/platform/client/{client_id}/stores/{store_id}/devices")
def create_store_device(client_id: int, store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_client_store(db, client_id, store_id)

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

    installed_agent = _resolve_agent(payload.get("installed_by_agent_id"), db)

    device = Device(
        i_store_id=store_id,
        i_license_id=license_id,
        i_installed_by_agent_id=installed_agent.i_agent_id if installed_agent else None,
        c_device_uuid=_generate_store_device_code(db, store_id),
        c_device_name=device_name,
        c_device_type=str(payload.get("device_type") or "POS").strip() or "POS",
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


@router.put("/platform/client/{client_id}/stores/{store_id}/devices/{device_id}")
def update_store_device(client_id: int, store_id: int, device_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_client_store(db, client_id, store_id)

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
        device.c_device_type = str(payload.get("device_type") or "").strip() or "POS"

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
    if "installed_by_agent_id" in payload:
        installed_agent = _resolve_agent(payload.get("installed_by_agent_id"), db)
        device.i_installed_by_agent_id = installed_agent.i_agent_id if installed_agent else None
    elif device.i_installed_by_agent_id:
        installed_agent = db.query(Agent).filter(Agent.i_agent_id == device.i_installed_by_agent_id).first()

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


@router.delete("/platform/client/{client_id}/stores/{store_id}/devices/{device_id}")
def delete_store_device(client_id: int, store_id: int, device_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _ensure_client_store(db, client_id, store_id)

    device = (
        db.query(Device)
        .filter(Device.i_device_id == device_id, Device.i_store_id == store_id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found for this store.")

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


# Agents

@router.get("/platform/agent-types")
def list_agent_types(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(AgentType)

    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(AgentType.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                AgentType.c_agent_type_code.ilike(term),
                AgentType.c_agent_type_name.ilike(term),
            )
        )

    items = query.order_by(AgentType.i_agent_type_id).all()
    return JSONResponse([
        {
            "id": item.i_agent_type_id,
            "agent_type_code": item.c_agent_type_code,
            "agent_type_name": item.c_agent_type_name,
            "status": item.c_status,
        }
        for item in items
    ])


@router.post("/platform/agent-type")
def create_agent_type(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    type_code = str(payload.get("agent_type_code") or "").strip().upper()
    type_name = str(payload.get("agent_type_name") or "").strip()
    if not type_code or not type_name:
        raise HTTPException(status_code=400, detail="agent_type_code and agent_type_name are required")

    item = AgentType(
        c_agent_type_code=type_code,
        c_agent_type_name=type_name,
        c_status=_normalize_status(payload.get("status"), {"active", "inactive"}) or "active",
    )
    db.add(item)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="agent_type_code already exists")

    db.refresh(item)
    return JSONResponse({"id": item.i_agent_type_id})


@router.put("/platform/agent-type/{agent_type_id}")
def update_agent_type(agent_type_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(AgentType).filter(AgentType.i_agent_type_id == agent_type_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Agent type not found")

    if "agent_type_code" in payload:
        type_code = str(payload.get("agent_type_code") or "").strip().upper()
        if not type_code:
            raise HTTPException(status_code=400, detail="agent_type_code is required")
        item.c_agent_type_code = type_code
    if "agent_type_name" in payload:
        type_name = str(payload.get("agent_type_name") or "").strip()
        if not type_name:
            raise HTTPException(status_code=400, detail="agent_type_name is required")
        item.c_agent_type_name = type_name
    if "status" in payload:
        item.c_status = _normalize_status(payload.get("status"), {"active", "inactive"}) or "active"

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="agent_type_code already exists")

    return JSONResponse({"success": True})


@router.delete("/platform/agent-type/{agent_type_id}")
def delete_agent_type(agent_type_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(AgentType).filter(AgentType.i_agent_type_id == agent_type_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Agent type not found")

    linked_agent_count = db.query(Agent.i_agent_id).filter(Agent.i_agent_type_id == agent_type_id).count()
    if linked_agent_count > 0:
        item.c_status = "inactive"
        db.commit()
        return JSONResponse({"success": True, "mode": "soft-delete", "status": "inactive"})

    db.delete(item)
    db.commit()
    return JSONResponse({"success": True, "mode": "hard-delete"})

@router.get("/platform/agents")
def list_agents(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(Agent, AgentType).outerjoin(AgentType, Agent.i_agent_type_id == AgentType.i_agent_type_id)

    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(Agent.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Agent.c_agent_code.ilike(term),
                Agent.c_agent_name.ilike(term),
                Agent.c_company_name.ilike(term),
                Agent.c_contact_name.ilike(term),
                Agent.c_email.ilike(term),
                Agent.c_phone.ilike(term),
                AgentType.c_agent_type_name.ilike(term),
            )
        )

    items = query.order_by(Agent.i_agent_id).all()
    return JSONResponse([
        {
            "id": agent.i_agent_id,
            "agent_type_id": agent.i_agent_type_id,
            "agent_type_code": agent_type.c_agent_type_code if agent_type else None,
            "agent_type_name": agent_type.c_agent_type_name if agent_type else None,
            "agent_code": agent.c_agent_code,
            "agent_name": agent.c_contact_name or agent.c_agent_name,
            "company_name": agent.c_company_name,
            "contact_name": agent.c_contact_name or agent.c_agent_name,
            "phone": agent.c_phone,
            "email": agent.c_email,
            "address_line1": agent.c_address_line1,
            "address_line2": agent.c_address_line2,
            "city": agent.c_city,
            "state": agent.c_state,
            "zip": agent.c_zip,
            "country": agent.c_country,
            "commission_rate": float(agent.n_commission_rate) if agent.n_commission_rate is not None else None,
            "memo": agent.c_memo,
            "status": agent.c_status,
            "created_at": agent.dt_created_at.isoformat() if agent.dt_created_at else None,
        }
        for agent, agent_type in items
    ])


@router.post("/platform/agent")
def create_agent(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    contact_name = str(payload.get("contact_name") or payload.get("agent_name") or "").strip()
    if not contact_name:
        raise HTTPException(status_code=400, detail="contact_name is required")

    agent_type_id = _parse_optional_int(payload.get("agent_type_id"), "agent_type_id")
    if agent_type_id is not None:
        agent_type = db.query(AgentType).filter(AgentType.i_agent_type_id == agent_type_id).first()
        if not agent_type:
            raise HTTPException(status_code=400, detail="Invalid agent_type_id")

    item = Agent(
        i_agent_type_id=agent_type_id,
        c_agent_code=f"TMP_{secrets.token_hex(6).upper()}",
        c_agent_name=contact_name,
        c_company_name=str(payload.get("company_name") or "").strip() or None,
        c_contact_name=contact_name,
        c_phone=str(payload.get("phone") or "").strip() or None,
        c_email=str(payload.get("email") or "").strip() or None,
        c_address_line1=str(payload.get("address_line1") or "").strip() or None,
        c_address_line2=str(payload.get("address_line2") or "").strip() or None,
        c_city=str(payload.get("city") or "").strip() or None,
        c_state=str(payload.get("state") or "").strip() or None,
        c_zip=str(payload.get("zip") or "").strip() or None,
        c_country=str(payload.get("country") or "").strip() or None,
        n_commission_rate=_parse_optional_decimal(payload.get("commission_rate"), "commission_rate"),
        c_memo=str(payload.get("memo") or "").strip() or None,
        c_status=_normalize_status(payload.get("status"), {"active", "inactive"}) or "active",
    )
    db.add(item)
    db.flush()
    item.c_agent_code = _generate_agent_code(item.i_agent_id)
    db.commit()
    db.refresh(item)

    return JSONResponse({"id": item.i_agent_id, "agent_code": item.c_agent_code})


@router.put("/platform/agent/{agent_id}")
def update_agent(agent_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(Agent).filter(Agent.i_agent_id == agent_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Agent not found")

    if "agent_type_id" in payload:
        agent_type_id = _parse_optional_int(payload.get("agent_type_id"), "agent_type_id")
        if agent_type_id is not None:
            agent_type = db.query(AgentType).filter(AgentType.i_agent_type_id == agent_type_id).first()
            if not agent_type:
                raise HTTPException(status_code=400, detail="Invalid agent_type_id")
        item.i_agent_type_id = agent_type_id
    if "contact_name" in payload or "agent_name" in payload:
        contact_name = str(payload.get("contact_name") or payload.get("agent_name") or "").strip()
        if not contact_name:
            raise HTTPException(status_code=400, detail="contact_name is required")
        item.c_agent_name = contact_name
        item.c_contact_name = contact_name
    if "company_name" in payload:
        item.c_company_name = str(payload.get("company_name") or "").strip() or None
    if "phone" in payload:
        item.c_phone = str(payload.get("phone") or "").strip() or None
    if "email" in payload:
        item.c_email = str(payload.get("email") or "").strip() or None
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
        item.c_country = str(payload.get("country") or "").strip() or None
    if "commission_rate" in payload:
        item.n_commission_rate = _parse_optional_decimal(payload.get("commission_rate"), "commission_rate")
    if "memo" in payload:
        item.c_memo = str(payload.get("memo") or "").strip() or None
    if "status" in payload:
        item.c_status = _normalize_status(payload.get("status"), {"active", "inactive"}) or "active"

    db.commit()
    return JSONResponse({"success": True})


@router.delete("/platform/agent/{agent_id}")
def delete_agent(agent_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(Agent).filter(Agent.i_agent_id == agent_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Agent not found")

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
    client_ids = [item.i_client_id for item in items if item.i_client_id is not None]
    agent_ids = [item.i_agent_id for item in items if item.i_agent_id is not None]

    store_map = {
        row.i_store_id: row.c_store_name
        for row in db.query(Store).filter(Store.i_store_id.in_(store_ids)).all()
    } if store_ids else {}
    client_map = {
        row.i_client_id: row.c_client_name
        for row in db.query(Client).filter(Client.i_client_id.in_(client_ids)).all()
    } if client_ids else {}
    agent_map = {
        row.i_agent_id: row.c_agent_name
        for row in db.query(Agent).filter(Agent.i_agent_id.in_(agent_ids)).all()
    } if agent_ids else {}

    return JSONResponse([
        {
            "id": item.i_license_id,
            "license_key": item.c_license_key,
            "plan_name": item.c_plan_name,
            "license_type": item.c_license_type,
            "store_id": item.i_store_id,
            "store_name": store_map.get(item.i_store_id),
            "client_id": item.i_client_id,
            "client_name": client_map.get(item.i_client_id),
            "agent_id": item.i_agent_id,
            "agent_name": agent_map.get(item.i_agent_id),
            "max_devices": item.i_max_devices,
            "max_users": item.i_max_users,
            "start_date": item.dt_start.isoformat() if item.dt_start else None,
            "end_date": item.dt_end.isoformat() if item.dt_end else None,
            "monthly_fee": float(item.n_monthly_fee) if item.n_monthly_fee is not None else None,
            "agent_commission": float(item.n_agent_commission) if item.n_agent_commission is not None else None,
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

    client_id = _parse_optional_int(payload.get("client_id"), "client_id")
    if client_id is not None:
        client = db.query(Client).filter(Client.i_client_id == client_id).first()
        if not client:
            raise HTTPException(status_code=400, detail="Invalid client_id")

    agent_id = _parse_optional_int(payload.get("agent_id"), "agent_id")
    if agent_id is not None:
        agent = db.query(Agent).filter(Agent.i_agent_id == agent_id).first()
        if not agent:
            raise HTTPException(status_code=400, detail="Invalid agent_id")

    item = License(
        c_license_key=license_key,
        c_plan_name=str(payload.get("plan_name") or "").strip() or None,
        c_license_type=str(payload.get("license_type") or "").strip() or None,
        i_store_id=store_id,
        i_client_id=client_id,
        i_agent_id=agent_id,
        i_max_devices=_parse_optional_int(payload.get("max_devices"), "max_devices"),
        i_max_users=_parse_optional_int(payload.get("max_users"), "max_users"),
        dt_start=_parse_optional_iso_datetime(payload.get("start_date")),
        dt_end=_parse_optional_iso_datetime(payload.get("end_date")),
        n_monthly_fee=_parse_optional_decimal(payload.get("monthly_fee"), "monthly_fee"),
        n_agent_commission=_parse_optional_decimal(payload.get("agent_commission"), "agent_commission"),
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
    if "client_id" in payload:
        client_id = _parse_optional_int(payload.get("client_id"), "client_id")
        if client_id is not None:
            client = db.query(Client).filter(Client.i_client_id == client_id).first()
            if not client:
                raise HTTPException(status_code=400, detail="Invalid client_id")
        item.i_client_id = client_id
    if "agent_id" in payload:
        agent_id = _parse_optional_int(payload.get("agent_id"), "agent_id")
        if agent_id is not None:
            agent = db.query(Agent).filter(Agent.i_agent_id == agent_id).first()
            if not agent:
                raise HTTPException(status_code=400, detail="Invalid agent_id")
        item.i_agent_id = agent_id
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
    if "agent_commission" in payload:
        item.n_agent_commission = _parse_optional_decimal(payload.get("agent_commission"), "agent_commission")
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
    client_ids = [item.i_client_id for item in items if item.i_client_id is not None]
    client_map = {
        row.i_client_id: row.c_client_name
        for row in db.query(Client).filter(Client.i_client_id.in_(client_ids)).all()
    } if client_ids else {}

    return JSONResponse([
        {
            "id": item.i_payment_id,
            "client_id": item.i_client_id,
            "client_name": client_map.get(item.i_client_id),
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

    client_id = _parse_optional_int(payload.get("client_id"), "client_id")
    if client_id is not None:
        client = db.query(Client).filter(Client.i_client_id == client_id).first()
        if not client:
            raise HTTPException(status_code=400, detail="Invalid client_id")

    item = PaymentMethod(
        i_client_id=client_id,
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

    if "client_id" in payload:
        client_id = _parse_optional_int(payload.get("client_id"), "client_id")
        if client_id is not None:
            client = db.query(Client).filter(Client.i_client_id == client_id).first()
            if not client:
                raise HTTPException(status_code=400, detail="Invalid client_id")
        item.i_client_id = client_id
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
def next_client_store_id(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    next_code, next_seq = _generate_client_store_code(db)
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

    query = db.query(Store)
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


# Users

@router.get("/platform/users")
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(PlatformUser)
    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(PlatformUser.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                PlatformUser.c_email.ilike(term),
                PlatformUser.c_first_name.ilike(term),
                PlatformUser.c_last_name.ilike(term),
            )
        )

    users = query.order_by(PlatformUser.i_platform_user_id).all()
    return JSONResponse([{
        "id": u.i_platform_user_id,
        "email": u.c_email,
        "first_name": u.c_first_name,
        "last_name": u.c_last_name,
        "store_id": u.i_store_id,
        "store_name": "Admin",
        "client_id": None,
        "client_name": None,
        "role": "PlatformAdmin",
        "status": u.c_status,
    } for u in users])


@router.post("/platform/user")
def create_user(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    email = payload.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="email is required")

    # TODO: implement proper password hashing + validation; using a placeholder for now
    pwd = payload.get("password") or "password"

    new_user = PlatformUser(
        c_email=email,
        c_password=pwd,
        c_first_name=payload.get("first_name"),
        c_last_name=payload.get("last_name"),
        i_store_id=ADMIN_STORE_ID,
        c_status=payload.get("status") or "active",
    )

    _stamp_created(new_user, user)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return JSONResponse({"id": new_user.i_platform_user_id})


@router.put("/platform/user/{user_id}")
def update_user(user_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    u = db.query(PlatformUser).filter(PlatformUser.i_platform_user_id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Platform user not found")

    if "email" in payload:
        u.c_email = payload.get("email")
    if "first_name" in payload:
        u.c_first_name = payload.get("first_name")
    if "last_name" in payload:
        u.c_last_name = payload.get("last_name")
    if "status" in payload:
        u.c_status = payload.get("status")

    # Platform DB users always belong to the admin store
    u.i_store_id = ADMIN_STORE_ID

    _stamp_updated(u, user)
    db.commit()
    return JSONResponse({"success": True})


@router.delete("/platform/user/{user_id}")
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    u = db.query(PlatformUser).filter(PlatformUser.i_platform_user_id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Platform user not found")

    u.c_status = "inactive"
    _stamp_updated(u, user)
    db.commit()
    return JSONResponse({"success": True})


# Clients

@router.get("/platform/clients")
def list_clients(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"),
    status: Optional[str] = None,
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(Client)

    normalized_status = _normalize_status(status, {"active", "inactive"})
    if normalized_status:
        query = query.filter(Client.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Client.c_client_code.ilike(term),
                Client.c_account_name.ilike(term),
                Client.c_first_name.ilike(term),
                Client.c_last_name.ilike(term),
                Client.c_email.ilike(term),
                Client.c_phone.ilike(term),
                Client.c_address_line1.ilike(term),
            )
        )

    items = query.order_by(Client.i_account_id).all()

    bt_name_map = {
        row.i_business_type_id: row.c_name
        for row in db.query(BusinessType).all()
    }

    client_business_map: dict[int, str | None] = {}
    client_store_rows = (
        db.query(Store.i_account_id, Store.i_business_type)
        .filter(Store.i_account_id.is_not(None))
        .order_by(Store.i_account_id.asc(), Store.i_store_id.asc())
        .all()
    )
    for account_id, business_type_id in client_store_rows:
        if account_id in client_business_map:
            continue
        if business_type_id is None:
            client_business_map[account_id] = None
            continue
        client_business_map[account_id] = bt_name_map.get(business_type_id)

    agent_name_map = {
        row.i_agent_id: (row.c_contact_name or row.c_agent_name)
        for row in db.query(Agent).all()
    }

    return JSONResponse([
        {
            "id": i.i_account_id,
            "c_client_code": i.c_client_code,
            "client_code": i.c_client_code,
            "client_name": i.c_account_name,
            "business_type": (
                bt_name_map.get(i.i_business_type)
                if getattr(i, "i_business_type", None)
                else client_business_map.get(i.i_account_id)
            ),
            "channel_type": i.c_channel_type,
            "i_agent_id": i.i_agent_id,
            "primary_agent_id": i.i_agent_id,
            "primary_agent_name": agent_name_map.get(i.i_agent_id),
            "first_name": i.c_first_name,
            "last_name": i.c_last_name,
            "email": i.c_email,
            "phone": i.c_phone,
            "address": i.c_address_line1,
            "address_line1": i.c_address_line1,
            "address_line2": i.c_address_line2,
            "city": i.c_city,
            "state": i.c_state,
            "zip": i.c_zip,
            "country": i.c_country,
            "memo": i.c_memo,
            "status": i.c_status,
        }
        for i in items
    ])


@router.post("/platform/client")
def create_client(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    client_name = (payload.get("client_name") or "").strip()
    if not client_name:
        raise HTTPException(status_code=400, detail="client_name is required")

    requested_bt_name = (payload.get("business_type") or "").strip()
    target_bt_id = None
    if requested_bt_name:
        bt_obj = db.query(BusinessType).filter(BusinessType.c_name == requested_bt_name).first()
        if not bt_obj:
            raise HTTPException(status_code=400, detail="invalid business_type")
        target_bt_id = bt_obj.i_business_type_id

    primary_agent_raw = payload.get("primary_agent_id") if "primary_agent_id" in payload else payload.get("i_agent_id")
    primary_agent_id = _parse_optional_int(primary_agent_raw, "primary_agent_id")
    if primary_agent_id is not None:
        primary_agent = db.query(Agent).filter(Agent.i_agent_id == primary_agent_id).first()
        if not primary_agent:
            raise HTTPException(status_code=400, detail="invalid primary_agent_id")

    address_line1 = (
        payload.get("address_line1")
        or payload.get("address")
        or payload.get("c_address_line1")
    )

    for _ in range(5):
        generated_client_code = _generate_next_client_code(db)

        item = Client(
            c_client_code=generated_client_code,
            c_account_name=client_name,
            i_business_type=target_bt_id,
            i_agent_id=primary_agent_id,
            c_channel_type=payload.get("channel_type"),
            c_first_name=payload.get("first_name"),
            c_last_name=payload.get("last_name"),
            c_email=payload.get("email"),
            c_phone=payload.get("phone"),
            c_address_line1=address_line1,
            c_address_line2=payload.get("address_line2"),
            c_city=payload.get("city"),
            c_state=payload.get("state"),
            c_zip=payload.get("zip"),
            c_country=payload.get("country") or "USA",
            c_memo=payload.get("memo"),
            c_status=_normalize_status(payload.get("status"), {"active", "inactive"}) or "active",
        )
        _stamp_created(item, user)
        db.add(item)

        try:
            db.commit()
            db.refresh(item)
            return JSONResponse({"id": item.i_account_id, "c_client_code": item.c_client_code})
        except IntegrityError as exc:
            db.rollback()
            lowered = str(exc).lower()
            if (
                "c_client_code" in lowered
                or "uq_tb_client_c_client_code" in lowered
                or "tb_client_c_client_code_key" in lowered
            ):
                continue
            if "c_client_name" in lowered or "tb_client_c_client_name_key" in lowered:
                raise HTTPException(status_code=409, detail="client_name already exists")
            raise

    raise HTTPException(status_code=409, detail="failed to generate unique client_code")


@router.put("/platform/client/{client_id}")
def update_client(client_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(Client).filter(Client.i_account_id == client_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Client not found")

    if "client_name" in payload:
        client_name = (payload.get("client_name") or "").strip()
        if not client_name:
            raise HTTPException(status_code=400, detail="client_name is required")
        item.c_account_name = client_name
    if "first_name" in payload:
        item.c_first_name = payload.get("first_name")
    if "last_name" in payload:
        item.c_last_name = payload.get("last_name")
    if "email" in payload:
        item.c_email = payload.get("email")
    if "phone" in payload:
        item.c_phone = payload.get("phone")
    if "address_line1" in payload or "address" in payload or "c_address_line1" in payload:
        item.c_address_line1 = (
            payload.get("address_line1")
            or payload.get("address")
            or payload.get("c_address_line1")
        )
    if "address_line2" in payload:
        item.c_address_line2 = payload.get("address_line2")
    if "city" in payload:
        item.c_city = payload.get("city")
    if "state" in payload:
        item.c_state = payload.get("state")
    if "zip" in payload:
        item.c_zip = payload.get("zip")
    if "country" in payload:
        item.c_country = payload.get("country") or "USA"
    if "memo" in payload:
        item.c_memo = payload.get("memo")
    if "status" in payload:
        item.c_status = _normalize_status(payload.get("status"), {"active", "inactive"})
    if "channel_type" in payload:
        item.c_channel_type = payload.get("channel_type") or None
    if "primary_agent_id" in payload or "i_agent_id" in payload:
        primary_agent_raw = payload.get("primary_agent_id") if "primary_agent_id" in payload else payload.get("i_agent_id")
        primary_agent_id = _parse_optional_int(primary_agent_raw, "primary_agent_id")
        if primary_agent_id is not None:
            primary_agent = db.query(Agent).filter(Agent.i_agent_id == primary_agent_id).first()
            if not primary_agent:
                raise HTTPException(status_code=400, detail="invalid primary_agent_id")
        item.i_agent_id = primary_agent_id

    if "business_type" in payload:
        requested_bt_name = (payload.get("business_type") or "").strip()
        target_bt_id = None
        if requested_bt_name:
            bt_obj = db.query(BusinessType).filter(BusinessType.c_name == requested_bt_name).first()
            if not bt_obj:
                raise HTTPException(status_code=400, detail="invalid business_type")
            target_bt_id = bt_obj.i_business_type_id

        item.i_business_type = target_bt_id

        linked_stores = db.query(Store).filter(Store.i_account_id == item.i_account_id).all()
        for linked_store in linked_stores:
            linked_store.i_business_type = target_bt_id
            _stamp_updated(linked_store, user)

    _stamp_updated(item, user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="client_name already exists")

    return JSONResponse({"success": True})


@router.delete("/platform/client/{client_id}")
def delete_client(client_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(Client).filter(Client.i_account_id == client_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Client not found")

    item.c_status = "inactive"
    _stamp_updated(item, user)
    db.commit()
    return JSONResponse({"success": True})


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

    query = db.query(SessionTbl, PlatformUser).outerjoin(
        PlatformUser, SessionTbl.i_platform_user_id == PlatformUser.i_platform_user_id
    )

    normalized_status = _normalize_status(status, {"active", "terminated"})
    if normalized_status:
        query = query.filter(SessionTbl.c_status == normalized_status)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                PlatformUser.c_email.ilike(term),
                PlatformUser.c_first_name.ilike(term),
                PlatformUser.c_last_name.ilike(term),
                SessionTbl.c_jwt_token.ilike(term),
            )
        )

    rows = query.order_by(SessionTbl.i_session_id.desc()).all()
    results = []
    for sess, u in rows:
        results.append(
            {
                "id": sess.i_session_id,
                "user_id": sess.i_platform_user_id,
                "user_email": u.c_email if u else None,
                "user_name": f"{(u.c_first_name or '').strip()} {(u.c_last_name or '').strip()}".strip() if u else None,
                "store_id": ADMIN_STORE_ID,
                "store_name": "Admin",
                "store_code": "Admin",
                "client_id": None,
                "client_name": None,
                "status": sess.c_status,
                "login_at": sess.dt_login.isoformat() if sess.dt_login else None,
                "last_active_at": sess.dt_last_active.isoformat() if sess.dt_last_active else None,
                "terminated_at": sess.dt_terminated.isoformat() if sess.dt_terminated else None,
            }
        )

    return JSONResponse(results)


@router.put("/platform/session/{session_id}")
def update_session(session_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(SessionTbl).filter(SessionTbl.i_session_id == session_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Session not found")

    if "status" in payload:
        normalized_status = _normalize_status(payload.get("status"), {"active", "terminated"})
        item.c_status = normalized_status
        if normalized_status == "terminated":
            item.dt_terminated = datetime.now()
        elif normalized_status == "active":
            item.dt_terminated = None

    if "last_active_at" in payload:
        item.dt_last_active = _parse_optional_iso_datetime(payload.get("last_active_at"))

    _stamp_updated(item, user)
    db.commit()
    return JSONResponse({"success": True})


@router.delete("/platform/session/{session_id}")
def delete_session(session_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    item = db.query(SessionTbl).filter(SessionTbl.i_session_id == session_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Session not found")

    item.c_status = "terminated"
    item.dt_terminated = datetime.now()
    _stamp_updated(item, user)
    db.commit()
    return JSONResponse({"success": True})
