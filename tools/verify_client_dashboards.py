import os
import re
import sys
from typing import Dict, List, Tuple

import requests


BASE_URL = os.getenv("AWSUM_BASE_URL", "http://127.0.0.1:8001")
CLIENT_STORE_CODE = os.getenv("AWSUM_CLIENT_STORE_CODE", "CUST_00001")
CLIENT_EMAIL = os.getenv("AWSUM_CLIENT_EMAIL", "owner@cust00001.local")
CLIENT_PASSWORD = os.getenv("AWSUM_CLIENT_PASSWORD", "Cust00001")


def login_client_admin(session: requests.Session) -> None:
    payload = {
        "store_code": CLIENT_STORE_CODE,
        "email": CLIENT_EMAIL,
        "password": CLIENT_PASSWORD,
    }
    resp = session.post(f"{BASE_URL}/auth/login", data=payload, allow_redirects=False, timeout=20)
    if resp.status_code not in (302, 303):
        raise RuntimeError(
            f"Client admin login failed: {resp.status_code} {resp.text[:200]} "
            f"(store_code={CLIENT_STORE_CODE}, email={CLIENT_EMAIL})"
        )


def validate_dashboard(
    session: requests.Session,
    path: str,
    dashboard_type: str,
    heading: str,
) -> Dict[str, bool]:
    resp = session.get(f"{BASE_URL}{path}", timeout=20)
    html = resp.text

    return {
        "status_200": resp.status_code == 200,
        "has_store_id": "Store ID:" in html,
        "has_dashboard_type": f"Type: {dashboard_type}" in html,
        "has_dashboard_menu_active": re.search(r"nav-link active[^>]*>Dashboard<", html) is not None,
        "has_heading": heading in html,
    }


def main() -> int:
    session = requests.Session()
    session.get(f"{BASE_URL}/", timeout=20)
    login_client_admin(session)

    cases: List[Tuple[str, str, str]] = [
        ("/client/dashboard/standard", "STANDARD", "Standard Dashboard"),
        ("/client/dashboard/restaurant", "RESTAURANT", "Restaurant Dashboard"),
        ("/client/dashboard/deli", "DELI", "Deli Dashboard"),
        ("/client/dashboard/tuxedo", "TUXEDO_RENTAL", "Tuxedo Rental Dashboard"),
    ]

    ok = True
    for path, dtype, heading in cases:
        checks = validate_dashboard(session, path, dtype, heading)
        this_ok = all(checks.values())
        ok = ok and this_ok
        print(path, checks, f"OK={this_ok}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
