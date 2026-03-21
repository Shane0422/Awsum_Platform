import json
import os
import sys
import time
from typing import Any, Dict, Optional

import psycopg
import requests

BASE_URL = "http://127.0.0.1:8001"
ADMIN_STORE_CODE = "Admin"
ADMIN_EMAIL = "is2ceo@gmail.com"
ADMIN_PASSWORD = "Awsum123!"
DB_URL = os.getenv(
    "AWSUM_PLATFORM_DATABASE_URL",
    "postgresql://postgres:Awsum123!@127.0.0.1:5432/awsum_platform",
)
if DB_URL.startswith("postgresql+psycopg://"):
    DB_URL = DB_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def login(session: requests.Session):
    return session.post(
        f"{BASE_URL}/auth/login",
        data={"store_code": ADMIN_STORE_CODE, "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        allow_redirects=False,
        timeout=20,
    )


def create_session_fixture(user_id: int) -> Optional[int]:
    token = f"crud-fixture-{int(time.time())}-{user_id}"
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into tb_session (i_user_id, c_jwt_token, c_status)
                values (%s, %s, 'active')
                returning i_session_id
                """,
                (user_id, token),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0]) if row else None


def main() -> int:
    report: Dict[str, Any] = {}
    ids: Dict[str, Optional[int]] = {"client_id": None, "store_id": None, "user_id": None, "session_id": None}

    s = requests.Session()
    l = login(s)
    if l.status_code not in (302, 303):
        report["login"] = {"status": l.status_code, "ok": False}
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 1

    ts = int(time.time())

    # Seed selection (minimal calls)
    role_name = requests.get(f"{BASE_URL}/platform/roles", cookies=s.cookies, timeout=20).json()[0]["name"]
    business_type_name = requests.get(f"{BASE_URL}/platform/business-types", cookies=s.cookies, timeout=20).json()[0]["name"]

    # Client CRUD
    acc = s.post(
        f"{BASE_URL}/platform/client",
        json={
            "client_name": f"E2E-Client-{ts}",
            "first_name": "E2E",
            "last_name": "Owner",
            "email": f"e2e-client-{ts}@example.com",
            "phone": "5551001000",
            "status": "active",
        },
        timeout=20,
    )
    ids["client_id"] = acc.json().get("id") if acc.ok else None
    acc_u = s.put(f"{BASE_URL}/platform/client/{ids['client_id']}", json={"phone": "5551002000"}, timeout=20) if ids["client_id"] else None

    # Store CRUD
    store = s.post(
        f"{BASE_URL}/platform/store",
        json={
            "store_name": f"E2E Store {ts}",
            "business_type": business_type_name,
            "operation_type": "Information POS",
            "channel_type": "POS",
            "device_type": "Windows POS",
            "store_status": "active",
            "client_id": ids["client_id"],
            "email": f"e2e-store-{ts}@example.com",
        },
        timeout=20,
    )
    store_json = store.json() if store.ok else {}
    ids["store_id"] = store_json.get("store_id")
    store_read = s.get(f"{BASE_URL}/platform/store/{ids['store_id']}", timeout=20) if ids["store_id"] else None
    store_u = s.put(f"{BASE_URL}/platform/store/{ids['store_id']}", json={"store_name": f"E2E Store {ts} Updated"}, timeout=20) if ids["store_id"] else None

    # User CRUD
    user_email = f"e2e-user-{ts}@example.com"
    usr = s.post(
        f"{BASE_URL}/platform/user",
        json={
            "email": user_email,
            "password": "Pass123!",
            "first_name": "E2E",
            "last_name": "User",
            "store_id": ids["store_id"],
            "role": role_name,
            "status": "active",
        },
        timeout=20,
    )
    ids["user_id"] = usr.json().get("id") if usr.ok else None
    usr_u = s.put(f"{BASE_URL}/platform/user/{ids['user_id']}", json={"first_name": "E2E-Updated"}, timeout=20) if ids["user_id"] else None

    # Session CRUD (fixture row)
    if ids["user_id"]:
        ids["session_id"] = create_session_fixture(ids["user_id"])
    sess_u = s.put(f"{BASE_URL}/platform/session/{ids['session_id']}", json={"status": "terminated"}, timeout=20) if ids["session_id"] else None
    sess_d = s.delete(f"{BASE_URL}/platform/session/{ids['session_id']}", timeout=20) if ids["session_id"] else None

    # Cleanup (soft delete)
    usr_d = s.delete(f"{BASE_URL}/platform/user/{ids['user_id']}", timeout=20) if ids["user_id"] else None
    store_d = s.delete(f"{BASE_URL}/platform/store/{ids['store_id']}", timeout=20) if ids["store_id"] else None
    acc_d = s.delete(f"{BASE_URL}/platform/client/{ids['client_id']}", timeout=20) if ids["client_id"] else None

    report["crud"] = {
        "clients": {
            "create": acc.status_code,
            "update": acc_u.status_code if acc_u else None,
        },
        "stores": {
            "create": store.status_code,
            "read": store_read.status_code if store_read else None,
            "update": store_u.status_code if store_u else None,
        },
        "users": {
            "create": usr.status_code,
            "update": usr_u.status_code if usr_u else None,
        },
        "sessions": {
            "fixture_session_id": ids["session_id"],
            "update": sess_u.status_code if sess_u else None,
            "delete": sess_d.status_code if sess_d else None,
        },
    }
    report["cleanup"] = {
        "user": usr_d.status_code if usr_d else None,
        "store": store_d.status_code if store_d else None,
        "client": acc_d.status_code if acc_d else None,
    }

    print(json.dumps(report, ensure_ascii=True, indent=2))

    ok = all(
        code == 200
        for code in [
            acc.status_code,
            acc_u.status_code if acc_u else None,
            store.status_code,
            store_read.status_code if store_read else None,
            store_u.status_code if store_u else None,
            usr.status_code,
            usr_u.status_code if usr_u else None,
            sess_u.status_code if sess_u else None,
            sess_d.status_code if sess_d else None,
        ]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
