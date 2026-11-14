"""Employee Management STEP 1A: Merchant Employee Master + Store assignments through the common Master Group flow.

Isolated SQLite app (tools.test_common_app_navigation fixture): Merchant 10 (MER26ABCD) with active Store 10; added
here Store 13 (active) and Store 12 (Inactive) of Merchant 10, and Store 11 of Merchant 11 (another tenant).
Covers the menu / page / scope, per-Merchant numbering (and its retry), the field allow-list, tenant isolation,
assignments (same Merchant, Inactive Store view only, one open per Store, one primary, dates), termination (every
assignment ends, Inactive Stores included, history kept), rehire, photo storage / read permission, export, the
database constraints SQLite enforces, and the screen (headless Chrome).
STEP 1A-2: address, POS Login ID (unique per Merchant), POS PIN set / reset (hash only), Portal Login account
(tb_platform_user of the Merchant, email unique per Merchant), session end, termination, other-Merchant isolation.
STEP 1A-3: Job Grade / Job Title (Common Maintenance codes of the Merchant; EmployeeJobGradeTitleTests).
Common Date standard: MM-DD-YYYY screens / export, ISO API (EmployeeDateFormatTests + the screen test).
STEP 1A-3: Job Grade / Job Title (Common Maintenance codes of the Merchant; EmployeeJobGradeTitleTests).
Tabs + Audit: GENERAL / ADDRESS / STORE TERMINAL ACCESS / PORTAL ACCESS (+ Soon: SKILLS, COMPENSATION, HISTORY); Created / Last Updated By names (this Merchant's users only, never
an ID); Last Updated moves only on a real change (no-op Save / Portal save: no UPDATE, no version bump).
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from sqlalchemy.exc import IntegrityError

from backend.database.common_code_migration import migrate_common_codes
from backend.models_admin.common_code import CommonCode, MerchantCommonInit
from backend.database.common_code_migration import migrate_common_codes
from backend.models_admin.common_code import CommonCode, MerchantCommonInit
from backend.models_admin.employee import Employee, EmployeeStore
from backend.models_admin.merchant import Merchant
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.role import Role
from backend.models_admin.session import SessionTbl
from backend.models_admin.portal_role import PortalRole
from backend.models_admin.store import Store
from backend.routers import images as image_routes
from backend.services import employees as emp
from backend.services import images
from backend.services import master_group as mg_svc
from backend.services import master_group as mg_svc
from backend.utils.passwords import verify_secret
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
        # Top navigation: Dashboard | Master Group | Business | Employee Group | Reports (common header).
        nav = dashboard.split('class="platform-nav-row"', 1)[1]
        tops = [nav.index("Dashboard</a>")] + [nav.index(f'id="nav-{key}"') for key in (
            "master-group", "business", "employee-group", "reports")]
        self.assertEqual(tops, sorted(tops))
        group = dashboard.split('id="mega-employee-group"', 1)[1].split('class="platform-mega-menu"', 1)[0]
        self.assertIn('href="/merchant/employee-group/employee-master"', group)
        for label in ("Time Card", "Scheduling", "Payroll", "Employee Reports"):  # common soon(): never linked
            self.assertIn(f'aria-disabled="true" title="Coming soon">{label}<span class="platform-menu-soon">', group)
        self.assertEqual(group.count("href="), 1)
        business = dashboard.split('id="mega-business"', 1)[1].split('class="platform-mega-menu"', 1)[0]
        self.assertNotIn("employee", business.lower())                     # moved out of Business
        self.assertIn('href="/merchant/business/stores"', business)
        for url in ("/merchant/employee-group/employee-master", "/merchant/business/employees"):  # same page
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200, page.text[:300])
            self.assertIn('data-entity="employee"', page.text)
            self.assertIn('data-api-base="/merchant/api/master-group"', page.text)
            self.assertIn('id="mgEmpStoreFilter"', page.text)
            self.assertIn('class="platform-menu-link active" href="/merchant/employee-group/employee-master"', page.text)
        options = self.client.get("/merchant/api/master-group/options").json()
        self.assertEqual([r["code"] for r in options["pos_roles"]], ["MANAGER", "CASHIER", "SERVER", "KITCHEN"])
        self.assertEqual(sorted(s["id"] for s in options["stores"]), [10, 12, 13])
        self.login("STORE")
        store_dashboard = self.client.get("/merchant/dashboard/standard").text
        self.assertNotIn("/merchant/business/employees", store_dashboard)
        self.assertNotIn('id="nav-employee-group"', store_dashboard)
        for call in (lambda: self.client.get("/store/master-group/employees"),
                     lambda: self.client.get("/merchant/employee-group/employee-master"),
                     lambda: self.client.get("/merchant/business/employees"),
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
        # Audit: Created / Last Updated By are names of this Merchant's users only (never an ID or another owner).
        with self.factory() as db:
            admin = db.query(PlatformUser).filter(PlatformUser.i_merchant_id == 10,
                                                  PlatformUser.c_email == "primary@example.test").one()
            admin.c_first_name, admin.c_last_name = "Pat", "Lee"
            platform = db.query(PlatformUser).filter(PlatformUser.i_merchant_id.is_(None),
                                                     PlatformUser.i_agency_id.is_(None)).first()
            platform.c_first_name, platform.c_last_name = "Platform", "Person"
            admin_id, platform_id = admin.i_platform_user_id, platform.i_platform_user_id
            db.commit()
        listed = next(r for r in self.client.get(API).json()["items"] if r["id"] == item["id"])
        self.assertEqual((listed["created_by"], listed["updated_by"]), ("Pat Lee", "Pat Lee"))
        self.assertFalse([key for key in listed if key.endswith("_by_id") or key.startswith("i_")])
        for key in ("created_by", "updated_by", "created_at", "updated_at"):  # read only: refused in a Save
            self.assertEqual(self.client.put(f"{API}/{item['id']}", json={key: "x"}).status_code, 422, key)
        # A Save that changes nothing: no UPDATE (dt_updated, row_version and stamp unchanged); a real change stamps.
        before = self.row(Employee, item["id"])
        same = self.client.put(f"{API}/{item['id']}", json={"first_name": "Minji", "last_name": "Park",
                                                           "status": "inactive", "phone": "703-555-0101"})
        self.assertEqual(same.status_code, 200, same.text)
        after = self.row(Employee, item["id"])
        self.assertEqual((after.dt_updated, after.i_row_version), (before.dt_updated, before.i_row_version))
        self.client.put(f"{API}/{item['id']}", json={"display_name": "MK"})
        changed = self.row(Employee, item["id"])
        self.assertEqual((changed.i_row_version, changed.i_updated_by), (before.i_row_version + 1, admin_id))
        self.assertGreaterEqual(changed.dt_updated, before.dt_updated)
        with self.factory() as db:  # stamped by a user outside this Merchant: no name is shown
            db.get(Employee, item["id"]).i_created_by = platform_id
            db.commit()
        shown = self.client.get(API).json()["items"]
        self.assertIsNone(next(r for r in shown if r["id"] == item["id"])["created_by"])
        self.assertNotIn("Platform Person", self.client.get(API).text)

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
        # Store assignments keep their own audit: the Employee's Last Updated does not move (its version still does).
        saved = self.row(Employee, eid)
        self.assertEqual((saved.dt_updated.isoformat(), saved.i_row_version), (item["updated_at"], item["row_version"] + 1))
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
        with redirect_stdout(io.StringIO()):
            migrate_common_codes(self.engine)  # Platform Job Grade / Job Title defaults
        self.login("MERCHANT")
        jobs = self.client.get("/merchant/api/master-group/options").json()  # copy-once: this Merchant's codes
        grade = next(c for c in jobs["job_grade_codes"] if c["code"] == "GRD00004")
        title = next(c for c in jobs["job_title_codes"] if c["code"] == "JOB00006")
        item = self.create(display_name="Minji K", hire_date="2026-11-01", phone="703-555-0101", pos_login_id="0101",
                           zip="22031", city="Fairfax", state="VA", job_grade_code_id=grade["id"],
                           job_title_code_id=title["id"], birth_date="1990-03-15", gender="FEMALE")
        off = self.client.put(f"/merchant/api/master-group/common_code/{title['id']}?group=JOB_TITLE",
                              json={"name": "Store Manager", "active": False})  # an inactive saved Title
        self.assertEqual(off.status_code, 200, off.text)
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
 if(/\/records\//.test(url.pathname))return J({items:[],can_add:true,read_only_message:null}); // HISTORY / BACKGROUND lists
 if(url.pathname==='/merchant/api/master-group/employee')return J(__list);
 if(window.__fail&&options.method==='PUT'){const body=__fail;window.__fail=null;
   return new Response(JSON.stringify(body),{status:422,headers:{'Content-Type':'application/json'}});}
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
 assert(/EMP00001/.test($('mgBody').textContent)&&/POS ID 0101/.test($('mgBody').textContent),'code and POS Login ID in the list');
 assert(/POS ID 0101 · Store <One> · Store Manager/.test($('mgBody').textContent),'Job Title in the existing subtitle: '+$('mgBody').textContent);
 doc.querySelector('#mgBody [data-row-select]').click();
 await wait(()=>doc.querySelectorAll('#mgAsgBody tr[data-asg]').length===2,'assignments loaded');
 // Tabs: the common Bootstrap Tab of the MD form; GENERAL first, one pane shown at a time.
 const tabs=[...$('mgEmpTabs').querySelectorAll('[role="tab"]')];
 assert(tabs.map(t=>t.textContent.trim()).join('|')==='GENERAL|ADDRESS|STORE TERMINAL ACCESS|PORTAL ACCESS|BACKGROUND|COMPENSATIONSoon|HISTORY','seven tabs in order: '+tabs.map(t=>t.textContent.trim()));
 assert($('mgEmpTabs').closest('.mg-md-pageframe')&&$('mgEmpTabs').classList.contains('nav-tabs'),'common tab markup');
 // Soon tabs: the common Soon badge, disabled, no pane; clicking does nothing.
 const soon=['Compensation'].map(k=>$('mgEmpTab'+k));
 assert(soon.every(t=>t.disabled&&t.classList.contains('disabled')&&!t.dataset.bsToggle&&t.querySelector('.platform-menu-soon')),'Soon tabs disabled');
 assert(doc.querySelectorAll('#mgFields .tab-pane').length===6,'panes only for built tabs');
 soon[0].click();await pause(250);
 // One tab row: scrolls sideways (this screen only) instead of wrapping.
 assert(tabs.every(t=>Math.abs(t.getBoundingClientRect().top-tabs[0].getBoundingClientRect().top)<2),'tabs on one row');
 assert(win.getComputedStyle($('mgEmpTabs')).overflowX==='auto','tab row scrolls');
 frame.style.width='760px';await pause(200);                       // a narrow panel: still one row, scrolls
 assert(tabs.every(t=>Math.abs(t.getBoundingClientRect().top-tabs[0].getBoundingClientRect().top)<2),'one row when narrow');
 assert($('mgEmpTabs').scrollWidth>$('mgEmpTabs').clientWidth,'narrow tab row scrolls sideways');
 frame.style.width='1400px';await pause(200);
 // The common PageFrame keeps one height: inactive panes are hidden (visibility), not removed.
 const shown=()=>[...doc.querySelectorAll('#mgFields .tab-pane')].filter(p=>win.getComputedStyle(p).visibility!=='hidden').map(p=>p.id);
 assert(shown().join()==='mgEmpPaneGeneral','only GENERAL shown (Soon click ignored): '+shown());
 // Personal Information (GENERAL): Birthday (common date input, no future date) + Gender (fixed list), optional.
 assert(/^Personal Information/.test($('mgEmpPersonal').querySelector('h3').textContent),'Personal Information section');
 assert($('mgEmpPersonal').contains($('mgf_birth_date'))&&$('mgEmpPersonal').contains($('mgf_gender')),'Birthday and Gender');
 // Common Date (AwsumDateInput): MM-DD-YYYY text input + calendar button; the API keeps ISO.
 const bd=$('mgf_birth_date');
 assert(bd.type==='text'&&bd.hasAttribute('data-awsum-date')&&bd.placeholder==='MM-DD-YYYY'&&bd.value==='03-15-1990','Birthday common date input (MM-DD-YYYY): '+bd.value);
 assert(bd.closest('.awsum-date-group')&&bd.closest('.awsum-date-group').querySelector('.awsum-date-trigger'),'calendar button');
 assert(['mgf_hire_date','mgf_termination_date','mgEmpTermDate','mgAsgStart'].every(id=>$(id).type==='text'&&$(id).hasAttribute('data-awsum-date')),'every Employee date uses the common input');
 assert(!doc.querySelector('#mgFields input[type="date"]'),'no native date input left');
 // STEP D: no termination value is shown for an employed person (saved value NULL; the Terminate input has no default)
 assert($('mgf_termination_date').value===''&&$('mgf_termination_reason').value===''&&$('mgEmpTermDate').value==='','no Termination Date / Reason for an Active employee: '+$('mgf_termination_date').value+'|'+$('mgEmpTermDate').value);
 assert($('mgf_termination_date').readOnly&&$('mgf_termination_reason').readOnly&&!$('mgf_termination_date').name&&!$('mgf_termination_reason').name,'termination values read only, never saved');
 assert($('mgf_termination_date').closest('.awsum-date-group').querySelector('.awsum-date-trigger').disabled,'no calendar on the read-only Termination Date');
 assert(/11-02-2026/.test($('mgAsgBody').textContent),'assignment start shown as MM-DD-YYYY');
 const genderSel=$('mgf_gender');
 assert([...genderSel.options].map(o=>o.text).join('|')==='Not specified|Male|Female'&&genderSel.value==='FEMALE','Gender list (Male / Female / not selected) and saved value');
 const after=(a,b)=>$(a).compareDocumentPosition($(b))&win.Node.DOCUMENT_POSITION_FOLLOWING;
 // STEP D-2: Job, Personal (both beside the photo, as in the MD form), Employment, Audit, Terminate Employee
 const heads=[...$('mgEmpPaneGeneral').querySelectorAll('.pm-section-head h3')].map(h=>h.textContent.trim());
 assert(heads.join('|')==='Job Information|Personal Information|Employment Information|Terminate Employee','GENERAL sections: '+heads);
 assert(after('mgEmpJob','mgEmpPersonal')&&after('mgEmpPersonal','mgEmpEmployment')&&after('mgEmpEmployment','mgEmpAudit')&&after('mgEmpAudit','mgEmpTerminate'),'section order');
 const fields=doc.querySelector('.mg-md-fields');
 assert(fields.contains($('mgEmpCode'))&&fields.contains($('mgEmpJob'))&&fields.contains($('mgEmpPersonal'))&&!fields.contains($('mgf_status')),'Job + Personal in the common top block, Status in Employment');
 assert(!/1990|Female|03\/15/.test($('mgBody').textContent),'Birthday / Gender never in the list display');
 // Employment Information (GENERAL): Hire Date moved here + Job Grade / Job Title (Common codes, optional).
 assert(/^Employment Information/.test($('mgEmpEmployment').querySelector('h3').textContent),'Employment Information section');
 assert(/^Job Information/.test($('mgEmpJob').querySelector('h3').textContent),'Job Information section');
 assert(['mgf_job_grade_code_id','mgf_job_title_code_id'].every(id=>$('mgEmpJob').contains($(id))),'Job Grade, Job Title');
 const top=id=>Math.round($(id).getBoundingClientRect().top);
 assert(top('mgf_job_grade_code_id')===top('mgf_job_title_code_id'),'Job Grade and Job Title on one row');
 assert(['mgf_hire_date','mgf_termination_date','mgf_status','mgf_termination_reason'].every(id=>$('mgEmpEmployment').contains($(id))),'Hire Date, Termination Date, Status, Termination Reason');
 assert(top('mgf_hire_date')===top('mgf_termination_date')&&top('mgf_status')===top('mgf_termination_reason')&&top('mgf_status')>top('mgf_hire_date'),'Hire | Termination Date, then Status | Termination Reason');
 // Personal Information rows: First | Last, Display (5) | Birthday (4) | Gender (3), Email | Phone (6 | 6)
 const colOf=id=>$(id).closest('[data-field-wrap]').className;
 assert(top('mgf_first_name')===top('mgf_last_name')&&colOf('mgf_first_name')==='col-md-6'&&colOf('mgf_last_name')==='col-md-6','First | Last Name');
 assert(top('mgf_display_name')===top('mgf_birth_date')&&top('mgf_birth_date')===top('mgf_gender')&&top('mgf_display_name')>top('mgf_first_name'),'Display Name | Birthday | Gender on one row');
 assert([colOf('mgf_display_name'),colOf('mgf_birth_date'),colOf('mgf_gender')].join()==='col-md-5,col-md-4,col-md-3','5:4:3');
 const w=id=>$(id).closest('[data-field-wrap]').getBoundingClientRect().width;
 assert(w('mgf_display_name')>w('mgf_birth_date')&&w('mgf_birth_date')>w('mgf_gender'),'Gender the narrowest');
 assert(top('mgf_email')===top('mgf_phone')&&top('mgf_email')>top('mgf_gender')&&$('mgf_email').getBoundingClientRect().left<$('mgf_phone').getBoundingClientRect().left&&colOf('mgf_email')==='col-md-6','Email | Phone');
 assert(top('mgf_job_grade_code_id')<top('mgf_first_name'),'Job Information above Personal Information');
 assert(!doc.querySelector('.mg-md-fields').contains($('mgf_hire_date'))&&$('mgf_hire_date').value==='11-01-2026','Hire Date moved, value kept (MM-DD-YYYY)');
 const gradeSel=$('mgf_job_grade_code_id'),titleSel=$('mgf_job_title_code_id'),txt=s=>s.options[s.selectedIndex].text;
 assert(gradeSel.options[0].value===''&&gradeSel.options[0].text==='None'&&gradeSel.options.length===9,'8 active Grades + None');
 assert(txt(gradeSel)==='Supervisor','saved Grade selected');
 assert(titleSel.options.length===11&&txt(titleSel)==='Store Manager (Inactive)','inactive saved Title kept by name: '+txt(titleSel));
 assert(gradeSel.compareDocumentPosition($('mgEmpAudit'))&win.Node.DOCUMENT_POSITION_FOLLOWING,'Employment Information above the audit');
 for(const [pane,ids] of [['mgEmpPaneGeneral',['mgEmpCode','mgImageUploader','mgf_first_name','mgEmpJob','mgEmpPersonal','mgf_hire_date','mgEmpEmployment','mgf_status','mgEmpAudit','mgEmpTermDate']],
   ['mgEmpPaneAddress',['mgf_zip','mgf_country']],['mgEmpPaneTerminal',['mgEmpPos','mgf_pos_login_id','mgPinOpen','mgAsgSection']],
   ['mgEmpPanePortal',['mgEmpPortal','mgPortalOpen']]])
   ids.forEach(id=>assert($(pane).contains($(id)),id+' in '+pane));
 assert($('mgEmpPos').compareDocumentPosition($('mgAsgSection'))&win.Node.DOCUMENT_POSITION_FOLLOWING,'POS Login, then Store Assignments');
 assert(/^POS Login/.test($('mgEmpPos').querySelector('h3').textContent),'POS Login section');
 // Common PageFrame: every tab has the same frame height, so Save never moves.
 const frameH=()=>Math.round($('mgEmpTabs').closest('.mg-md-pageframe').getBoundingClientRect().height);
 const saveTop=()=>Math.round($('mgSave').getBoundingClientRect().top);
 const heights=[],saves=[];
 assert($('mgEmpAudit').compareDocumentPosition($('mgEmpTermDate'))&win.Node.DOCUMENT_POSITION_FOLLOWING,'Termination at the bottom of GENERAL');
 // Audit: read only, never part of Save; the stored timestamp text is shown without a time-zone conversion.
 const listed=win.__list.items[0],us=iso=>{const [d,t]=iso.split('T');const h=Number(t.slice(0,2));
   return `${d.slice(5,7)}-${d.slice(8,10)}-${d.slice(0,4)} ${String(h%12||12).padStart(2,'0')}:${t.slice(3,5)} ${h<12?'AM':'PM'}`;};
 assert(listed.created_by&&$('mgf_created_by').value===listed.created_by&&$('mgf_updated_by').value===listed.updated_by,'audit names');
 assert($('mgf_created_at').value===us(listed.created_at)&&$('mgf_updated_at').value===us(listed.updated_at),'audit times as stored');
 assert(['created_by','created_at','updated_by','updated_at'].every(k=>$('mgf_'+k).readOnly&&!$('mgf_'+k).name),'audit read only, unnamed');
 // Switching tabs keeps typed values (no reload, no save).
 heights.push(frameH());saves.push(saveTop());
 $('mgf_first_name').value='Typed';$('mgEmpTabAddress').click();await pause(250);
 assert(shown().join()==='mgEmpPaneAddress','ADDRESS shown');heights.push(frameH());saves.push(saveTop());
 $('mgf_city').value='Typed City';$('mgEmpTabTerminal').click();await pause(250);
 assert(shown().join()==='mgEmpPaneTerminal','STORE TERMINAL ACCESS shown');heights.push(frameH());saves.push(saveTop());
 $('mgEmpTabPortal').click();await pause(250);
 assert(shown().join()==='mgEmpPanePortal','PORTAL ACCESS shown');heights.push(frameH());saves.push(saveTop());
 assert(new Set(heights).size===1&&new Set(saves).size===1,'same frame height and Save position: '+heights+' / '+saves);
 $('mgEmpTabGeneral').click();await pause(250);
 assert($('mgf_first_name').value==='Typed'&&$('mgf_city').value==='Typed City'&&!win.requests.some(r=>r.method==='PUT'),'values kept, nothing saved');
 // A Save error on a field of a hidden tab selects that tab and focuses the field.
 win.__fail={detail:'Please fix the highlighted fields.',errors:{pos_login_id:'POS Login ID must be 4 to 10 digits.'}};
 $('mgSave').click();
 await wait(()=>shown().join()==='mgEmpPaneTerminal'&&doc.activeElement===$('mgf_pos_login_id'),'error tab selected');
 assert($('mgf_pos_login_id').classList.contains('is-invalid'),'error marked');
 win.requests.length=0;$('mgEmpTabGeneral').click();await pause(250);
 $('mgf_first_name').value='Minji';$('mgf_city').value='Fairfax';
 assert($('mgEmpCode').textContent==='EMP00001'&&!$('mgEmpPosNumber'),'read-only Employee Code (no POS # shown)');
 assert($('mgf_zip').value==='22031'&&$('mgf_state').value==='VA'&&$('mgf_country').value==='United States','address filled');
 assert($('mgf_pos_login_id').value==='0101'&&!$('mgf_pos_login_enabled').checked,'POS Login filled');
 assert($('mgPosStatus').textContent==='Disabled'&&$('mgPinOpen').textContent==='Set PIN'&&!$('mgPinOpen').disabled,'POS status / Set PIN');
 assert($('mgPortalStatus').textContent==='Not created'&&!$('mgPortalOpen').disabled,'Portal status');
 assert($('mgPortalPasswordState').textContent==='Not Set'&&$('mgPortalPasswordMask').textContent==='—','Portal Password Not Set');
 assert($('mgPortalPasswordMask').tagName==='SPAN'&&$('mgPortalEditor').contains($('mgPortalPassword'))&&!$('mgPortalEditor').contains($('mgPortalPasswordState')),'state display is separate from the password input');
 assert(typeof win.AwsumAddress==='object'&&$('mgf_zip').dataset.zipAutofillBound==='1','common address ZIP autofill bound');
 $('mgPortalOpen').click();
 assert(!$('mgPortalEditor').classList.contains('d-none')&&$('mgPortalEmail').value==='','Portal editor opens');
 assert(!$('mgPortalEmail').name&&!$('mgPortalPassword').name&&!$('mgPin').name,'login secrets are never part of Save');
 assert($('mgf_first_name').value==='Minji'&&$('mgf_status').value==='active','form filled');
 assert(!!$('mgImageUploader').querySelector('input[type=file]'),'common photo uploader');
 assert($('mgMdDelete').textContent==='Terminate'&&$('mgMdToggle').textContent==='Deactivate','header actions');
 $('mgAsgAdd').click();await pause(50);
 const opts=[...$('mgAsgStore').options];
 assert(opts.find(o=>/Idle Store/.test(o.textContent)).disabled,'Inactive Store cannot be chosen');
 assert(opts.filter(o=>o.disabled).length===3,'assigned Stores and the Inactive Store are disabled');
 // Date input behaviour: typing mask, blur check (future Birthday refused in place), picker with the max, ISO payload.
 const type=(el,text)=>{el.focus();el.value=text;el.dispatchEvent(new Event('input',{bubbles:true}));};
 const fb=el=>el.closest('[data-field-wrap]').querySelector('.invalid-feedback');
 type(bd,'12312999');assert(bd.value==='12-31-2999','mask: '+bd.value);
 bd.dispatchEvent(new Event('blur'));
 assert(bd.classList.contains('is-invalid')&&fb(bd).textContent==='Birthday cannot be in the future.'&&win.getComputedStyle(fb(bd)).display!=='none','future Birthday error shown');
 type(bd,'02302020');bd.dispatchEvent(new Event('blur'));
 assert(fb(bd).textContent==='Enter Birthday as MM-DD-YYYY.','not a real date');
 bd.closest('.awsum-date-group').querySelector('.awsum-date-trigger').click();await pause(50);
 const pk=doc.querySelector('.awsum-date-picker');
 assert(pk&&!pk.classList.contains('is-hidden'),'picker open');
 const tomorrow=new Date();tomorrow.setDate(tomorrow.getDate()+1);
 const tIso=`${tomorrow.getFullYear()}-${String(tomorrow.getMonth()+1).padStart(2,'0')}-${String(tomorrow.getDate()).padStart(2,'0')}`;
 const tBtn=pk.querySelector(`[data-iso="${tIso}"]`);
 assert(!tBtn||tBtn.disabled,'days after today cannot be picked for the Birthday');
 assert(pk.querySelector('[data-date-action="day"]:not(:disabled):not(.is-outside)'),'earlier days can be picked');
 pk.querySelector('[data-date-action="year"]').value='1990';pk.querySelector('[data-date-action="year"]').dispatchEvent(new Event('change',{bubbles:true}));
 pk.querySelector('[data-date-action="month"]').value='2';pk.querySelector('[data-date-action="month"]').dispatchEvent(new Event('change',{bubbles:true}));
 pk.querySelector('[data-iso="1990-03-15"]').click();await pause(50);
 assert(bd.value==='03-15-1990'&&!bd.classList.contains('is-invalid')&&pk.classList.contains('is-hidden'),'picked from the calendar: '+bd.value);
 type($('mgf_hire_date'),'11/01/2026');$('mgf_hire_date').dispatchEvent(new Event('blur'));
 assert($('mgf_hire_date').value==='11-01-2026','earlier MM/DD/YYYY typing is accepted and shown as MM-DD-YYYY');
 $('mgf_last_name').value='Park';
 $('mgf_pos_login_enabled').checked=true;
 $('mgSave').click();
 await wait(()=>win.requests.some(r=>r.method==='PUT'),'save');
 const put=win.requests.find(r=>r.method==='PUT');
 assert(put.path==='/merchant/api/master-group/employee/'+__ID__+'/with-image','photo-aware save route');
 const keys=Object.keys(JSON.parse(put.body.get('payload'))).sort().join(',');
 assert(keys==='address_line1,address_line2,background_remark,birth_date,city,country,display_name,email,first_name,gender,hire_date,job_grade_code_id,job_title_code_id,last_name,phone,pos_login_enabled,pos_login_id,state,status,zip','only the editable fields: '+keys);
 assert(JSON.parse(put.body.get('payload')).job_title_code_id===String(win.__list.items[0].job_title_code_id),'saved inactive Title sent back unchanged');
 assert(JSON.parse(put.body.get('payload')).pos_login_enabled===true,'Enable POS Login sent');
 const sent=JSON.parse(put.body.get('payload'));
 assert(sent.birth_date==='1990-03-15'&&sent.hire_date==='2026-11-01','dates sent as ISO: '+sent.birth_date+' / '+sent.hire_date);
 assert(win.__errors.length===0,'no script errors: '+win.__errors);
 $('mgAddBtn').click();await pause(100);
 assert($('mgf_pos_login_id').value===''&&$('mgf_country').value==='United States','new form defaults');
 $('mgf_phone').value='(972) 555-1234';$('mgf_phone').dispatchEvent(new Event('input',{bubbles:true}));
 assert($('mgf_pos_login_id').value==='1234','POS Login ID suggested from the phone');
 $('mgf_phone').value='972-555-8899';$('mgf_phone').dispatchEvent(new Event('input',{bubbles:true}));
 assert($('mgf_pos_login_id').value==='8899','suggestion follows the phone until edited');
 $('mgf_pos_login_id').value='7777';
 $('mgf_phone').value='972-555-0000';$('mgf_phone').dispatchEvent(new Event('input',{bubbles:true}));
 assert($('mgf_pos_login_id').value==='7777','a typed ID is kept');
 assert($('mgPinOpen').disabled&&$('mgPortalOpen').disabled,'PIN / Portal need a saved employee');
 assert(!$('mgEmpAudit')&&$('mgEmpTabs'),'a new employee has no audit yet');
 // A terminated (read-only) employee: every field and action stays disabled, the tabs stay usable to view.
 win.__list.items[0]=Object.assign({},win.__list.items[0],{status:'terminated',read_only:true,termination_date:'2026-11-10',created_by:null,updated_by:null,updated_at:null});
 $('mgActiveFilter').dispatchEvent(new Event('change',{bubbles:true}));
 await wait(()=>/Terminated/.test($('mgBody').textContent),'terminated row listed');
 doc.querySelector('#mgBody [data-row-select]').click();await pause(100);
 assert($('mgf_first_name').disabled&&$('mgPinOpen').disabled&&$('mgAsgAdd').disabled,'read-only fields and actions');
 assert([...$('mgEmpTabs').querySelectorAll('[data-bs-toggle="tab"]')].every(t=>!t.disabled),'built tabs usable');
 assert(['Compensation'].every(k=>$('mgEmpTab'+k).disabled),'Soon tab stays disabled');
 $('mgEmpTabPortal').click();await pause(250);
 assert(shown().join()==='mgEmpPanePortal'&&$('mgPortalOpen').disabled,'read-only tab switch');
 // A registered password: fixed mask + Password Set, the password inputs stay empty (nothing is sent to the screen).
 win.__list.items[0]=Object.assign({},win.__list.items[0],{read_only:false,status:'active',portal_login:{enabled:true,email:'minji.portal@example.test',status:'active',must_change_password:true,password_set:true}});
 $('mgActiveFilter').dispatchEvent(new Event('change',{bubbles:true}));
 await wait(()=>!/Terminated/.test($('mgBody').textContent),'active row listed');
 doc.querySelector('#mgBody [data-row-select]').click();await pause(100);
 assert($('mgPortalPasswordMask').textContent==='••••••••'&&$('mgPortalPasswordState').textContent==='Password Set','Password Set mask');
 $('mgPortalOpen').click();
 assert($('mgPortalPassword').value===''&&$('mgPortalConfirm').value===''&&/leave empty to keep/.test($('mgPortalPasswordLabel').textContent)&&$('mgPortalConfirmLabel').textContent==='Confirm New Password','reset inputs empty, labelled New Password');
 assert(!$('mgPortalPassword').name&&!$('mgPortalConfirm').name,'password inputs are not part of the Employee Save');
 assert($('mgf_created_by').value==='—'&&$('mgf_updated_by').value==='—'&&$('mgf_updated_at').value==='—','no value: em dash');
 assert(win.__errors.length===0,'no script errors: '+win.__errors);
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack});});
""".replace("__ID__", str(item["id"]))
        self.run_browser(script)


class EmployeeLoginAccessTests(unittest.TestCase):
    """STEP 1A-2: address, POS Login ID, POS PIN, Portal Login; every uniqueness rule in the Merchant scope."""

    login, create, row = EmployeeMasterTests.login, EmployeeMasterTests.create, EmployeeMasterTests.row

    def setUp(self):
        EmployeeMasterTests.setUp(self)
        with self.factory() as db:
            db.add(Role(c_name="Employee", c_description="Merchant Employee (Portal Login)", c_status="active"))
            db.commit()
        self.login("MERCHANT")

    def portal(self, employee_id, **values):
        return self.client.post(f"{API}/{employee_id}/portal-access", json={"enabled": True, **values})

    def test_address_is_saved_and_exported(self):
        item = self.create(zip="22031", city="Fairfax", state="va", address_line1="1 Main St", address_line2="Suite 2")
        self.assertEqual((item["zip"], item["city"], item["state"], item["country"]),
                         ("22031", "Fairfax", "VA", "United States"))
        updated = self.client.put(f"{API}/{item['id']}", json={"country": "Canada", "zip": "", "city": ""})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual((updated.json()["item"]["country"], updated.json()["item"]["zip"]), ("Canada", None))
        too_long = self.client.put(f"{API}/{item['id']}", json={"zip": "1" * 21})
        self.assertEqual(too_long.status_code, 422)
        csv = self.client.get(f"{API}/export", params={"format": "csv"}).content.decode("utf-8-sig")
        self.assertIn("Address Line 1,Address Line 2,City,State,ZIP Code,Country", csv)
        self.assertIn("1 Main St,Suite 2,,VA,,Canada", csv)
        with self.factory() as db:  # independent of the Merchant / Store address
            self.assertIsNone(db.get(Store, 10).c_address_line1)

    def test_pos_login_id_is_unique_per_merchant_and_never_follows_the_phone(self):
        first = self.create(phone="972-555-1234", pos_login_id="1234", pos_login_enabled=True)
        self.assertEqual((first["pos_login_id"], first["pos_login_enabled"], first["pos_login_status"]),
                         ("1234", True, "pin_not_set"))
        duplicate = self.client.post(API, json={"first_name": "Joon", "pos_login_id": "1234"})
        self.assertEqual(duplicate.status_code, 422, duplicate.text)
        self.assertIn("already used", duplicate.json()["errors"]["pos_login_id"])
        second = self.create(first_name="Joon", pos_login_id="5678")
        taken = self.client.put(f"{API}/{second['id']}", json={"pos_login_id": "1234"})
        self.assertEqual(taken.status_code, 422)
        self.assertEqual(self.row(Employee, second["id"]).c_pos_login_id, "5678")   # never overwritten
        self.assertEqual(self.row(Employee, first["id"]).c_pos_login_id, "1234")
        for bad in ("123", "12345678901", "12a4"):
            response = self.client.put(f"{API}/{second['id']}", json={"pos_login_id": bad})
            self.assertEqual(response.status_code, 422, bad)
        enable = self.client.put(f"{API}/{second['id']}", json={"pos_login_id": "", "pos_login_enabled": True})
        self.assertEqual(enable.json()["errors"]["pos_login_id"], "Enter a POS Login ID to enable POS Login.")
        phone = self.client.put(f"{API}/{first['id']}", json={"phone": "972-555-9999"})
        self.assertEqual(phone.json()["item"]["pos_login_id"], "1234")                # a saved ID stays
        with self.factory() as db:  # another Merchant may use the same ID; this Merchant's index refuses a copy
            other = emp.create_employee(db, 11, {"first_name": "Other", "pos_login_id": "1234"}, 1)
            self.assertEqual(other["pos_login_id"], "1234")
            db.add(Employee(i_merchant_id=10, c_employee_code="EMP00900", i_pos_number=900, c_first_name="X",
                            c_pos_login_id="1234"))
            with self.assertRaises(IntegrityError):
                db.commit()
        found = self.client.get(API, params={"q": "1234"}).json()["items"]
        self.assertEqual([r["id"] for r in found], [first["id"]])

    def test_pos_pin_is_hashed_never_returned_and_cleared_by_termination(self):
        item = self.create(pos_login_enabled=True, pos_login_id="4321", hire_date="2026-01-01")
        no_id = self.create(first_name="NoId")
        self.assertEqual(self.client.post(f"{API}/{no_id['id']}/pos-pin", json={"pin": "1111", "confirm_pin": "1111"})
                         .json()["errors"]["pin"], "Save a POS Login ID before setting the PIN.")
        for pin, confirm, key in (("12", "12", "pin"), ("12ab", "12ab", "pin"), ("1234", "9999", "confirm_pin")):
            response = self.client.post(f"{API}/{item['id']}/pos-pin", json={"pin": pin, "confirm_pin": confirm})
            self.assertEqual(response.status_code, 422)
            self.assertIn(key, response.json()["errors"])
        response = self.client.post(f"{API}/{item['id']}/pos-pin", json={"pin": "246813", "confirm_pin": "246813"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["item"]
        self.assertEqual((body["pos_pin_set"], body["pos_login_status"]), (True, "active"))
        row = self.row(Employee, item["id"])
        self.assertTrue(row.c_pin_hash.startswith("$2") and verify_secret("246813", row.c_pin_hash))
        self.assertEqual(row.i_pin_version, 1)
        for text in (response.text, self.client.get(API).text,
                     self.client.get(f"{API}/export", params={"format": "csv"}).text):
            self.assertNotIn("246813", text)
            self.assertNotIn(row.c_pin_hash, text)
            self.assertNotIn("pin_hash", text)
        reset = self.client.post(f"{API}/{item['id']}/pos-pin", json={"pin": "1357", "confirm_pin": "1357"})
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(self.row(Employee, item["id"]).i_pin_version, 2)
        self.client.post(f"{API}/{item['id']}/terminate", json={"termination_date": "2026-11-10"})
        row = self.row(Employee, item["id"])
        self.assertEqual((row.c_pin_hash, row.b_pos_login_enabled, row.c_pos_login_id), (None, False, "4321"))
        self.assertEqual(self.client.post(f"{API}/{item['id']}/pos-pin", json={"pin": "1357", "confirm_pin": "1357"})
                         .status_code, 409)

    def test_portal_login_account_of_the_merchant(self):
        item = self.create(email="minji@example.test", phone="703-555-0101")
        self.assertFalse(item["portal_login"]["password_set"])                      # Not Set before the account
        missing = self.portal(item["id"], email="minji.portal@example.test")
        self.assertEqual(missing.json()["errors"]["password"], "Set a password to create the Portal Login.")
        mismatch = self.portal(item["id"], email="minji.portal@example.test", password="Temp#1234",
                               confirm_password="Other")
        self.assertIn("confirm_password", mismatch.json()["errors"])
        admin = self.portal(item["id"], email=" PRIMARY@example.test ", password="Temp#1234",
                            confirm_password="Temp#1234")               # the Merchant Admin's email (same Merchant)
        self.assertEqual(admin.status_code, 422)
        self.assertIn("already a login of this Merchant", admin.json()["errors"]["email"])
        created = self.portal(item["id"], email=" Minji.Portal@Example.test ", password="Temp#1234",
                              confirm_password="Temp#1234")
        self.assertEqual(created.status_code, 200, created.text)
        portal = created.json()["item"]["portal_login"]
        self.assertEqual(portal, {"enabled": True, "email": "minji.portal@example.test", "status": "active",
                                  "must_change_password": True, "password_set": True})
        self.assertNotIn("Temp#1234", created.text)
        self.assertNotIn("$2", created.text)
        row = self.row(Employee, item["id"])
        user = self.row(PlatformUser, row.i_platform_user_id)
        with self.factory() as db:
            role = db.query(Role).filter(Role.c_name == "Employee").one()
        self.assertEqual((user.i_merchant_id, user.i_store_id, user.i_agency_id, user.i_role_id, user.c_status),
                         (10, emp.ADMIN_STORE_ID, None, role.i_role_id, "active"))
        self.assertTrue(verify_secret("Temp#1234", user.c_password))
        self.assertIsNone(getattr(row, "c_password", None))
        # Password Set is only a flag: neither the password nor its hash reaches the screen (detail / list / export).
        for text in (created.text, self.client.get(API).text,
                     self.client.get(f"{API}/export", params={"format": "csv"}).text):
            self.assertNotIn("Temp#1234", text)
            self.assertNotIn(user.c_password, text)
        # A general Employee Save never changes or clears the Portal Password: password keys are refused there.
        refused = self.client.put(f"{API}/{item['id']}", json={"first_name": "Minjee", "password": "",
                                                               "confirm_password": ""})
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(set(refused.json()["errors"]), {"password", "confirm_password"})
        self.assertEqual(self.client.put(f"{API}/{item['id']}", json={"first_name": "Minjee"}).status_code, 200)
        self.assertEqual(self.row(PlatformUser, row.i_platform_user_id).c_password, user.c_password)
        listed = next(i for i in self.client.get(API).json()["items"] if i["id"] == item["id"])
        self.assertTrue(listed["portal_login"]["password_set"])
        # The POS PIN is separate: setting it leaves the Portal Password as it was, and vice versa.
        self.client.put(f"{API}/{item['id']}", json={"pos_login_id": "0101"})
        self.assertEqual(self.client.post(f"{API}/{item['id']}/pos-pin", json={"pin": "2468", "confirm_pin": "2468"})
                         .status_code, 200)
        self.assertEqual(self.row(PlatformUser, row.i_platform_user_id).c_password, user.c_password)
        pin_hash = self.row(Employee, item["id"]).c_pin_hash
        # A second employee cannot take the same login email in this Merchant; another Merchant may.
        other = self.create(first_name="Joon")
        self.assertEqual(self.portal(other["id"], email="minji.portal@example.test", password="Temp#1234",
                                     confirm_password="Temp#1234").status_code, 422)
        with self.factory() as db:
            db.query(Merchant).filter(Merchant.i_merchant_id == 11).one()
            foreign = emp.set_portal_access(db, 11, self.foreign["id"], {"enabled": True, "email":
                                            "minji.portal@example.test", "password": "Other#1",
                                            "confirm_password": "Other#1"}, 1)
            self.assertEqual(foreign["portal_login"]["status"], "active")
        # Sessions end on an email change / password reset / disable. (The common login context refuses an
        # Employee-role session today, so an ACTIVE row stands in for one opened after the Portal Role step.)
        with self.factory() as db:
            db.add(SessionTbl(i_platform_user_id=row.i_platform_user_id, c_status="ACTIVE", c_jwt_token="t"))
            db.commit()

        def active_sessions():
            with self.factory() as db:
                return db.query(SessionTbl).filter(SessionTbl.i_platform_user_id == row.i_platform_user_id,
                                                   SessionTbl.c_status == "ACTIVE").count()
        self.assertEqual(active_sessions(), 1)
        version = self.row(Employee, item["id"]).i_row_version
        keep = self.portal(item["id"], email="minji.portal@example.test")              # nothing changed
        self.assertEqual((keep.status_code, active_sessions()), (200, 1))
        self.assertEqual(self.row(Employee, item["id"]).i_row_version, version)      # no UPDATE, no stamp
        moved = self.portal(item["id"], email="minji.new@example.test")
        self.assertEqual((moved.json()["item"]["portal_login"]["email"], active_sessions()),
                         ("minji.new@example.test", 0))
        reset = self.portal(item["id"], email="minji.new@example.test", password="Reset#5678",
                            confirm_password="Reset#5678")
        self.assertEqual(reset.status_code, 200)
        self.assertTrue(verify_secret("Reset#5678", self.row(PlatformUser, row.i_platform_user_id).c_password))
        self.assertEqual(self.row(Employee, item["id"]).c_pin_hash, pin_hash)          # the PIN is untouched
        self.assertTrue(reset.json()["item"]["portal_login"]["password_set"])
        self.assertEqual(self.row(Employee, item["id"]).i_row_version, version + 2)  # email change + reset
        disabled = self.client.post(f"{API}/{item['id']}/portal-access", json={"enabled": False})
        self.assertEqual(disabled.json()["item"]["portal_login"]["status"], "inactive")
        self.client.post(f"{API}/{item['id']}/portal-access", json={"enabled": False})    # already disabled
        self.assertEqual(self.row(Employee, item["id"]).i_row_version, version + 3)
        # Portal sign-in for the Employee role is not opened in this step (permissions unchanged).
        self.portal(item["id"], email="minji.new@example.test")
        login = self.client.post("/auth/login", data={"store_code": "MER26ABCD", "email": "minji.new@example.test",
                                                      "password": "Reset#5678"}, follow_redirects=False)
        self.assertEqual(login.status_code, 401)
        # Termination disables the account; Reactivate does not restore it.
        self.client.post(f"{API}/{item['id']}/terminate", json={"termination_date": date.today().isoformat()})
        self.assertEqual(self.row(PlatformUser, row.i_platform_user_id).c_status, "inactive")
        back = self.client.post(f"{API}/{item['id']}/reactivate", json={"hire_date": date.today().isoformat()})
        self.assertEqual(back.json()["item"]["portal_login"]["status"], "inactive")

    def test_role_missing_and_other_merchant_and_store_logins_are_refused(self):
        item = self.create()
        with self.factory() as db:
            db.query(Role).filter(Role.c_name == "Employee").delete()
            db.commit()
        self.assertEqual(self.portal(item["id"], email="a@example.test", password="Temp#1",
                                     confirm_password="Temp#1").status_code, 409)
        self.assertIsNone(self.row(Employee, item["id"]).i_platform_user_id)
        foreign = self.foreign["id"]
        self.assertEqual(self.client.post(f"{API}/{foreign}/pos-pin", json={"pin": "1234", "confirm_pin": "1234"})
                         .status_code, 404)
        self.assertEqual(self.portal(foreign, email="b@example.test", password="x", confirm_password="x").status_code,
                         404)
        self.login("STORE")
        self.assertEqual(self.client.post(f"{API}/{item['id']}/pos-pin", json={"pin": "1234", "confirm_pin": "1234"})
                         .status_code, 403)
        self.assertEqual(self.portal(item["id"], email="c@example.test").status_code, 403)


class EmployeeJobGradeTitleTests(unittest.TestCase):
    """STEP 1A-3: Job Grade / Job Title = this Merchant's Common Maintenance JOB_GRADE / JOB_TITLE codes (copied once
    from the Platform defaults), optional, inactive saved codes kept by name, codes in use never deleted."""

    login, create, row = EmployeeMasterTests.login, EmployeeMasterTests.create, EmployeeMasterTests.row
    COMMON = "/merchant/api/master-group/common_code"

    def setUp(self):
        EmployeeMasterTests.setUp(self)
        with redirect_stdout(io.StringIO()):
            migrate_common_codes(self.engine)  # Platform defaults, as in the real database
        self.login("MERCHANT")
        options = self.client.get("/merchant/api/master-group/options")  # first visit: copy-once initialization
        self.assertEqual(options.status_code, 200, options.text)
        self.options = options.json()
        self.grades = {c["code"]: c for c in self.options["job_grade_codes"]}
        self.titles = {c["code"]: c for c in self.options["job_title_codes"]}

    def test_options_are_this_merchants_copied_defaults(self):
        self.assertEqual([(c["code"], c["name"]) for c in self.options["job_grade_codes"]][:2],
                         [("GRD00001", "Entry Level"), ("GRD00002", "Staff")])
        self.assertEqual((len(self.grades), len(self.titles)), (8, 10))
        self.assertEqual(self.titles["JOB00010"]["name"], "General Manager")
        for item in (*self.grades.values(), *self.titles.values()):  # the Merchant's own copies, never Platform rows
            self.assertEqual(self.row(CommonCode, item["id"]).i_merchant_id, 10)
        added = self.client.post(f"{self.COMMON}?group=JOB_GRADE", json={"name": "Intern"})
        self.assertEqual(added.status_code, 201, added.text)
        self.assertEqual(added.json()["item"]["code"], "GRD00009")  # AUTO: the Merchant's own sequence
        duplicate = self.client.post(f"{self.COMMON}?group=JOB_TITLE", json={"name": "cashier"})
        self.assertEqual(duplicate.status_code, 422, duplicate.text)
        self.assertIn("name", duplicate.json()["errors"])

    def test_save_read_list_and_export(self):
        new = self.create()  # both optional
        self.assertEqual((new["job_grade_code_id"], new["job_grade"], new["job_title_code_id"], new["job_title"]),
                         (None, None, None, None))
        grade, title = self.grades["GRD00004"], self.titles["JOB00001"]
        item = self.create(first_name="Jisoo", job_grade_code_id=grade["id"], job_title_code_id=str(title["id"]))
        self.assertEqual((item["job_grade"]["name"], item["job_title"]["code"]), ("Supervisor", "JOB00001"))
        with self.factory() as db:
            saved = db.get(Employee, item["id"])
            self.assertEqual((saved.i_job_grade_code_id, saved.i_job_title_code_id), (grade["id"], title["id"]))
        listed = {i["id"]: i for i in self.client.get(API, params={"size": 100}).json()["items"]}
        self.assertEqual(listed[item["id"]]["job_title"]["name"], "Cashier")
        self.assertIsNone(listed[new["id"]]["job_title"])
        cleared = self.client.put(f"{API}/{item['id']}", json={"job_title_code_id": ""})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual((cleared.json()["item"]["job_title"], cleared.json()["item"]["job_grade"]["name"]),
                         (None, "Supervisor"))  # key absent keeps the saved Job Grade
        self.client.put(f"{API}/{item['id']}", json={"job_title_code_id": title["id"]})
        text = self.client.get(f"{API}/export", params={"format": "csv"}).content.decode("utf-8-sig")
        header, *lines = text.split("\r\n")
        self.assertIn("Hire Date,Job Grade,Job Title,Termination Date", header)
        self.assertTrue(any(",Supervisor,Cashier," in line for line in lines), text)

    def test_wrong_group_other_merchant_platform_and_inactive_codes_are_refused(self):
        with self.factory() as db:
            other = mg_svc.create_item(db, "common_code", {"name": "Other Grade"}, 1, merchant_id=11, group="JOB_GRADE")
            platform = db.query(CommonCode.i_common_code_id).filter(
                CommonCode.c_code == "GRD00001", CommonCode.i_merchant_id.is_(None)).scalar()
        for payload in ({"job_grade_code_id": self.titles["JOB00001"]["id"]},  # a Title is not a Grade
                        {"job_grade_code_id": other["id"]}, {"job_grade_code_id": platform},
                        {"job_grade_code_id": 999999}, {"job_grade_code_id": "abc"}):
            response = self.client.post(API, json={"first_name": "X", **payload})
            self.assertEqual(response.status_code, 422, (payload, response.text))
            self.assertIn("job_grade_code_id", response.json()["errors"])
        staff = self.grades["GRD00002"]
        item = self.create(job_grade_code_id=staff["id"])
        off = self.client.put(f"{self.COMMON}/{staff['id']}?group=JOB_GRADE", json={"name": "Staff", "active": False})
        self.assertEqual(off.status_code, 200, off.text)
        self.assertEqual(self.client.post(API, json={"first_name": "Y", "job_grade_code_id": staff["id"]}).status_code,
                         422)  # an inactive code is not selectable for another Employee
        kept = self.client.put(f"{API}/{item['id']}", json={"first_name": "Kept", "job_grade_code_id": staff["id"]})
        self.assertEqual(kept.status_code, 200, kept.text)  # ... but the Employee's saved one is kept by name
        self.assertEqual((kept.json()["item"]["job_grade"]["name"], kept.json()["item"]["job_grade"]["active"]),
                         ("Staff", False))
        options = self.client.get("/merchant/api/master-group/options").json()
        self.assertFalse(next(c for c in options["job_grade_codes"] if c["id"] == staff["id"])["active"])

    def test_codes_in_use_are_never_deleted_even_by_terminated_employees(self):
        grade, title, unused = self.grades["GRD00003"], self.titles["JOB00002"], self.titles["JOB00003"]
        item = self.create(job_grade_code_id=grade["id"], job_title_code_id=title["id"], hire_date="2026-01-05")
        for group, code in (("JOB_GRADE", grade), ("JOB_TITLE", title)):
            self.assertEqual(self.client.delete(f"{self.COMMON}/{code['id']}?group={group}").status_code, 409)
        ended = self.client.post(f"{API}/{item['id']}/terminate", json={"termination_date": "2026-11-01"})
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertEqual(self.client.delete(f"{self.COMMON}/{grade['id']}?group=JOB_GRADE").status_code, 409)
        self.assertEqual(self.client.delete(f"{self.COMMON}/{unused['id']}?group=JOB_TITLE").status_code, 200)
        with self.factory() as db:
            saved = db.get(Employee, item["id"])
            self.assertEqual((saved.i_job_grade_code_id, saved.i_job_title_code_id), (grade["id"], title["id"]))

    def test_audit_moves_only_on_a_real_job_change(self):
        item = self.create(job_title_code_id=self.titles["JOB00001"]["id"])
        same = self.client.put(f"{API}/{item['id']}", json={"job_title_code_id": self.titles["JOB00001"]["id"],
                                                             "job_grade_code_id": ""}).json()["item"]
        self.assertEqual((same["row_version"], same["updated_at"]), (item["row_version"], item["updated_at"]))
        changed = self.client.put(f"{API}/{item['id']}",
                                  json={"job_title_code_id": self.titles["JOB00006"]["id"]}).json()["item"]
        self.assertEqual((changed["row_version"], changed["job_title"]["name"]),
                         (item["row_version"] + 1, "Store Manager"))
        self.assertIsNotNone(changed["updated_by"])

    def test_already_initialized_merchant_gets_only_the_job_groups(self):
        """Merchant 20 case: COLOR / UNIT initialized before this step stay exactly as they are."""
        with self.factory() as db:
            with patch.object(mg_svc, "COMMON_MAINTENANCE_GROUPS", ("COLOR", "UNIT")):
                mg_svc.initialize_merchant_common_codes(db, 11, 1)
            db.query(CommonCode).filter(CommonCode.i_merchant_id == 11, CommonCode.c_code == "CLR00001").update(
                {"c_name": "Jet Black"})
            db.commit()
            snapshot = lambda: sorted((r.i_common_code_id, r.c_code, r.c_name, r.b_active) for r in
                                      db.query(CommonCode).filter(CommonCode.i_merchant_id == 11))
            before = snapshot()
            self.assertEqual(sorted(r.c_group_code for r in db.query(MerchantCommonInit).filter(
                MerchantCommonInit.i_merchant_id == 11)), ["COLOR", "UNIT"])
            result = mg_svc.initialize_merchant_common_codes(db, 11, 1)  # the next Merchant visit
            self.assertEqual(sorted(result), ["JOB_GRADE", "JOB_SKILL", "JOB_TITLE"])  # JOB_SKILL: empty group
            after = snapshot()
            self.assertEqual([r for r in after if not r[1].startswith(("GRD", "JOB"))], before)
            self.assertTrue(any(r[1:] == ("CLR00001", "Jet Black", True) for r in after))
            self.assertEqual(len(after) - len(before), 18)
            self.assertEqual(mg_svc.initialize_merchant_common_codes(db, 11, 1), {})


class EmployeePersonalInfoTests(unittest.TestCase):
    """STEP 1A-3 Personal Information: optional Birthday (never in the future) and Gender (fixed list in code); Merchant
    scope only; never in the list display, search or export."""

    login, create, row = EmployeeMasterTests.login, EmployeeMasterTests.create, EmployeeMasterTests.row

    def setUp(self):
        EmployeeMasterTests.setUp(self)
        # The fixture's initial client is the Platform Admin: no Employee access, no Gender list.
        self.platform = (self.client.get("/platform/api/master-group/employee").status_code,
                         self.client.get("/platform/api/master-group/options").json()["genders"])
        self.login("MERCHANT")

    def test_optional_crud_and_key_absent_keeps(self):
        plain = self.create()
        self.assertEqual((plain["birth_date"], plain["gender"], plain["gender_name"]), (None, None, None))
        item = self.create(first_name="Jisoo", birth_date="03/15/1990", gender="female")
        self.assertEqual((item["birth_date"], item["gender"], item["gender_name"]),
                         ("1990-03-15", "FEMALE", "Female"))
        with self.factory() as db:
            saved = db.get(Employee, item["id"])
            self.assertEqual((saved.d_birth_date, saved.c_gender), (date(1990, 3, 15), "FEMALE"))
        kept = self.client.put(f"{API}/{item['id']}", json={"phone": "703-555-0199"}).json()["item"]
        self.assertEqual((kept["birth_date"], kept["gender"]), ("1990-03-15", "FEMALE"))  # keys absent: kept
        changed = self.client.put(f"{API}/{item['id']}", json={"birth_date": "1991-01-02", "gender": "MALE"}).json()["item"]
        self.assertEqual((changed["birth_date"], changed["gender_name"]), ("1991-01-02", "Male"))
        self.assertEqual(self.client.get(API).json()["items"][-1]["gender"], "MALE")  # re-read
        cleared = self.client.put(f"{API}/{item['id']}", json={"birth_date": "", "gender": ""})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual((cleared.json()["item"]["birth_date"], cleared.json()["item"]["gender"]), (None, None))
        self.assertIsNone(self.row(Employee, item["id"]).c_gender)  # not selected = NULL
        for code, label in (("MALE", "Male"), ("FEMALE", "Female")):
            response = self.client.put(f"{API}/{item['id']}", json={"gender": code})
            self.assertEqual((response.status_code, response.json()["item"]["gender_name"]), (200, label))

    def test_validation(self):
        today = date.today()
        tomorrow = date.fromordinal(today.toordinal() + 1)
        cases = [({"birth_date": tomorrow.isoformat()}, "birth_date", "Birthday cannot be in the future."),
                 ({"birth_date": "13/45/1990"}, "birth_date", "Enter Birthday as MM-DD-YYYY."),
                 ({"birth_date": "02-30-1990"}, "birth_date", "Enter Birthday as MM-DD-YYYY."),
                 ({"gender": "UNKNOWN"}, "gender", "Select a valid Gender."),
                 ({"gender": "M"}, "gender", "Select a valid Gender."),  # an abbreviation is not a code
                 ({"gender": "NON_BINARY"}, "gender", "Select a valid Gender."),  # removed from the list
                 ({"gender": "PREFER_NOT_TO_SAY"}, "gender", "Select a valid Gender.")]
        for payload, field, message in cases:
            response = self.client.post(API, json={"first_name": "X", **payload})
            self.assertEqual(response.status_code, 422, (payload, response.text))
            self.assertEqual(response.json()["errors"][field], message)
        self.assertEqual(self.create(birth_date=today.isoformat())["birth_date"], today.isoformat())  # today is allowed
        item = self.create(birth_date="1990-03-15")
        self.assertEqual(self.client.put(f"{API}/{item['id']}", json={"birth_date": tomorrow.isoformat()}).status_code, 422)
        self.assertEqual(self.row(Employee, item["id"]).d_birth_date, date(1990, 3, 15))  # refused: unchanged

    def test_not_in_export_search_or_pos_and_options_merchant_only(self):
        item = self.create(birth_date="1990-03-15", gender="FEMALE", phone="703-555-0101")
        header = self.client.get(f"{API}/export", params={"format": "csv"}).content.decode("utf-8-sig").split("\r\n")[0]
        for word in ("Birth", "Gender"):
            self.assertNotIn(word, header)
        self.assertEqual(self.client.get(API, params={"q": "1990"}).json()["items"], [])  # never searched
        self.assertEqual(self.client.get(API, params={"q": "FEMALE"}).json()["items"], [])
        options = self.client.get("/merchant/api/master-group/options").json()
        self.assertEqual(options["genders"], [{"code": "MALE", "name": "Male"}, {"code": "FEMALE", "name": "Female"}])
        self.login("STORE")
        self.assertEqual(self.client.get("/store/api/master-group/options").json()["genders"], [])
        self.assertEqual(self.client.get(API).status_code, 403)
        self.assertEqual(self.client.put(f"{API}/{item['id']}", json={"gender": "MALE"}).status_code, 403)
        self.assertEqual(self.row(Employee, item["id"]).c_gender, "FEMALE")

    def test_other_merchant_platform_and_terminated_access(self):
        fid = self.foreign["id"]
        self.assertEqual(self.client.put(f"{API}/{fid}", json={"birth_date": "1990-01-01"}).status_code, 404)
        self.assertNotIn(fid, [i["id"] for i in self.client.get(API).json()["items"]])
        self.assertIsNone(self.row(Employee, fid).d_birth_date)
        item = self.create(birth_date="1990-03-15", hire_date="2026-01-05")
        self.client.post(f"{API}/{item['id']}/terminate", json={"termination_date": "2026-11-01"})
        self.assertEqual(self.client.put(f"{API}/{item['id']}", json={"gender": "MALE"}).status_code, 409)  # view only
        self.assertEqual(self.platform, (403, []))

    def test_audit_moves_only_on_a_real_change(self):
        item = self.create(birth_date="1990-03-15", gender="MALE")
        same = self.client.put(f"{API}/{item['id']}", json={"birth_date": "03/15/1990", "gender": "male"}).json()["item"]
        self.assertEqual((same["row_version"], same["updated_at"]), (item["row_version"], item["updated_at"]))
        changed = self.client.put(f"{API}/{item['id']}", json={"gender": "FEMALE"}).json()["item"]
        self.assertEqual(changed["row_version"], item["row_version"] + 1)  # current rule: any real change bumps

    def test_database_check_refuses_an_unknown_gender(self):
        for code in ("OTHER", "NON_BINARY", "PREFER_NOT_TO_SAY"):  # the CHECK follows the Male / Female list
            with self.subTest(code=code), self.factory() as db, self.assertRaises(IntegrityError):
                db.query(Employee).filter(Employee.i_employee_id == self.foreign["id"]).update({"c_gender": code})
                db.commit()


class EmployeeDateFormatTests(unittest.TestCase):
    """Common Date standard on the Employee API: MM-DD-YYYY (and the earlier MM/DD/YYYY) accepted besides ISO; responses
    stay ISO; export and messages show MM-DD-YYYY; the DB keeps DATE."""

    login, create, assign, row = (EmployeeMasterTests.login, EmployeeMasterTests.create, EmployeeMasterTests.assign,
                                  EmployeeMasterTests.row)

    def setUp(self):
        EmployeeMasterTests.setUp(self)
        self.login("MERCHANT")

    def test_every_employee_date_accepts_the_screen_formats_and_returns_iso(self):
        item = self.create(birth_date="04-22-1961", hire_date="11/01/2026")
        self.assertEqual((item["birth_date"], item["hire_date"]), ("1961-04-22", "2026-11-01"))
        with self.factory() as db:
            saved = db.get(Employee, item["id"])
            self.assertEqual((saved.d_birth_date, saved.d_hire_date), (date(1961, 4, 22), date(2026, 11, 1)))
        same = self.client.put(f"{API}/{item['id']}", json={"birth_date": "1961-04-22", "hire_date": "11-01-2026"})
        self.assertEqual(same.json()["item"]["row_version"], item["row_version"])  # the same date: no change
        start = self.assign(item["id"], start_date="11-02-2026")
        self.assertEqual(start.status_code, 201, start.text)
        self.assertEqual(start.json()["item"]["start_date"], "2026-11-02")
        early = self.assign(item["id"], store_id=13, start_date="10-01-2026")
        self.assertEqual(early.json()["errors"]["start_date"], "Start Date must be on or after the Hire Date (11-01-2026).")
        ended = self.client.post(f"{API}/{item['id']}/terminate", json={"termination_date": "11-30-2026"})
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertEqual(ended.json()["item"]["termination_date"], "2026-11-30")
        # the read-only Termination Date may be sent back unchanged in the screen format (no 403)
        self.client.post(f"{API}/{item['id']}/reactivate", json={"hire_date": "12-01-2026"})
        again = self.create(first_name="Two", hire_date="2026-01-05")
        kept = self.client.put(f"{API}/{again['id']}", json={"termination_date": "", "first_name": "Two"})
        self.assertEqual(kept.status_code, 200, kept.text)

    def test_invalid_dates_are_refused_with_the_standard_message(self):
        for key, label in (("birth_date", "Birthday"), ("hire_date", "Hire Date")):
            for bad in ("02-30-2024", "13-01-2020", "2024/01/01", "abc"):
                response = self.client.post(API, json={"first_name": "X", key: bad})
                self.assertEqual(response.status_code, 422, (key, bad, response.text))
                self.assertEqual(response.json()["errors"][key], f"Enter {label} as MM-DD-YYYY.")
        item = self.create(hire_date="2026-01-05")
        bad = self.assign(item["id"], start_date="02-30-2026")
        self.assertEqual(bad.status_code, 422, bad.text)

    def test_export_shows_mm_dd_yyyy(self):
        item = self.create(hire_date="2026-01-05", birth_date="1961-04-22")
        self.assign(item["id"], start_date="2026-01-06")
        self.client.post(f"{API}/{item['id']}/terminate", json={"termination_date": "2026-11-30"})
        text = self.client.get(f"{API}/export", params={"format": "csv"}).content.decode("utf-8-sig")
        header, line = text.split("\r\n")[:2]
        columns = dict(zip(header.split(","), line.split(",")))
        self.assertEqual((columns["Hire Date"], columns["Termination Date"]), ("01-05-2026", "11-30-2026"))
        self.assertNotIn("1961", text)  # Birthday is never exported
        self.assertNotIn("2026-01-05", text)


if __name__ == "__main__":
    unittest.main()
