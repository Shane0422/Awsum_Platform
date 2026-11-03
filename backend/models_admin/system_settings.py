from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from backend.database.pg_platform import PlatformBase


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SystemSettings(PlatformBase):
    __tablename__ = "tb_system_settings"

    i_settings_id = Column(Integer, primary_key=True)
    i_revision = Column(Integer, nullable=False, default=1)

    c_company_name = Column(String(255), nullable=True)
    c_display_name = Column(String(255), nullable=True)
    c_logo_url = Column(String(1000), nullable=True)
    c_website = Column(String(1000), nullable=True)
    c_contact_email = Column(String(254), nullable=True)
    c_phone = Column(String(80), nullable=True)
    c_address = Column(Text, nullable=True)

    c_data_root_path = Column(String(1000), nullable=True)

    c_sender_name = Column(String(255), nullable=True)
    c_from_email = Column(String(254), nullable=True)
    c_reply_to_email = Column(String(254), nullable=True)
    c_smtp_host = Column(String(255), nullable=True)
    i_smtp_port = Column(Integer, nullable=True)
    c_smtp_username = Column(String(255), nullable=True)
    c_smtp_password_encrypted = Column(Text, nullable=True)
    c_smtp_encryption = Column(String(20), nullable=True)
    b_smtp_authentication = Column(Integer, nullable=True)
    c_test_recipient_email = Column(String(254), nullable=True)
    c_email_test_status = Column(String(30), nullable=True)
    c_email_test_result = Column(String(1000), nullable=True)
    dt_email_tested_at = Column(DateTime, nullable=True)

    c_default_timezone = Column(String(100), nullable=True)
    c_date_format = Column(String(30), nullable=True)
    c_time_format = Column(String(10), nullable=True)
    i_web_session_idle_minutes = Column(Integer, nullable=True)

    i_updated_by = Column(
        Integer,
        ForeignKey("tb_platform_user.i_platform_user_id"),
        nullable=True,
        index=True,
    )
    dt_updated_at = Column(DateTime, nullable=False, default=_utcnow_naive)
