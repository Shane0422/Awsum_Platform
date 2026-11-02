from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, func

from backend.database.pg_platform import PlatformBase
from backend.database.pg_platform import ADMIN_STORE_ID

AGENCY_ADMIN_ROLE_NAME = "Agency Admin"
MERCHANT_ADMIN_ROLE_NAME = "Merchant Admin"
STORE_ADMIN_ROLE_NAME = "Admin"


class PlatformUser(PlatformBase):
    __tablename__ = "tb_platform_user"

    i_platform_user_id = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # Preserve the technical Admin Store FK; i_agency_id identifies Agency users.
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, default=ADMIN_STORE_ID)
    # 플랫폼 관리자 권한
    i_role_id = Column(Integer, ForeignKey("tb_role.i_role_id"), nullable=False, default=1, index=True)
    i_agency_id = Column(Integer, ForeignKey("tb_agency.i_agency_id"), nullable=True, index=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id"), nullable=True, index=True)

    c_email = Column(String, nullable=False)
    c_password = Column(String, nullable=False)
    c_first_name = Column(String, nullable=True)
    c_last_name = Column(String, nullable=True)
    c_phone = Column(String, nullable=True)
    c_image_path = Column(String(1000), nullable=True)

    i_must_change_password = Column(Integer, default=0)
    dt_password_changed = Column(DateTime, nullable=True)
    c_status = Column(String, default="active")
    dt_last_login = Column(DateTime, nullable=True)
    dt_created = Column(DateTime, default=datetime.now)
    dt_updated = Column(DateTime, nullable=True, onupdate=datetime.now)

    __table_args__ = (
        Index(
            "uq_platform_user_agency_email", i_agency_id, func.lower(func.trim(c_email)),
            unique=True, postgresql_where=i_agency_id.is_not(None), sqlite_where=i_agency_id.is_not(None),
        ),
        Index(
            "uq_platform_user_merchant_email", i_merchant_id, func.lower(func.trim(c_email)),
            unique=True, postgresql_where=i_merchant_id.is_not(None), sqlite_where=i_merchant_id.is_not(None),
        ),
        Index(
            "uq_platform_user_store_email", i_store_id, func.lower(func.trim(c_email)),
            unique=True,
            postgresql_where=i_agency_id.is_(None) & i_merchant_id.is_(None),
            sqlite_where=i_agency_id.is_(None) & i_merchant_id.is_(None),
        ),
    )
