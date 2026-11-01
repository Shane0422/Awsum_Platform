# backend/models_admin/session.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index, text
from sqlalchemy.orm import synonym
from datetime import datetime
from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin

class SessionTbl(PlatformBase, AuditMixin):
    __tablename__ = "tb_session"

    i_session_id   = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_platform_user_id = Column(Integer, ForeignKey("tb_platform_user.i_platform_user_id"), nullable=True)
    # Legacy column retained for data compatibility; new sessions never store bearer tokens.
    c_jwt_token    = Column(String, nullable=False, default="")
    c_session_key = Column(String(36), nullable=True, unique=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=True)
    c_login_context = Column(String(16), nullable=True)
    i_account_id = Column(Integer, ForeignKey("tb_client.i_client_id"), nullable=True)
    i_agency_id = Column(Integer, ForeignKey("tb_agency.i_agency_id"), nullable=True)
    c_client_id = Column(String(128), nullable=True)
    # Canonical public/client transport IP; may correctly be private/loopback.
    c_ip_address = Column(String(64), nullable=True)
    c_client_public_ip = synonym("c_ip_address")
    c_transport_peer_ip = Column(String(64), nullable=True)
    c_client_ip_source = Column(String(16), nullable=True)
    c_connection_type = Column(String(16), nullable=True)
    c_user_agent = Column(String(1024), nullable=True)
    c_local_ip = Column(String(64), nullable=True)
    c_client_local_ip = synonym("c_local_ip")
    c_country_code = Column(String(2), nullable=True)
    c_region = Column(String(128), nullable=True)
    c_city = Column(String(128), nullable=True)
    c_client_country_code = synonym("c_country_code")
    c_client_region = synonym("c_region")
    c_client_city = synonym("c_city")
    c_browser_name = Column(String(80), nullable=True)
    c_browser_version = Column(String(64), nullable=True)
    c_os_name = Column(String(80), nullable=True)
    c_os_version = Column(String(64), nullable=True)
    c_device_name = Column(String(128), nullable=True)
    c_device_uuid = Column(String(128), nullable=True)
    c_app_version = Column(String(64), nullable=True)
    c_login_path = Column(String(64), nullable=True)
    dt_login       = Column(DateTime, default=datetime.now)
    dt_last_active = Column(DateTime, default=datetime.now)
    c_status       = Column(String, default="ACTIVE")
    dt_expires = Column(DateTime, nullable=True)
    dt_logout = Column(DateTime, nullable=True)
    dt_terminated  = Column(DateTime, nullable=True)
    i_terminated_by = Column(Integer, nullable=True)

    __table_args__ = (
        Index(
            "uq_web_session_active_user", "i_platform_user_id", unique=True,
            postgresql_where=text("c_status = 'ACTIVE'"),
            sqlite_where=text("c_status = 'ACTIVE'"),
        ),
    )

    # ✅ AuditMixin 상속
    # dt_created, i_created_by, dt_updated, i_updated_by
