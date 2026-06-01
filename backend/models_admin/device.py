from sqlalchemy import Column, Integer, String, DateTime, Date, ForeignKey

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class Device(PlatformBase, AuditMixin):
    __tablename__ = "tb_device"

    i_device_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    i_license_id = Column(Integer, ForeignKey("tb_license.i_license_id"), nullable=True, index=True)
    i_installed_by_agent_id = Column(Integer, ForeignKey("tb_agent.i_agent_id"), nullable=True, index=True)

    # New: FK to tb_device_type master (nullable for backward compat)
    i_device_type_id = Column(
        Integer,
        ForeignKey("tb_device_type.i_device_type_id"),
        nullable=True,
        index=True,
    )

    c_device_uuid = Column(String, nullable=False, unique=True)
    c_device_name = Column(String, nullable=True)
    c_device_type = Column(String, nullable=True)   # Legacy: free-text; use i_device_type_id going forward
    c_serial_no = Column(String(100), nullable=True)
    dt_installed_at = Column(Date, nullable=True)
    c_activation_code = Column(String(64), nullable=True, index=True)
    dt_activation_expiry = Column(DateTime, nullable=True)
    dt_first_activated_at = Column(DateTime, nullable=True)
    c_activated_by = Column(String(255), nullable=True)
    c_bound_hardware_id = Column(String(255), nullable=True)
    c_last_ip = Column(String(64), nullable=True)
    c_os = Column(String, nullable=True)
    c_app_version = Column(String, nullable=True)

    dt_last_seen = Column(DateTime, nullable=True)
    c_note = Column(String, nullable=True)
    c_memo = Column(String, nullable=True)   # Admin-facing install notes
    c_status = Column(String, default="active")
