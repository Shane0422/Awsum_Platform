from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from backend.database.pg_platform import PlatformBase
from backend.database.pg_platform import ADMIN_STORE_ID


class PlatformUser(PlatformBase):
    __tablename__ = "tb_platform_user"

    i_platform_user_id = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # 플랫폼 관리자는 Admin 스토어 범위로 고정
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, default=ADMIN_STORE_ID)

    c_email = Column(String, nullable=False)
    c_password = Column(String, nullable=False)
    c_first_name = Column(String, nullable=True)
    c_last_name = Column(String, nullable=True)
    c_phone = Column(String, nullable=True)

    i_must_change_password = Column(Integer, default=0)
    dt_password_changed = Column(DateTime, nullable=True)
    c_status = Column(String, default="active")

    dt_created = Column(DateTime, default=datetime.now)
    dt_updated = Column(DateTime, nullable=True, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("i_store_id", "c_email", name="uq_platform_store_email"),
    )
