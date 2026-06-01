from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Numeric, Date
from sqlalchemy.orm import synonym

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class Subscription(PlatformBase, AuditMixin):
    """
    Subscription: 계약/요금/기간 관리
    - Activation Code는 Device 인증용으로 License에 유지
    - Subscription은 Store 기준 계약/요금 관리
    """
    __tablename__ = "tb_subscription"

    # Primary key
    i_subscription_id = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # Account & Store (Store 기준 계약)
    i_account_id = Column(Integer, ForeignKey("tb_client.i_client_id"), nullable=False, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    i_plan_id = Column(Integer, ForeignKey("tb_pricing_plan.i_plan_id"), nullable=True, index=True)

    # Plan reference + contract-time pricing snapshot
    c_plan_code = Column(String(50), nullable=False)  # Snapshot of code at contract time
    c_plan_name = Column(String(100), nullable=True)  # Snapshot of name at contract time
    n_store_base_fee = Column(Numeric(10, 2), nullable=True)
    i_included_pos_count = Column(Integer, nullable=True)
    n_pos_fee = Column(Numeric(10, 2), nullable=True)
    i_included_kiosk_count = Column(Integer, nullable=True)
    n_kiosk_fee = Column(Numeric(10, 2), nullable=True)
    i_included_mobile_order_count = Column(Integer, nullable=True)
    n_mobile_order_fee = Column(Numeric(10, 2), nullable=True)
    i_included_user_count = Column(Integer, nullable=True)
    n_extra_user_fee = Column(Numeric(10, 2), nullable=True)
    n_setup_fee = Column(Numeric(10, 2), nullable=True)
    i_contract_term_month = Column(Integer, nullable=True)
    n_transaction_fee_rate = Column(Numeric(6, 4), nullable=True)
    n_extra_device_fee = Column(Numeric(10, 2), nullable=True)
    n_monthly_fee = Column(Numeric(10, 2), nullable=False)  # Contract monthly fee snapshot (can be overridden)
    c_currency = Column(String(10), nullable=True)

    # Duration (Date not DateTime for contract periods)
    dt_start_date = Column(Date, nullable=False, index=True)
    dt_end_date = Column(Date, nullable=True, index=True)

    # Device management
    i_device_limit = Column(Integer, nullable=False, default=5)

    # Status & Billing
    c_status = Column(String(50), nullable=False, default='active', index=True)  # active / paused / cancelled / expired
    c_billing_cycle = Column(String(50), nullable=False, default='monthly')  # monthly / annual / one-time

    # Tracking
    c_renewal_status = Column(String(50), nullable=True, default='active')  # active / pending / failed
    dt_next_billing = Column(Date, nullable=True)

    # Metadata
    c_memo = Column(String, nullable=True)
