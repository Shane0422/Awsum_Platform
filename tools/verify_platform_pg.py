import json
import os
import sys
from typing import Dict, Any

import psycopg
import requests


BASE_URL = "http://127.0.0.1:8001"
DB_URL = os.getenv(
    "AWSUM_PLATFORM_DATABASE_URL",
    "postgresql://postgres:Awsum123!@127.0.0.1:5432/awsum_platform",
)
if DB_URL.startswith("postgresql+psycopg://"):
    DB_URL = DB_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def check_db() -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_database(), current_user")
            db_name, db_user = cur.fetchone()
            result["db"] = {"name": db_name, "user": db_user}

            required_tables = [
                "tb_client",
                "tb_business_type",
                "tb_role",
                "tb_store",
                "tb_user",
                "tb_session",
            ]
            cur.execute(
                """
                select table_name
                from information_schema.tables
                where table_schema='public' and table_name = any(%s)
                order by table_name
                """,
                (required_tables,),
            )
            result["tables"] = [r[0] for r in cur.fetchall()]

            cur.execute("select count(*) from tb_business_type")
            bt_count = cur.fetchone()[0]
            cur.execute("select count(*) from tb_role")
            role_count = cur.fetchone()[0]
            cur.execute("select count(*) from tb_store where i_store_id=1000001 and c_store_code='Admin'")
            admin_store_count = cur.fetchone()[0]
            cur.execute("select count(*) from tb_user where c_email='is2ceo@gmail.com'")
            super_admin_count = cur.fetchone()[0]

            result["seed"] = {
                "tb_business_type": bt_count,
                "tb_role": role_count,
                "admin_store": admin_store_count,
                "super_admin_user": super_admin_count,
            }
    return result


def check_login_and_store_crud() -> Dict[str, Any]:
    s = requests.Session()

    login_resp = s.post(
        f"{BASE_URL}/auth/login",
        data={
            "store_code": "Admin",
            "email": "is2ceo@gmail.com",
            "password": "Awsum123!",
        },
        allow_redirects=False,
        timeout=20,
    )

    if login_resp.status_code not in (302, 303):
        return {
            "login": {
                "ok": False,
                "status": login_resp.status_code,
                "body": login_resp.text[:300],
            }
        }

    created_id = None
    created_code = None

    create_payload = {
        "store_name": "E2E Test Store",
        "business_type": "Tuxedo Rental",
        "operation_type": "Information POS",
        "channel_type": "POS",
        "device_type": "Windows POS",
        "store_status": "active",
        "owner_name": "QA Owner",
        "phone": "5551234567",
        "email": "qa-store@example.com",
        "remark": "created by verify script",
    }
    create_resp = s.post(f"{BASE_URL}/platform/store", json=create_payload, timeout=20)
    create_ok = create_resp.status_code == 200
    if create_ok:
        data = create_resp.json()
        created_id = data.get("store_id")
        created_code = data.get("store_code")

    read_ok = False
    update_ok = False
    delete_ok = False

    if created_id:
        read_resp = s.get(f"{BASE_URL}/platform/store/{created_id}", timeout=20)
        read_ok = read_resp.status_code == 200

        update_resp = s.put(
            f"{BASE_URL}/platform/store/{created_id}",
            json={"store_name": "E2E Test Store Updated", "store_status": "active"},
            timeout=20,
        )
        update_ok = update_resp.status_code == 200

        delete_resp = s.delete(f"{BASE_URL}/platform/store/{created_id}", timeout=20)
        delete_ok = delete_resp.status_code == 200

    return {
        "login": {
            "ok": True,
            "status": login_resp.status_code,
            "location": login_resp.headers.get("Location"),
            "has_access_token_cookie": "access_token" in s.cookies,
        },
        "store_crud": {
            "create_ok": create_ok,
            "read_ok": read_ok,
            "update_ok": update_ok,
            "delete_ok": delete_ok,
            "created_store_id": created_id,
            "created_store_code": created_code,
        },
    }


def main() -> int:
    report: Dict[str, Any] = {}
    try:
        report["db_check"] = check_db()
    except Exception as exc:
        report["db_check_error"] = str(exc)

    try:
        report["api_check"] = check_login_and_store_crud()
    except Exception as exc:
        report["api_check_error"] = str(exc)

    print(json.dumps(report, ensure_ascii=True, indent=2))

    db_ok = "db_check" in report
    api = report.get("api_check", {})
    login_ok = api.get("login", {}).get("ok") is True
    crud = api.get("store_crud", {})
    crud_ok = all([
        crud.get("create_ok") is True,
        crud.get("read_ok") is True,
        crud.get("update_ok") is True,
        crud.get("delete_ok") is True,
    ])

    return 0 if db_ok and login_ok and crud_ok else 1


if __name__ == "__main__":
    sys.exit(main())
