from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from backend.database.pg_platform import PlatformBase


class AgentType(PlatformBase):
    __tablename__ = "tb_agent_type"

    i_agent_type_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_agent_type_code = Column(String(30), nullable=False, unique=True, index=True)
    c_agent_type_name = Column(String(120), nullable=False)
    c_status = Column(String(20), default="active", nullable=False)
    dt_created_at = Column(DateTime, default=datetime.now, nullable=False)
