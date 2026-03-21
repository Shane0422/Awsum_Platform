from sqlalchemy import Column, Integer, String, DateTime, ForeignKey

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class Device(PlatformBase, AuditMixin):
    __tablename__ = "tb_device"

    i_device_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    i_license_id = Column(Integer, ForeignKey("tb_license.i_license_id"), nullable=True, index=True)

    c_device_uuid = Column(String, nullable=False, unique=True)
    c_device_name = Column(String, nullable=True)
    c_device_type = Column(String, nullable=True)
    c_os = Column(String, nullable=True)
    c_app_version = Column(String, nullable=True)

    dt_last_seen = Column(DateTime, nullable=True)
    c_status = Column(String, default="active")
