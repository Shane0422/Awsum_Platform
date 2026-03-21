import json
import requests

BASE = "http://127.0.0.1:8001"
CLIENT_ID = 12

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
    "address": "123 Main St",
    "address_line1": "123 Main St",
    "address_line2": "Suite 200",
    "city": "Dallas",
    "state": "TX",
    "zip": "75001",
    "country": "USA",
    "status": "active",
}
upd = s.put(f"{BASE}/platform/client/{CLIENT_ID}", json=payload, timeout=20)
print("update_status=", upd.status_code)
print("update_body=", upd.text[:300])

lst = s.get(f"{BASE}/platform/clients?q=SaveCheck", timeout=20)
print("list_status=", lst.status_code)
if lst.ok:
    rows = lst.json()
    print("list_count=", len(rows))
    if rows:
        print("latest=", json.dumps(rows[-1], ensure_ascii=True))
