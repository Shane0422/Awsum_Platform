from sqlalchemy import Column, Integer, String

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class DeviceCategory(PlatformBase, AuditMixin):
    """
    tb_device_category — 장비 대분류 마스터 (업종 공통)
    예: POS, KIOSK, KDS, TABLET, PAYMENT, PRINTER, DISPLAY, SCANNER, MOBILE, OTHER
    """
    __tablename__ = "tb_device_category"

    i_device_category_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_category_code = Column(String(50), nullable=False, unique=True, index=True)
    c_category_name = Column(String(100), nullable=False)
    c_description = Column(String, nullable=True)
    i_sort_order = Column(Integer, nullable=False, default=100)
    c_status = Column(String(50), nullable=False, default="active", index=True)
