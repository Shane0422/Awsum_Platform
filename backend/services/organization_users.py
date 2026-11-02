"""Owner identity shared by registration and existing web-user authentication."""
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.models_admin.agency import Agency
from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.models_admin.platform_user import (
    AGENCY_ADMIN_ROLE_NAME, MERCHANT_ADMIN_ROLE_NAME, STORE_ADMIN_ROLE_NAME, PlatformUser,
)
from backend.models_admin.role import Role


REGISTRATION_OWNERS = {
    "AGENCY": (Agency, "i_agency_id", "c_agency_code", "c_agency_name", AGENCY_ADMIN_ROLE_NAME),
    "MERCHANT": (Merchant, "i_merchant_id", "c_merchant_code", "c_merchant_name", MERCHANT_ADMIN_ROLE_NAME),
    "STORE": (Store, "i_store_id", "c_store_code", "c_store_name", STORE_ADMIN_ROLE_NAME),
}


def registration_owner_config(owner_type: str):
    config = REGISTRATION_OWNERS.get(owner_type)
    if config is None:
        raise HTTPException(400, "A valid registration owner type is required.")
    return config


def get_registration_owner(db: Session, owner_type: str, owner_id: int):
    model, *_ = registration_owner_config(owner_type)
    if owner_type == "STORE" and owner_id == ADMIN_STORE_ID:
        raise HTTPException(403, "The technical Admin Store is not a registration owner.")
    owner = db.get(model, owner_id)
    if owner is None:
        raise HTTPException(404, f"{owner_type.title()} not found.")
    return owner


def organization_user_query(db: Session, owner_type: str, owner_id: int):
    _, field, *_ = registration_owner_config(owner_type)
    query = db.query(PlatformUser).filter(getattr(PlatformUser, field) == owner_id)
    if owner_type != "AGENCY":
        query = query.filter(PlatformUser.i_agency_id.is_(None))
    if owner_type != "MERCHANT":
        query = query.filter(PlatformUser.i_merchant_id.is_(None))
    if owner_type != "STORE":
        query = query.filter(PlatformUser.i_store_id == ADMIN_STORE_ID)
    return query


def organization_admin_query(db: Session, owner_type: str, owner_id: int):
    *_, role_name = registration_owner_config(owner_type)
    role_names = {role_name, "CustomerAdmin", "StoreAdmin"} if owner_type == "STORE" else {role_name}
    return organization_user_query(db, owner_type, owner_id).join(
        Role, Role.i_role_id == PlatformUser.i_role_id,
    ).filter(Role.c_name.in_(role_names), Role.c_status == "active")


def initial_phone_password(phone: object) -> str | None:
    digits = re.sub(r"[^0-9]", "", str(phone or ""))
    return f"Awsum@{digits[-4:]}" if len(digits) >= 4 else None
