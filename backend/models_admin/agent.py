from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String

from backend.database.pg_platform import PlatformBase


class Agent(PlatformBase):
    __tablename__ = "tb_agent"

    i_agent_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_agent_type_id = Column(Integer, ForeignKey("tb_agent_type.i_agent_type_id"), nullable=True, index=True)
    c_agent_code = Column(String(20), nullable=False, unique=True, index=True)
    c_agent_name = Column(String(120), nullable=False)
    c_company_name = Column(String(255), nullable=True)
    c_contact_name = Column(String(120), nullable=True)
    c_phone = Column(String(50), nullable=True)
    c_email = Column(String(255), nullable=True)
    c_address_line1 = Column(String(255), nullable=True)
    c_address_line2 = Column(String(255), nullable=True)
    c_city = Column(String(120), nullable=True)
    c_state = Column(String(120), nullable=True)
    c_zip = Column(String(20), nullable=True)
    c_country = Column(String(120), nullable=True)
    n_commission_rate = Column(Numeric(5, 2), nullable=True)
    c_memo = Column(String, nullable=True)
    c_status = Column(String(20), default="active", nullable=False)
    dt_created_at = Column(DateTime, default=datetime.now, nullable=False)