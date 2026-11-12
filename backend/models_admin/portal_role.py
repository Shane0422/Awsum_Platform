"""Merchant-defined Portal Roles (Employee web access, STEP 1B/1C). STEP 1A creates the tables only.

- tb_portal_role: one Merchant's role for one scope (MERCHANT Portal or STORE Portal). b_store_assignable marks STORE
  roles a Store Admin may assign (inside the Store Admin ceiling, enforced by the server in STEP 1D).
- tb_portal_role_permission: resource (Portal Resource Registry code, validated by the server) x View / Add / Edit /
  Delete / Export. Any action requires View.

Not tb_role: tb_role stays the global web login role list (Admin detection, organization login). A Portal Role never
grants Admin rights. Referencing rows include the scope in their composite FK, so a STORE role can never be stored as
a Merchant Portal role (and the reverse) and a role of another Merchant can never be referenced.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer, PrimaryKeyConstraint, String, UniqueConstraint

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin

PORTAL_SCOPES = ("MERCHANT", "STORE")
PORTAL_ROLE_STATUSES = ("active", "inactive")


class PortalRole(PlatformBase, AuditMixin):
    __tablename__ = "tb_portal_role"
    __table_args__ = (
        UniqueConstraint("i_merchant_id", "c_scope", "c_name", name="uq_tb_portal_role_name"),
        UniqueConstraint("i_portal_role_id", "i_merchant_id", "c_scope", name="uq_tb_portal_role_owner_scope"),
        CheckConstraint("c_scope IN ('MERCHANT', 'STORE')", name="ck_tb_portal_role_scope"),
        CheckConstraint("c_status IN ('active', 'inactive')", name="ck_tb_portal_role_status"),
        CheckConstraint("NOT b_store_assignable OR c_scope = 'STORE'", name="ck_tb_portal_role_assignable"),
    )

    i_portal_role_id = Column(Integer, primary_key=True, autoincrement=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id", ondelete="RESTRICT"), nullable=False,
                           index=True)
    c_scope = Column(String(10), nullable=False)
    c_name = Column(String(60), nullable=False)
    c_description = Column(String(255), nullable=True)
    b_store_assignable = Column(Boolean, nullable=False, default=False)
    c_status = Column(String(10), nullable=False, default="active")


class PortalRolePermission(PlatformBase):
    __tablename__ = "tb_portal_role_permission"
    __table_args__ = (
        PrimaryKeyConstraint("i_portal_role_id", "c_resource", name="pk_tb_portal_role_permission"),
        CheckConstraint("b_view OR NOT (b_add OR b_edit OR b_delete OR b_export)", name="ck_tb_portal_role_perm_view"),
    )

    i_portal_role_id = Column(Integer, ForeignKey("tb_portal_role.i_portal_role_id", ondelete="CASCADE"),
                              nullable=False)
    c_resource = Column(String(60), nullable=False)
    b_view = Column(Boolean, nullable=False, default=False)
    b_add = Column(Boolean, nullable=False, default=False)
    b_edit = Column(Boolean, nullable=False, default=False)
    b_delete = Column(Boolean, nullable=False, default=False)
    b_export = Column(Boolean, nullable=False, default=False)
