# backend/models_admin/role.py
from sqlalchemy import Column, Integer, String
from backend.database.admin_db import AdminBase
from backend.models_common.audit_mixin import AuditMixin

class Role(AdminBase, AuditMixin):
    __tablename__ = "tb_role"

    i_role_id     = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_name        = Column(String, nullable=False, unique=True)   # Role명
    c_description = Column(String, nullable=True)                 # 설명
    c_status      = Column(String, default="active")              # active / inactive
