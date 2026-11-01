"""Idempotent additive PostgreSQL migration; never deletes session history."""
from sqlalchemy import inspect, text


def migrate_web_sessions(engine) -> None:
    if not inspect(engine).has_table("tb_session"):
        from backend.models_admin.session import SessionTbl
        SessionTbl.__table__.create(engine, checkfirst=True)
        return
    with engine.begin() as conn:
        conn.execute(text("LOCK TABLE tb_session IN ACCESS EXCLUSIVE MODE"))
        columns = {
            "c_session_key": "VARCHAR(36)", "i_store_id": "INTEGER REFERENCES tb_store(i_store_id)",
            "c_client_id": "VARCHAR(128)", "c_ip_address": "VARCHAR(64)", "c_user_agent": "VARCHAR(1024)",
            "c_login_path": "VARCHAR(64)", "dt_expires": "TIMESTAMP", "dt_logout": "TIMESTAMP",
            "i_terminated_by": "INTEGER",
            "c_login_context": "VARCHAR(16)",
            "i_account_id": "INTEGER REFERENCES tb_client(i_client_id)",
            "i_agency_id": "INTEGER REFERENCES tb_agency(i_agency_id)",
            "c_local_ip": "VARCHAR(64)", "c_country_code": "VARCHAR(2)",
            "c_region": "VARCHAR(128)", "c_city": "VARCHAR(128)",
            "c_browser_name": "VARCHAR(80)", "c_browser_version": "VARCHAR(64)",
            "c_os_name": "VARCHAR(80)", "c_os_version": "VARCHAR(64)",
            "c_device_name": "VARCHAR(128)", "c_device_uuid": "VARCHAR(128)",
            "c_app_version": "VARCHAR(64)",
            "c_transport_peer_ip": "VARCHAR(64)", "c_client_ip_source": "VARCHAR(16)",
            "c_connection_type": "VARCHAR(16)",
        }
        for name, definition in columns.items():
            conn.execute(text(f"ALTER TABLE tb_session ADD COLUMN IF NOT EXISTS {name} {definition}"))
        # Historical UA is evidence we can parse; never invent past network/device data.
        from backend.services.session_metadata import parse_user_agent
        rows = conn.execute(text("""
            SELECT i_session_id,c_user_agent FROM tb_session
            WHERE c_user_agent IS NOT NULL AND c_browser_name IS NULL AND c_os_name IS NULL
        """)).all()
        for session_id, user_agent in rows:
            parsed = parse_user_agent(user_agent)
            conn.execute(text("""
                UPDATE tb_session SET c_browser_name=:c_browser_name,c_browser_version=:c_browser_version,
                    c_os_name=:c_os_name,c_os_version=:c_os_version
                WHERE i_session_id=:session_id
            """), {**parsed, "session_id": session_id})
        # The recorded login endpoint is evidence of platform context; a user's
        # technical Store ID alone is not evidence of customer membership.
        conn.execute(text("""
            UPDATE tb_session SET c_login_context='PLATFORM',
                i_store_id=NULL, i_account_id=NULL, i_agency_id=NULL
            WHERE c_login_context IS NULL AND c_login_path='/platform-admin'
        """))
        conn.execute(text("UPDATE tb_session SET c_status=UPPER(c_status) WHERE c_status <> UPPER(c_status)"))
        # Pre-policy sessions cannot be authenticated: preserve them as expired history.
        conn.execute(text("""
            UPDATE tb_session SET c_status='EXPIRED', dt_updated=timezone('utc', now())
            WHERE c_status='ACTIVE' AND (c_session_key IS NULL OR dt_expires IS NULL)
        """))
        duplicates = conn.execute(text("""
            SELECT i_platform_user_id, count(*) FROM tb_session
            WHERE c_status='ACTIVE' GROUP BY i_platform_user_id HAVING count(*) > 1
        """)).all()
        if duplicates:
            raise RuntimeError(f"Duplicate active sessions need administrator review: {duplicates}")
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_web_session_key ON tb_session(c_session_key)
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_web_session_active_user
            ON tb_session(i_platform_user_id) WHERE c_status='ACTIVE'
        """))
        conn.execute(text("ALTER TABLE tb_session ALTER COLUMN c_status SET DEFAULT 'ACTIVE'"))
