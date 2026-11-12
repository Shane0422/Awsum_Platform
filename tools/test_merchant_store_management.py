"""Merchant Store Management (routers.merchant_store): the Platform Store screen / processing in the Merchant scope.

Isolated SQLite app (tools.test_common_app_navigation fixture): Merchant 10 (MER26ABCD) with active Store 10
(STR26ABCD) and its Primary Contact; Merchant 11 (MER26EFGH) is another tenant. Added here: Store 12 (Inactive, Merchant
10) and Store 11 (Merchant 11) with a contact. Covers the field allow-list (editable / protected 403 / hidden 403 /
unexpected 422), tenant isolation, Inactive view-only, feature activation limits, Store Logo and Business Contact
ownership, the Store login denial and the unchanged Platform Store Management.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.models_admin.business_contact import BusinessContact
from backend.models_admin.merchant_feature import MerchantFeatureChoice
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.store import Store
from backend.routers import merchant_store
from backend.services import images
from backend.services.merchant_features import save_business_options
from tools import test_common_app_navigation
from tools import test_image_components as browser
from tools.test_master_images import image_bytes

API = "/merchant/api"
SECRETS = ("SECRET-NOTE", "SECRET-MEMO", "SECRET-REMARK", "SL-SECRET1")


# The exact payload the common Store form sends in the Merchant scope (asserted by the browser test below).
FORM_PAYLOAD = {
    "store_name": "Form Save", "business_type": "", "operation_type": "", "zip": "", "city": "", "state": "",
    "address_line1": "", "address_line2": "", "country": "United States", "default_tax_rate": "",
    "timezone": "America/New_York", "receipt_store_name": "", "receipt_phone": "", "receipt_email": "",
    "receipt_website_url": "", "receipt_message": "", "opt_customer": "off", "customer_scope": "", "opt_loyalty": "off",
    "loyalty_scope": "", "opt_time_attendance": "off", "opt_inventory": "off", "opt_location_management": "off",
    "opt_accounting": "off", "opt_use_color": "off", "opt_use_modifier": "on", "opt_use_bottle_deposit": "off",
    "opt_use_sugar_tax": "off", "opt_use_output_device": "on",
}


def inline_page(html: str, head_script: str) -> str:
    """The rendered page with its /static scripts and stylesheets inlined (runs offline in an iframe)."""
    def asset(match, tag):
        file = browser.ROOT / match.group(1).split("?")[0].lstrip("/")
        if not file.is_file() or file.suffix not in (".js", ".css"):
            return ""
        text = file.read_text(encoding="utf-8")
        return "<script>" + text.replace("</script", "<\\/script") + "</script>" if tag == "script" \
            else f"<style>{text}</style>"
    html = re.sub(r'<script\b[^>]*\bsrc="([^"]+)"[^>]*>\s*</script>', lambda m: asset(m, "script"), html)
    html = re.sub(r'<link\b[^>]*href="([^"]+)"[^>]*>', lambda m: asset(m, "style"), html)
    return html.replace("<head>", f"<head><script>{head_script}</script>", 1)


class MerchantStoreManagementTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def setUp(self):
        test_common_app_navigation.CommonAppNavigationTests.setUp(self)
        self.app.include_router(merchant_store.router)
        temp = tempfile.TemporaryDirectory(prefix="awsum-merchant-store-")
        self.addCleanup(temp.cleanup)
        storage = patch.object(images, "image_storage_root", return_value=Path(temp.name))
        storage.start()
        self.addCleanup(storage.stop)
        with self.factory() as db:
            store = db.get(Store, 10)
            store.c_notes, store.c_memo, store.c_remark = SECRETS[:3]
            store.c_store_license_key = SECRETS[3]
            store.n_license_count = 3
            store.c_timezone = "America/New_York"
            db.add(Store(i_store_id=12, i_merchant_id=10, c_store_code="STR26INAC", c_store_name="Inactive One",
                         c_status="inactive"))
            db.add(Store(i_store_id=11, i_merchant_id=11, c_store_code="STR26OTHR", c_store_name="Other Tenant Store",
                         c_status="active"))
            db.flush()
            db.add(BusinessContact(c_owner_type="STORE", i_owner_id=11, c_contact_role="PRIMARY", c_first_name="Other",
                                   c_last_name="Tenant", c_email="other@example.test", b_is_primary=True,
                                   c_status="active"))
            db.add(BusinessContact(c_owner_type="STORE", i_owner_id=12, c_contact_role="PRIMARY", c_first_name="Idle",
                                   c_last_name="Store", c_email="idle@example.test", b_is_primary=True,
                                   c_status="active"))
            db.commit()
            self.other_contact = db.query(BusinessContact).filter_by(c_owner_type="STORE", i_owner_id=11).one().i_contact_id
            self.idle_contact = db.query(BusinessContact).filter_by(c_owner_type="STORE", i_owner_id=12).one().i_contact_id
            self.own_contact = db.query(BusinessContact).filter_by(c_owner_type="STORE", i_owner_id=10).one().i_contact_id

    def login(self, scope):
        test_common_app_navigation.CommonAppNavigationTests.login(self, scope)

    def store(self, store_id=10) -> Store:
        with self.factory() as db:
            row = db.get(Store, store_id)
            db.expunge(row)
            return row

    def put(self, changes, store_id=10):
        return self.client.put(f"{API}/store/{store_id}", json=changes)

    # ---- menu / page ----
    def test_menu_only_for_merchant_and_page_reuses_the_platform_store_screen(self):
        self.login("MERCHANT")
        dashboard = self.client.get("/merchant/dashboard/standard").text
        menu = dashboard.split('id="mega-business"', 1)[1].split("</section>", 1)[0]
        self.assertIn('href="/merchant/business/stores"', menu)
        page = self.client.get("/merchant/business/stores")
        self.assertEqual(page.status_code, 200, page.text[:300])
        self.assertIn('class="platform-shell"', page.text)
        self.assertIn('"api_base": "/merchant/api"', page.text)  # the common Store screen in the Merchant scope
        self.assertIn('id="masterListTable"', page.text)
        for foreign in ("MER26EFGH", "Other Merchant", "STR26OTHR", "Other Tenant Store", *SECRETS):
            self.assertNotIn(foreign, page.text)  # seed data: this Merchant only, no Platform internal values
        platform_page = self.client.get("/platform/master/store")
        self.assertNotEqual(platform_page.status_code, 200)
        # Store login: no Store Management menu, page or API.
        self.login("STORE")
        dashboard = self.client.get("/merchant/dashboard/standard").text
        self.assertNotIn('id="mega-business"', dashboard)
        self.assertNotIn('/merchant/business/stores', dashboard)
        self.assertEqual(self.client.get("/merchant/business/stores").status_code, 403)
        self.assertEqual(self.client.get(f"{API}/stores").status_code, 403)
        self.assertEqual(self.client.get(f"{API}/store/10").status_code, 403)
        self.assertEqual(self.put({"store_name": "By Store"}).status_code, 403)

    # ---- tenant isolation and hidden values ----
    def test_lists_and_reads_only_its_own_stores_without_platform_internal_values(self):
        self.login("MERCHANT")
        for query in ("", "?page=1&sort=store_asc"):
            body = self.client.get(f"{API}/stores{query}")
            self.assertEqual(body.status_code, 200, body.text)
            rows = body.json()["items"] if query else body.json()
            self.assertEqual(sorted(row["store_id"] for row in rows), [10, 12])
            for secret in SECRETS:
                self.assertNotIn(secret, body.text)
            for key in merchant_store.MERCHANT_STORE_HIDDEN:
                self.assertTrue(all(key not in row for row in rows), key)
        # Filters cannot widen the scope.
        self.assertEqual(self.client.get(f"{API}/stores?merchant_id=11").json()[0]["merchant_id"], 10)
        detail = self.client.get(f"{API}/store/10")
        self.assertEqual(detail.status_code, 200, detail.text)
        item = detail.json()
        self.assertEqual((item["store_code"], item["license_count"], item["read_only"]), ("STR26ABCD", 3, False))
        self.assertNotIn("store_license_key", item)
        for secret in SECRETS:
            self.assertNotIn(secret, detail.text)
        for foreign in (11, ADMIN_STORE_ID, 999):
            self.assertEqual(self.client.get(f"{API}/store/{foreign}").status_code, 404)
            self.assertEqual(self.put({"store_name": "Hijack"}, foreign).status_code, 404)
        self.assertEqual(self.store(11).c_store_name, "Other Tenant Store")

    # ---- list: scope, Store Name / Code search, sorts ----
    def test_list_search_and_sort_stay_inside_the_login_merchant(self):
        with self.factory() as db:
            db.add(Store(i_store_id=13, i_merchant_id=10, c_store_code="STR26ZZZZ", c_store_name="Alpha Branch",
                         c_status="active"))
            db.commit()
        self.login("MERCHANT")
        ids = lambda query: [row["store_id"] for row in self.client.get(f"{API}/stores?{query}").json()["items"]]
        # Default sort: Store Name (A-Z).
        listed = self.client.get(f"{API}/stores?page=1").json()
        self.assertEqual(listed["sort"], "store_asc")
        self.assertEqual([row["store_id"] for row in listed["items"]], [13, 12, 10])  # Alpha, Inactive, Store <One>
        self.assertEqual(ids("page=1&sort=store_desc"), [10, 12, 13])
        # Search covers Store Name / Store Code of its own Stores only.
        self.assertEqual(ids("page=1&q=alpha"), [13])
        self.assertEqual(ids("page=1&q=STR26INAC"), [12])
        for foreign in ("Other Tenant", "STR26OTHR", "MER26EFGH", "Other Merchant", "Merchant"):
            self.assertEqual(ids(f"page=1&q={foreign}"), [], foreign)  # no other tenant, no Merchant / Agency match
        # Request Merchant / Agency parameters cannot widen or change the scope.
        self.assertEqual(sorted(ids("page=1&merchant_id=11&agency_id=1")), [10, 12, 13])
        self.assertEqual(sorted(ids("page=1&merchant_id=11&q=Other")), [])
        for sort in ("merchant_asc", "agency_desc", "bogus"):
            self.assertEqual(self.client.get(f"{API}/stores?page=1&sort={sort}").status_code, 400, sort)
        page = self.client.get("/merchant/business/stores").text
        self.assertIn("Merchant: Merchant &lt;One&gt;", page)  # the login's Merchant under the title (escaped)
        self.assertIn('"sorts": ["store_asc", "store_desc", "newest", "oldest"]', page)
        # Platform search is unchanged (Merchant / Agency names still match there).

    # ---- allow-list ----
    def test_allowed_fields_save_and_platform_fields_are_refused(self):
        self.login("MERCHANT")
        saved = self.put({"store_name": "Renamed", "operation_type": "Branch", "timezone": "America/Chicago",
                          "default_tax_rate": "6.0000", "receipt_message": "Thanks", "address_line1": "1 Main",
                          # protected fields with their saved values pass (the common form sends the whole form)
                          "merchant_id": "10", "store_status": "active", "store_code": "str26abcd",
                          "license_count": "3", "tax_source": ""})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn("temporary_password", saved.text)
        store = self.store()
        self.assertEqual((store.c_store_name, store.c_operation_type, store.c_timezone, store.c_default_tax_rate,
                          store.c_receipt_message, store.c_address_line1),
                         ("Renamed", "Branch", "America/Chicago", "6.0000", "Thanks", "1 Main"))
        form = self.put(FORM_PAYLOAD)  # the whole Merchant-scope form passes the allow-list
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(self.store().c_store_name, "Form Save")
        before = self.store()
        refused = {
            403: [{"merchant_id": 11}, {"installed_by_agency_id": 1}, {"store_status": "inactive"},
                  {"status": "inactive"}, {"store_code": "STR26XXXX"}, {"license_count": 99},
                  {"store_license_status": "SUSPENDED"}, {"license_expire_date": "2030-01-01"},
                  # Platform internal values: refused whatever the value (never compared, so never probed)
                  {"notes": SECRETS[0]}, {"memo": ""}, {"remark": "x"}, {"store_license_key": SECRETS[3]},
                  {"max_user_count": 5}, {"contract_end_date": "2030-01-01"}, {"dashboard_type": "STANDARD"},
                  {"is_test": 0}],
            422: [{"unexpected": 1}, {"_agency_optional": True}, {"channel_type": "POS"}, {"store_name": " "}],
        }
        for status, payloads in refused.items():
            for payload in payloads:
                with self.subTest(payload=payload):
                    response = self.put({"store_name": "Must not save", **payload})
                    self.assertEqual(response.status_code, status, response.text)
        after = self.store()
        for column in ("c_store_name", "i_merchant_id", "c_status", "c_store_code", "n_license_count", "c_notes",
                       "c_memo", "c_remark", "c_store_license_key", "i_installed_by_agency_id"):
            self.assertEqual(getattr(after, column), getattr(before, column), column)

    def test_no_create_delete_or_deactivate_for_merchants(self):
        self.login("MERCHANT")
        self.assertIn(self.client.post(f"{API}/store", json={"store_name": "New"}).status_code, (404, 405))
        self.assertIn(self.client.post(f"{API}/stores", json={"store_name": "New"}).status_code, (404, 405))
        self.assertIn(self.client.delete(f"{API}/store/10").status_code, (404, 405))
        for request in (lambda: self.client.delete("/platform/store/10"),
                        lambda: self.client.put("/platform/store/10", json={"store_status": "inactive"}),
                        lambda: self.client.post("/platform/store", json={"store_name": "X", "merchant_id": 10}),
                        lambda: self.client.get("/platform/store/10")):
            self.assertEqual(request().status_code, 403)
        self.assertEqual(self.store().c_status, "active")

    # ---- Inactive Store ----
    def test_inactive_store_is_view_only(self):
        self.login("MERCHANT")
        item = self.client.get(f"{API}/store/12").json()
        self.assertTrue(item["read_only"])
        self.assertEqual(self.put({"store_name": "Changed"}, 12).status_code, 403)
        self.assertEqual(self.client.get(f"{API}/business-contacts/STORE/12").status_code, 200)
        self.assertEqual(self.client.post(f"{API}/business-contacts/STORE/12", json={
            "first_name": "New", "email": "n@example.test"}).status_code, 403)
        self.assertEqual(self.client.put(f"{API}/business-contact/{self.idle_contact}",
                                         json={"first_name": "X"}).status_code, 403)
        self.assertEqual(self.store(12).c_store_name, "Inactive One")

    # ---- features ----
    def test_features_outside_the_contract_or_merchant_choice_cannot_be_turned_on(self):
        self.login("MERCHANT")
        access = self.client.get(f"{API}/store/10").json()["option_access"]
        self.assertFalse(access["opt_location_management"]["allowed"])  # ADDON, no Business Option
        self.assertIn("contract", access["opt_location_management"]["reason"])
        self.assertTrue(access["opt_use_modifier"]["allowed"])  # BASE
        self.assertEqual(self.put({"opt_location_management": "on"}).status_code, 403)
        self.assertEqual(self.put({"opt_location_management": "off"}).status_code, 200)  # unchanged OFF is fine
        with self.factory() as db:
            save_business_options(db, 10, {"location_management": True}, 1)
            db.commit()
        self.assertEqual(self.put({"opt_location_management": "on"}).status_code, 200)
        self.assertTrue(self.store().b_opt_use_shelf_location)
        # Merchant Feature Entitlement OFF: a saved ON stays (re-sending it passes), a new ON is refused.
        with self.factory() as db:
            db.add(MerchantFeatureChoice(i_merchant_id=10, c_feature_key="location_management", b_enabled=False))
            db.add(MerchantFeatureChoice(i_merchant_id=10, c_feature_key="color", b_enabled=False))
            db.commit()
        item = self.client.get(f"{API}/store/10").json()
        self.assertFalse(item["option_access"]["opt_location_management"]["effective"])  # enforcement unchanged
        self.assertEqual(self.put({"opt_location_management": "on"}).status_code, 200)
        self.assertTrue(self.store().b_opt_use_shelf_location)
        self.assertEqual(self.put({"opt_use_color": "on"}).status_code, 403)
        self.assertFalse(bool(self.store().b_opt_use_color))
        self.assertEqual(self.client.get("/merchant/api/master-group/location").status_code, 403)

    # ---- Primary Contact ----
    def test_contacts_are_limited_to_its_own_active_stores(self):
        self.login("MERCHANT")
        own = self.client.get(f"{API}/business-contacts/STORE/10")
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual([row["contact_id"] for row in own.json()], [self.own_contact])
        # The common service end to end. (SQLite applies the one-Primary-per-owner index to every row, so this
        # isolated fixture keeps one contact per Store: replace the existing one.)
        self.assertEqual(self.client.delete(f"{API}/business-contact/{self.own_contact}").status_code, 200)
        created = self.client.post(f"{API}/business-contacts/STORE/10", json={
            "first_name": "Ops", "last_name": "Lead", "email": "ops@example.test", "contact_role": "OPERATIONS"})
        self.assertEqual(created.status_code, 200, created.text)
        new_id = created.json()["contact_id"]
        self.assertEqual(self.client.put(f"{API}/business-contact/{new_id}", json={
            "first_name": "Ops", "last_name": "Head", "email": "ops@example.test",
            "contact_role": "PRIMARY"}).status_code, 200)
        self.assertEqual(self.client.post(f"{API}/business-contact/{new_id}/primary").status_code, 200)
        self.assertEqual(self.client.put(f"{API}/business-contacts/STORE/10/order",
                                         json={"contact_ids": [new_id]}).status_code, 200)
        with self.factory() as db:
            primary = db.get(BusinessContact, new_id)
            self.assertEqual((primary.c_owner_type, primary.i_owner_id, primary.b_is_primary, primary.c_last_name),
                             ("STORE", 10, True, "Head"))
        # Another Merchant's Store / contact, another owner type: not found, nothing changed.
        for path in (f"{API}/business-contacts/STORE/11", f"{API}/business-contacts/MERCHANT/10",
                     f"{API}/business-contacts/AGENCY/1"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
            self.assertEqual(self.client.post(path, json={"first_name": "X"}).status_code, 404, path)
        for call in (lambda: self.client.put(f"{API}/business-contact/{self.other_contact}", json={"first_name": "X"}),
                     lambda: self.client.post(f"{API}/business-contact/{self.other_contact}/primary"),
                     lambda: self.client.delete(f"{API}/business-contact/{self.other_contact}")):
            self.assertEqual(call().status_code, 404)
        with self.factory() as db:
            self.assertEqual(db.get(BusinessContact, self.other_contact).c_first_name, "Other")
            merchant_contact = db.query(BusinessContact).filter_by(c_owner_type="MERCHANT").one().i_contact_id
        self.assertEqual(self.client.delete(f"{API}/business-contact/{merchant_contact}").status_code, 404)
        # The existing Store Admin login is unchanged by contact edits (it is created only by Platform activation).
        with self.factory() as db:
            self.assertEqual(db.query(PlatformUser).filter_by(i_store_id=10).one().c_email, "primary@example.test")

    # ---- Store Logo ----
    def test_store_logo_uses_the_common_uploader_for_its_own_store_only(self):
        self.login("MERCHANT")

        def save(store_id, data=None, remove=False, changes=None):
            return self.client.put(f"{API}/store/{store_id}/with-image", data={
                "payload": json.dumps({"store_name": "Logo Store", **(changes or {})}),
                "remove_image": str(remove).lower()},
                files={"image": ("logo.png", data, "image/png")} if data is not None else None)

        saved = save(10, image_bytes())
        self.assertEqual(saved.status_code, 200, saved.text)
        path = saved.json()["image_path"]
        self.assertTrue(path.endswith("/store/STR26ABCD/images/logo/logo.webp"), path)
        self.assertTrue(images.resolve_image_file(path).is_file())
        self.assertEqual(self.client.get(f"{API}/store/10").json()["image_url"], saved.json()["image_url"])
        self.assertEqual(save(10, image_bytes("red"), changes={"notes": "x"}).status_code, 403)
        self.assertEqual(save(11, image_bytes()).status_code, 404)
        self.assertIsNone(self.store(11).c_logo_url)
        self.assertEqual(save(12, image_bytes()).status_code, 403)  # Inactive: view only
        removed = save(10, remove=True)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertIsNone(self.store().c_logo_url)

    # ---- Platform unchanged ----
    def test_platform_store_management_is_unchanged(self):
        detail = self.client.get("/platform/store/10").json()
        self.assertEqual((detail["notes"], detail["memo"], detail["remark"], detail["store_license_key"]),
                         SECRETS)
        rows = self.client.get("/platform/stores").json()
        self.assertEqual(sorted(row["store_id"] for row in rows), [10, 11, 12])
        self.assertEqual(next(r for r in rows if r["store_id"] == 10)["notes"], SECRETS[0])
        moved = self.client.put("/platform/store/12", json={"merchant_id": 11, "notes": "Platform note"})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual((self.store(12).i_merchant_id, self.store(12).c_notes), (11, "Platform note"))
        self.assertEqual(self.client.get(f"{API}/stores").status_code, 403)  # Platform login: not the Merchant API
        found = self.client.get("/platform/stores?page=1&q=Other Merchant").json()["items"]
        self.assertEqual(sorted(row["store_id"] for row in found), [11, 12])  # Platform search still matches Merchant names
        self.assertEqual(self.client.get("/platform/stores?page=1").json()["sort"], "merchant_asc")

    # ---- the common Store screen in the Merchant scope (headless Chrome, API mocked) ----
    @unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
    def test_store_screen_in_the_merchant_scope(self):
        self.login("MERCHANT")
        rows = self.client.get(f"{API}/stores?page=1&sort=store_asc").json()
        mock = ("window.__errors=[];addEventListener('error',e=>__errors.push(String(e.message)));"
                f"window.__rows={json.dumps(rows)};" + r"""
window.requests=[];
const J=b=>new Response(typeof b==='string'?b:JSON.stringify(b),{headers:{'Content-Type':'application/json'}});
window.fetch=async(path,options={})=>{const url=new URL(path,'http://x');
 requests.push({path:url.pathname,search:url.search,method:options.method||'GET',body:options.body});
 if(url.pathname==='/merchant/api/stores')return J(__rows);
 if(url.pathname.startsWith('/merchant/api/business-contacts/'))return J('[]');
 return J({success:true,image_path:null,image_url:null});};""")
        page = inline_page(self.client.get("/merchant/business/stores").text, mock)
        expected = json.dumps(sorted(FORM_PAYLOAD))
        script = "const page=" + json.dumps(page).replace("</", "<\\/") + f";\nconst expectedKeys={expected};\n" + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const wait=async(f,m)=>{for(let i=0;i<100;i++){try{if(f())return;}catch(e){}await pause(50);}throw Error('timeout: '+m);};
(async()=>{
 const frame=document.createElement('iframe');frame.style.width='1400px';frame.style.height='900px';frame.srcdoc=page;
 const loaded=new Promise(r=>frame.addEventListener('load',r,{once:true}));document.body.appendChild(frame);await loaded;
 const doc=frame.contentDocument,win=frame.contentWindow,f=n=>doc.getElementById('field_'+n);
 await wait(()=>doc.querySelectorAll('#masterListBody tr').length===2,'its two Stores listed');
 const headers=[...doc.querySelectorAll('#masterListHeader th')].map(th=>th.textContent.trim()).filter(Boolean);
 assert(JSON.stringify(headers)==='["Store ID","Store Code","Store Name","Status","Created Date"]','no Agency / Merchant column: '+headers);
 const shown=el=>!!el&&el.getClientRects().length>0;
 assert(!shown(doc.getElementById('storeAgencyCombo'))&&!shown(doc.getElementById('storeMerchantCombo')),'no Agency / Merchant filter');
 for(const id of ['masterSearch','masterStatusFilter','storeSortSelect','storePageSize'])assert(shown(doc.getElementById(id)),id+' kept');
 assert(doc.getElementById('masterSearch').placeholder==='Search Store name or code...','Store Name / Code search');
 const sorts=[...doc.getElementById('storeSortSelect').options].map(o=>o.value);
 assert(JSON.stringify(sorts)==='["store_asc","store_desc","newest","oldest"]'&&doc.getElementById('storeSortSelect').value==='store_asc','sorts: '+sorts);
 const first=new URLSearchParams(win.requests.find(r=>r.path==='/merchant/api/stores').search);
 assert(first.get('sort')==='store_asc'&&!first.has('merchant_id')&&!first.has('agency_id'),'default Store Name (A-Z), no Merchant / Agency parameter');
 assert(/Merchant: /.test(doc.getElementById('masterListHead').textContent),'Merchant name under the title');
 const gone=id=>{const el=doc.getElementById(id);return !el||el.classList.contains('d-none');};
 for(const id of ['btnListAdd','btnListDelete','btnNewRecord','btnDeleteRecord','storeDevicesCol','storeSplitter'])
   assert(gone(id),id+' hidden (Platform only)');
 [...doc.querySelectorAll('#masterListBody tr')].find(tr=>tr.textContent.includes('STR26ABCD')).click();await pause(150);
 doc.getElementById('btnListEdit').click();await pause(300);
 assert(!f('notes')&&!f('store_license_key'),'Platform internal fields are not rendered');
 assert(f('store_code').readOnly&&f('license_count').readOnly&&f('status').disabled&&f('merchant_id').disabled,'Platform fields read-only');
 assert(!f('store_name').readOnly&&!f('receipt_message').readOnly,'operating fields editable');
 assert(f('installed_by_agency_id').disabled,'Agency stays read-only in the Store detail');
 assert(f('opt_location_management').disabled&&/contract/.test(f('opt_location_management').title),'not contracted: disabled with the reason');
 assert(!f('opt_use_modifier').disabled,'contracted Item Feature can be changed');
 const tabs=[...doc.querySelectorAll('#masterEditModal .merchant-tab-btn')].map(b=>b.textContent.trim());
 assert(!tabs.includes('Notes')&&tabs.includes('License'),'Notes tab hidden, License shown');
 f('store_name').value='Browser Renamed';f('store_name').dispatchEvent(new Event('input',{bubbles:true}));
 doc.getElementById('btnModalSave').click();
 await wait(()=>win.requests.some(r=>r.method==='PUT'),'save');
 const put=win.requests.find(r=>r.method==='PUT');
 assert(put.path==='/merchant/api/store/10/with-image','Merchant save route');
 const keys=Object.keys(JSON.parse(put.body.get('payload'))).sort();
 assert(JSON.stringify(keys)===JSON.stringify(expectedKeys),'only the allow-listed form fields are sent: '+keys);
 assert(!win.requests.some(r=>r.path.startsWith('/platform')),'no Platform API call');
 assert(win.__errors.length===0,'no script errors: '+win.__errors);
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack});});
"""
        self.assertTrue(set(FORM_PAYLOAD) <= merchant_store.MERCHANT_STORE_EDITABLE)
        self.run_browser(script)


    @unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
    def test_platform_store_list_is_unchanged(self):
        rows = self.client.get("/platform/stores?page=1&sort=merchant_asc").json()
        mock = ("window.__errors=[];addEventListener('error',e=>__errors.push(String(e.message)));"
                f"window.__rows={json.dumps(rows)};" + r"""
window.requests=[];
const J=b=>new Response(typeof b==='string'?b:JSON.stringify(b),{headers:{'Content-Type':'application/json'}});
window.fetch=async(path,options={})=>{const url=new URL(path,'http://x');requests.push({path:url.pathname,search:url.search});
 if(url.pathname==='/platform/stores')return J(__rows);return J('[]');};""")
        page = inline_page(self.client.get("/platform/master/store").text, mock)
        script = "const page=" + json.dumps(page).replace("</", "<\\/") + ";\n" + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
const wait=async(f,m)=>{for(let i=0;i<100;i++){try{if(f())return;}catch(e){}await new Promise(r=>setTimeout(r,50));}throw Error('timeout: '+m);};
(async()=>{
 const frame=document.createElement('iframe');frame.style.width='1400px';frame.style.height='900px';frame.srcdoc=page;
 const loaded=new Promise(r=>frame.addEventListener('load',r,{once:true}));document.body.appendChild(frame);await loaded;
 const doc=frame.contentDocument,win=frame.contentWindow,shown=el=>!!el&&el.getClientRects().length>0;
 await wait(()=>doc.querySelectorAll('#masterListBody tr').length===3,'every Store listed');
 const headers=[...doc.querySelectorAll('#masterListHeader th')].map(th=>th.textContent.trim()).filter(Boolean);
 assert(JSON.stringify(headers)==='["Agency","Merchant","Store ID","Store Code","Store Name","Status","Created Date"]','Platform columns: '+headers);
 for(const id of ['storeAgencyCombo','storeMerchantCombo','btnListAdd'])assert(shown(doc.getElementById(id)),id+' still shown');
 assert(doc.getElementById('storeSortSelect').options.length===8&&doc.getElementById('storeSortSelect').value==='merchant_asc','Platform sorts');
 assert(new URLSearchParams(win.requests[0].search).get('sort')==='merchant_asc','Platform default sort');
 assert(!/Merchant: /.test(doc.getElementById('masterListHead').textContent),'Platform subtitle unchanged');
 assert(win.__errors.length===0,'no script errors: '+win.__errors);
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
