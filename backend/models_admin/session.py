# backend/models_admin/session.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from datetime import datetime
from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin

class SessionTbl(PlatformBase, AuditMixin):
    __tablename__ = "tb_session"

    i_session_id   = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_platform_user_id = Column(Integer, ForeignKey("tb_platform_user.i_platform_user_id"), nullable=True)
    c_jwt_token    = Column(String, nullable=False)
    dt_login       = Column(DateTime, default=datetime.now)
    dt_last_active = Column(DateTime, default=datetime.now)
    c_status       = Column(String, default="active")   # active / terminated
    dt_terminated  = Column(DateTime, nullable=True)

    # ✅ AuditMixin 상속
    # dt_created, i_created_by, dt_updated, i_updated_by
