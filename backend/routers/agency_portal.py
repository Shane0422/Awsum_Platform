"""Agency-scoped portal using existing Platform users, sessions, and records."""
from datetime import datetime
import csv
import io
import logging
from typing import Annotated, Literal

import bcrypt
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.config.templates import templates, get_brand_context
from backend.database.pg_platform import ADMIN_STORE_ID, PlatformSessionLocal
from backend.models_admin.merchant import Merchant
from backend.models_admin.agency import Agency
from backend.models_admin.device import Device
from backend.models_admin.device_type import DeviceType
from backend.models_admin.platform_user import AGENCY_ADMIN_ROLE_NAME, PlatformUser
from backend.models_admin.role import Role
from backend.models_admin.session import SessionTbl
from backend.models_admin.store import Store
from backend.routers.platform_store import (
    _platform_user_email, _platform_user_name, _require_unique_platform_user_email,
    _reset_user_password, _validate_platform_user_password,
)
from backend.services import web_sessions
from backend.services.agency_permissions import AgencyPortalPermissions
from backend.services.images import image_mutation_form, image_url
from backend.services.image_service import commit_entity_image, image_operation_lock
from backend.services.resource_storage import AwsumStorageService

logger = logging.getLogger(__name__)


def get_db():
    with PlatformSessionLocal() as db:
        yield db


def agency_user(request: Request, db: Session = Depends(get_db)) -> PlatformUser:
    user = web_sessions.authenticate(request, db)
    AgencyPortalPermissions(user)
    return user


def authorize_portal(request: Request, user: PlatformUser = Depends(agency_user)):
    AgencyPortalPermissions(user).authorize_request(request.url.path, request.method)


router = APIRouter(prefix="/agency", tags=["AgencyPortal"], dependencies=[Depends(authorize_portal)])


def merchants(db, user):
    return AgencyPortalPermissions(user).query(db, "merchants")


def stores(db, user):
    return AgencyPortalPermissions(user).query(db, "stores")


def users(db, user):
    return AgencyPortalPermissions(user).query(db, "users")


def devices(db, user):
    return AgencyPortalPermissions(user).query(db, "devices")


def scoped_record(query, column, record_id):
    record = query.filter(column == record_id).first()
    if record is None:
        raise HTTPException(404, "Record not found in your Agency.")
    return record


def commit(db):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning("Agency update conflicts with an existing record")
        raise HTTPException(409, "The update conflicts with an existing record.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Unable to save Agency portal changes")
        raise HTTPException(500, "Unable to save changes. Please try again.") from exc


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(max_length=254)
    phone: str = Field(default="", max_length=80)


class UserEdit(Profile):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    first_name: str = Field(default="", max_length=255)
    last_name: str = Field(default="", max_length=255)
    status: Literal["active", "inactive"] = "active"
    must_change_password: bool | None = None


class UserCreate(UserEdit):
    password: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1)]
    confirm_password: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1)]


def fields(kind):
    result = [
        {"name": "name", "label": "Name", "type": "text", "required": True, "max": 255},
        {"name": "email", "label": "Email", "type": "email", "required": kind == "users", "max": 254},
        {"name": "phone", "label": "Phone", "type": "tel", "max": 80},
    ]
    if kind == "users":
        result.append({"name": "status", "label": "Status", "options": ["active", "inactive"]})
    return result


def serialize(record, kind):
    if kind == "merchants":
        return {"id": record.i_merchant_id, "code": record.c_merchant_code, "name": record.c_merchant_name,
                "email": record.c_email or "", "phone": record.c_phone or "", "status": record.c_status}
    if kind == "stores":
        return {"id": record.i_store_id, "code": record.c_store_code, "name": record.c_store_name,
                "email": record.c_email or "", "phone": record.c_phone or "", "status": record.c_status}
    if kind == "users":
        return {"id": record.i_platform_user_id, "code": str(record.i_platform_user_id),
                "name": f"{record.c_first_name or ''} {record.c_last_name or ''}".strip(),
                "first_name": record.c_first_name or "", "last_name": record.c_last_name or "",
                "email": record.c_email, "phone": record.c_phone or "", "status": record.c_status}
    return {"id": record.i_device_id, "code": record.c_device_name or "Unnamed",
            "name": record.c_device_type or "", "email": record.c_os or "",
            "phone": record.dt_last_seen.isoformat(sep=" ", timespec="minutes") if record.dt_last_seen else "Never",
            "status": record.c_status}


def context(db, user, section, **extra):
    agency = db.get(Agency, user.i_agency_id)
    return {
        **get_brand_context(context_type="agency", merchant_code=agency.c_agency_code),
        "user": user, "role": AGENCY_ADMIN_ROLE_NAME, "agency": agency,
        "active_page": "agency-portal", "section": section,
        "permissions": AgencyPortalPermissions(user),
        "title": "",
        **extra,
    }


def render(request, db, user, section, **extra):
    return templates.TemplateResponse(
        request, "agency/portal.html", context(db, user, section, **extra),
        headers={"Cache-Control": "no-store"},
    )


def summary(db, user):
    return {"Merchants": merchants(db, user).count(), "Stores": stores(db, user).count(),
            "Devices": devices(db, user).count(), "Users": users(db, user).count()}


@router.get("", dependencies=[Depends(agency_user)])
def portal_home():
    return RedirectResponse("/agency/dashboard", status_code=303)


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user)):
    return render(request, db, user, "dashboard", title="Agency Dashboard", counts=summary(db, user))


@router.get("/reports")
def reports(request: Request, db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user)):
    return render(request, db, user, "reports", counts=summary(db, user))


@router.get("/reports/export")
def export_report(db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user)):
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(["Metric", "Count"])
    for label, count in summary(db, user).items():
        if label != "Users":
            writer.writerow([label, count])
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="agency-overview.csv"',
                             "Cache-Control": "no-store"})


@router.get("/settings")
def settings(request: Request, db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user)):
    return render(request, db, user, "settings", profile=serialize(user, "users"), fields=fields("settings"))


@router.put("/settings")
def save_settings(payload: Profile, db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user)):
    # Login email is managed through Users; changing a display profile never changes identity.
    if payload.email != user.c_email:
        raise HTTPException(400, "Login Email cannot be changed from Settings.")
    user.c_first_name, user.c_last_name = _platform_user_name(payload.model_dump())
    user.c_phone = payload.phone
    user.dt_updated = datetime.now()
    commit(db)
    return {"success": True}


@router.get("/stores/{store_id}/devices")
def store_devices(store_id: int, request: Request, q: str = Query("", max_length=200),
                  status: str = "", page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                  db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user)):
    store = scoped_record(stores(db, user), Store.i_store_id, store_id)
    query = devices(db, user).outerjoin(
        DeviceType, DeviceType.i_device_type_id == Device.i_device_type_id,
    ).filter(Device.i_store_id == store_id)
    if q:
        query = query.filter(or_(Device.c_device_name.ilike(f"%{q}%"), Device.c_device_type.ilike(f"%{q}%"),
                                DeviceType.c_device_type_name.ilike(f"%{q}%")))
    if status:
        query = query.filter(Device.c_status == status)
    total = query.count()
    records = query.add_columns(DeviceType.c_device_type_name).order_by(
        Device.i_device_id,
    ).offset((page - 1) * size).limit(size).all()
    return render(request, db, user, "stores", title=f"{store.c_store_name} - Devices",
                  kind="devices", rows=[{**serialize(item, "devices"), "name": type_name or item.c_device_type or ""}
                                        for item, type_name in records],
                  fields=[], q=q, status=status, page=page, size=size, total=total,
                  pages=max(1, (total + size - 1) // size), list_path=f"/agency/stores/{store_id}/devices")


@router.get("/{kind}")
def list_records(kind: Literal["merchants", "stores", "users"], request: Request,
                 q: str = Query("", max_length=200), status: str = "",
                 page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                 db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user)):
    query, model, pk, name = {
        "merchants": (merchants(db, user), Merchant, Merchant.i_merchant_id, Merchant.c_merchant_name),
        "stores": (stores(db, user), Store, Store.i_store_id, Store.c_store_name),
        "users": (users(db, user), PlatformUser, PlatformUser.i_platform_user_id, PlatformUser.c_first_name),
    }[kind]
    if q:
        extra_search = {
            "merchants": Merchant.c_merchant_code, "stores": Store.c_store_code,
            "users": PlatformUser.c_last_name,
        }[kind]
        query = query.filter(or_(name.ilike(f"%{q}%"), model.c_email.ilike(f"%{q}%"),
                                model.c_phone.ilike(f"%{q}%"), extra_search.ilike(f"%{q}%")))
    if status:
        query = query.filter(model.c_status == status)
    total = query.count()
    records = query.order_by(pk).offset((page - 1) * size).limit(size).all()
    if kind == "users" and "application/json" in request.headers.get("accept", ""):
        return JSONResponse({
            "users": [{
                **serialize(record, "users"), "role": AGENCY_ADMIN_ROLE_NAME,
                "must_change_password": bool(record.i_must_change_password),
                "last_login": record.dt_last_login.isoformat() if record.dt_last_login else None,
                "created_at": record.dt_created.isoformat() if record.dt_created else None,
                "image_path": record.c_image_path, "image_url": image_url(record.c_image_path),
            } for record in records],
            "pages": max(1, (total + size - 1) // size),
        }, headers={"Cache-Control": "no-store"})
    return render(request, db, user, kind, title=kind.title(), kind=kind,
                  rows=[serialize(item, kind) for item in records], fields=fields(kind),
                  q=q, status=status, page=page, size=size, total=total,
                  pages=max(1, (total + size - 1) // size), list_path=f"/agency/{kind}")


@router.api_route("/merchants/{record_id}", methods=["POST", "PUT", "PATCH", "DELETE"])
@router.api_route("/stores/{record_id}", methods=["POST", "PUT", "PATCH", "DELETE"])
def unsupported_record_mutation(record_id: int):
    # The common router dependency denies these writes before this handler runs.
    raise HTTPException(405, f"Mutation of record {record_id} is not supported.")


@router.post("/users")
def create_user(payload: UserCreate, db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user)):
    return _create_user(payload, db, user)


def _create_user(payload: UserCreate, db: Session, user: PlatformUser, *, image_data: bytes | None = None):
    db.query(Agency).filter(Agency.i_agency_id == user.i_agency_id).with_for_update().one()
    role = db.query(Role).filter(Role.c_name == AGENCY_ADMIN_ROLE_NAME, Role.c_status == "active").first()
    if role is None:
        raise HTTPException(409, "An active Agency Admin role is required.")
    email = _platform_user_email(payload.email)
    _require_unique_platform_user_email(db, email, agency_id=user.i_agency_id)
    password = _validate_platform_user_password(payload.password, payload.confirm_password)
    first, last = _platform_user_name(payload.model_dump(exclude_none=True))
    record = PlatformUser(
        i_store_id=ADMIN_STORE_ID, i_agency_id=user.i_agency_id, i_role_id=role.i_role_id,
        c_email=email, c_first_name=first, c_last_name=last, c_phone=payload.phone,
        c_password=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        c_status=payload.status, i_must_change_password=int(payload.must_change_password or False),
    )
    db.add(record)
    return save_user_image(db, user, record, image_data=image_data)


@router.put("/users/{record_id}")
def update_user(record_id: int, payload: UserEdit, db: Session = Depends(get_db),
                user: PlatformUser = Depends(agency_user)):
    return _update_user(record_id, payload, db, user)


def _update_user(record_id: int, payload: UserEdit, db: Session, user: PlatformUser,
                 *, image_data: bytes | None = None, remove_image: bool = False):
    db.query(Agency).filter(Agency.i_agency_id == user.i_agency_id).with_for_update().one()
    record = scoped_record(users(db, user).with_for_update(), PlatformUser.i_platform_user_id, record_id)
    if payload.status == "inactive":
        AgencyPortalPermissions(user).require_deactivation(db, record)
    email = _platform_user_email(payload.email)
    _require_unique_platform_user_email(db, email, agency_id=user.i_agency_id, exclude_user_id=record_id)
    record.c_first_name, record.c_last_name = _platform_user_name(payload.model_dump(exclude_none=True))
    record.c_email, record.c_phone, record.c_status = email, payload.phone, payload.status
    if payload.must_change_password is not None:
        record.i_must_change_password = int(payload.must_change_password)
    record.dt_updated = datetime.now()
    if payload.status == "inactive":
        terminate_user_sessions(db, user, record_id)
    return save_user_image(db, user, record, image_data=image_data, remove_image=remove_image)


def save_user_image(db, user, record, *, image_data=None, remove_image=False):
    owner = AwsumStorageService(db).resolve_owner("AGENCY", user.i_agency_id)
    try:
        db.flush()
        warning = commit_entity_image(db, record, owner, record.i_platform_user_id,
                                      category="users", image_data=image_data, remove_image=remove_image)
    except IntegrityError as exc:
        db.rollback()
        logger.warning("Agency User update conflicts with an existing record")
        raise HTTPException(409, "The update conflicts with an existing record.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Unable to save Agency User changes")
        raise HTTPException(500, "Unable to save changes. Please try again.") from exc
    return {"success": True, "id": record.i_platform_user_id,
            "image_path": record.c_image_path, "image_url": image_url(record.c_image_path),
            "image_warning": warning}


@router.post("/users/with-image")
@router.put("/users/{record_id}/with-image")
def save_user_with_image(
    request: Request, payload: str = Form(...), image: UploadFile | None = File(None),
    remove_image: bool = Form(False), record_id: int | None = None,
    db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user),
):
    from backend.routers.system_settings import _validate_origin
    _validate_origin(request)
    if request.method == "POST" and record_id is not None:
        raise HTTPException(422, "User ID cannot be supplied when creating a User.")
    # Resolve membership before reading uploads; the browser never chooses the owner.
    if record_id is not None:
        scoped_record(users(db, user), PlatformUser.i_platform_user_id, record_id)
    values, data = image_mutation_form(payload, remove_image, image)
    try:
        model = UserCreate.model_validate(values) if record_id is None else UserEdit.model_validate(values)
    except ValidationError as exc:
        raise RequestValidationError([
            {**error, "loc": ("body", *error["loc"])} for error in exc.errors()
        ]) from exc
    owner = AwsumStorageService(db).resolve_owner("AGENCY", user.i_agency_id)
    with image_operation_lock(owner, "users"):
        if isinstance(model, UserCreate):
            return _create_user(model, db, user, image_data=data)
        return _update_user(record_id, model, db, user, image_data=data, remove_image=remove_image)


@router.post("/users/{record_id}/reset-password")
def reset_user_password(record_id: int, payload: dict, db: Session = Depends(get_db),
                        user: PlatformUser = Depends(agency_user)):
    record = scoped_record(users(db, user).with_for_update(), PlatformUser.i_platform_user_id, record_id)
    return _reset_user_password(record, payload, user, db)


def terminate_user_sessions(db, user, record_id):
    for active in db.query(SessionTbl).filter(
        SessionTbl.i_platform_user_id == record_id, SessionTbl.c_status == "ACTIVE",
    ).with_for_update().all():
        web_sessions._mark_session_terminated(active, user.i_platform_user_id, web_sessions.utcnow())


@router.delete("/users/{record_id}")
def delete_user(record_id: int, db: Session = Depends(get_db), user: PlatformUser = Depends(agency_user)):
    """Match existing Platform User deletion: deactivate, preserving identity/history."""
    db.query(Agency).filter(Agency.i_agency_id == user.i_agency_id).with_for_update().one()
    record = scoped_record(users(db, user).with_for_update(), PlatformUser.i_platform_user_id, record_id)
    AgencyPortalPermissions(user).require_deactivation(db, record)
    record.c_status = "inactive"
    record.dt_updated = datetime.now()
    terminate_user_sessions(db, user, record_id)
    commit(db)
    return {"success": True, "status": record.c_status}
