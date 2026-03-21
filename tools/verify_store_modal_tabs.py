import json
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
SOURCE = Path(r"d:\Awsum_Projects\Awsum_Platform\templates\platform\master_management.html")


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    session = requests.Session()
    login = session.post(
        f"{BASE}/auth/login",
        data={"store_code": "Admin", "email": "is2ceo@gmail.com", "password": "Awsum123!"},
        allow_redirects=False,
        timeout=20,
    )
    if login.status_code not in (302, 303):
        raise SystemExit(f"login failed: {login.status_code} {login.text[:200]}")

    page = session.get(f"{BASE}/platform/master/client", timeout=20)
    html = page.text

    checks = {
        "client_page_200": page.status_code == 200,
        "modal_tab_buttons_present": all(
            token in html
            for token in [
                'data-store-modal-tab="basic"',
                'data-store-modal-tab="contact"',
                'data-store-modal-tab="address"',
                'data-store-modal-tab="receipt"',
            ]
        ),
        "modal_tab_panels_present": all(
            token in html
            for token in [
                'data-store-modal-panel="basic"',
                'data-store-modal-panel="contact"',
                'data-store-modal-panel="address"',
                'data-store-modal-panel="receipt"',
            ]
        ),
        "js_has_tab_reset_on_open": "setClientStoreModalTab('basic');" in source,
        "js_has_tab_init": "function initClientStoreModalTabs()" in source,
        "js_has_hidden_reset": "hidden.bs.modal" in source,
        "js_has_add_mode_switch": "updateClientStoreModalChrome('add')" in source,
        "js_has_edit_mode_switch": "updateClientStoreModalChrome('edit')" in source,
    }

    print(json.dumps(checks, ensure_ascii=True, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
