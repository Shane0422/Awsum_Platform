from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from backend.database.pg_platform import PlatformBase


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SystemSettingsAudit(PlatformBase):
    __tablename__ = "tb_system_settings_audit"

    i_audit_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_settings_id = Column(Integer, nullable=False)
    i_revision = Column(Integer, nullable=False)
    i_changed_by = Column(
        Integer,
        ForeignKey("tb_platform_user.i_platform_user_id"),
        nullable=False,
        index=True,
    )
    c_changed_fields = Column(Text, nullable=False)
    dt_changed_at = Column(DateTime, nullable=False, default=_utcnow_naive)
