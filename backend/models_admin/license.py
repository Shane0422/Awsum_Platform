from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Numeric

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class License(PlatformBase, AuditMixin):
    __tablename__ = "tb_license"

    i_license_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_device_id = Column(Integer, ForeignKey("tb_device.i_device_id"), nullable=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    i_account_id = Column(Integer, ForeignKey("tb_client.i_client_id"), nullable=True, index=True)
    i_agent_id = Column(Integer, ForeignKey("tb_agent.i_agent_id"), nullable=True, index=True)

    c_license_key = Column(String, nullable=False, unique=True)
    c_plan_name = Column(String, nullable=True)
    c_license_type = Column(String, nullable=True)
    i_max_devices = Column(Integer, nullable=True)
    i_max_users = Column(Integer, nullable=True)

    dt_start = Column(DateTime, nullable=True)
    dt_end = Column(DateTime, nullable=True)
    c_status = Column(String, default="active")
    n_monthly_fee = Column(Numeric(12, 2), nullable=True)
    n_agent_commission = Column(Numeric(12, 2), nullable=True)
    n_platform_fee = Column(Numeric(12, 2), nullable=True)
