"""Shared tenant navigation and API permission policy; scope comes from the session."""
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.models_admin.role import Role
from backend.models_admin.platform_user import MERCHANT_ADMIN_ROLE_NAME
from backend.services.agency_permissions import PortalPermission


MASTER_SLUGS = (("tax", "Tax"), ("category-1", "Category I"),
                ("category-2", "Category II"), ("category-3", "Category III"),
                ("location-master", "Location Master"))
TENANT_ROLES = {
    "MERCHANT": {MERCHANT_ADMIN_ROLE_NAME},
    "STORE": {"Admin", "CustomerAdmin", "StoreAdmin"},
}
MASTER_PERMISSION = PortalPermission(create=True, update=True, delete=True)


@dataclass(frozen=True)
class AppScope:
    context: str
    merchant_id: int
    role_name: str
    permitted: bool
    # Store login only: its Business Options > Use Location Management (Merchant always manages its Stores' locations).
    location_management: bool = True

    def require(self, action: str) -> None:
        if not self.permitted or action not in {"read", "create", "update", "delete", "export"}:
            raise HTTPException(403, "Master Group permission denied.")
        if not getattr(MASTER_PERMISSION, "read" if action == "export" else action):
            raise HTTPException(403, "Master Group permission denied.")

    def navigation(self) -> dict:
        return {
            "scope": self.context, "dashboard_url": "/merchant/dashboard/standard",
            "settings_url": "/auth/change-password",
            "master_items": [
                {"label": label, "slug": slug, "url": f"/{self.context.lower()}/master-group/{slug}"}
                for slug, label in MASTER_SLUGS
                if slug != "location-master" or self.location_management
            ] if self.permitted else [],
            "business": self.permitted, "reports": self.permitted,
            "api_base": f"/{self.context.lower()}/api/master-group",
        }


def resolve_app_scope(request, db: Session, user) -> AppScope:
    from backend.services.web_sessions import decode_web_token
    claims = decode_web_token(request.cookies.get("access_token") or "")
    context = str(claims.get("scope_type") or "").upper()
    role = db.get(Role, user.i_role_id)
    if context == "MERCHANT":
        merchant_id = user.i_merchant_id
    elif context == "STORE":
        store = db.get(Store, user.i_store_id)
        merchant_id = store.i_merchant_id if store and store.c_status == "active" else None
        from backend.services.store_locations import location_management_enabled
        location_management = location_management_enabled(store)
    else:
        raise HTTPException(403, "Merchant or Store login required.")
    merchant = db.get(Merchant, merchant_id) if merchant_id is not None else None
    if merchant is None or merchant.c_status != "active":
        raise HTTPException(403, "An active Merchant scope is required.")
    return AppScope(context, merchant_id, role.c_name if role else "",
                    bool(role and role.c_status == "active" and role.c_name in TENANT_ROLES[context]),
                    location_management if context == "STORE" else True)
