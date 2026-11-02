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

    merchant_page = session.get(f"{BASE}/platform/master/merchant", timeout=20)
    merchant_html = merchant_page.text

    report_page = session.get(f"{BASE}/store/{REPORT_STORE_ID}/workspace/reports", timeout=20)
    report_html = report_page.text

    result = {
        "merchant_master_200": merchant_page.status_code == 200,
        "stores_tab_present": "{ id: 'stores', label: 'Stores'" in merchant_html,
        "has_add_store_button": 'id="btnMerchantStoreAdd"' in merchant_html,
        "has_merchant_store_modal": 'id="merchantStoreModal"' in merchant_html,
        "has_store_code_readonly": 'id="field_merchant_store_code"' in merchant_html and 'readonly' in merchant_html,
        "modal_has_receipt_fields": 'field_merchant_store_receipt_message' in merchant_html,
        "store_reports_200": report_page.status_code == 200,
        "receipt_preview_present": "Receipt Preview" in report_html,
        "receipt_data_source_present": "Receipt Data Source" in report_html,
    }

    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
