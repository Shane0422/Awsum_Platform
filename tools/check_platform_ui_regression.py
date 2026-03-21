import json
import re
import sys

import requests

BASE_URL = "http://127.0.0.1:8001"
ADMIN_STORE_CODE = "Admin"
ADMIN_EMAIL = "is2ceo@gmail.com"
ADMIN_PASSWORD = "Awsum123!"


def active_link_present(html: str, href: str) -> bool:
    p1 = rf'<a[^>]*href="{re.escape(href)}"[^>]*class="[^"]*active[^"]*"'
    p2 = rf'<a[^>]*class="[^"]*active[^"]*"[^>]*href="{re.escape(href)}"'
    return bool(re.search(p1, html) or re.search(p2, html))


def login(session: requests.Session):
    return session.post(
        f"{BASE_URL}/auth/login",
        data={"store_code": ADMIN_STORE_CODE, "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        allow_redirects=False,
        timeout=20,
    )


def main() -> int:
    report = {}

    home = requests.get(f"{BASE_URL}/", timeout=20)
    report["login_page"] = {
        "status": home.status_code,
        "ok_200": home.status_code == 200,
        "has_login_ui": "Sign In" in home.text or "Login" in home.text,
    }

    s = requests.Session()
    first_login = login(s)
    report["login"] = {
        "status": first_login.status_code,
        "ok_redirect": first_login.status_code in (302, 303),
        "location": first_login.headers.get("Location"),
    }

    dash = s.get(f"{BASE_URL}/platform/dashboard", allow_redirects=False, timeout=20)
    if dash.status_code in (302, 303) and dash.headers.get("Location") == "/":
        second_login = login(s)
        report["dashboard_relogin"] = {
            "status": second_login.status_code,
            "ok_redirect": second_login.status_code in (302, 303),
            "location": second_login.headers.get("Location"),
        }
        dash = s.get(f"{BASE_URL}/platform/dashboard", allow_redirects=False, timeout=20)

    report["dashboard"] = {
        "status": dash.status_code,
        "ok_200": dash.status_code == 200,
        "active_dashboard_menu": active_link_present(dash.text if dash.status_code == 200 else "", "/platform/dashboard"),
    }

    modules = ["client", "store", "user", "session"]
    report["master_menu"] = {}
    for module in modules:
        resp = s.get(f"{BASE_URL}/platform/master/{module}", timeout=20)
        report["master_menu"][module] = {
            "status": resp.status_code,
            "ok_200": resp.status_code == 200,
            "active_menu": active_link_present(resp.text if resp.status_code == 200 else "", f"/platform/master/{module}"),
        }

    print(json.dumps(report, ensure_ascii=True, indent=2))

    modules_ok = all(v["ok_200"] and v["active_menu"] for v in report["master_menu"].values() if "active_menu" in v)
    ok = (
        report["login_page"]["ok_200"]
        and report["login"]["ok_redirect"]
        and report["dashboard"]["ok_200"]
        and report["dashboard"]["active_dashboard_menu"]
        and modules_ok
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
