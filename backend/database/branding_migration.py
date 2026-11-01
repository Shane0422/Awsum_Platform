"""Preserve existing code-based logo files and persist their references once."""
import re

from sqlalchemy import inspect, text

def migrate_branding(engine) -> None:
    from backend.services.images import legacy_physical_path
    with engine.begin() as conn:
        added = set()
        for table in ("tb_agency", "tb_client", "tb_store"):
            if inspect(conn).has_table(table):
                if "c_logo_url" not in {column["name"] for column in inspect(conn).get_columns(table)}:
                    added.add(table)
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS c_logo_url VARCHAR(1000)"))
        for table, key, query in (
            ("tb_client", "i_client_id", "SELECT i_client_id,c_client_code FROM tb_client WHERE c_logo_url IS NULL"),
            ("tb_store", "i_store_id", """SELECT s.i_store_id,c.c_client_code,s.c_store_code
                FROM tb_store s JOIN tb_client c ON c.i_client_id=s.i_account_id WHERE s.c_logo_url IS NULL"""),
        ):
            if table not in added:
                continue
            for row in conn.execute(text(query)).all():
                identifier, *codes = row
                if not all(code and re.fullmatch(r"[A-Za-z0-9_-]+", code) for code in codes):
                    continue
                suffix = f"clients/{codes[0]}/" + (f"stores/{codes[1]}/" if len(codes) == 2 else "")
                for root in (suffix, suffix.replace("clients/", "customers/", 1)):
                    url = f"/static/images/brands/{root}logo.png"
                    file = legacy_physical_path(url)
                    if file is not None and file.is_file():
                        conn.execute(text(f"UPDATE {table} SET c_logo_url=:url WHERE {key}=:id AND c_logo_url IS NULL"),
                                     {"url": url, "id": identifier})
                        break
        if inspect(conn).has_table("tb_session") and "c_login_context" in {
            column["name"] for column in inspect(conn).get_columns("tb_session")
        }:
            for context, table, session_key, organization_key, code in (
                ("AGENCY", "tb_agency", "i_agency_id", "i_agency_id", "c_agency_code"),
                ("MERCHANT", "tb_client", "i_account_id", "i_client_id", "c_client_code"),
                ("STORE", "tb_store", "i_store_id", "i_store_id", "c_store_code"),
            ):
                conn.execute(text(f"""
                    UPDATE tb_session s SET c_login_path='/login/{context.lower()}/' || o.{code}
                    FROM {table} o WHERE s.{session_key}=o.{organization_key}
                    AND s.c_login_context=:context AND (s.c_login_path IS NULL OR s.c_login_path='/')
                    AND o.{code} ~ '^[A-Za-z0-9_-]{{1,32}}$'
                """), {"context": context})
