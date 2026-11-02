"""Shared Agency portal capabilities and record scope for UI and API."""
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.models_admin.merchant import Merchant
from backend.models_admin.device import Device
from backend.models_admin.platform_user import AGENCY_ADMIN_ROLE_NAME, PlatformUser
from backend.models_admin.role import Role
from backend.models_admin.store import Store


@dataclass(frozen=True)
class PortalPermission:
    read: bool = True
    create: bool = False
    update: bool = False
    delete: bool = False

    @property
    def read_only(self) -> bool:
        return self.read and not (self.create or self.update or self.delete)


class AgencyPortalPermissions:
    POLICY = {
        "dashboard": PortalPermission(),
        "merchants": PortalPermission(),
        "stores": PortalPermission(),
        "devices": PortalPermission(),
        "users": PortalPermission(create=True, update=True, delete=True),
        "reports": PortalPermission(),
        "settings": PortalPermission(update=True),
    }
    METHODS = {"GET": "read", "POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}

    def __init__(self, user: PlatformUser):
        if user.i_agency_id is None:
            raise HTTPException(403, "Agency login required.")
        self.agency_id = user.i_agency_id
        self.user_id = user.i_platform_user_id

    def capabilities(self, resource: str) -> PortalPermission:
        if resource not in self.POLICY:
            raise HTTPException(403, "Agency portal permission denied.")
        return self.POLICY[resource]

    def require(self, resource: str, action: str) -> None:
        permission = self.capabilities(resource)
        if action not in ("read", "create", "update", "delete") or not getattr(permission, action):
            raise HTTPException(403, "This resource is Read Only in the Agency Portal.")

    def authorize_request(self, path: str, method: str) -> None:
        parts = path.strip("/").split("/")
        resource = parts[1] if len(parts) > 1 else "dashboard"
        if resource == "stores" and len(parts) > 3 and parts[3] == "devices":
            resource = "devices"
        if resource == "users" and len(parts) == 4 and parts[3] == "reset-password":
            self.require("users", "update")
            return
        self.require(resource, self.METHODS.get(method, "denied"))

    def can_deactivate_user(self, record_id: int) -> bool:
        return record_id != self.user_id

    def require_deactivation(self, db: Session, record: PlatformUser) -> None:
        if record.i_agency_id != self.agency_id:
            raise HTTPException(404, "Record not found in your Agency.")
        if not self.can_deactivate_user(record.i_platform_user_id):
            raise HTTPException(409, "You cannot deactivate your own merchant.")
        if record.c_status == "active" and self.query(db, "users").filter(PlatformUser.c_status == "active").count() <= 1:
            raise HTTPException(409, "At least one active Agency Admin must remain.")

    def query(self, db: Session, resource: str):
        self.require(resource, "read")
        if resource == "merchants":
            return db.query(Merchant).filter(Merchant.i_agency_id == self.agency_id)
        if resource == "stores":
            return db.query(Store).filter(
                Store.i_installed_by_agency_id == self.agency_id, Store.i_store_id != ADMIN_STORE_ID,
            )
        if resource == "users":
            return db.query(PlatformUser).join(Role, Role.i_role_id == PlatformUser.i_role_id).filter(
                PlatformUser.i_agency_id == self.agency_id, Role.c_name == AGENCY_ADMIN_ROLE_NAME,
            )
        if resource == "devices":
            return db.query(Device).join(Store, Store.i_store_id == Device.i_store_id).filter(
                Store.i_installed_by_agency_id == self.agency_id, Store.i_store_id != ADMIN_STORE_ID,
            )
        raise HTTPException(403, "No record scope is available for this resource.")
