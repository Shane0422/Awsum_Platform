from sqlalchemy import Column, Integer, String
from backend.database.admin_db import AdminBase
from backend.models_common.audit_mixin import AuditMixin

class BusinessType(AdminBase, AuditMixin):
    __tablename__ = "tb_business_type"

    i_business_type_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_name        = Column(String, nullable=False, unique=True)  # Laundry, Wedding, FlowerShop, CakeShop, Other
    c_description = Column(String, nullable=True)                # 한글 설명 (세탁소, 웨딩, 꽃집 등)
    c_status      = Column(String, default="active")             # active / inactive

    # ✅ AuditMixin 상속 컬럼
    # dt_created, i_created_by, dt_updated, i_updated_by
