from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from backend.database.pg_platform import PlatformBase


class LicenseDeviceLink(PlatformBase):
    """
    Link history between a License (usage right) and a Device (physical unit).
    A row with dt_unlinked_at IS NULL is the active link. Partial unique indexes
    (created in db_init_platform) allow only one active link per license and per device.
    Rows are never deleted so replacement history is preserved.
    """
    __tablename__ = "tb_license_device_link"

    i_link_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_license_id = Column(Integer, ForeignKey("tb_license.i_license_id"), nullable=False, index=True)
    i_device_id = Column(Integer, ForeignKey("tb_device.i_device_id"), nullable=False, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    dt_linked_at = Column(DateTime, nullable=False, default=datetime.now)
    c_linked_by = Column(String(255), nullable=True)
    dt_unlinked_at = Column(DateTime, nullable=True)
    c_unlinked_by = Column(String(255), nullable=True)
    c_unlink_reason = Column(String(255), nullable=True)
