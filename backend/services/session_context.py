"""Organization identity comes from the authenticated login scope, not admin Store IDs."""
from fastapi import HTTPException

from backend.models_admin.merchant import Merchant
from backend.models_admin.agency import Agency
from backend.models_admin.store import Store
from backend.models_admin.system_settings import SystemSettings
from backend.models_admin.platform_user import AGENCY_ADMIN_ROLE_NAME
from backend.models_admin.role import Role


def login_identity(db, user, claims):
    context = str(claims.get("scope_type") or "").upper()
    identity = {"c_login_context": context, "i_store_id": None, "i_merchant_id": None, "i_agency_id": None}
    if context == "PLATFORM":
        if user.i_agency_id is not None:
            raise HTTPException(401, "Agency users cannot use the Platform login scope.")
        return identity
    if context == "STORE":
        store = db.get(Store, user.i_store_id)
        if store:
            identity.update(i_store_id=store.i_store_id, i_merchant_id=store.i_merchant_id)
            return identity
    if context == "MERCHANT":
        merchant = db.get(Merchant, claims.get("merchant_id")) if claims.get("merchant_id") else None
        if merchant:
            identity["i_merchant_id"] = merchant.i_merchant_id
            return identity
    if context == "AGENCY":
        agency = db.get(Agency, claims.get("agency_id")) if claims.get("agency_id") else None
        role = db.get(Role, user.i_role_id)
        if (agency and agency.c_status == "active" and user.i_agency_id == agency.i_agency_id
                and role and role.c_name == AGENCY_ADMIN_ROLE_NAME):
            identity["i_agency_id"] = agency.i_agency_id
            return identity
    raise HTTPException(status_code=401, detail="A valid authenticated login context is required.")


def session_organization(db, session):
    context = session.c_login_context
    if context == "PLATFORM":
        settings = db.get(SystemSettings, 1)
        name = ((settings.c_company_name or "").strip() or (settings.c_display_name or "").strip()) if settings else ""
        return name or "AWSUM PLATFORM", "Platform Admin"
    if context == "AGENCY":
        agency = db.get(Agency, session.i_agency_id) if session.i_agency_id else None
        return agency.c_agency_name if agency else "Unknown agency", "Agency"
    if context in {"MERCHANT", "STORE"}:
        merchant = db.get(Merchant, session.i_merchant_id) if session.i_merchant_id else None
        name = merchant.c_merchant_name if merchant else "Unknown merchant"
        if context == "MERCHANT":
            return name, "Merchant"
        store = db.get(Store, session.i_store_id) if session.i_store_id else None
        return name, store.c_store_name if store else "Unknown store"
    return "Unknown organization", "Unknown context"
