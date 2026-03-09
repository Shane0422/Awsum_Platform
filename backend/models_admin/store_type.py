from sqlalchemy import Column, Integer, String
from backend.database.admin_db import AdminBase
from backend.models_common.audit_mixin import AuditMixin

class StoreType(AdminBase, AuditMixin):
    __tablename__ = "tb_store_type"

    i_store_type_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_name        = Column(String, nullable=False, unique=True)  # HeadOffice, Branch, Agency, Partner
    c_description = Column(String, nullable=True)                # 한글 설명 (예: 본사, 지사, 대리점, 협력사)
    c_status      = Column(String, default="active")             # active / inactive

    # ✅ AuditMixin 상속 컬럼
    # dt_created, i_created_by, dt_updated, i_updated_by
