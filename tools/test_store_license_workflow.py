"""Store License workflow through the existing license APIs (routers.platform_store_license), as used by the License
tab of Common Store Management: additional issues keep earlier licenses, the contracted limit is enforced, the email
preview and the send carry only the selected licenses (same content), a failed send keeps every license, Merchant
logins are refused and field activation still works. Isolated SQLite app (tools.test_common_app_navigation fixture);
the mailer is mocked (no SMTP connection).
"""
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.models_admin.license import License
from backend.models_admin.license_email_log import LicenseEmailLog
from backend.routers import platform_store_license as psl
from backend.services.mail_common import MailDeliveryError
from tools import test_common_app_navigation

KEY = re.compile(r"AWS-(?:POS|KSK|ORD)-[A-HJ-NP-Z2-9]{4}(?:-[A-HJ-NP-Z2-9]{4}){3}")
# License row keys before the read-only additions (kept with the same meaning).
ORIGINAL_ROW_KEYS = {"license_id", "name", "device_name", "type", "license_key", "os", "status", "status_label",
                     "device_id", "issued_at", "activated_at"}
ADDED_ROW_KEYS = {"valid_until", "issued_by", "issue_request_id", "email_sent_count", "email_last_sent_at",
                  "last_seen_at", "last_ip"}


class StoreLicenseWorkflowTests(unittest.TestCase):
    def setUp(self):
        test_common_app_navigation.CommonAppNavigationTests.setUp(self)
        self.app.include_router(psl.router)
        branding = patch.object(psl, "load_company_branding", return_value={"name": "Test Platform"})
        branding.start()
        self.addCleanup(branding.stop)
        self.sent = []
        mailer = patch.object(psl, "send_license_email", side_effect=self.mock_send)
        mailer.start()
        self.addCleanup(mailer.stop)

    def mock_send(self, message, db):
        html = next(part.get_content() for part in message.walk() if part.get_content_type() == "text/html")
        self.sent.append({"to": message["To"], "html": html})
        return "<mock@test>", "accepted (mock)"

    def issue(self, ltype, quantity, request_id):
        return self.client.post("/platform/store/10/licenses/issue",
                                json={"license_type": ltype, "quantity": quantity, "request_id": request_id})

    def licenses(self):
        with self.factory() as db:
            return [(lic.i_license_id, lic.c_license_key, lic.c_license_type, lic.i_slot_no)
                    for lic in db.query(License).filter_by(i_store_id=10).order_by(License.i_license_id)]

    def test_not_activated_license_has_no_last_seen(self):
        self.client.put("/platform/store/10/entitlement", json={"pos_qty": 1, "kiosk_qty": 0, "customer_ordering": False})
        self.issue("POS", 1, "issue-pos-0501")
        row = self.client.get("/platform/store/10/devices-licenses").json()["licenses"][0]
        self.assertEqual((row["device_id"], row["last_seen_at"], row["last_ip"]), (None, None, None))

    def test_additional_issue_keeps_earlier_licenses_and_limit_is_enforced(self):
        self.assertEqual(self.client.put("/platform/store/10/entitlement",
                                         json={"pos_qty": 3, "kiosk_qty": 1, "customer_ordering": False}).status_code, 200)
        first = self.issue("POS", 2, "issue-pos-0001")
        self.assertEqual(first.status_code, 200, first.text)
        before = self.licenses()
        second = self.issue("POS", 1, "issue-pos-0002")
        self.assertEqual(second.status_code, 200, second.text)
        after = self.licenses()
        self.assertEqual(after[:2], before)  # earlier licenses unchanged (IDs, keys, slots)
        self.assertEqual([slot for *_, slot in after], [1, 2, 3])
        self.assertTrue(all(KEY.fullmatch(key) and key.startswith("AWS-POS-") for _, key, *_ in after))
        rows = second.json()["workspace"]["licenses"]
        self.assertEqual([row["name"] for row in rows], ["POS-01", "POS-02", "POS-03"])
        self.assertTrue(all(row["issued_at"] for row in rows))  # Issued Date shown for every license
        self.assertEqual(second.json()["issued"], [after[2][0]])
        # Read-only additions: existing keys unchanged; issuer / issue request / validity from the license columns.
        self.assertTrue(all(set(row) == ORIGINAL_ROW_KEYS | ADDED_ROW_KEYS for row in rows))
        self.assertEqual([row["issue_request_id"] for row in rows], ["issue-pos-0001", "issue-pos-0001", "issue-pos-0002"])
        self.assertTrue(all(row["issued_by"] for row in rows))
        self.assertEqual([row["valid_until"] for row in rows], [None, None, None])  # no end date set at issue
        self.assertEqual([row["email_sent_count"] for row in rows], [0, 0, 0])
        with self.factory() as db:
            stored = db.query(License).filter_by(i_store_id=10).order_by(License.i_license_id).all()
            self.assertEqual([lic.c_issue_request_id for lic in stored], [row["issue_request_id"] for row in rows])
            self.assertEqual([lic.c_issued_by for lic in stored], [row["issued_by"] for row in rows])
        # The contracted limit: no fourth POS; the same request_id replays without issuing again.
        refused = self.issue("POS", 1, "issue-pos-0003")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("remaining 0", refused.json()["detail"])
        replay = self.issue("POS", 1, "issue-pos-0002")
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(self.licenses(), after)
        kiosk = self.issue("KIOSK", 1, "issue-ksk-0001")
        self.assertEqual(kiosk.status_code, 200)
        self.assertTrue(self.licenses()[-1][1].startswith("AWS-KSK-"))

    def test_preview_and_send_carry_only_the_selected_licenses(self):
        self.client.put("/platform/store/10/entitlement", json={"pos_qty": 2, "kiosk_qty": 0, "customer_ordering": False})
        self.issue("POS", 2, "issue-pos-0101")
        (id1, key1, *_), (id2, key2, *_) = self.licenses()
        email_state = lambda: {row["license_id"]: (row["email_sent_count"], bool(row["email_last_sent_at"]))  # noqa: E731
                               for row in self.client.get("/platform/store/10/devices-licenses").json()["licenses"]}
        self.assertEqual(email_state(), {id1: (0, False), id2: (0, False)})
        preview = self.client.post("/platform/store/10/licenses/email/preview", json={"license_ids": [id2], "message": "Hello"})
        self.assertEqual(preview.status_code, 200, preview.text)
        html = preview.json()["html"]
        for section in ("Your License Information", "Merchant", "Store", "Agency", "STORE LICENSE", "Store License Key",
                        "DEVICE LICENSES", "Activation"):
            self.assertIn(section, html)
        self.assertIn(key2, html)
        self.assertNotIn(key1, html)
        self.assertEqual(email_state(), {id1: (0, False), id2: (0, False)})  # a preview is not a sent email
        sent = self.client.post("/platform/store/10/licenses/email", json={
            "license_ids": [id2], "recipient": "agent@example.test", "subject": "License", "message": "Hello",
            "request_id": "mail-request-0001"})
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertEqual(len(self.sent), 1)
        mail = self.sent[0]
        self.assertEqual(mail["to"], "agent@example.test")
        self.assertIn(key2, mail["html"])
        self.assertNotIn(key1, mail["html"])
        self.assertEqual(KEY.findall(html), KEY.findall(mail["html"]))  # preview and sent email list the same keys
        with self.factory() as db:
            log = db.query(LicenseEmailLog).one()
            self.assertEqual((log.c_status, log.c_license_ids), ("accepted", f"[{id2}]"))
        self.assertEqual(email_state(), {id1: (0, False), id2: (1, True)})  # only the selected, accepted license
        # Duplicate send (same request) is not sent twice.
        again = self.client.post("/platform/store/10/licenses/email", json={
            "license_ids": [id2], "recipient": "agent@example.test", "subject": "License", "message": "Hello",
            "request_id": "mail-request-0001"})
        self.assertTrue(again.json()["replayed"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(email_state()[id2], (1, True))  # the replay is not counted again

    def test_failed_send_keeps_every_license(self):
        self.client.put("/platform/store/10/entitlement", json={"pos_qty": 1, "kiosk_qty": 0, "customer_ordering": False})
        self.issue("POS", 1, "issue-pos-0201")
        before = self.licenses()
        with patch.object(psl, "send_license_email", side_effect=MailDeliveryError("SMTP unavailable")):
            failed = self.client.post("/platform/store/10/licenses/email", json={
                "license_ids": [before[0][0]], "recipient": "agent@example.test", "subject": "License",
                "message": "Hello", "request_id": "mail-request-0201"})
        self.assertGreaterEqual(failed.status_code, 500)
        self.assertEqual(self.licenses(), before)  # nothing re-issued or removed
        with self.factory() as db:
            self.assertNotEqual(db.query(LicenseEmailLog).one().c_status, "accepted")
        row = self.client.get("/platform/store/10/devices-licenses").json()["licenses"][0]
        self.assertEqual((row["email_sent_count"], row["email_last_sent_at"]), (0, None))  # a failed send is not Sent

    def test_merchant_cannot_issue_change_quantities_or_email(self):
        self.client.put("/platform/store/10/entitlement", json={"pos_qty": 1, "kiosk_qty": 0, "customer_ordering": False})
        self.issue("POS", 1, "issue-pos-0301")
        license_id = self.licenses()[0][0]
        self.login("MERCHANT")
        calls = [
            self.client.get("/platform/store/10/devices-licenses"),
            self.client.put("/platform/store/10/entitlement", json={"pos_qty": 9, "kiosk_qty": 0, "customer_ordering": False}),
            self.issue("POS", 1, "merchant-issue-01"),
            self.client.post("/platform/store/10/licenses/email/preview", json={"license_ids": [license_id]}),
            self.client.post("/platform/store/10/licenses/email", json={
                "license_ids": [license_id], "recipient": "x@example.test", "subject": "x", "message": "x",
                "request_id": "merchant-mail-01"}),
        ]
        self.assertEqual([call.status_code for call in calls], [403] * 5)
        self.assertEqual(len(self.licenses()), 1)
        self.assertEqual(self.sent, [])

    def login(self, scope):
        test_common_app_navigation.CommonAppNavigationTests.login(self, scope)

    def test_field_activation_is_unchanged(self):
        self.client.put("/platform/store/10/entitlement", json={"pos_qty": 1, "kiosk_qty": 0, "customer_ordering": False})
        self.issue("POS", 1, "issue-pos-0401")
        key = self.licenses()[0][1]
        with TestClient(self.app) as device:
            activated = device.post("/platform/license/activate", json={"license_key": key, "device_fingerprint": "HW-1"})
            self.assertEqual(activated.status_code, 200, activated.text)
            again = device.post("/platform/license/activate", json={"license_key": key, "device_fingerprint": "HW-1"})
            self.assertEqual(again.status_code, 200)
            other = device.post("/platform/license/activate", json={"license_key": key, "device_fingerprint": "HW-2"})
            self.assertEqual(other.status_code, 409)
        rows = self.client.get("/platform/store/10/devices-licenses").json()["licenses"]
        self.assertEqual((rows[0]["status"], bool(rows[0]["device_id"])), ("activated", True))
        # Last Seen / Last IP: the server's record of the activation (tb_device), read only.
        with self.factory() as db:
            from backend.models_admin.device import Device
            device = db.get(Device, rows[0]["device_id"])
            self.assertEqual(rows[0]["last_seen_at"], device.dt_last_seen.isoformat())
            self.assertEqual(rows[0]["last_ip"], device.c_last_ip)
        self.assertTrue(rows[0]["last_seen_at"])  # the test client sends no IP; the value equals the stored one
        detail = self.client.get(f"/platform/store/10/licenses/{rows[0]['license_id']}").json()
        self.assertEqual((detail["device"]["last_seen"], detail["device"]["last_ip"]), (rows[0]["last_seen_at"], rows[0]["last_ip"]))


class DeviceServerIpTests(unittest.TestCase):
    """Device activation / validation record the server-confirmed client IP (services.session_metadata.client_ip);
    a device-reported IP goes to the device log note only; admin actions never set the device's Last IP."""

    issue = StoreLicenseWorkflowTests.issue
    licenses = StoreLicenseWorkflowTests.licenses
    mock_send = StoreLicenseWorkflowTests.mock_send

    def setUp(self):
        StoreLicenseWorkflowTests.setUp(self)
        self.client.put("/platform/store/10/entitlement", json={"pos_qty": 2, "kiosk_qty": 0, "customer_ordering": False})
        self.issue("POS", 2, "issue-pos-ip-01")
        self.keys = [key for _, key, *_ in self.licenses()]

    def device_for(self, key):
        from backend.models_admin.device import Device
        from backend.models_admin.device_log import DeviceLog
        with self.factory() as db:
            lic = db.query(License).filter_by(c_license_key=key).one()
            device = db.query(Device).filter_by(i_license_id=lic.i_license_id).one()
            logs = [(log.c_event_type, log.c_ip_address, log.c_note)
                    for log in db.query(DeviceLog).filter_by(i_device_id=device.i_device_id).order_by(DeviceLog.i_log_id)]
            return device.c_device_uuid, device.c_last_ip, logs

    def activate_device(self, key, peer, headers=None, **extra):
        with TestClient(self.app, client=(peer, 50000)) as device:
            return device.post("/platform/license/activate", headers=headers or {},
                               json={"license_key": key, "device_fingerprint": "HW-" + key[-4:], **extra})

    def test_direct_connection_records_the_peer_and_keeps_the_reported_ip_apart(self):
        response = self.activate_device(self.keys[0], "198.51.100.10", ip_address="10.0.0.5")
        self.assertEqual(response.status_code, 200, response.text)
        code, last_ip, logs = self.device_for(self.keys[0])
        self.assertEqual(last_ip, "198.51.100.10")  # server IP, not the device-reported 10.0.0.5
        self.assertEqual(logs[-1][:2], ("ACTIVATED", "198.51.100.10"))
        self.assertEqual(logs[-1][2], f"Activated with POS-01. | Reported IP: 10.0.0.5")  # note kept, IP appended
        # Validation: the server IP again; a spoofed X-Forwarded-For from an untrusted peer is ignored.
        with TestClient(self.app, client=("198.51.100.20", 50001)) as device:
            valid = device.post("/platform/device/validate", headers={"X-Forwarded-For": "1.2.3.4"},
                                json={"device_code": code, "hardware_id": "HW-" + self.keys[0][-4:], "ip_address": "10.0.0.6",
                                      "note": "Shift start"})
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(valid.json()["last_ip"], "198.51.100.20")
        _, last_ip, logs = self.device_for(self.keys[0])
        self.assertEqual(last_ip, "198.51.100.20")
        self.assertEqual(logs[-1], ("LOGIN", "198.51.100.20", "Shift start | Reported IP: 10.0.0.6"))

    def test_trusted_proxy_records_the_forwarded_client(self):
        with patch.dict("os.environ", {"AWSUM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"}):
            response = self.activate_device(self.keys[1], "10.1.2.3", headers={"X-Forwarded-For": "203.0.113.50"})
        self.assertEqual(response.status_code, 200, response.text)
        _, last_ip, logs = self.device_for(self.keys[1])
        self.assertEqual((last_ip, logs[-1][1]), ("203.0.113.50", "203.0.113.50"))
        self.assertNotIn("Reported IP", logs[-1][2])  # nothing reported by the device

    def test_untrusted_proxy_header_is_ignored(self):
        response = self.activate_device(self.keys[1], "198.51.100.30", headers={"X-Forwarded-For": "203.0.113.99"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.device_for(self.keys[1])[1], "198.51.100.30")

    def test_invalid_or_oversized_reported_ip_is_not_logged(self):
        self.activate_device(self.keys[0], "198.51.100.11", ip_address="not-an-ip")
        self.activate_device(self.keys[1], "198.51.100.12", last_ip="1" * 80)
        for key in self.keys:
            _, last_ip, logs = self.device_for(key)
            self.assertNotIn("Reported IP", logs[-1][2])
            self.assertTrue(last_ip.startswith("198.51.100."))

    def test_admin_device_creation_does_not_set_the_device_last_ip(self):
        from backend.models_admin.device import Device
        with self.factory() as db:
            license_id = db.query(License).filter_by(c_license_key=self.keys[0]).one().i_license_id
        created = self.client.post("/platform/merchant/10/stores/10/devices",
                                   json={"device_name": "Admin Added", "last_ip": "192.0.2.77", "ip_address": "192.0.2.78",
                                         "license_id": license_id})
        self.assertEqual(created.status_code, 200, created.text)
        with self.factory() as db:
            device = db.query(Device).filter_by(c_device_name="Admin Added").one()
            self.assertIsNone(device.c_last_ip)  # neither the admin's IP nor a typed value


if __name__ == "__main__":
    unittest.main()
