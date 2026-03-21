from sqlalchemy import Column, Integer, String, DateTime, ForeignKey

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class ProvisionLog(PlatformBase, AuditMixin):
    __tablename__ = "tb_provision_log"

    i_provision_log_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=True, index=True)
    i_device_id = Column(Integer, ForeignKey("tb_device.i_device_id"), nullable=True, index=True)

    c_action = Column(String, nullable=False)
    c_target = Column(String, nullable=True)
    c_result = Column(String, nullable=True)
    c_message = Column(String, nullable=True)
    dt_event = Column(DateTime, nullable=True)
