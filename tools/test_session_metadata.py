import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend.database.pg_platform import PlatformBase
from backend.main import app
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.session import SessionTbl
from backend.routers import auth, dashboard, platform_store, store
from backend.services.ip_geolocation import ApproximateLocation, LocalGeoIpProvider, locate_ip
from backend.services.session_metadata import (
    TrustedClientMetadata, client_ip, collect_session_metadata, trusted_proxy_networks,
    resolve_client_network, session_client_network,
)
from backend.services.web_sessions import create_session, logout_session
from tools.test_web_sessions import PASSWORD, request, seed


CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)


def network_request(peer="127.0.0.1", headers=None):
    return Request({
        "type": "http", "method": "POST", "scheme": "http", "path": "/platform-admin/login",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (peer, 1234) if peer else None,
        "server": ("testserver", 80), "query_string": b"",
    })


class NetworkMetadataTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": "", "AWSUM_GEOIP_DATABASE_PATH": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_direct_and_local_ips_are_not_fabricated(self):
        for peer, expected in (("127.0.0.1", "127.0.0.1"), ("73.1.2.3", "73.1.2.3"),
                               ("::1", "::1"), ("::ffff:73.1.2.3", "73.1.2.3"),
                               ("2001:4860:4860::8888", "2001:4860:4860::8888")):
            with self.subTest(peer=peer):
                self.assertEqual(client_ip(network_request(peer)), expected)

    def test_untrusted_forwarded_headers_are_ignored(self):
        for header in ("x-forwarded-for", "x-real-ip"):
            self.assertEqual(client_ip(network_request("73.1.2.3", {header: "8.8.8.8"})), "73.1.2.3")
        # Localhost is not implicitly a trusted proxy.
        self.assertEqual(client_ip(network_request(headers={"x-forwarded-for": "8.8.8.8"})), "127.0.0.1")

    def test_trusted_proxy_chain_selects_nearest_untrusted_client(self):
        with patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8,2001:db8:1::/48"}):
            self.assertEqual(client_ip(network_request("10.0.0.2", {
                "x-forwarded-for": "1.1.1.1, 73.1.2.3, 10.0.0.1",
            })), "73.1.2.3")
            self.assertEqual(client_ip(network_request("10.0.0.2", {"x-real-ip": "73.1.2.3"})), "73.1.2.3")
            self.assertEqual(client_ip(network_request("2001:db8:1::2", {
                "x-forwarded-for": "2001:4860:4860::8888",
            })), "2001:4860:4860::8888")
            # A header from an untrusted peer cannot add itself to the trust chain.
            self.assertEqual(client_ip(network_request("73.1.2.3", {
                "x-forwarded-for": "8.8.8.8, 10.0.0.1",
            })), "73.1.2.3")

    def test_malformed_proxy_headers_never_fall_back_to_proxy_location(self):
        with patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"}):
            for value in ("unknown", "8.8.8.8,", "8.8.8.8:1234", "[::1]", "fe80::1%eth0",
                          ",".join(["8.8.8.8"] * 33)):
                with self.subTest(value=value), self.assertLogs("backend.services.session_metadata", level="WARNING"):
                    self.assertIsNone(client_ip(network_request("10.0.0.1", {
                        "x-forwarded-for": value, "x-real-ip": "1.1.1.1",
                    })))
        for invalid in ("*", "0.0.0.0/0", "::/0"):
            with self.subTest(config=invalid), patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": invalid}), \
                 self.assertRaises(ValueError):
                trusted_proxy_networks()
        with self.assertLogs("backend.services.session_metadata", level="WARNING"):
            self.assertIsNone(client_ip(network_request(None)))

    def test_trusted_proxy_without_provable_client_is_never_geolocated(self):
        provider = Mock()
        with patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": "8.8.8.8/32,10.0.0.0/8"}):
            for headers in ({}, {"x-real-ip": "invalid"}, {"x-real-ip": "8.8.8.8"},
                            {"x-forwarded-for": "bad"},
                            {"x-forwarded-for": "10.0.0.1", "x-real-ip": "1.1.1.1"}):
                with self.subTest(headers=headers), self.assertLogs("backend.services.session_metadata", level="WARNING"):
                    metadata = collect_session_metadata(network_request("8.8.8.8", headers),
                                                        geolocation_provider=provider)
                    self.assertIsNone(metadata["c_ip_address"])
                    self.assertIsNone(metadata["c_city"])
                    self.assertEqual(metadata["c_transport_peer_ip"], "8.8.8.8")
                    self.assertEqual(metadata["c_client_ip_source"], "UNAVAILABLE")
        provider.lookup.assert_not_called()

    def test_same_server_two_client_countries_use_only_original_client_ip(self):
        # Provider data is a fixture, not a claim about these IPs' real locations.
        locations = {
            "1.1.1.1": ApproximateLocation("KR", "Gangwon-do", "Hoengseong"),
            "9.9.9.9": ApproximateLocation("US", "Texas", "Lewisville"),
        }
        provider = Mock()
        provider.lookup.side_effect = lambda ip: locations[ip]
        with patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": "8.8.8.8/32"}):
            for ip, location in locations.items():
                with self.subTest(ip=ip):
                    req = network_request("8.8.8.8", {"x-forwarded-for": ip})
                    req.scope["server"] = ("virginia-server.example.test", 443)
                    metadata = collect_session_metadata(req, geolocation_provider=provider)
                    self.assertEqual(metadata["c_ip_address"], ip)
                    self.assertEqual(metadata["c_transport_peer_ip"], "8.8.8.8")
                    self.assertEqual(metadata["c_client_ip_source"], "FORWARDED")
                    self.assertEqual((metadata["c_city"], metadata["c_region"], metadata["c_country_code"]),
                                     (location.city, location.region, location.country_code))
                    provider.lookup.assert_called_with(ip)
        self.assertEqual(provider.lookup.call_count, 2)
        direct = resolve_client_network(network_request("73.1.2.3"))
        self.assertEqual((direct.client_public_ip, direct.source), ("73.1.2.3", "DIRECT"))

    def test_local_and_unavailable_location_have_explicit_client_semantics(self):
        for ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            metadata = collect_session_metadata(network_request(ip))
            network = session_client_network(SessionTbl(**metadata))
            self.assertEqual(network["client_location"], "Local Development")
            self.assertEqual(network["connection_type"], "Web")
        for ip in ("73.1.2.3", None):
            network = session_client_network(SessionTbl(c_ip_address=ip))
            self.assertEqual(network["client_location"], "Location unavailable")
        # Stored legacy geolocation must not imply a location for loopback/private IPs.
        row = SessionTbl(c_ip_address="127.0.0.1", c_city="Server City", c_country_code="US")
        network = session_client_network(row)
        self.assertEqual(network["client_location"], "Local Development")
        self.assertIsNone(network["client_city"])
        row.c_ip_address = "192.168.1.105"
        self.assertEqual(session_client_network(row)["client_location_status"], "NON_PUBLIC")
        row.c_ip_address = "127.0.0.1"
        row.c_client_ip_source = "FORWARDED"
        self.assertNotEqual(session_client_network(row)["client_location"], "Local Development")

    def test_geolocation_only_for_global_ips_and_failure_is_observable(self):
        provider = Mock()
        provider.lookup.return_value = ApproximateLocation("US", "Texas", "Dallas")
        for ip in ("127.0.0.1", "192.168.1.105", "::1", None, "192.0.2.10"):
            self.assertEqual(locate_ip(ip, provider), ApproximateLocation())
        provider.lookup.assert_not_called()
        self.assertEqual(locate_ip("73.1.2.3", provider), ApproximateLocation("US", "Texas", "Dallas"))
        provider.lookup.side_effect = TimeoutError("provider unavailable")
        with self.assertLogs("backend.services.ip_geolocation", level="WARNING") as logs:
            self.assertEqual(locate_ip("73.1.2.3", provider), ApproximateLocation())
        self.assertIn("continuing without location", logs.output[0])
        self.assertNotIn("73.1.2.3", logs.output[0])
        with patch.dict(os.environ, {"AWSUM_GEOIP_DATABASE_PATH": r"Z:\missing\city.mmdb"}), \
             self.assertLogs("backend.services.ip_geolocation", level="WARNING"):
            self.assertEqual(locate_ip("73.1.2.3"), ApproximateLocation())

    def test_local_mmdb_provider_extracts_only_country_region_city(self):
        with patch("backend.services.ip_geolocation.maxminddb.open_database") as open_db:
            reader = open_db.return_value.__enter__.return_value
            reader.get.return_value = {
                "country": {"iso_code": "US"}, "subdivisions": [{"names": {"en": "Texas"}}],
                "city": {"names": {"en": "Dallas"}}, "location": {"latitude": 1, "longitude": 2},
            }
            self.assertEqual(LocalGeoIpProvider("city.mmdb").lookup("73.1.2.3"),
                             ApproximateLocation("US", "Texas", "Dallas"))
            reader.get.return_value = None
            self.assertEqual(LocalGeoIpProvider("city.mmdb").lookup("73.1.2.3"), ApproximateLocation())
        with patch("backend.services.ip_geolocation.maxminddb.open_database") as open_db:
            self.assertEqual(locate_ip("73.1.2.3"), ApproximateLocation())
            open_db.assert_not_called()

    def test_web_metadata_does_not_accept_claimed_local_ip_or_device_headers(self):
        metadata = collect_session_metadata(network_request(headers={
            "user-agent": CHROME_WINDOWS, "x-public-ip": "8.8.8.8",
            "x-local-ip": "192.168.1.105", "x-device-uuid": "unverified", "x-device-name": "unverified",
        }))
        self.assertEqual(metadata["c_ip_address"], "127.0.0.1")
        self.assertEqual(metadata["c_user_agent"], CHROME_WINDOWS)
        self.assertEqual(metadata["c_browser_name"], "Chrome")
        self.assertEqual(metadata["c_browser_version"], "155.0.0")
        self.assertEqual((metadata["c_os_name"], metadata["c_os_version"]), ("Windows", "10"))
        for field in ("c_local_ip", "c_device_uuid", "c_device_name", "c_country_code"):
            self.assertIsNone(metadata[field])

    def test_trusted_native_metadata_is_separate_from_browser_client_and_license(self):
        metadata = collect_session_metadata(network_request(headers={"user-agent": CHROME_WINDOWS}),
            trusted_client=TrustedClientMetadata(
                local_ip="192.168.1.105", device_uuid="desktop-uuid", device_name="Desktop A",
                app_version="1.2.3", os_name="Windows", os_version="11",
            ))
        self.assertEqual(metadata["c_local_ip"], "192.168.1.105")
        self.assertEqual(metadata["c_device_uuid"], "desktop-uuid")
        self.assertEqual(metadata["c_device_name"], "Desktop A")
        self.assertEqual(metadata["c_app_version"], "1.2.3")
        self.assertEqual(metadata["c_os_version"], "11")
        for invalid in ("8.8.8.8", "127.0.0.1", "not-an-ip"):
            with self.subTest(ip=invalid), self.assertLogs("backend.services.session_metadata", level="WARNING"):
                value = collect_session_metadata(network_request(),
                    trusted_client=TrustedClientMetadata(local_ip=invalid))
                self.assertIsNone(value["c_local_ip"])
        self.assertEqual(collect_session_metadata(network_request())["c_browser_name"], None)


class SessionMetadataIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        PlatformBase.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        seed(self.factory)

    def test_login_persists_metadata_once_and_authentication_stays_independent(self):
        def dependency():
            with self.factory() as db:
                yield db
        overrides = {module.get_db: dependency for module in (auth, platform_store, dashboard, store)}
        with patch.dict(app.dependency_overrides, overrides), \
             patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": "", "AWSUM_GEOIP_DATABASE_PATH": ""}), \
             patch("backend.main.PlatformSessionLocal", self.factory), \
             patch("backend.services.web_session_http.PlatformSessionLocal", self.factory), \
             patch("backend.services.reporting.PlatformSessionLocal", self.factory), \
             patch("backend.services.session_metadata.locate_ip", return_value=ApproximateLocation("US", "Texas", "Dallas")) as geo:
            first = TestClient(app, follow_redirects=False, client=("73.1.2.3", 1234))
            second = TestClient(app, follow_redirects=False, client=("73.1.2.4", 1234))
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            data = {
                "access_code": "ADMIN", "login_mode": "platform_admin", "email": "user1@example.test",
                "password": PASSWORD, "local_ip": "192.168.1.105", "device_uuid": "untrusted-form-value",
            }
            self.assertEqual(first.post("/platform-admin/login", data=data,
                                       headers={"User-Agent": CHROME_WINDOWS, "X-Forwarded-For": "8.8.8.8"}).status_code, 303)
            self.assertEqual(second.post("/platform-admin/login", data=data).status_code, 409)
            rows = first.get("/platform/sessions").json()
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual((r["public_ip"], r["ip_address"]), ("73.1.2.3", "73.1.2.3"))
            self.assertEqual(r["client_public_ip"], "73.1.2.3")
            self.assertEqual(r["client_location"], "Dallas, Texas, US")
            self.assertEqual(r["client_ip_source"], "DIRECT")
            self.assertEqual(r["connection_type"], "Web")
            self.assertEqual((r["city"], r["region"], r["country_code"]), ("Dallas", "Texas", "US"))
            self.assertEqual(r["user_agent"], CHROME_WINDOWS)
            self.assertEqual(r["browser_name"], "Chrome")
            self.assertIsNone(r["local_ip"])
            self.assertIsNone(r["device_uuid"])
            self.assertTrue(r["browser_client_id"])
            self.assertEqual(first.get("/platform/sessions?q=Dallas").json()[0]["id"], r["id"])
            self.assertEqual(first.get("/platform/sessions?q=Windows").json()[0]["id"], r["id"])
            self.assertEqual(first.get("/platform/master/session", headers={"Accept": "text/html"}).status_code, 200)
            geo.assert_called_once_with("73.1.2.3", None)
            self.assertEqual(first.get("/auth/logout").status_code, 303)

    def test_real_provider_failure_still_creates_session_and_does_not_touch_lifecycle(self):
        with patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": "", "AWSUM_GEOIP_DATABASE_PATH": r"Z:\missing\city.mmdb"}), \
             self.assertLogs("backend.services.ip_geolocation", level="WARNING"), self.factory() as db:
            token = create_session(db, db.get(PlatformUser, 1),
                                   network_request("73.1.2.3"), {"scope_type": "platform"})
            self.assertTrue(token)
            row = db.query(SessionTbl).one()
            self.assertEqual(row.c_status, "ACTIVE")
            self.assertEqual(row.c_ip_address, "73.1.2.3")
            self.assertIsNone(row.c_city)
            self.assertIsNone(row.c_local_ip)

    def test_trusted_proxy_ip_is_stored_but_untrusted_forwarding_cannot_override_it(self):
        for config, expected in (("", "10.0.0.2"), ("10.0.0.0/8", "73.1.2.3")):
            with self.subTest(config=config), patch.dict(os.environ, {
                "AWSUM_TRUSTED_PROXY_CIDRS": config, "AWSUM_GEOIP_DATABASE_PATH": "",
            }), self.factory() as db:
                token = create_session(
                    db, db.get(PlatformUser, 1),
                    network_request("10.0.0.2", {"x-forwarded-for": "1.1.1.1, 73.1.2.3, 10.0.0.1"}),
                    {"scope_type": "platform"},
                )
                row = db.query(SessionTbl).filter_by(c_status="ACTIVE").one()
                self.assertEqual(row.c_ip_address, expected)
                logout_session(request(token), db)

    def test_two_users_same_host_have_separate_persisted_client_locations(self):
        provider = Mock()
        provider.lookup.side_effect = lambda ip: {
            "1.1.1.1": ApproximateLocation("KR", "Gangwon-do", "Hoengseong"),
            "9.9.9.9": ApproximateLocation("US", "Texas", "Lewisville"),
        }[ip]
        with patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": "8.8.8.8/32"}), \
             patch("backend.services.session_metadata.locate_ip",
                   side_effect=lambda ip, _: locate_ip(ip, provider)), self.factory() as db:
            for user_id, ip in ((1, "1.1.1.1"), (2, "9.9.9.9")):
                req = network_request("8.8.8.8", {"x-forwarded-for": ip})
                req.scope["server"] = ("virginia-server.example.test", 443)
                create_session(db, db.get(PlatformUser, user_id), req, {"scope_type": "platform"})
            rows = db.query(SessionTbl).order_by(SessionTbl.i_platform_user_id).all()
            self.assertEqual([r.c_status for r in rows], ["ACTIVE", "ACTIVE"])
            self.assertEqual([r.c_client_public_ip for r in rows], ["1.1.1.1", "9.9.9.9"])
            self.assertEqual([session_client_network(r)["client_location"] for r in rows],
                             ["Hoengseong, Gangwon-do, KR", "Lewisville, Texas, US"])
            self.assertEqual([r.c_transport_peer_ip for r in rows], ["8.8.8.8", "8.8.8.8"])
        self.assertEqual(provider.lookup.call_count, 2)

    def test_missing_trusted_proxy_client_ip_does_not_block_login_or_record_proxy_location(self):
        provider = Mock()
        with patch.dict(os.environ, {"AWSUM_TRUSTED_PROXY_CIDRS": "8.8.8.8/32"}), \
             patch("backend.services.session_metadata.locate_ip",
                   side_effect=lambda ip, _: locate_ip(ip, provider)), \
             self.assertLogs("backend.services.session_metadata", level="WARNING"), self.factory() as db:
            token = create_session(db, db.get(PlatformUser, 1),
                                   network_request("8.8.8.8"), {"scope_type": "platform"})
            self.assertTrue(token)
            row = db.query(SessionTbl).one()
            self.assertEqual(row.c_status, "ACTIVE")
            self.assertIsNone(row.c_client_public_ip)
            self.assertEqual(row.c_transport_peer_ip, "8.8.8.8")
            self.assertEqual(row.c_client_ip_source, "UNAVAILABLE")
            self.assertEqual(session_client_network(row)["client_location"], "Location unavailable")
        provider.lookup.assert_not_called()


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the session view behavior test.")
class SessionMetadataViewTests(unittest.TestCase):
    def test_compact_list_grouped_details_exports_and_display_timezone(self):
        script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const row = {
  id: 1, user_name: '+Test User', organization_name: 'Configured Platform',
  context_label: 'Platform Admin', login_context: 'PLATFORM', status: 'ACTIVE', current: true,
  client_public_ip: '73.1.2.3', client_local_ip: null,
  client_location: '<script>city</script>, Texas, US', connection_type: 'Web', client_ip_source: 'DIRECT',
  browser_name: 'Chrome', browser_version: '155.0.0', os_name: 'Windows', os_version: '10',
  user_agent: 'Mozilla/5.0 RAW AUDIT UA', device_uuid: null, device_name: null, browser_client_id: 'browser-id',
  login_at: '2026-10-29T07:02:03.123456', last_active_at: '2026-10-29T07:14:00',
};
async function check(timezone, expectedTime) {
  const elements = new Map(), exports = [], blobs = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', innerHTML: '', textContent: '', dataset: {}, events: {},
      addEventListener(name, fn) { this.events[name] = fn; },
    });
    return elements.get(id);
  }
  const buttons = ['csv', 'pdf'].map(format => ({
    dataset: {sessionExport: format}, events: {},
    addEventListener(name, fn) { this.events[name] = fn; },
  }));
  const root = element('sessionPage');
  root.dataset = {displayTimezone: timezone, canTerminate: 'true'};
  root.querySelectorAll = () => buttons;
  const context = {
    document: {getElementById: element, createElement: () => ({click() {}})},
    fetch: async () => ({ok: true, json: async () => [row]}),
    Intl, Date, URLSearchParams, Blob, setTimeout, clearTimeout,
    URL: {createObjectURL: blob => {blobs.push(blob); return 'blob:test';}, revokeObjectURL() {}},
    bootstrap: {Modal: {getOrCreateInstance: () => ({show() {}})}},
    AwsumReports: {printTable: (...args) => exports.push(args)},
  };
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  const html = element('sessionRows').innerHTML;
  assert(html.includes('Chrome 155 / Windows 10'));
  assert(html.includes('73.1.2.3'));
  assert(html.includes('&lt;script&gt;city&lt;/script&gt;'));
  assert(!html.includes('<script>city</script>'));
  assert(!html.includes('Mozilla/5.0'));
  assert(html.includes(expectedTime));
  assert.equal(element('sessionTimezone').textContent, timezone);
  await element('sessionRows').events.click({
    target: {closest: selector => selector === '[data-details]' ? {dataset: {details: '1'}} : null},
  });
  const details = element('sessionDetailsBody').innerHTML;
  for (const section of ['Session', 'Network', 'Client', 'Activity', 'Technical'])
    assert(details.includes('>' + section + '</h3>'));
  assert(details.includes('Mozilla/5.0 RAW AUDIT UA'));
  assert(details.includes('Local IP'));
  assert(details.includes('Device UUID'));
  assert(details.includes('Not Available'));
  assert(details.includes('Browser Client ID'));
  assert(details.includes('browser-id'));
  buttons[1].events.click();
  assert.equal(exports[0][1].length, exports[0][2][0].length);
  assert(exports[0][1].includes('Approximate Client Location'));
  assert.equal(exports[0][2][0][2], 'Chrome 155 / Windows 10');
  buttons[0].events.click();
  const csv = await blobs[0].text();
  assert(csv.includes("\"'+Test User\""));
  assert(csv.includes(expectedTime));
  assert(!csv.includes('Mozilla/5.0'));
  for (const [ip, location] of [
    ['127.0.0.1', 'Local Development'], ['73.1.2.3', 'Location unavailable'],
    [null, 'Location unavailable'],
  ]) {
    row.client_public_ip = ip;
    row.client_location = location;
    await element('sessionRefresh').events.click();
    assert(element('sessionRows').innerHTML.includes(location));
    if (ip) assert(element('sessionRows').innerHTML.includes(ip));
    await element('sessionRows').events.click({
      target: {closest: s => s === '[data-details]' ? {dataset: {details: '1'}} : null},
    });
    assert(element('sessionDetailsBody').innerHTML.includes(location));
    assert(element('sessionDetailsBody').innerHTML.includes('Client Public IP'));
    assert(element('sessionDetailsBody').innerHTML.includes('Client Local IP'));
    assert(element('sessionDetailsBody').innerHTML.includes('Connection Type'));
    buttons[1].events.click();
    assert.equal(exports[exports.length - 1][2][0][4], location);
  }
  row.client_public_ip = '73.1.2.3';
  row.client_location = '<script>city</script>, Texas, US';
}
(async () => {
  await check('UTC', '2026-10-29 07:02:03 UTC');
  await check('Asia/Seoul', '2026-10-29 16:02:03 Asia/Seoul');
  console.log('Compact list, escaped metadata, grouped details, CSV/PDF data and UTC/timezone display verified.');
})().catch(error => { console.error(error); process.exit(1); });
"""
        path = Path(__file__).resolve().parents[1] / "static" / "js" / "sessions.js"
        result = subprocess.run(
            ["node", "-", str(path)], input=script.encode("utf-8"), capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
