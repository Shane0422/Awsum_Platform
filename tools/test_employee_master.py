"""Employee Management STEP 1A: Merchant Employee Master + Store assignments through the common Master Group flow.

Isolated SQLite app (tools.test_common_app_navigation fixture): Merchant 10 (MER26ABCD) with active Store 10; added
here Store 13 (active) and Store 12 (Inactive) of Merchant 10, and Store 11 of Merchant 11 (another tenant).
Covers the menu / page / scope, per-Merchant numbering (and its retry), the field allow-list, tenant isolation,
assignments (same Merchant, Inactive Store view only, one open per Store, one primary, dates), termination (every
assignment ends, Inactive Stores included, history kept), rehire, photo storage / read permission, export, the
database constraints SQLite enforces, and the screen (headless Chrome).
"""
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from sqlalchemy.exc import IntegrityError

from backend.models_admin.employee import Employee, EmployeeStore
from backend.models_admin.portal_role import PortalRole
from backend.models_admin.store import Store
from backend.routers import images as image_routes
from backend.services import employees as emp
from backend.services import images
from tools import test_common_app_navigation
from tools import test_image_components as browser
from tools.test_merchant_store_management import inline_page

API = "/merchant/api/master-group/employee"


def png(color="blue") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


class EmployeeMasterTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def setUp(self):
        test_common_app_navigation.CommonAppNavigationTests.setUp(self)
        temp = tempfile.TemporaryDirectory(prefix="awsum-employee-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        storage = patch.object(images, "image_storage_root", return_value=self.root)
        storage.start()
        self.addCleanup(storage.stop)
        self.app.include_router(image_routes.router)
        self.app.dependency_overrides[image_routes.get_db] = \
            self.app.dependency_overrides[test_common_app_navigation.platform_store.get_db]
        with self.factory() as db:
            db.add_all([
                Store(i_store_id=13, i_merchant_id=10, c_store_code="STR26BBBB", c_store_name="Second Store",
                      c_status="active"),
                Store(i_store_id=12, i_merchant_id=10, c_store_code="STR26INAC", c_store_name="Idle Store",
                      c_status="inactive"),
                Store(i_store_id=11, i_merchant_id=11, c_store_code="STR26OTHR", c_store_name="Other Tenant",
                      c_status="active"),
            ])
            db.commit()
            self.foreign = emp.create_employee(db, 11, {"first_name": "Other", "last_name": "Tenant"}, 1)

    def login(self, scope):
        test_common_app_navigation.CommonAppNavigationTests.login(self, scope)

    def create(self, **values):
        response = self.client.post(API, json={"first_name": "Minji", "last_name": "Kim", **values})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["item"]

    def assign(self, employee_id, **values):
        return self.client.post(f"{API}/{employee_id}/assignments", json={"store_id": 10, **values})

    def row(self, model, pk):
        with self.factory() as db:
            item = db.get(model, pk)
            db.expunge(item)
            return item

    # ---- menu / page / scope ----
    def test_menu_page_and_merchant_only_scope(self):
        self.login("MERCHANT")
        dashboard = self.client.get("/merchant/dashboard/standard").text
        self.assertIn('href="/merchant/business/employees"', dashboard.split('id="mega-business"', 1)[1])
        page = self.client.get("/merchant/business/employees")
        self.assertEqual(page.status_code, 200, page.text[:300])
        self.assertIn('data-entity="employee"', page.text)
        self.assertIn('data-api-base="/merchant/api/master-group"', page.text)
        self.assertIn('id="mgEmpStoreFilter"', page.text)
        options = self.client.get("/merchant/api/master-group/options").json()
        self.assertEqual([r["code"] for r in options["pos_roles"]], ["MANAGER", "CASHIER", "SERVER", "KITCHEN"])
        self.assertEqual(sorted(s["id"] for s in options["stores"]), [10, 12, 13])
        self.login("STORE")
        self.assertNotIn("/merchant/business/employees", self.client.get("/merchant/dashboard/standard").text)
        for call in (lambda: self.client.get("/store/master-group/employees"),
                     lambda: self.client.get("/store/api/master-group/employee"),
                     lambda: self.client.post("/store/api/master-group/employee", json={"first_name": "X"}),
                     lambda: self.client.get(API)):
            self.assertEqual(call().status_code, 403)

    def test_platform_scope_is_refused(self):
        # The fixture's initial client is the Platform Admin.
        for call in (lambda: self.client.get("/platform/api/master-group/employee"),
                     lambda: self.client.post("/platform/api/master-group/employee", json={"first_name": "X"}),
                     lambda: self.client.get("/platform/master-group/employees")):
            self.assertEqual(call().status_code, 403)

    # ---- create / numbering / field policy ----
    def test_codes_and_pos_numbers_are_issued_per_merchant(self):
        self.login("MERCHANT")
        first, second = self.create(), self.create(first_name="Joon", last_name="Lee")
        self.assertEqual([(i["code"], i["pos_number"]) for i in (first, second)], [("EMP00001", 1001), ("EMP00002", 1002)])
        self.assertEqual((self.foreign["code"], self.foreign["pos_number"]), ("EMP00001", 1001))  # its own sequence
        # A concurrent create that took the same numbers first: the unique constraint + retry issue the next ones.
        real = emp._next_numbers
        calls = []

        def stale(db, merchant_id):
            calls.append(1)
            return ("EMP00002", 1002) if len(calls) == 1 else real(db, merchant_id)
        with patch.object(emp, "_next_numbers", stale):
            third = self.create(first_name="Ara")
        self.assertEqual((third["code"], third["pos_number"], len(calls)), ("EMP00003", 1003, 2))

    def test_field_allow_list_protected_fields_and_validation(self):
        self.login("MERCHANT")
        item = self.create(display_name="Minji K", phone="703-555-0101", email="minji@example.test",
                           hire_date="2026-11-01")
        self.assertEqual((item["display_name"], item["hire_date"], item["status"]), ("Minji K", "2026-11-01", "active"))
        saved = self.client.put(f"{API}/{item['id']}", json={
            "first_name": "Minji", "last_name": "Park", "status": "inactive",
            "code": "EMP00001", "pos_number": 1001, "merchant_id": 10})  # unchanged protected values pass
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual((saved.json()["item"]["last_name"], saved.json()["item"]["status"]), ("Park", "inactive"))
        cases = {
            403: [{"code": "EMP09999"}, {"pos_number": 5}, {"merchant_id": 11}, {"termination_date": "2026-12-01"}],
            422: [{"unexpected": 1}, {"i_merchant_id": 11}, {"c_pin_hash": "x"}, {"status": "terminated"},
                  {"status": "bogus"}, {"first_name": "", "last_name": ""}, {"email": "not-an-email"},
                  {"hire_date": "31/31/2026"}],
        }
        for status, payloads in cases.items():
            for payload in payloads:
                with self.subTest(payload=payload):
                    self.assertEqual(self.client.put(f"{API}/{item['id']}", json=payload).status_code, status)
        self.assertEqual(self.row(Employee, item["id"]).c_employee_code, "EMP00001")
        self.assertEqual(self.client.post(API, json={"first_name": "X", "code": "EMP00077"}).status_code, 403)
        self.assertEqual(self.client.post(API, json={"last_name": ""}).status_code, 422)
        self.assertEqual(self.client.delete(f"{API}/{item['id']}").status_code, 409)  # never deleted
        self.assertIsNotNone(self.row(Employee, item["id"]))

    def test_other_merchant_employees_are_invisible(self):
        self.login("MERCHANT")
        own = self.create()
        listed = self.client.get(API).json()
        self.assertEqual([i["id"] for i in listed["items"]], [own["id"]])
        fid = self.foreign["id"]
        for call in (lambda: self.client.put(f"{API}/{fid}", json={"first_name": "X"}),
                     lambda: self.client.get(f"{API}/{fid}/assignments"),
                     lambda: self.client.post(f"{API}/{fid}/assignments", json={"store_id": 10}),
                     lambda: self.client.post(f"{API}/{fid}/terminate", json={"termination_date": "2026-12-01"})):
            self.assertEqual(call().status_code, 404)
        self.assertEqual(self.row(Employee, fid).c_first_name, "Other")

    # ---- Store assignments ----
    def test_assignment_rules(self):
        self.login("MERCHANT")
        item = self.create(hire_date="2026-11-01")
        eid = item["id"]
        self.assertEqual(self.assign(eid, store_id=11).status_code, 422)   # another Merchant's Store
        self.assertEqual(self.assign(eid, store_id=12).status_code, 403)   # Inactive Store: view only
        self.assertEqual(self.assign(eid, pos_access=True).status_code, 422)  # POS access needs a POS Role
        self.assertEqual(self.assign(eid, pos_access=True, pos_role="OWNER").status_code, 422)
        self.assertEqual(self.assign(eid, start_date="2026-10-01").status_code, 422)  # before the Hire Date
        first = self.assign(eid, pos_access=True, pos_role="manager", primary=True, start_date="2026-11-02")
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual((first.json()["item"]["pos_role"], first.json()["item"]["primary"]), ("MANAGER", True))
        self.assertEqual(self.assign(eid).status_code, 409)  # one open assignment per Store
        second = self.assign(eid, store_id=13, pos_access=True, pos_role="CASHIER", primary=True).json()["item"]
        rows = {a["store_id"]: a for a in self.client.get(f"{API}/{eid}/assignments").json()["items"]}
        self.assertEqual((rows[10]["primary"], rows[13]["primary"]), (False, True))  # the new primary moved
        listed = self.client.get(API, params={"store_id": 13, "pos_role": "CASHIER"}).json()["items"]
        self.assertEqual([i["id"] for i in listed], [eid])
        self.assertEqual(listed[0]["primary_store"]["id"], 13)
        updated = self.client.put(f"{API}/{eid}/assignments/{second['id']}", json={"status": "inactive"})
        self.assertEqual((updated.status_code, updated.json()["item"]["primary"]), (200, False))
        self.assertEqual(self.client.put(f"{API}/{eid}/assignments/{second['id']}",
                                         json={"status": "ended"}).status_code, 422)  # End has its own command
        self.assertEqual(self.client.put(f"{API}/{eid}/assignments/{second['id']}",
                                         json={"store_id": 11}).status_code, 422)
        early = self.client.post(f"{API}/{eid}/assignments/{second['id']}/end", json={"end_date": "2026-01-01"})
        self.assertEqual(early.status_code, 422)  # before the start
        ended = self.client.post(f"{API}/{eid}/assignments/{second['id']}/end", json={})
        self.assertEqual((ended.status_code, ended.json()["item"]["status"]), (200, "ended"))
        self.assertEqual(self.client.put(f"{API}/{eid}/assignments/{second['id']}",
                                         json={"pos_access": False}).status_code, 409)  # history
        again = self.assign(eid, store_id=13)  # a new period at the same Store after the earlier one ended
        self.assertEqual(again.status_code, 201, again.text)
        # Hire Date cannot move after an open assignment start.
        self.assertEqual(self.client.put(f"{API}/{eid}", json={"hire_date": "2026-11-20"}).status_code, 422)
        with self.factory() as db:
            self.assertEqual(db.query(EmployeeStore).filter_by(i_employee_id=eid).count(), 3)  # history kept

    # ---- termination / rehire ----
    def test_termination_ends_every_assignment_and_rehire_does_not_restore_them(self):
        self.login("MERCHANT")
        item = self.create(hire_date="2026-11-01")
        eid = item["id"]
        self.assign(eid, pos_access=True, pos_role="CASHIER", primary=True, start_date="2026-11-05")
        with self.factory() as db:  # an assignment at a Store that became Inactive afterwards
            db.add(EmployeeStore(i_employee_id=eid, i_store_id=12, i_merchant_id=10, b_pos_access=True,
                                 c_pos_role="SERVER", c_status="active", d_start=date(2026, 11, 3)))
            db.commit()
        too_early = self.client.post(f"{API}/{eid}/terminate", json={"termination_date": "2026-11-04"})
        self.assertEqual(too_early.status_code, 422)  # before an open assignment start
        self.assertIn("Store assignment start", too_early.text)
        self.assertEqual(self.client.post(f"{API}/{eid}/terminate", json={"termination_date": "2026-10-01"}).status_code, 422)
        self.assertEqual(self.client.post(f"{API}/{eid}/terminate", json={}).status_code, 422)
        before = self.row(Employee, eid)
        done = self.client.post(f"{API}/{eid}/terminate", json={"termination_date": "2026-11-30",
                                                                "termination_reason": "Moved away"})
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual((done.json()["item"]["status"], done.json()["item"]["read_only"]), ("terminated", True))
        after = self.row(Employee, eid)
        self.assertEqual((after.d_termination_date, after.c_termination_reason), (date(2026, 11, 30), "Moved away"))
        self.assertEqual(after.i_pin_version, before.i_pin_version + 1)
        self.assertIsNone(after.c_pin_hash)
        with self.factory() as db:
            rows = db.query(EmployeeStore).filter_by(i_employee_id=eid).all()
            self.assertEqual(sorted((r.i_store_id, r.c_status, r.d_end, r.b_pos_access, r.b_primary) for r in rows),
                             [(10, "ended", date(2026, 11, 30), False, False),
                              (12, "ended", date(2026, 11, 30), False, False)])  # Inactive Store included
        for call in (lambda: self.client.put(f"{API}/{eid}", json={"first_name": "X"}),
                     lambda: self.assign(eid, store_id=13),
                     lambda: self.client.post(f"{API}/{eid}/terminate", json={"termination_date": "2026-12-01"})):
            self.assertEqual(call().status_code, 409)
        rehired = self.client.post(f"{API}/{eid}/reactivate", json={"hire_date": "2027-01-10"})
        self.assertEqual(rehired.status_code, 200, rehired.text)
        self.assertEqual((rehired.json()["item"]["status"], rehired.json()["item"]["termination_date"],
                          rehired.json()["item"]["hire_date"]), ("active", None, "2027-01-10"))
        statuses = [a["status"] for a in self.client.get(f"{API}/{eid}/assignments").json()["items"]]
        self.assertEqual(statuses, ["ended", "ended"])  # not restored
        self.assertEqual(self.client.post(f"{API}/{eid}/reactivate", json={}).status_code, 409)

    # ---- photo ----
    def test_photo_uses_the_merchant_path_and_only_its_merchant_reads_it(self):
        self.login("MERCHANT")
        created = self.client.post(f"{API}/with-image", data={"payload": json.dumps({"first_name": "Photo"})},
                                   files={"image": ("me.png", png(), "image/png")})
        self.assertEqual(created.status_code, 201, created.text)
        item = created.json()["item"]
        path = item["image_path"]
        self.assertEqual(path, f"/merchant/MER26ABCD/images/employee/{item['code']}.webp")
        self.assertTrue((self.root / "merchant" / "MER26ABCD" / "images" / "employee" / f"{item['code']}.webp").is_file())
        self.assertEqual(self.client.get(path).status_code, 200)
        upload = self.client.post(f"/api/images/merchant/MER26ABCD/employee?entity_key={item['code']}",
                                  files={"file": ("x.png", png("red"), "image/png")})
        self.assertIn(upload.status_code, (403, 409))  # only Employee Save changes it
        self.assertIn(self.client.delete(f"/api/images?image_path={path}").status_code, (403, 409))
        self.login("STORE")
        self.assertEqual(self.client.get(path).status_code, 403)  # Store login: not in STEP 1A
        self.login("MERCHANT")
        self.assertEqual(self.client.get(f"/merchant/MER26EFGH/images/employee/{self.foreign['code']}.webp").status_code, 403)
        removed = self.client.put(f"{API}/{item['id']}/with-image",
                                  data={"payload": json.dumps({"first_name": "Photo"}), "remove_image": "true"})
        self.assertEqual((removed.status_code, removed.json()["item"]["image_path"]), (200, None))

    # ---- export ----
    def test_export_lists_its_employees_without_pin_or_login_data(self):
        self.login("MERCHANT")
        item = self.create(phone="703-555-0101")
        self.assign(item["id"], pos_access=True, pos_role="CASHIER", primary=True)
        csv = self.client.get(f"{API}/export", params={"format": "csv"})
        self.assertEqual(csv.status_code, 200, csv.text)
        text = csv.content.decode("utf-8-sig")
        self.assertIn("EMP00001,1001,Kim,Minji", text)
        self.assertIn("Store <One>", text)
        self.assertNotIn("Other", text)
        for word in ("pin", "password", "PIN"):
            self.assertNotIn(word, text.split("\r\n", 1)[0])

    # ---- database constraints (SQLite enforces CHECKs and partial unique indexes; FKs are checked on PostgreSQL) ----
    def test_database_constraints(self):
        def fails(build):
            with self.factory() as db:
                build(db)
                with self.assertRaises(IntegrityError):
                    db.commit()
        base = dict(i_merchant_id=10, c_employee_code="EMP00500", i_pos_number=500, c_first_name="A")
        fails(lambda db: db.add(Employee(**{**base, "c_first_name": None})))                   # a name is required
        fails(lambda db: db.add(Employee(**base, c_status="terminated")))                       # needs its date
        fails(lambda db: db.add(Employee(**base, c_status="gone")))
        fails(lambda db: db.add(Employee(**base, c_merchant_portal_scope="STORE")))             # scope pinned
        fails(lambda db: db.add(Employee(**base, d_hire_date=date(2026, 5, 1), d_termination_date=date(2026, 4, 1))))
        fails(lambda db: db.add(Employee(**{**base, "c_employee_code": self.foreign["code"], "i_merchant_id": 11})))
        fails(lambda db: db.add(Employee(**{**base, "i_pos_number": 0})))
        fails(lambda db: db.add(PortalRole(i_merchant_id=10, c_scope="MERCHANT", c_name="X", b_store_assignable=True)))
        fails(lambda db: db.add(PortalRole(i_merchant_id=10, c_scope="OTHER", c_name="Y")))
        eid = self.foreign["id"]
        row = dict(i_employee_id=eid, i_store_id=11, i_merchant_id=11, d_start=date(2026, 11, 1))
        fails(lambda db: db.add(EmployeeStore(**row, b_pos_access=True)))                         # POS needs a role
        fails(lambda db: db.add(EmployeeStore(**row, c_store_portal_scope="MERCHANT")))
        fails(lambda db: db.add(EmployeeStore(**row, c_status="ended")))                          # needs its end
        fails(lambda db: db.add(EmployeeStore(**row, d_end=date(2026, 10, 1))))
        with self.factory() as db:
            db.add(EmployeeStore(**row, b_primary=True))
            db.commit()
        fails(lambda db: db.add(EmployeeStore(**row)))                                            # one open per Store
        with self.factory() as db:
            db.add(Store(i_store_id=14, i_merchant_id=11, c_store_code="STR26CCCC", c_store_name="Other 2",
                         c_status="active"))
            db.commit()
        fails(lambda db: db.add(EmployeeStore(**{**row, "i_store_id": 14}, b_primary=True)))      # one primary

    # ---- screen ----
    @unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
    def test_employee_screen(self):
        self.login("MERCHANT")
        item = self.create(display_name="Minji K", hire_date="2026-11-01", phone="703-555-0101")
        self.assign(item["id"], pos_access=True, pos_role="MANAGER", primary=True, start_date="2026-11-02")
        self.assign(item["id"], store_id=13, pos_access=True, pos_role="CASHIER", start_date="2026-11-02")
        self.create(first_name="Joon", last_name="Lee")
        listing = self.client.get(API, params={"page": 1, "size": 20}).json()
        options = self.client.get("/merchant/api/master-group/options").json()
        asg = self.client.get(f"{API}/{item['id']}/assignments").json()
        mock = ("window.__errors=[];addEventListener('error',e=>__errors.push(String(e.message)));"
                f"window.__list={json.dumps(listing)};window.__options={json.dumps(options)};window.__asg={json.dumps(asg)};"
                + r"""
window.requests=[];window.bootstrap=window.bootstrap||{};
const J=b=>new Response(JSON.stringify(b),{headers:{'Content-Type':'application/json'}});
window.fetch=async(path,options={})=>{const url=new URL(path,'http://x');
 requests.push({path:url.pathname,search:url.search,method:options.method||'GET',body:options.body});
 if(url.pathname.endsWith('/options'))return J(__options);
 if(url.pathname.endsWith('/assignments'))return J(__asg);
 if(url.pathname==='/merchant/api/master-group/employee')return J(__list);
 return J({item:__list.items[0]});};""")
        page = inline_page(self.client.get("/merchant/business/employees").text, mock)
        script = "const page=" + json.dumps(page).replace("</", "<\\/") + ";\n" + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const wait=async(f,m)=>{for(let i=0;i<100;i++){try{if(f())return;}catch(e){}await pause(50);}throw Error('timeout: '+m);};
(async()=>{
 const frame=document.createElement('iframe');frame.style.width='1400px';frame.style.height='1000px';frame.srcdoc=page;
 const loaded=new Promise(r=>frame.addEventListener('load',r,{once:true}));document.body.appendChild(frame);await loaded;
 const doc=frame.contentDocument,win=frame.contentWindow,$=id=>doc.getElementById(id);
 await wait(()=>doc.querySelectorAll('#mgBody [data-row-select]').length===2,'two employees listed');
 assert([...$('mgEmpStoreFilter').options].length===4&&[...$('mgEmpRoleFilter').options].length===5,'Store / POS Role filters');
 assert([...$('mgActiveFilter').options].some(o=>o.value==='terminated'),'Terminated status filter');
 assert(/EMP00001/.test($('mgBody').textContent)&&/POS 1001/.test($('mgBody').textContent),'code and POS # in the list');
 doc.querySelector('#mgBody [data-row-select]').click();
 await wait(()=>doc.querySelectorAll('#mgAsgBody tr[data-asg]').length===2,'assignments loaded');
 assert($('mgEmpCode').textContent==='EMP00001'&&$('mgEmpPos').textContent==='1001','read-only Employee Code / POS #');
 assert($('mgf_first_name').value==='Minji'&&$('mgf_status').value==='active','form filled');
 assert(!!$('mgImageUploader').querySelector('input[type=file]'),'common photo uploader');
 assert($('mgMdDelete').textContent==='Terminate'&&$('mgMdToggle').textContent==='Deactivate','header actions');
 $('mgAsgAdd').click();await pause(50);
 const opts=[...$('mgAsgStore').options];
 assert(opts.find(o=>/Idle Store/.test(o.textContent)).disabled,'Inactive Store cannot be chosen');
 assert(opts.filter(o=>o.disabled).length===3,'assigned Stores and the Inactive Store are disabled');
 $('mgf_last_name').value='Park';
 $('mgSave').click();
 await wait(()=>win.requests.some(r=>r.method==='PUT'),'save');
 const put=win.requests.find(r=>r.method==='PUT');
 assert(put.path==='/merchant/api/master-group/employee/'+__ID__+'/with-image','photo-aware save route');
 const keys=Object.keys(JSON.parse(put.body.get('payload'))).sort().join(',');
 assert(keys==='display_name,email,first_name,hire_date,last_name,phone,status','only the editable fields: '+keys);
 assert(win.__errors.length===0,'no script errors: '+win.__errors);
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack});});
""".replace("__ID__", str(item["id"]))
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
