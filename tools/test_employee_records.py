"""Employee HISTORY and BACKGROUND records (STEP 1A-4) and the Employee status filter (Active / Terminated / All).

Isolated SQLite app (tools.test_employee_master fixture: Merchant 10 with its Stores, an Employee of Merchant 11).
"""
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta

from backend.database.common_code_migration import migrate_common_codes
from backend.models_admin.common_code import CommonCode
from backend.models_admin.employee import Employee
from backend.models_admin.employee_records import EmployeeHistory, EmployeeSkill
from backend.services import master_group as mg_svc
from tools.test_employee_master import API, EmployeeMasterTests
from tools.test_merchant_store_management import inline_page

COMMON = "/merchant/api/master-group/common_code"


class EmployeeRecordTestBase(unittest.TestCase):
    login, create, assign, row = (EmployeeMasterTests.login, EmployeeMasterTests.create, EmployeeMasterTests.assign,
                                  EmployeeMasterTests.row)

    def setUp(self):
        EmployeeMasterTests.setUp(self)
        with redirect_stdout(io.StringIO()):
            migrate_common_codes(self.engine)  # Common groups incl. JOB_SKILL
        self.login("MERCHANT")
        self.assertEqual(self.client.get("/merchant/api/master-group/options").status_code, 200)  # copy-once
        self.employee = self.create(hire_date="2026-01-05")
        self.eid = self.employee["id"]

    def url(self, kind, record_id=None, eid=None):
        return f"{API}/{eid or self.eid}/records/{kind}" + (f"/{record_id}" if record_id else "")

    def add(self, kind, expect=201, eid=None, **values):
        response = self.client.post(self.url(kind, eid=eid), json=values)
        self.assertEqual(response.status_code, expect, response.text)
        return response.json().get("item") if expect in (200, 201) else response.json()

    def items(self, kind, eid=None):
        response = self.client.get(self.url(kind, eid=eid))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def terminate(self):
        response = self.client.post(f"{API}/{self.eid}/terminate", json={"termination_date": "2026-11-01"})
        self.assertEqual(response.status_code, 200, response.text)


class EmployeeHistoryTests(EmployeeRecordTestBase):
    def test_add_list_newest_first_with_author_and_iso_dates(self):
        first = self.add("history", record_date="2026-03-01", subject="Coaching", description="Register training.")
        second = self.add("history", record_date="04-15-2026", subject="Praise", description="Line 1\nLine 2")
        self.add("history", record_date="2026-04-15", subject="Same day later", description="Later entry")
        data = self.items("history")
        self.assertEqual([i["subject"] for i in data["items"]], ["Same day later", "Praise", "Coaching"])  # newest first
        self.assertEqual((second["record_date"], second["description"]), ("2026-04-15", "Line 1\nLine 2"))
        self.assertEqual(first["created_by"], "Pat Merchant")  # a name, never an ID
        self.assertIsNotNone(first["created_at"])
        self.assertEqual((first["edited"], first["updated_by"], first["updated_at"]), (False, None, None))
        self.assertTrue(first["can_edit"] and data["can_add"])
        self.assertFalse(first["can_delete"])
        self.assertNotIn("i_created_by", str(data))

    def test_required_fields_and_dates(self):
        errors = self.add("history", expect=422, record_date="", subject=" ", description="")["errors"]
        self.assertEqual(errors, {"record_date": "Record Date is required.", "subject": "Subject is required.",
                                  "description": "Description is required."})
        bad = self.add("history", expect=422, record_date="02-30-2026", subject="S", description="D")["errors"]
        self.assertEqual(bad["record_date"], "Enter Record Date as MM-DD-YYYY.")
        long = self.add("history", expect=422, record_date="2026-01-01", subject="x" * 201, description="D")["errors"]
        self.assertIn("subject", long)
        self.assertEqual(self.add("history", expect=422, record_date="2026-01-01", subject="S", description="D",
                                  i_created_by=99)["errors"], {"i_created_by": "This field cannot be saved here."})

    def test_edit_by_author_only_and_no_op_save_keeps_audit(self):
        item = self.add("history", record_date="2026-03-01", subject="Coaching", description="First")
        same = self.client.put(self.url("history", item["id"]),
                               json={"record_date": "03-01-2026", "subject": "Coaching", "description": "First"})
        self.assertEqual(same.status_code, 200, same.text)
        self.assertEqual((same.json()["item"]["row_version"], same.json()["item"]["edited"]), (1, False))
        with self.factory() as db:
            created = db.get(EmployeeHistory, item["id"])
            created_at, created_by = created.dt_created, created.i_created_by
        changed = self.client.put(self.url("history", item["id"]), json={"description": "First, corrected"}).json()["item"]
        self.assertEqual((changed["edited"], changed["updated_by"], changed["row_version"]), (True, "Pat Merchant", 2))
        self.assertIsNotNone(changed["updated_at"])
        with self.factory() as db:
            saved = db.get(EmployeeHistory, item["id"])
            self.assertEqual((saved.dt_created, saved.i_created_by, saved.c_subject), (created_at, created_by, "Coaching"))
            saved.i_created_by = 987654  # written by another manager
            db.commit()
        other = self.client.put(self.url("history", item["id"]), json={"subject": "Mine now"})
        self.assertEqual(other.status_code, 403)
        self.assertIn("Only the author", other.text)
        self.assertFalse(self.items("history")["items"][0]["can_edit"])
        self.assertEqual(self.row(EmployeeHistory, item["id"]).c_subject, "Coaching")

    def test_no_delete(self):
        item = self.add("history", record_date="2026-03-01", subject="S", description="D")
        self.assertEqual(self.client.delete(self.url("history", item["id"])).status_code, 403)
        self.assertIsNotNone(self.row(EmployeeHistory, item["id"]))

    def test_terminated_employee_history_add_and_author_edit(self):
        item = self.add("history", record_date="2026-03-01", subject="Before", description="D")
        self.terminate()
        data = self.items("history")
        self.assertTrue(data["can_add"] and data["items"][0]["can_edit"])
        after = self.add("history", record_date="2026-11-02", subject="Exit interview", description="Notes")
        self.assertEqual(after["subject"], "Exit interview")
        self.assertEqual(self.client.put(self.url("history", item["id"]), json={"subject": "Before (fixed)"}).status_code, 200)
        employee = self.row(Employee, self.eid)
        self.assertEqual((employee.c_status, employee.c_employee_code), ("terminated", self.employee["code"]))
        self.assertEqual(self.client.put(f"{API}/{self.eid}", json={"first_name": "X"}).status_code, 409)  # General stays read-only

    def test_scope_isolation_and_access(self):
        fid = self.foreign["id"]  # Merchant 11
        self.assertEqual(self.client.get(self.url("history", eid=fid)).status_code, 404)
        self.add("history", expect=404, eid=fid, record_date="2026-01-01", subject="S", description="D")
        item = self.add("history", record_date="2026-03-01", subject="S", description="D")
        self.assertEqual(self.client.put(f"{API}/{fid}/records/history/{item['id']}", json={"subject": "X"}).status_code, 404)
        self.assertEqual(self.client.get(self.url("bogus")).status_code, 404)
        self.login("STORE")
        self.assertEqual(self.client.get(self.url("history")).status_code, 403)
        self.assertEqual(self.client.post(self.url("history"), json={}).status_code, 403)
        self.assertEqual(self.client.get(f"/store/api/master-group/employee/{self.eid}/records/history").status_code, 404)


class EmployeeBackgroundTests(EmployeeRecordTestBase):
    def skill_code(self, name="Forklift"):
        response = self.client.post(f"{COMMON}?group=JOB_SKILL", json={"name": name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["item"]

    def test_multiple_rows_of_each_kind_with_edit_and_delete(self):
        self.add("education", school="State University", degree="BS", major="Business", graduation_year=2010,
                 remark="Honors")
        self.add("education", school="City College", graduation_year="2006")
        self.add("experience", company="Shop A", position="Cashier", start_date="01-02-2015", end_date="12-31-2018",
                 description="Front counter", remark="Rehire OK")
        current = self.add("experience", company="Shop B", start_date="2019-01-01")  # still working: no End Date
        self.add("certification", name="Food Handler", issuer="County", issued_date="2024-05-01",
                 expiration_date="2027-05-01")
        self.add("certification", name="First Aid", issued_date="2020-01-01")  # no expiration
        forklift, pos = self.skill_code("Forklift"), self.skill_code("POS")
        self.add("skill", skill_code_id=forklift["id"], proficiency="advanced", remark="Licensed")
        self.add("skill", skill_code_id=pos["id"])
        counts = {kind: len(self.items(kind)["items"]) for kind in ("education", "experience", "certification", "skill")}
        self.assertEqual(counts, {"education": 2, "experience": 2, "certification": 2, "skill": 2})
        skills = self.items("skill")["items"]
        self.assertEqual((skills[0]["skill"]["name"], skills[0]["proficiency"], skills[0]["proficiency_name"]),
                         ("Forklift", "ADVANCED", "Advanced"))
        self.assertIsNone(self.items("experience")["items"][0]["end_date"])
        edited = self.client.put(self.url("experience", current["id"]), json={"end_date": "06-30-2026"})
        self.assertEqual(edited.json()["item"]["end_date"], "2026-06-30")
        self.assertTrue(all(i["can_edit"] and i["can_delete"] for i in self.items("education")["items"]))
        gone = self.items("education")["items"][1]["id"]
        self.assertEqual(self.client.delete(self.url("education", gone)).status_code, 200)
        self.assertEqual(len(self.items("education")["items"]), 1)

    def test_validation(self):
        cases = [
            ("education", {"school": ""}, "school", "School is required."),
            ("education", {"school": "S", "graduation_year": 1800}, "graduation_year", "Graduation Year must be between 1900 and 2100."),
            ("education", {"school": "S", "graduation_year": "20x"}, "graduation_year", "Graduation Year is invalid."),
            ("experience", {"company": "C", "start_date": "2020-05-01", "end_date": "2020-04-30"}, "end_date",
             "End Date must be on or after the Start Date (05-01-2020)."),
            ("experience", {"company": "C", "start_date": "13-01-2020"}, "start_date", "Enter Start Date as MM-DD-YYYY."),
            ("certification", {"name": "N", "issued_date": "2024-01-01", "expiration_date": "2023-01-01"},
             "expiration_date", "Expiration Date must be on or after the Issued Date (01-01-2024)."),
            ("skill", {}, "skill_code_id", "Skill Name is required."),
            ("skill", {"skill_code_id": 999999}, "skill_code_id", "Skill Name does not exist."),
        ]
        for kind, payload, field, message in cases:
            with self.subTest(kind=kind, payload=payload):
                self.assertEqual(self.add(kind, expect=422, **payload)["errors"][field], message)
        code = self.skill_code()
        self.assertIn("proficiency", self.add("skill", expect=422, skill_code_id=code["id"], proficiency="GURU")["errors"])
        exp = self.add("experience", company="C", start_date="2020-05-01")
        bad = self.client.put(self.url("experience", exp["id"]), json={"end_date": "2020-01-01"})  # vs the saved start
        self.assertEqual(bad.status_code, 422, bad.text)

    def test_job_skill_common_code_rules(self):
        code = self.skill_code("Barista")
        self.assertEqual(code["code"], "SKL00001")  # AUTO, this Merchant's sequence
        with self.factory() as db:
            other = mg_svc.create_item(db, "common_code", {"name": "Other"}, 1, merchant_id=11, group="JOB_SKILL")
            grade = db.query(CommonCode.i_common_code_id).filter(CommonCode.i_merchant_id == 10,
                                                                 CommonCode.c_code == "GRD00001").scalar()
        for wrong in (other["id"], grade):  # another Merchant's skill / not a JOB_SKILL code
            self.assertIn("skill_code_id", self.add("skill", expect=422, skill_code_id=wrong)["errors"])
        item = self.add("skill", skill_code_id=code["id"], proficiency="EXPERT")
        self.assertEqual(self.client.delete(f"{COMMON}/{code['id']}?group=JOB_SKILL").status_code, 409)  # in use
        self.client.put(f"{COMMON}/{code['id']}?group=JOB_SKILL", json={"name": "Barista", "active": False})
        self.assertIn("skill_code_id", self.add("skill", expect=422, skill_code_id=code["id"])["errors"])  # inactive
        kept = self.client.put(self.url("skill", item["id"]), json={"skill_code_id": code["id"], "remark": "kept"})
        self.assertEqual((kept.status_code, kept.json()["item"]["skill"]["active"]), (200, False))
        options = self.client.get("/merchant/api/master-group/options").json()
        self.assertEqual([p["name"] for p in options["skill_proficiencies"]], ["Beginner", "Intermediate", "Advanced", "Expert"])
        self.assertIn("JOB_SKILL", [g["code"] for g in options["common_groups"]])

    def test_background_remark_is_saved_with_the_employee(self):
        saved = self.client.put(f"{API}/{self.eid}", json={"background_remark": "Bilingual.\nPrefers mornings."})
        self.assertEqual(saved.json()["item"]["background_remark"], "Bilingual.\nPrefers mornings.")
        same = self.client.put(f"{API}/{self.eid}", json={"background_remark": "Bilingual.\nPrefers mornings."})
        self.assertEqual(same.json()["item"]["row_version"], saved.json()["item"]["row_version"])  # no-op save
        self.assertEqual(self.row(Employee, self.eid).c_background_remark, "Bilingual.\nPrefers mornings.")

    def test_terminated_background_is_read_only(self):
        edu = self.add("education", school="S")
        code = self.skill_code()
        skill = self.add("skill", skill_code_id=code["id"])
        self.terminate()
        data = self.items("education")
        self.assertEqual((data["can_add"], data["items"][0]["can_edit"], data["items"][0]["can_delete"]), (False, False, False))
        self.assertIn("Terminated", data["read_only_message"])
        self.assertEqual(self.client.post(self.url("education"), json={"school": "X"}).status_code, 409)
        self.assertEqual(self.client.put(self.url("education", edu["id"]), json={"school": "X"}).status_code, 409)
        self.assertEqual(self.client.delete(self.url("skill", skill["id"])).status_code, 409)
        self.assertIsNotNone(self.row(EmployeeSkill, skill["id"]))

    def test_scope_isolation(self):
        fid = self.foreign["id"]
        self.add("education", expect=404, eid=fid, school="X")
        edu = self.add("education", school="Mine")
        self.assertEqual(self.client.delete(f"{API}/{fid}/records/education/{edu['id']}").status_code, 404)
        self.login("STORE")
        self.assertEqual(self.client.get(self.url("education")).status_code, 403)


class EmployeeStatusFilterTests(EmployeeRecordTestBase):
    def setUp(self):
        super().setUp()
        self.create(first_name="Inactive", status="inactive")
        gone = self.create(first_name="Gone", last_name="Aaron", hire_date="2026-01-05")
        self.client.post(f"{API}/{gone['id']}/terminate", json={"termination_date": "2026-11-01"})
        self.create(first_name="Zed", last_name="Zulu")

    def names(self, **params):
        data = self.client.get(API, params=params).json()
        return [i["first_name"] for i in data["items"]], data

    def test_filters_counts_and_terminated_last(self):
        current, data = self.names(active="current")
        self.assertNotIn("Gone", current)
        self.assertIn("Inactive", current)  # employed (Active + Inactive)
        self.assertEqual(data["status_counts"], {"current": 3, "terminated": 1, "all": 4})
        self.assertEqual(self.names(active="terminated")[0], ["Gone"])
        everyone, _ = self.names(active="all", sort="name")
        self.assertEqual(everyone[-1], "Gone")  # terminated last even though "Aaron" sorts first
        self.assertEqual(self.names(active="all", sort="name", direction="desc")[0][-1], "Gone")
        searched, data = self.names(active="terminated", q="Gone")
        self.assertEqual((searched, data["status_counts"]), (["Gone"], {"current": 0, "terminated": 1, "all": 1}))
        self.assertEqual(self.names(active="current", q="Gone")[0], [])
        page2, data = self.names(active="all", sort="name", size=2, page=2)
        self.assertEqual((page2[-1], data["pages"]), ("Gone", 2))  # paged after the status order
        self.assertEqual(len(self.names(active="active")[0]), 2)  # earlier single-status filter still works


class EmployeeRecordScreenTests(EmployeeRecordTestBase):
    """The screen: HISTORY / BACKGROUND through the Common Child Grid, the fill panes, the status filter."""
    run_browser = EmployeeMasterTests.run_browser

    def test_history_background_and_status_filter_screen(self):
        mine = self.add("history", record_date="2026-03-01", subject="Coaching", description="Line one\nLine two")
        theirs = self.add("history", record_date="2026-02-01", subject="Older note", description="By another manager")
        with self.factory() as db:
            db.get(EmployeeHistory, theirs["id"]).i_created_by = 987654
            db.commit()
        for n in range(30):  # many records: the HISTORY pane scrolls, the frame does not grow
            self.add("history", record_date="2025-01-01", subject=f"Bulk {n}", description="x")
        self.add("education", school="State University", degree="BS", graduation_year=2010, remark="Honors")
        gone = self.create(first_name="Gone", hire_date="2026-01-05")
        self.client.post(f"{API}/{gone['id']}/terminate", json={"termination_date": "2026-11-01"})
        lists = {kind: self.items(kind) for kind in ("history", "education", "experience", "certification", "skill")}
        current = self.client.get(API, params={"active": "current"}).json()
        terminated = self.client.get(API, params={"active": "terminated"}).json()
        options = self.client.get("/merchant/api/master-group/options").json()
        mock = ("window.__errors=[];addEventListener('error',e=>__errors.push(String(e.message)));"
                f"window.__lists={json.dumps(lists)};window.__current={json.dumps(current)};"
                f"window.__terminated={json.dumps(terminated)};window.__options={json.dumps(options)};"
                f"window.__mine={mine['id']};" + r"""
window.requests=[];window.bootstrap=window.bootstrap||{};
const J=(b,s=200)=>new Response(JSON.stringify(b),{status:s,headers:{'Content-Type':'application/json'}});
window.fetch=async(path,options={})=>{const url=new URL(path,'http://x');const method=options.method||'GET';
 requests.push({path:url.pathname,search:url.search,method,body:options.body});
 if(url.pathname.endsWith('/options'))return J(__options);
 if(url.pathname.endsWith('/assignments'))return J({items:[]});
 const rec=/\/records\/(\w+)(?:\/(\d+))?$/.exec(url.pathname);
 if(rec){const kind=rec[1],list=__lists[kind];
   if(method==='GET')return J(list);
   const body=JSON.parse(options.body||'{}');
   if(kind==='history'&&!body.subject)return J({message:'Check the fields.',errors:{subject:'Subject is required.'}},422);
   const item=Object.assign({id:9000+requests.length,created_by:'Pat Merchant',created_at:'2026-11-13T10:00:00',updated_by:null,updated_at:null,edited:false,can_edit:true,can_delete:kind!=='history'},body);
   if(method==='POST')list.items.unshift(item);
   return J({item,message:'Saved.'},method==='POST'?201:200);}
 if(url.pathname==='/merchant/api/master-group/employee')return J(url.searchParams.get('active')==='terminated'?__terminated:__current);
 return J({item:__current.items[0]});};""")
        page = inline_page(self.client.get("/merchant/employee-group/employee-master").text, mock)
        script = "const page=" + json.dumps(page).replace("</", "<\\/") + ";\n" + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const wait=async(f,m)=>{for(let i=0;i<100;i++){try{if(f())return;}catch(e){}await pause(50);}throw Error('timeout: '+m);};
(async()=>{
 const frame=document.createElement('iframe');frame.style.width='1400px';frame.style.height='1000px';frame.srcdoc=page;
 const loaded=new Promise(r=>frame.addEventListener('load',r,{once:true}));document.body.appendChild(frame);await loaded;
 const doc=frame.contentDocument,win=frame.contentWindow,$=id=>doc.getElementById(id);
 await wait(()=>doc.querySelectorAll('#mgBody [data-row-select]').length>=1,'employees listed');
 // Status filter: the common status select, Active by default, real counts.
 const filter=$('mgActiveFilter');
 assert(filter.value==='current','Active is the default filter');
 assert([...filter.options].map(o=>o.textContent).join('|')==='Active (1)|Terminated (1)|All (2)','filter counts: '+[...filter.options].map(o=>o.textContent));
 assert(win.requests.find(r=>r.path==='/merchant/api/master-group/employee').search.includes('active=current'),'first list request uses Active');
 doc.querySelector('#mgBody [data-row-select]').click();
 await wait(()=>doc.querySelectorAll('#mgCghistory [data-cg-row]').length===32,'history loaded');
 // Every tab, BACKGROUND / HISTORY included, keeps one PageFrame height (fill panes scroll inside it).
 const frameH=()=>Math.round($('mgEmpTabs').closest('.mg-md-pageframe').getBoundingClientRect().height);
 const saveTop=()=>Math.round($('mgSave').getBoundingClientRect().top);
 const heights=[],saves=[];
 for(const key of ['General','Address','Terminal','Portal','Background','History']){$('mgEmpTab'+key).click();await pause(220);heights.push(frameH());saves.push(saveTop());}
 assert(new Set(heights).size===1&&new Set(saves).size===1,'same frame and Save position on every tab: '+heights+' / '+saves);
 const hp=$('mgEmpPaneHistory');
 assert(hp.classList.contains('mg-pane-fill')&&hp.scrollHeight>hp.clientHeight+50&&hp.clientHeight>200,'HISTORY scrolls inside the frame: '+hp.scrollHeight+'/'+hp.clientHeight);
 // HISTORY: newest first, author-only Edit, no Delete, row expand.
 const rows=[...doc.querySelectorAll('#mgCghistory [data-cg-row]')];
 const cells=r=>[...r.children].slice(0,4).map(c=>c.textContent.trim());
 assert(cells(rows[0]).join('|')==='03-01-2026|Coaching|Pat Merchant|—','first row: '+cells(rows[0]));
 assert(cells(rows[1])[1]==='Older note','second row older');
 assert(rows[0].querySelector('[data-cg-act="edit"]')&&!rows[1].querySelector('[data-cg-act="edit"]'),'Edit only on own records');
 assert(!doc.querySelector('#mgCghistory [data-cg-act="delete"]'),'no Delete for history');
 const detail=doc.querySelector(`#mgCghistory [data-cg-detail="${win.__mine}"]`);
 assert(detail.hidden,'collapsed');
 rows[0].querySelector('td').click();
 assert(!detail.hidden&&rows[0].getAttribute('aria-expanded')==='true'&&/Line one\s*Line two/.test(detail.textContent)&&/Created 11|Created \d{2}-\d{2}-\d{4}/.test(detail.textContent),'expanded: '+detail.textContent);
 // Add Record: today in MM-DD-YYYY, field error in place, ISO payload, list refreshed.
 doc.querySelector('#mgCghistory [data-cg-act="add"]').click();
 const rd=$('mgCg_history_record_date');
 assert(/^\d{2}-\d{2}-\d{4}$/.test(rd.value)&&rd.hasAttribute('data-awsum-date')&&!rd.name,'Record Date: today, common date input, never in the main Save');
 rd.value='03/02/2026';rd.dispatchEvent(new Event('input',{bubbles:true}));rd.dispatchEvent(new Event('blur'));
 doc.querySelector('#mgCghistory [data-cg-act="save"]').click();
 await wait(()=>/Subject is required/.test(doc.querySelector('#mgCghistory [data-error-for="subject"]').textContent),'server field error shown');
 $('mgCg_history_subject').value='Follow-up';$('mgCg_history_description').value='Done';
 doc.querySelector('#mgCghistory [data-cg-act="save"]').click();
 await wait(()=>win.requests.filter(r=>r.method==='POST'&&/records\/history/.test(r.path)).length===2,'saved');
 const posted=JSON.parse(win.requests.filter(r=>r.method==='POST').pop().body);
 assert(posted.record_date==='2026-03-02'&&posted.subject==='Follow-up','ISO payload: '+JSON.stringify(posted));
 await wait(()=>doc.querySelector('#mgCghistory [data-cg-editor]').classList.contains('d-none'),'editor closed after save');
 // BACKGROUND: five sections in order, compact lists, common date inputs with the period rule.
 $('mgEmpTabBackground').click();await pause(200);
 const heads=[...$('mgEmpPaneBackground').querySelectorAll('.pm-section-head h3')].map(h=>h.textContent.trim());
 assert(heads.join('|')==='Education|Work Experience|Certifications & Licenses|Job Skills|Remark','sections: '+heads);
 assert(/State University/.test($('mgCgeducation').textContent)&&doc.querySelector('#mgCgeducation [data-cg-act="delete"]'),'education row with Delete');
 doc.querySelector('#mgCgexperience [data-cg-act="add"]').click();
 const sd=$('mgCg_experience_start_date'),ed=$('mgCg_experience_end_date');
 sd.value='05012020';sd.dispatchEvent(new Event('input',{bubbles:true}));sd.dispatchEvent(new Event('blur'));
 ed.value='04302020';ed.dispatchEvent(new Event('input',{bubbles:true}));ed.dispatchEvent(new Event('blur'));
 assert(sd.value==='05-01-2020'&&/End Date must be on or after the Start Date \(05-01-2020\)/.test(ed.closest('[data-field-wrap]').querySelector('.invalid-feedback').textContent),'period rule in place');
 doc.querySelector('#mgCgexperience [data-cg-act="cancel"]').click();
 doc.querySelector('#mgCgskill [data-cg-act="add"]').click();
 assert([...$('mgCg_skill_proficiency').options].map(o=>o.textContent).join('|')==='Not specified|Beginner|Intermediate|Advanced|Expert','proficiency list');
 assert(/Common Maintenance/.test(doc.querySelector('#mgCgskill [data-cg-message]').textContent),'no skills yet: where to add them');
 doc.querySelector('#mgCgskill [data-cg-act="cancel"]').click();
 assert($('mgf_background_remark').name==='background_remark','overall Remark is a main-form field');
 // Main Save carries the overall Remark and never a Child Grid field.
 $('mgf_background_remark').value='Bilingual';
 $('mgSave').click();
 await wait(()=>win.requests.some(r=>r.method==='PUT'&&/\/employee\/\d+\/with-image$/.test(r.path)),'main save');
 const payload=JSON.parse(win.requests.find(r=>r.method==='PUT').body.get('payload'));
 assert(payload.background_remark==='Bilingual'&&!('subject' in payload)&&!('school' in payload)&&!('record_date' in payload),'main payload: '+Object.keys(payload));
 // Status filter: Terminated lists only them and the detail panel drops the now-hidden Active employee.
 filter.value='terminated';filter.dispatchEvent(new Event('change',{bubbles:true}));
 await wait(()=>/Gone/.test($('mgBody').textContent),'terminated listed');
 await wait(()=>!$('mgEmpTabs'),'detail panel reset (selected employee not in this filter)');
 assert(win.__errors.length===0,'no script errors: '+win.__errors);
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
