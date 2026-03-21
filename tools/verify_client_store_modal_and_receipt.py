import json
import requests

BASE = "http://127.0.0.1:8001"
ADMIN_STORE_CODE = "Admin"
ADMIN_EMAIL = "is2ceo@gmail.com"
ADMIN_PASSWORD = "Awsum123!"
REPORT_STORE_ID = 1100006


def main() -> int:
    session = requests.Session()
    login = session.post(
        f"{BASE}/auth/login",
        data={
            "store_code": ADMIN_STORE_CODE,
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        },
        allow_redirects=False,
        timeout=20,
    )
    if login.status_code not in (302, 303):
        raise SystemExit(f"login failed: {login.status_code} {login.text[:200]}")

    client_page = session.get(f"{BASE}/platform/master/client", timeout=20)
    client_html = client_page.text

    report_page = session.get(f"{BASE}/store/{REPORT_STORE_ID}/workspace/reports", timeout=20)
    report_html = report_page.text

    result = {
        "client_master_200": client_page.status_code == 200,
        "stores_tab_present": "{ id: 'stores', label: 'Stores'" in client_html,
        "has_add_store_button": 'id="btnClientStoreAdd"' in client_html,
        "has_client_store_modal": 'id="clientStoreModal"' in client_html,
        "has_store_code_readonly": 'id="field_client_store_code"' in client_html and 'readonly' in client_html,
        "modal_has_receipt_fields": 'field_client_store_receipt_message' in client_html,
        "store_reports_200": report_page.status_code == 200,
        "receipt_preview_present": "Receipt Preview" in report_html,
        "receipt_data_source_present": "Receipt Data Source" in report_html,
    }

    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
