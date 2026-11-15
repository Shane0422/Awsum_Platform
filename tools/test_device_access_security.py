"""Activation Security S1: Store / Merchant status and rate limits on the device APIs
(POST /platform/license/activate, /platform/device/activate, /platform/device/validate).

Isolated SQLite app (tools.test_store_license_workflow fixture: Merchant 10 / Store 10 active, Platform Admin session).
Rate-limit time is controlled through services.rate_limit._clock. Covers: Active / Inactive Store and Merchant with no
database change on 403, 429 + Retry-After per IP / License Key / Activation Code / device, normal POS reconnects and
many devices behind one NAT IP, trusted proxy vs forged X-Forwarded-For, the IP-unavailable case, window expiry,
configured limits (invalid values fall back to the defaults) and no raw key / code in the logs.
"""
import json
import logging
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.models_admin.device import Device
from backend.models_admin.device_log import DeviceLog
from backend.models_admin.license import License
from backend.models_admin.license_device_link import LicenseDeviceLink
from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.services import rate_limit
from tools.test_store_license_workflow import StoreLicenseWorkflowTests


class DeviceAccessSecurityTests(unittest.TestCase):
    issue = StoreLicenseWorkflowTests.issue
    licenses = StoreLicenseWorkflowTests.licenses
    mock_send = StoreLicenseWorkflowTests.mock_send

    def setUp(self):
        StoreLicenseWorkflowTests.setUp(self)
        self.now = [1000.0]
        clock = patch.object(rate_limit, "_clock", lambda: self.now[0])
        clock.start()
        self.addCleanup(clock.stop)
        self.client.put("/platform/store/10/entitlement", json={"pos_qty": 3, "kiosk_qty": 0, "customer_ordering": False})
        self.issue("POS", 3, "sec-issue-0001")
        self.keys = [key for _, key, *_ in self.licenses()]

    # ---- helpers ----
    def device_client(self, ip="198.51.100.10", headers=None):
        client = TestClient(self.app, client=(ip, 50000), headers=headers or {})
        self.addCleanup(client.close)
        return client

    def activate_license(self, client, key, fingerprint="HW-1"):
        return client.post("/platform/license/activate", json={"license_key": key, "device_fingerprint": fingerprint})

    def validate(self, client, code, hardware="HW-1"):
        return client.post("/platform/device/validate", json={"device_code": code, "hardware_id": hardware})

    def set_status(self, store=None, merchant=None):
        with self.factory() as db:
            if store:
                db.get(Store, 10).c_status = store
            if merchant:
                db.get(Merchant, 10).c_status = merchant
            db.commit()

    def counts(self):
        with self.factory() as db:
            return (db.query(Device).count(), db.query(LicenseDeviceLink).count(), db.query(DeviceLog).count())

    def device_state(self, code):
        with self.factory() as db:
            device = db.query(Device).filter_by(c_device_uuid=code).one()
            links = db.query(LicenseDeviceLink).filter_by(i_device_id=device.i_device_id, dt_unlinked_at=None).count()
            logs = db.query(DeviceLog).filter_by(i_device_id=device.i_device_id).count()
            return (device.dt_last_seen, device.c_last_ip, device.c_status, device.c_bound_hardware_id, links, logs)

    # ---- Store / Merchant status ----
    def test_license_activation_requires_active_store_and_merchant(self):
        client = self.device_client()
        for store, merchant in (("inactive", "active"), ("active", "inactive")):
            self.set_status(store=store, merchant=merchant)
            before = self.counts()
            refused = self.activate_license(client, self.keys[0])
            self.assertEqual(refused.status_code, 403, refused.text)
            self.assertEqual(refused.json()["detail"], "Store or Merchant is not active.")
            self.assertEqual(self.counts(), before)  # no device, link or log
        self.set_status(store="active", merchant="active")
        ok = self.activate_license(client, self.keys[0])
        self.assertEqual(ok.status_code, 200, ok.text)
        # The same device re-activating while the Store is inactive is refused too, without changes.
        self.set_status(store="inactive")
        code = ok.json()["device_code"]
        before = self.device_state(code)
        self.assertEqual(self.activate_license(client, self.keys[0]).status_code, 403)
        self.assertEqual(self.device_state(code), before)

    def test_validation_refused_while_inactive_and_works_again_after(self):
        client = self.device_client()
        code = self.activate_license(client, self.keys[0]).json()["device_code"]
        self.assertEqual(self.validate(client, code).status_code, 200)
        for store, merchant in (("inactive", "active"), ("active", "inactive")):
            self.set_status(store=store, merchant=merchant)
            before = self.device_state(code)
            self.now[0] += 1
            refused = self.validate(self.device_client("198.51.100.11"), code)
            self.assertEqual(refused.status_code, 403, refused.text)
            self.assertEqual(self.device_state(code), before)  # Last Seen / IP / status / link / log unchanged
        self.set_status(store="active", merchant="active")
        again = self.validate(self.device_client("198.51.100.12"), code)
        self.assertEqual(again.status_code, 200, again.text)  # link and device kept: works again
        self.assertEqual(again.json()["last_ip"], "198.51.100.12")

    def test_store_without_merchant_is_refused(self):
        client = self.device_client()
        code = self.activate_license(client, self.keys[0]).json()["device_code"]
        with self.factory() as db:
            db.get(Store, 10).i_merchant_id = None  # not creatable through the Store APIs; refused like an inactive one
            db.commit()
        before = self.device_state(code)
        self.now[0] += 1
        self.assertEqual(self.validate(client, code).status_code, 403)
        self.assertEqual(self.activate_license(client, self.keys[1], "HW-2").status_code, 403)
        self.assertEqual(self.device_state(code), before)

    def test_activation_code_requires_active_store_and_is_not_consumed_when_refused(self):
        with self.factory() as db:
            db.add(Device(i_store_id=10, c_device_uuid="DEV_CODE_1", c_device_name="Code Device",
                          c_activation_code="ACT-CODE-0001", dt_activation_expiry=datetime.now() + timedelta(hours=1),
                          c_status="active"))
            db.commit()
        client = self.device_client()
        self.set_status(merchant="inactive")
        refused = client.post("/platform/device/activate", json={"activation_code": "ACT-CODE-0001", "hardware_id": "HW-9"})
        self.assertEqual(refused.status_code, 403, refused.text)
        with self.factory() as db:
            device = db.query(Device).filter_by(c_device_uuid="DEV_CODE_1").one()
            self.assertEqual((device.c_activation_code, device.c_bound_hardware_id, device.dt_last_seen),
                             ("ACT-CODE-0001", None, None))
        self.set_status(merchant="active")
        ok = client.post("/platform/device/activate", json={"activation_code": "ACT-CODE-0001", "hardware_id": "HW-9"})
        self.assertEqual(ok.status_code, 200, ok.text)

    # ---- rate limits ----
    def assert_429(self, response, scope_seconds):
        self.assertEqual(response.status_code, 429, response.text)
        retry = int(response.headers["Retry-After"])
        self.assertTrue(1 <= retry <= scope_seconds, retry)
        self.assertIn(f"Retry after {retry} seconds", response.json()["detail"])

    def test_license_key_limit_and_window_expiry(self):
        client = self.device_client()
        unknown = "AWS-POS-ZZZZ-ZZZZ-ZZZZ-ZZZZ"
        for _ in range(10):
            self.assertEqual(self.activate_license(client, unknown).status_code, 404)
        self.assert_429(self.activate_license(client, unknown), 600)
        self.assertEqual(self.activate_license(client, "AWS-POS-YYYY-YYYY-YYYY-YYYY").status_code, 404)  # another key
        self.now[0] += 601
        self.assertEqual(self.activate_license(client, unknown).status_code, 404)  # window passed

    def test_ip_limit_on_license_activation_and_other_ips_unaffected(self):
        client = self.device_client("198.51.100.20")
        for n in range(20):
            self.assertEqual(self.activate_license(client, f"AWS-POS-AAAA-AAAA-AAAA-{n:04d}").status_code, 404)
        self.assert_429(self.activate_license(client, "AWS-POS-AAAA-AAAA-AAAA-9999"), 600)
        self.assertEqual(self.activate_license(self.device_client("198.51.100.21"), "AWS-POS-AAAA-AAAA-AAAA-9999").status_code, 404)

    def test_activation_code_limits(self):
        client = self.device_client("198.51.100.30")
        for _ in range(10):
            self.assertEqual(client.post("/platform/device/activate", json={"activation_code": "NOPE-1", "hardware_id": "H"}).status_code, 404)
        self.assert_429(client.post("/platform/device/activate", json={"activation_code": "NOPE-1", "hardware_id": "H"}), 600)
        for n in range(9):  # IP limit (20 / 10 min): 11 used, 9 left
            self.assertEqual(client.post("/platform/device/activate", json={"activation_code": f"OTHER-{n}", "hardware_id": "H"}).status_code, 404)
        self.assert_429(client.post("/platform/device/activate", json={"activation_code": "OTHER-X", "hardware_id": "H"}), 600)

    def test_device_limit_allows_normal_reconnects(self):
        client = self.device_client()
        code = self.activate_license(client, self.keys[0]).json()["device_code"]
        for _ in range(30):  # app restarts / reconnects within one minute
            self.assertEqual(self.validate(client, code).status_code, 200)
        self.assert_429(self.validate(client, code), 60)
        self.now[0] += 61
        self.assertEqual(self.validate(client, code).status_code, 200)

    def test_many_devices_behind_one_nat_ip(self):
        nat = self.device_client("203.0.113.1")
        codes = [self.activate_license(nat, key, f"HW-{n}").json()["device_code"] for n, key in enumerate(self.keys)]
        for _ in range(5):
            for n, code in enumerate(codes):
                self.assertEqual(self.validate(nat, code, f"HW-{n}").status_code, 200)
        # 200 more stations of the same store (unknown codes): counted per device, never 429 under the IP limit.
        for n in range(200):
            self.assertEqual(self.validate(nat, f"DEV_10_X{n}").status_code, 404)

    def test_trusted_proxy_and_forged_forwarded_for(self):
        with patch.dict("os.environ", {"AWSUM_RL_DEVICE_VALIDATE_IP": "3/60", "AWSUM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"}):
            proxy = self.device_client("10.1.1.1")
            for client_ip in ("198.51.100.50", "198.51.100.51"):  # two real clients behind the trusted proxy
                for n in range(3):
                    self.assertEqual(proxy.post("/platform/device/validate", headers={"X-Forwarded-For": client_ip},
                                                json={"device_code": f"D-{client_ip}-{n}", "hardware_id": "H"}).status_code, 404)
            self.assert_429(proxy.post("/platform/device/validate", headers={"X-Forwarded-For": "198.51.100.50"},
                                       json={"device_code": "D-next", "hardware_id": "H"}), 60)
            # An untrusted peer cannot dodge the IP limit by forging X-Forwarded-For.
            direct = self.device_client("198.51.100.60")
            for n in range(3):
                self.assertEqual(direct.post("/platform/device/validate", headers={"X-Forwarded-For": f"1.2.3.{n}"},
                                             json={"device_code": f"F-{n}", "hardware_id": "H"}).status_code, 404)
            self.assert_429(direct.post("/platform/device/validate", headers={"X-Forwarded-For": "1.2.3.99"},
                                        json={"device_code": "F-next", "hardware_id": "H"}), 60)

    def test_ip_unavailable_skips_only_the_ip_limit(self):
        with patch.dict("os.environ", {"AWSUM_RL_LICENSE_ACTIVATE_IP": "1/600"}):
            anonymous = TestClient(self.app)  # transport peer "testclient": no valid client IP
            self.addCleanup(anonymous.close)
            with self.assertLogs("backend.services.device_access", level="INFO") as logs:
                for n in range(3):
                    self.assertEqual(self.activate_license(anonymous, f"AWS-POS-BBBB-BBBB-BBBB-{n:04d}").status_code, 404)
            self.assertTrue(any("client IP unavailable" in line for line in logs.output))
            for _ in range(9):  # the key limit still applies: 1 + 9 = 10 requests for this key
                self.activate_license(anonymous, "AWS-POS-BBBB-BBBB-BBBB-0000")
            self.assert_429(self.activate_license(anonymous, "AWS-POS-BBBB-BBBB-BBBB-0000"), 600)

    def test_configured_limits_and_invalid_values(self):
        with patch.dict("os.environ", {"AWSUM_RL_LICENSE_ACTIVATE_KEY": "2/30"}):
            client = self.device_client()
            for _ in range(2):
                self.activate_license(client, "AWS-POS-CCCC-CCCC-CCCC-CCCC")
            self.assert_429(self.activate_license(client, "AWS-POS-CCCC-CCCC-CCCC-CCCC"), 30)
        rate_limit.reset()
        with patch.dict("os.environ", {"AWSUM_RL_LICENSE_ACTIVATE_KEY": "ten/minutes"}):
            with self.assertLogs("backend.services.rate_limit", level="WARNING") as logs:
                for _ in range(10):  # default 10 / 10 min applies
                    self.assertEqual(self.activate_license(self.device_client(), "AWS-POS-DDDD-DDDD-DDDD-DDDD").status_code, 404)
            self.assertTrue(any("AWSUM_RL_LICENSE_ACTIVATE_KEY" in line and "default" in line for line in logs.output))
            self.assert_429(self.activate_license(self.device_client(), "AWS-POS-DDDD-DDDD-DDDD-DDDD"), 600)

    def test_no_raw_key_or_code_in_logs(self):
        secret_key, secret_code = "AWS-POS-SECR-ETKE-Y000-0001", "SECRET-CODE-0001"
        client = self.device_client()
        with self.assertLogs("backend.services.device_access", level="WARNING") as logs:
            for _ in range(11):
                self.activate_license(client, secret_key)
            for _ in range(11):
                client.post("/platform/device/activate", json={"activation_code": secret_code, "hardware_id": "H"})
        text = "\n".join(logs.output)
        self.assertIn("Device rate limit exceeded", text)
        self.assertNotIn(secret_key, text)
        self.assertNotIn(secret_code, text)
        self.assertNotIn("198.51.100.10", text)


class AppExceptionHandlerHeaderTests(unittest.TestCase):
    """The application's own HTTPException handler (backend.main) keeps the exception headers (Retry-After on 429)."""

    def test_api_error_keeps_retry_after(self):
        import asyncio
        from fastapi import HTTPException
        from starlette.requests import Request
        from backend.main import custom_http_exception_handler
        scope = {"type": "http", "method": "POST", "path": "/platform/device/validate", "headers": [(b"accept", b"application/json")],
                 "query_string": b"", "client": ("198.51.100.1", 1), "server": ("testserver", 80), "scheme": "http"}
        response = asyncio.run(custom_http_exception_handler(
            Request(scope), HTTPException(status_code=429, detail="Too many requests. Retry after 12 seconds.",
                                          headers={"Retry-After": "12"})))
        self.assertEqual((response.status_code, response.headers.get("retry-after")), (429, "12"))
        self.assertEqual(json.loads(response.body), {"detail": "Too many requests. Retry after 12 seconds."})


class RateLimitServiceTests(unittest.TestCase):
    def setUp(self):
        rate_limit.reset()
        self.addCleanup(rate_limit.reset)
        self.now = [0.0]
        clock = patch.object(rate_limit, "_clock", lambda: self.now[0])
        clock.start()
        self.addCleanup(clock.stop)

    def test_fixed_window_counts_and_refusals_are_not_counted(self):
        rule = rate_limit.Policy(2, 10)
        self.assertEqual([rate_limit.hit("b", "k", rule)[0] for _ in range(4)], [True, True, False, False])
        self.assertEqual(rate_limit.hit("b", "k", rule), (False, 10))
        self.now[0] = 4.2
        self.assertEqual(rate_limit.hit("b", "k", rule), (False, 6))
        self.assertTrue(rate_limit.hit("b", "other", rule)[0])
        self.assertTrue(rate_limit.hit("c", "k", rule)[0])
        self.now[0] = 10
        self.assertTrue(rate_limit.hit("b", "k", rule)[0])

    def test_policy_parsing(self):
        default = rate_limit.Policy(5, 60)
        with patch.dict("os.environ", {"X_RL": "7/30"}):
            self.assertEqual(rate_limit.policy("X_RL", default), rate_limit.Policy(7, 30))
        for bad in ("0/30", "7/0", "7", "-1/5", "a/b"):
            with patch.dict("os.environ", {"X_RL": bad}), self.assertLogs("backend.services.rate_limit", level="WARNING"):
                self.assertEqual(rate_limit.policy("X_RL", default), default)
        self.assertEqual(rate_limit.policy("X_RL_UNSET", default), default)


if __name__ == "__main__":
    unittest.main()
