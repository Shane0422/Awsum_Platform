from sqlalchemy import Column, Integer, String, DateTime, ForeignKey

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class StoreSyncStatus(PlatformBase, AuditMixin):
    __tablename__ = "tb_store_sync_status"

    i_sync_status_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, unique=True, index=True)

    c_sync_state = Column(String, default="idle")
    c_last_sync_token = Column(String, nullable=True)
    i_pending_events = Column(Integer, nullable=True)
    dt_last_synced = Column(DateTime, nullable=True)
