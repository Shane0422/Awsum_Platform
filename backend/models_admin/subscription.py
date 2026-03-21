from sqlalchemy import Column, Integer, String, DateTime, ForeignKey

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class Subscription(PlatformBase, AuditMixin):
    __tablename__ = "tb_subscription"

    i_subscription_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    i_license_id = Column(Integer, ForeignKey("tb_license.i_license_id"), nullable=True, index=True)

    c_billing_cycle = Column(String, nullable=True)  # monthly/yearly
    c_currency = Column(String, nullable=True)
    c_status = Column(String, default="active")

    dt_started = Column(DateTime, nullable=True)
    dt_next_billing = Column(DateTime, nullable=True)
    dt_ended = Column(DateTime, nullable=True)
