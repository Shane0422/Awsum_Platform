"""Shared tenant navigation and API permission policy; scope comes from the session."""
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.models_admin.role import Role
from backend.models_admin.platform_user import MERCHANT_ADMIN_ROLE_NAME
from backend.services.agency_permissions import PortalPermission


# Merchant / Store Master Group: the same items in the same order as the Platform Master Group menu
# (templates/platform/platform_base.html; guarded by tools.test_merchant_common_maintenance).
# available=False: not built for the Merchant scope yet, shown as "Soon" and never linked (its API stays refused).
# MD Master (STEP 2): the Merchant manages its own MD; a Store login views its Merchant's MD only.
MASTER_SLUGS = (("common-maintenance", "Common Maintenance", True), ("tax", "Tax", True),
                ("category-1", "Category I", True), ("category-2", "Category II", True),
                ("category-3", "Category III", True), ("md-master", "MD Master", True),
                ("location-master", "Location Master", True))
# Business > Organizations (Platform: Agencies, Merchants, Stores). Merchant Store Management: the Merchant login
# manages its own Stores (routers.merchant_store). A Store login never has Store Management (no Business menu).
BUSINESS_SLUGS = (("stores", "Stores", True),)
BUSINESS_URLS = {"stores": "/merchant/business/stores"}
# Employee Group (Merchant login, same visibility as Business): Employee Master is the Merchant-wide Employee screen
# (routers.master_group, entity employee; /merchant/business/employees stays as a compatible URL of the same page).
# The rest are not built yet: available=False -> "Soon", never linked.
EMPLOYEE_SLUGS = (("employee-master", "Employee Master", True), ("time-card", "Time Card", False),
                  ("scheduling", "Scheduling", False), ("payroll", "Payroll", False),
                  ("employee-reports", "Employee Reports", False))
EMPLOYEE_URLS = {"employee-master": "/merchant/employee-group/employee-master"}
TENANT_ROLES = {
    "MERCHANT": {MERCHANT_ADMIN_ROLE_NAME},
    "STORE": {"Admin", "CustomerAdmin", "StoreAdmin"},
}
MASTER_PERMISSION = PortalPermission(create=True, update=True, delete=True)
# Merchant Store Management: view its Stores and edit the allowed operating fields. Store registration, deletion,
# deactivation and Merchant / Agency assignment are Platform Admin only.
STORE_MANAGEMENT_PERMISSION = PortalPermission(read=True, update=True)
# Store Status rule (separate from Feature Entitlement, User Permission and Device License).
STORE_INACTIVE_READ_ONLY = "This Store is Inactive: view only. Platform Admin can edit and activate it."
READ_METHODS = frozenset({"GET", "HEAD"})


@dataclass(frozen=True)
class AppScope:
    context: str
    merchant_id: int
    role_name: str
    permitted: bool
    # Use Location Management in effect for this login (STEP 3-3C, Common Feature Resolver): Merchant login = its
    # Merchant level (Business Option contract + Merchant Feature Entitlement); Store login = the full policy for its
    # Store (+ Store Option + Store Status). False when the saved layers cannot be read (fail-closed).
    location_management: bool = True
    # Saved Merchant feature layers of this login (services.features.load_feature_snapshot), read once per request.
    # None = they could not be read: every feature check of this request is OFF (fail-closed), nothing is written.
    feature_snapshot: object = None

    def require(self, action: str, permission: PortalPermission = MASTER_PERMISSION,
                label: str = "Master Group") -> None:
        if not self.permitted or action not in {"read", "create", "update", "delete", "export"}:
            raise HTTPException(403, f"{label} permission denied.")
        if not getattr(permission, "read" if action == "export" else action):
            raise HTTPException(403, f"{label} permission denied.")

    # ---- Store Status (common for every Store-scoped API and screen of a Merchant / Store login) ----
    def store_read_only(self, store) -> bool:
        """A Merchant views an Inactive Store of its own (basic info, Status, data) but cannot change it.

        Platform Admin (no AppScope) is never limited by this rule. A Store login needs an active Store to sign in
        at all (resolve_app_scope), so its own Store is always active here.
        """
        return self.context == "MERCHANT" and store is not None and (store.c_status or "").lower() != "active"

    def require_store_access(self, store, method: str) -> None:
        """Backend enforcement: reads are allowed; any other request on a read-only Store is refused (403)."""
        if method.upper() not in READ_METHODS and self.store_read_only(store):
            raise HTTPException(403, STORE_INACTIVE_READ_ONLY)

    # ---- Features (common resolver, fail-closed). STEP 3-3C: enforced through feature_on / location_management. ----
    def feature_states(self, db, store=None) -> dict:
        """Merchant level (store=None) or Store level FeatureState per key for this login's Merchant.

        Raises features.FeatureCheckUnavailable when the saved layers cannot be read (never "everything allowed").
        """
        from backend.services.features import load_feature_snapshot, resolve_features

        if store is not None and store.i_merchant_id != self.merchant_id:
            raise HTTPException(403, "This Store does not belong to your Merchant.")
        return resolve_features(store, load_feature_snapshot(db, store=store, merchant_id=self.merchant_id))

    def feature_on(self, key: str, store=None) -> bool:
        """STEP 3-3C enforcement: effective status of one feature for this login (store=None = Merchant level, or a
        Store of this Merchant: its Store Options and Status too). Uses the snapshot read for this request; the Admin
        Store's own snapshot is exempt (central rule). Fail-closed: no snapshot = OFF."""
        from backend.services.features import FeatureSnapshot, entitlement_exempt, feature_enabled

        if self.feature_snapshot is None:
            return False
        snapshot = self.feature_snapshot
        if store is not None and entitlement_exempt(store) and not snapshot.exempt:
            snapshot = FeatureSnapshot(merchant_id=self.merchant_id, business={}, choices={}, exempt=True)
        return feature_enabled(key, store, snapshot)

    def store_status_info(self, store) -> dict:
        read_only = self.store_read_only(store)
        return {"read_only": read_only, "read_only_message": STORE_INACTIVE_READ_ONLY if read_only else None}

    def navigation(self) -> dict:
        # Store Management is a Merchant login menu only (Store login: no Business > Organizations > Stores).
        business_items = [{"label": label, "slug": slug, "available": available, "url": BUSINESS_URLS.get(slug)}
                          for slug, label, available in BUSINESS_SLUGS]             if self.permitted and self.context == "MERCHANT" else []
        employee_items = [{"label": label, "slug": slug, "available": available, "url": EMPLOYEE_URLS.get(slug)}
                          for slug, label, available in EMPLOYEE_SLUGS]             if self.permitted and self.context == "MERCHANT" else []
        return {
            "scope": self.context, "dashboard_url": "/merchant/dashboard/standard",
            "settings_url": "/auth/change-password",
            "master_items": [
                {"label": label, "slug": slug, "url": f"/{self.context.lower()}/master-group/{slug}",
                 "available": available}
                for slug, label, available in MASTER_SLUGS
                if slug != "location-master" or self.location_management
            ] if self.permitted else [],
            "business_items": business_items,
            "business": bool(business_items),
            "employee_items": employee_items, "employee": bool(employee_items),
            "reports": self.permitted,
            "api_base": f"/{self.context.lower()}/api/master-group",
        }


def resolve_app_scope(request, db: Session, user) -> AppScope:
    from backend.services.web_sessions import decode_web_token
    claims = decode_web_token(request.cookies.get("access_token") or "")
    context = str(claims.get("scope_type") or "").upper()
    role = db.get(Role, user.i_role_id)
    store = None
    if context == "MERCHANT":
        merchant_id = user.i_merchant_id
    elif context == "STORE":
        store = db.get(Store, user.i_store_id)
        merchant_id = store.i_merchant_id if store and store.c_status == "active" else None
    else:
        raise HTTPException(403, "Merchant or Store login required.")
    merchant = db.get(Merchant, merchant_id) if merchant_id is not None else None
    if merchant is None or merchant.c_status != "active":
        raise HTTPException(403, "An active Merchant scope is required.")
    # STEP 3-3C: the saved feature layers, read once (Common Feature Resolver; the Admin Store is exempt there).
    from backend.services.features import FeatureCheckUnavailable, feature_enabled, load_feature_snapshot
    try:
        snapshot = load_feature_snapshot(db, store=store, merchant_id=merchant_id)
    except FeatureCheckUnavailable:
        snapshot = None  # fail-closed: every feature of this request is OFF; the login itself still works
    location_management = snapshot is not None and feature_enabled("location_management", store, snapshot)
    return AppScope(context, merchant_id, role.c_name if role else "",
                    bool(role and role.c_status == "active" and role.c_name in TENANT_ROLES[context]),
                    location_management, snapshot)
