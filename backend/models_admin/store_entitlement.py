from sqlalchemy import Boolean, Column, ForeignKey, Integer

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class StoreEntitlement(PlatformBase, AuditMixin):
    """Contracted usage rights per store. Pricing/billing stays in Subscription/Contract."""
    __tablename__ = "tb_store_entitlement"

    i_entitlement_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, unique=True, index=True)
    i_pos_qty = Column(Integer, nullable=False, default=0)
    i_kiosk_qty = Column(Integer, nullable=False, default=0)
    b_customer_ordering = Column(Boolean, nullable=False, default=False)
