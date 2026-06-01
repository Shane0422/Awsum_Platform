from datetime import datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, Numeric, String

from backend.database.pg_platform import PlatformBase


class Contract(PlatformBase):
    __tablename__ = "tb_contract"

    i_contract_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_subscription_id = Column(Integer, ForeignKey("tb_subscription.i_subscription_id"), nullable=False, index=True)
    i_account_id = Column(Integer, ForeignKey("tb_client.i_client_id"), nullable=False, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    i_pricing_plan_id = Column(Integer, ForeignKey("tb_pricing_plan.i_plan_id"), nullable=True, index=True)

    dt_contract_start_date = Column(Date, nullable=False, index=True)
    dt_contract_end_date = Column(Date, nullable=True, index=True)
    i_contract_term_month = Column(Integer, nullable=False, default=1)

    n_setup_fee = Column(Numeric(12, 2), nullable=False, default=0)
    n_monthly_base_fee = Column(Numeric(12, 2), nullable=False, default=0)
    n_monthly_device_fee = Column(Numeric(12, 2), nullable=False, default=0)
    n_monthly_user_fee = Column(Numeric(12, 2), nullable=False, default=0)
    n_monthly_total_fee = Column(Numeric(12, 2), nullable=False, default=0)
    n_tax_rate = Column(Numeric(6, 4), nullable=False, default=0)
    n_tax_amount = Column(Numeric(12, 2), nullable=False, default=0)
    n_total_monthly_fee = Column(Numeric(12, 2), nullable=False, default=0)

    c_status = Column(String(30), nullable=False, default="active", index=True)
    c_contract_pdf_path = Column(String(255), nullable=True)
    dt_created_at = Column(DateTime, nullable=False, default=datetime.now)
