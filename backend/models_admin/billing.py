from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String

from backend.database.pg_platform import PlatformBase


class Billing(PlatformBase):
    __tablename__ = "tb_billing"

    i_billing_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_client_id = Column(Integer, ForeignKey("tb_client.i_client_id"), nullable=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=True, index=True)
    i_agent_id = Column(Integer, ForeignKey("tb_agent.i_agent_id"), nullable=True, index=True)
    n_amount = Column(Numeric(12, 2), nullable=True)
    dt_billing_date = Column(DateTime, nullable=True)
    dt_due_date = Column(DateTime, nullable=True)
    c_status = Column(String(30), nullable=True)
    c_payment_method = Column(String(50), nullable=True)