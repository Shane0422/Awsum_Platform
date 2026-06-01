from sqlalchemy import Column, Integer, String, Numeric, Boolean

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class PricingPlan(PlatformBase, AuditMixin):
    __tablename__ = "tb_pricing_plan"

    i_plan_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_plan_code = Column(String(50), nullable=False, unique=True, index=True)
    c_plan_name = Column(String(100), nullable=False)

    n_store_base_fee = Column(Numeric(10, 2), nullable=False, default=0)
    i_included_pos_count = Column(Integer, nullable=False, default=0)
    n_pos_fee = Column(Numeric(10, 2), nullable=False, default=0)
    i_included_kiosk_count = Column(Integer, nullable=False, default=0)
    n_kiosk_fee = Column(Numeric(10, 2), nullable=False, default=0)
    i_included_mobile_order_count = Column(Integer, nullable=False, default=0)
    n_mobile_order_fee = Column(Numeric(10, 2), nullable=False, default=0)

    i_included_user_count = Column(Integer, nullable=False, default=0)
    n_extra_user_fee = Column(Numeric(10, 2), nullable=False, default=0)

    n_setup_fee = Column(Numeric(10, 2), nullable=False, default=0)
    i_contract_term_month = Column(Integer, nullable=False, default=1)
    n_transaction_fee_rate = Column(Numeric(6, 4), nullable=False, default=0)

    i_sort_order = Column(Integer, nullable=False, default=100)
    b_is_default = Column(Boolean, nullable=False, default=False)

    # Distinct from POS/Kiosk count overage. Use for other device categories if needed.
    n_extra_device_fee = Column(Numeric(10, 2), nullable=False, default=0)

    c_currency = Column(String(10), nullable=False, default="USD")
    c_status = Column(String(50), nullable=False, default="active", index=True)
    c_memo = Column(String, nullable=True)
