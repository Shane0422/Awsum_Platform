"""Merchant read-only Store Licenses (routers.merchant_store): GET /merchant/api/store/{id}/devices-licenses and
GET /merchant/api/store/{id}/licenses/{license_id}, built from the Platform workspace / details through an allow-list.

Isolated SQLite app (tools.test_merchant_store_management fixture: Merchant 10 with Store 10 and Inactive Store 12,
Merchant 11 with Store 11). Licenses are issued, one is activated by a device and one is emailed (mock mailer) through
the existing Platform APIs; the Merchant then reads them. Covers the response keys, the full License Keys of its own
Store only (viewing and copying a key is allowed; managing is not), the hidden values (admin email, request ID,
fingerprint, device code, logs, unlink reason, Agency email, contract), tenant isolation (404), the absence of Merchant
write routes, the Store login and the unchanged Platform responses.
"""
import json
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.models_admin.license import License
from backend.routers import platform_store_license as psl
from tools import test_merchant_store_management as store_tests

API = "/merchant/api"
STORE_KEYS = {"store_id", "store_name", "store_code", "merchant_id", "merchant_name", "agency_name", "business_type", "status"}
ROW_KEYS = {"license_id", "name", "device_name", "type", "license_key", "os", "status", "status_label", "device_id",
            "issued_at", "activated_at", "valid_until", "email_sent_count", "email_last_sent_at", "last_seen_at",
            "last_ip", "issue_batch"}
DEVICE_KEYS = {"name", "os", "os_version", "model", "app_version", "last_seen", "last_ip", "first_activated_at", "status"}
LINK_KEYS = {"device_name", "linked_at", "unlinked_at"}
FULL_KEY = re.compile(r"^AWS-(POS|KSK|ORD)-[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){3}$")


class MerchantLicenseViewTests(unittest.TestCase):
    def setUp(self):
        store_tests.MerchantStoreManagementTests.setUp(self)
        self.app.include_router(psl.router)
        for target, value in (("load_company_branding", {"name": "Test Platform"}),):
            patcher = patch.object(psl, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        mailer = patch.object(psl, "send_license_email", return_value=("<mock@test>", "accepted (mock)"))
        mailer.start()
        self.addCleanup(mailer.stop)
        # Platform Admin (the fixture's session): licenses for Store 10 (two issues) and Store 11.
        for store_id, qty in ((10, 3), (11, 1)):
            put = self.client.put(f"/platform/store/{store_id}/entitlement", json={"pos_qty": qty, "kiosk_qty": 0, "customer_ordering": False})
            self.assertEqual(put.status_code, 200, put.text)
        self.issue(10, 2, "m-issue-0001")
        self.issue(10, 1, "m-issue-0002")
        self.issue(11, 1, "m-issue-0101")
        with self.factory() as db:
            rows = db.query(License).order_by(License.i_license_id).all()
            self.keys = {lic.i_license_id: lic.c_license_key for lic in rows}
            self.store10 = [lic.i_license_id for lic in rows if lic.i_store_id == 10]
            self.store11 = [lic.i_license_id for lic in rows if lic.i_store_id == 11]
        with TestClient(self.app, client=("198.51.100.40", 50000)) as device:
            activated = device.post("/platform/license/activate", json={
                "license_key": self.keys[self.store10[0]], "device_fingerprint": "FP-SECRET-0001",
                "device_name": "Counter POS", "ip_address": "10.9.9.9"})
            self.assertEqual(activated.status_code, 200, activated.text)
        sent = self.client.post("/platform/store/10/licenses/email", json={
            "license_ids": [self.store10[1]], "recipient": "agency@example.test", "subject": "License", "message": "Hi",
            "request_id": "m-mail-0001"})
        self.assertEqual(sent.status_code, 200, sent.text)
        self.platform_workspace = self.client.get("/platform/store/10/devices-licenses").json()
        self.platform_detail = self.client.get(f"/platform/store/10/licenses/{self.store10[0]}").json()

    def issue(self, store_id, quantity, request_id):
        response = self.client.post(f"/platform/store/{store_id}/licenses/issue",
                                    json={"license_type": "POS", "quantity": quantity, "request_id": request_id})
        self.assertEqual(response.status_code, 200, response.text)

    def login(self, scope):
        store_tests.test_common_app_navigation.CommonAppNavigationTests.login(self, scope)

    def assert_hidden(self, text):
        for license_id in self.store11:
            self.assertNotIn(self.keys[license_id], text)  # never another Merchant's key
        for secret in ("m-issue-", "admin@example.test", "FP-SECRET", "10.9.9.9", "Reported IP", "agency_email",
                       "fingerprint", "device_code", "unlink_reason", "unlinked_by", "issued_by", "issue_request_id",
                       '"contract"', "DEV_"):
            self.assertNotIn(secret, text, secret)

    def test_workspace_is_the_allow_listed_read_only_view(self):
        self.login("MERCHANT")
        response = self.client.get(f"{API}/store/10/devices-licenses")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("no-store", response.headers.get("cache-control", ""))
        body = response.json()
        self.assertEqual(set(body), {"store", "summary", "licenses", "service_license"})
        self.assertEqual(set(body["store"]), STORE_KEYS)
        self.assertEqual(body["summary"], self.platform_workspace["summary"])  # Contract Quantities as on Platform
        rows = body["licenses"]
        self.assertEqual([row["license_id"] for row in rows], self.store10)
        for row in rows:
            self.assertEqual(set(row), ROW_KEYS)
            self.assertRegex(row["license_key"], FULL_KEY)
            self.assertEqual(row["license_key"], self.keys[row["license_id"]])  # its own Store: the full key
        # Last Seen / Last IP (server) and the email status are visible.
        first, second, third = rows
        self.assertEqual((first["device_name"], first["last_ip"]), ("Counter POS", "198.51.100.40"))
        self.assertTrue(first["last_seen_at"])
        self.assertEqual((second["email_sent_count"], bool(second["email_last_sent_at"])), (1, True))
        # One issue = one opaque batch (no request ID).
        self.assertEqual(first["issue_batch"], second["issue_batch"])
        self.assertNotEqual(first["issue_batch"], third["issue_batch"])
        self.assert_hidden(response.text)

    def test_details_show_device_last_seen_and_ip_but_no_internal_values(self):
        self.login("MERCHANT")
        response = self.client.get(f"{API}/store/10/licenses/{self.store10[0]}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"license", "device", "history", "logs"})
        self.assertEqual(set(body["license"]), ROW_KEYS)
        self.assertEqual(body["license"]["license_key"], self.keys[self.store10[0]])
        self.assertEqual(set(body["device"]), DEVICE_KEYS)
        self.assertEqual((body["device"]["name"], body["device"]["last_ip"]), ("Counter POS", "198.51.100.40"))
        self.assertEqual(body["device"]["last_seen"], self.platform_detail["device"]["last_seen"])
        self.assertTrue(body["history"] and all(set(link) == LINK_KEYS for link in body["history"]))
        self.assertEqual(body["logs"], [])
        self.assert_hidden(response.text)
        not_linked = self.client.get(f"{API}/store/10/licenses/{self.store10[2]}").json()
        self.assertEqual((not_linked["device"], not_linked["history"]), (None, []))

    def test_only_its_own_stores_and_licenses(self):
        self.login("MERCHANT")
        self.assertEqual(self.client.get(f"{API}/store/11/devices-licenses").status_code, 404)  # another Merchant
        self.assertEqual(self.client.get(f"{API}/store/11/licenses/{self.store11[0]}").status_code, 404)
        self.assertEqual(self.client.get(f"{API}/store/10/licenses/{self.store11[0]}").status_code, 404)  # not of Store 10
        self.assertEqual(self.client.get(f"{API}/store/999/devices-licenses").status_code, 404)
        inactive = self.client.get(f"{API}/store/12/devices-licenses")  # its Inactive Store: view only
        self.assertEqual(inactive.status_code, 200, inactive.text)
        self.assertEqual(inactive.json()["licenses"], [])

    def test_no_merchant_write_route_and_platform_routes_refused(self):
        self.login("MERCHANT")
        lid = self.store10[0]
        merchant_writes = [
            self.client.post(f"{API}/store/10/licenses/issue", json={"license_type": "POS", "quantity": 1, "request_id": "x-0000001"}),
            self.client.put(f"{API}/store/10/entitlement", json={"pos_qty": 9, "kiosk_qty": 0, "customer_ordering": False}),
            self.client.post(f"{API}/store/10/licenses/email/preview", json={"license_ids": [lid]}),
            self.client.post(f"{API}/store/10/licenses/email", json={"license_ids": [lid]}),
            self.client.post(f"{API}/store/10/licenses/{lid}/unlink", json={"device_id": 1}),
            self.client.put(f"{API}/store/10/licenses/{lid}", json={"status": "suspended"}),
        ]
        self.assertTrue(all(call.status_code in (404, 405) for call in merchant_writes), [c.status_code for c in merchant_writes])
        platform_calls = [
            self.client.get("/platform/store/10/devices-licenses"),
            self.client.post("/platform/store/10/licenses/issue", json={"license_type": "POS", "quantity": 1, "request_id": "x-0000002"}),
            self.client.post(f"/platform/store/10/licenses/{lid}/unlink", json={"device_id": 1}),
        ]
        self.assertEqual([call.status_code for call in platform_calls], [403, 403, 403])
        with self.factory() as db:
            self.assertEqual(db.query(License).filter_by(i_store_id=10).count(), 3)  # nothing issued

    def test_store_login_is_refused(self):
        self.login("STORE")
        self.assertEqual(self.client.get(f"{API}/store/10/devices-licenses").status_code, 403)
        self.assertEqual(self.client.get(f"{API}/store/10/licenses/{self.store10[0]}").status_code, 403)

    def test_platform_responses_are_unchanged(self):
        # Platform still gets the full keys and every existing field (the Merchant view is a filtered copy).
        row = self.platform_workspace["licenses"][0]
        self.assertEqual(row["license_key"], self.keys[self.store10[0]])
        self.assertIn("issued_by", row)
        self.assertEqual(row["issue_request_id"], "m-issue-0001")
        self.assertIn("agency_email", self.platform_workspace["store"])
        self.assertIn("contract", self.platform_workspace)
        self.assertNotIn("issue_batch", row)
        self.assertEqual(set(self.platform_detail["device"]) >= {"device_code", "fingerprint_tail"}, True)
        self.assertTrue(self.platform_detail["logs"])
        self.assertIn("unlink_reason", self.platform_detail["history"][0])
        self.assertEqual(json.loads(json.dumps(self.platform_detail["license"]))["license_key"], self.keys[self.store10[0]])


if __name__ == "__main__":
    unittest.main()
