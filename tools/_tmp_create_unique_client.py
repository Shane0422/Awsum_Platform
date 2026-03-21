import time
import requests

BASE = "http://127.0.0.1:8001"
name = f"SaveCheck-{int(time.time())}"

s = requests.Session()
login = s.post(
    f"{BASE}/auth/login",
    data={"store_code": "Admin", "email": "is2ceo@gmail.com", "password": "Awsum123!"},
    allow_redirects=False,
    timeout=20,
)
print("login_status=", login.status_code)

payload = {
    "client_name": name,
    "business_type": "Tuxedo Rental",
    "status": "active",
    "first_name": "A",
    "last_name": "B",
    "email": f"{name.lower()}@example.com",
    "phone": "5551230000",
    "address_line1": "500 Elm St",
    "address_line2": "Floor 5",
    "city": "Dallas",
    "state": "TX",
    "zip": "75201",
    "country": "USA",
}
res = s.post(f"{BASE}/platform/client", json=payload, timeout=20)
print("create_status=", res.status_code)
print("create_body=", res.text[:500])

lst = s.get(f"{BASE}/platform/clients?q={name}", timeout=20)
print("list_status=", lst.status_code)
print("list_body=", lst.text[:500])
