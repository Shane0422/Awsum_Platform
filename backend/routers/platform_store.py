from datetime import datetime
from pathlib import Path
import re

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
ALLOWED_DASHBOARD_TYPES = {"PLATFORM", "STANDARD", "RESTAURANT", "DELI", "TUXEDO_RENTAL"}
BASE_DIR = Path(__file__).resolve().parents[2]
BRANDS_STATIC_ROOT = BASE_DIR / "static" / "images" / "brands"

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
    if client_id is not None:
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

    if "client_id" in payload:
        client_id_raw = payload.get("client_id")
        client_id = _parse_optional_int(client_id_raw, "client_id")
        if client_id is None:
            store.i_account_id = None
        else:
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
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(Role)
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
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(BusinessType)
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
):
    user = get_current_user(request, db)
    require_platform_admin(user)

    query = db.query(PlatformUser)
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
    if "status" in payload:
        item.c_status = _normalize_status(payload.get("status"), {"active", "inactive"})
    if "channel_type" in payload:
        item.c_channel_type = payload.get("channel_type") or None

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
