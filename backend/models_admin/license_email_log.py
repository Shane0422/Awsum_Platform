from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from backend.database.pg_platform import PlatformBase


class LicenseEmailLog(PlatformBase):
    __tablename__ = "tb_license_email_log"

    i_email_log_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_request_id = Column(String(64), nullable=False, unique=True)
    c_request_fingerprint = Column(String(64), nullable=False)
    i_sender_user_id = Column(
        Integer,
        ForeignKey("tb_platform_user.i_platform_user_id"),
        nullable=False,
        index=True,
    )
    c_sender_email = Column(String(255), nullable=False)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    c_recipient_email = Column(String(254), nullable=False)
    c_subject = Column(String(255), nullable=False)
    c_license_ids = Column(Text, nullable=False)
    c_status = Column(String(20), nullable=False, default="sending")
    dt_sent_at = Column(DateTime, nullable=True)
    c_provider_message_id = Column(String(255), nullable=True)
    c_provider_result = Column(Text, nullable=True)
    dt_created_at = Column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        Index("ix_tb_license_email_log_store_created", "i_store_id", "dt_created_at"),
    )
