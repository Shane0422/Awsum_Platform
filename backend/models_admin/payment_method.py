from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from backend.database.pg_platform import PlatformBase


class PaymentMethod(PlatformBase):
    __tablename__ = "tb_payment_method"

    i_payment_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_account_id = Column(Integer, ForeignKey("tb_client.i_client_id"), nullable=True, index=True)
    c_payment_type = Column(String(30), nullable=True)
    c_card_token = Column(String(255), nullable=True)
    c_bank_account = Column(String(255), nullable=True)
    c_billing_cycle = Column(String(30), nullable=True)
    dt_next_billing = Column(DateTime, nullable=True)
    c_status = Column(String(20), default="active", nullable=False)