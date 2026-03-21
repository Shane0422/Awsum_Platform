import json
import requests

BASE = "http://127.0.0.1:8001"

s = requests.Session()
login = s.post(
    f"{BASE}/auth/login",
    data={
        "store_code": "Admin",
        "email": "is2ceo@gmail.com",
        "password": "Awsum123!",
    },
    allow_redirects=False,
    timeout=20,
)
print("login_status=", login.status_code)

payload = {
    "client_name": "SaveCheck Client",
    "business_type": "Tuxedo Rental",
    "status": "active",
    "first_name": "John",
    "last_name": "Doe",
    "email": "savecheck-client@example.com",
    "phone": "5551234567",
    "address_line1": "123 Main St",
    "address_line2": "Suite 200",
    "city": "Dallas",
    "state": "TX",
    "zip": "75001",
    "country": "USA",
}
create = s.post(f"{BASE}/platform/client", json=payload, timeout=20)
print("create_status=", create.status_code)
print("create_body=", create.text[:300])

lst = s.get(f"{BASE}/platform/clients?q=SaveCheck", timeout=20)
print("list_status=", lst.status_code)
if lst.ok:
    rows = lst.json()
    print("list_count=", len(rows))
    if rows:
        print("latest=", json.dumps(rows[-1], ensure_ascii=True))
