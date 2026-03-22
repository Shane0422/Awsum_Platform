from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from backend.database.pg_platform import PlatformBase


class DeviceLog(PlatformBase):
    __tablename__ = "tb_device_log"

    i_log_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_device_id = Column(Integer, ForeignKey("tb_device.i_device_id", ondelete="CASCADE"), nullable=False, index=True)
    c_event_type = Column(String(40), nullable=False, index=True)
    c_hardware_id = Column(String(255), nullable=True)
    c_ip_address = Column(String(64), nullable=True)
    c_agent = Column(String(255), nullable=True)
    c_os = Column(String(120), nullable=True)
    c_version = Column(String(120), nullable=True)
    c_action_by = Column(String(255), nullable=True)
    dt_event_time = Column(DateTime, default=datetime.now, nullable=False, index=True)
    c_note = Column(String, nullable=True)