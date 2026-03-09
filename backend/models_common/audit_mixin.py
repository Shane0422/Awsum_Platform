from sqlalchemy import Column, DateTime, Integer, ForeignKey
from datetime import datetime

class AuditMixin:
    """공통 감사(Audit) 필드 - 모든 테이블에 적용"""
    dt_created   = Column(DateTime, default=datetime.now)  # 최초 등록 일자
    i_created_by = Column(Integer, ForeignKey("tb_user.i_user_id"), nullable=True)  # 최초 등록자
    dt_updated   = Column(DateTime, nullable=True)         # 마지막 수정 일자
    i_updated_by = Column(Integer, ForeignKey("tb_user.i_user_id"), nullable=True)  # 마지막 수정자
