"""Common Framework P1-a: the Tax form is built from its declarative Field Definition (FORM_FIELDS.tax in
static/js/master_group.js) with the existing field() / input() / select() / textarea() helpers.

The fixture tools/fixtures/master_group_tax_form.json holds the canonical DOM of the Tax form (Add and Edit) as the
earlier hand-written formHtml() case rendered it, captured before the change. The browser test renders the real page
script against a mocked API (no DB, nothing reaches the network) and requires the same DOM, the same filled values,
the same payload (Rate 0 and 4 decimal places sent as typed), field errors in their slots and the read-only state.
"""
import json
import re
import unittest
from pathlib import Path

from tools import test_employee_master as employee_master
from tools import test_employee_records as employee_records
from tools import test_image_components as browser
from tools import test_merchant_store_management as store_management
from tools.test_master_group_ui import render_list, toast_head

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "master_group_tax_form.json"

# Canonical DOM: tag, sorted attributes, collapsed text. Values typed into inputs are properties, not attributes,
# so the structure is compared here and the filled values separately.
CANON_JS = r"""
const canon=(node)=>{
  if(node.nodeType===3){const t=node.textContent.replace(/\s+/g,' ').trim();return t?JSON.stringify(t):'';}
  if(node.nodeType!==1)return '';
  const attrs=[...node.attributes].map(a=>`${a.name}=${JSON.stringify(a.value)}`).sort().join(' ');
  const kids=[...node.childNodes].map(canon).filter(Boolean).join('');
  return `<${node.tagName.toLowerCase()}${attrs?' '+attrs:''}>${kids}</${node.tagName.toLowerCase()}>`;
};
"""

MOCK_JS = r"""
window.requests=[];
window.bootstrap={Modal:class{show(){}hide(){}}};
window.AwsumConfirm={show:async()=>({confirmed:true,choice:null})};
const rows=[{id:1,code:'TAX00001',name:'State',rate:'7.0000',active:true,notes:'Main rate',in_use:false},
            {id:2,code:'TAX00002',name:'City',rate:'1.5000',active:false,notes:'',in_use:false},
            {id:3,code:'TAX00003',name:'Locked',rate:'2.0000',active:true,notes:'',in_use:false,read_only:true,read_only_message:'View only.'}];
window.fetch=async(path,options={})=>{
  const url=new URL(path,'http://127.0.0.1'),method=options.method||'GET';
  const payload=options.body?JSON.parse(options.body):null;
  requests.push({path:url.pathname,method,payload});
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({stores:[],taxes:[]}));
  if(method==='PUT'||method==='POST'){
    if(payload&&payload.rate==='bad')return new Response(JSON.stringify({detail:'Please check the highlighted fields.',
      errors:{rate:'Enter Tax Rate as a number from 0 to 100 with up to 4 decimal places.'}}),{status:422});
    const item={...(rows.find(r=>url.pathname.endsWith('/'+r.id))||{id:99,code:'TAX00099'}),...payload};
    return new Response(JSON.stringify({item}),{status:method==='POST'?201:200});
  }
  return new Response(JSON.stringify({items:rows,total:rows.length,page:1,pages:1,sort:'code',direction:'asc'}));
};
"""


def tax_page():
    js = "\n".join((browser.ROOT / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")
                   for name in ("money", "option-group", "master-detail", "master_group"))
    html = render_list("tax", "Tax")
    html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
    html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", toast_head(), 1)
    return html.replace("</body>", f"<script>{MOCK_JS}\n{js}</script></body>")


# Opens the page in a frame, then Add and Edit; returns {create, edit, values} (used by the test and the capture).
OPEN_JS = r"""
const tick=()=>new Promise(r=>setTimeout(r,40));
const wait=async(check,label)=>{for(let i=0;i<100;i++){try{if(check())return;}catch(e){}await tick();}throw Error('Timed out: '+label);};
async function openTax(){
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1280px;height:800px';frame.srcdoc=PAGE;
  const loaded=new Promise(r=>frame.addEventListener('load',r,{once:true}));document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),f=n=>find('mgf_'+n);
  await wait(()=>find('mgBody').textContent.includes('State'),'list');
  find('mgAddBtn').click();
  await wait(()=>f('name')&&f('rate')&&f('active').options.length,'add form');
  const create=canon(find('mgFields'));
  doc.querySelector('#mgBody [data-row-select="1"]').click();
  await wait(()=>f('rate').value==='7.0000','edit form');
  const edit=canon(find('mgFields'));
  const values={name:f('name').value,rate:f('rate').value,active:f('active').value,notes:f('notes').value};
  return {frame,doc,win,find,f,create,edit,values};
}
"""


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class TaxFormDefinitionBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_tax_form_from_definition_keeps_dom_values_payload_errors_and_read_only(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        script = (f"const PAGE={json.dumps(tax_page())};\nconst FIXTURE={json.dumps(fixture)};\n".replace("</", "<\\/")
                  + CANON_JS + OPEN_JS + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
(async()=>{
  const {doc,win,find,f,create,edit,values}=await openTax();
  // 1. The same DOM as the earlier hand-written Tax form (Add and Edit).
  assert(create===FIXTURE.create,'Add form DOM differs:\n'+create+'\n---\n'+FIXTURE.create);
  assert(edit===FIXTURE.edit,'Edit form DOM differs:\n'+edit+'\n---\n'+FIXTURE.edit);
  assert(JSON.stringify(values)===JSON.stringify(FIXTURE.values),'filled values: '+JSON.stringify(values));
  // 2. Rate 0 and 4 decimal places are sent as typed (the server validates; tools.test_master_group covers the rules).
  const lastWrite=()=>win.requests.filter(r=>r.method==='PUT'||r.method==='POST').pop();
  f('rate').value='0';find('mgSave').click();
  await wait(()=>lastWrite()&&lastWrite().payload.rate==='0','save rate 0');
  const zero=lastWrite();
  assert(zero.method==='PUT'&&zero.path.endsWith('/tax/1')&&zero.payload.name==='State'&&zero.payload.active===true&&zero.payload.notes==='Main rate','payload: '+JSON.stringify(zero));
  assert(Object.keys(zero.payload).sort().join()==='active,name,notes,rate','same payload keys: '+Object.keys(zero.payload));
  doc.querySelector('#mgBody [data-row-select="1"]').click();await wait(()=>f('rate'),'reopen');
  f('rate').value='7.1234';find('mgSave').click();
  await wait(()=>lastWrite().payload.rate==='7.1234','save 4 decimals');
  // 3. Field errors (422 errors map) in the field's slot.
  doc.querySelector('#mgBody [data-row-select="1"]').click();await wait(()=>f('rate'),'reopen');
  f('rate').value='bad';find('mgSave').click();
  await wait(()=>f('rate').classList.contains('is-invalid'),'field error');
  assert(find('mgFields').querySelector('[data-error-for="rate"]').textContent==='Enter Tax Rate as a number from 0 to 100 with up to 4 decimal places.','error text in the Rate slot');
  assert(!f('name').classList.contains('is-invalid'),'only the failing field is marked');
  // 4. New record: POST with the same keys; the code is not sent.
  find('mgAddBtn').click();await wait(()=>f('name')&&f('name').value==='','add again');
  f('name').value='Exempt';f('rate').value='0';find('mgSave').click();
  await wait(()=>lastWrite().method==='POST','create');
  const created=lastWrite();
  assert(created.path.endsWith('/tax')&&created.payload.name==='Exempt'&&created.payload.rate==='0'&&!('code' in created.payload),'create payload: '+JSON.stringify(created.payload));
  // 5. Read-only row: every input disabled, Save disabled (the server refuses writes too).
  doc.querySelector('#mgBody [data-row-select="3"]').click();
  await wait(()=>f('rate')&&f('rate').value==='2.0000','read-only row');
  assert([...find('mgFields').querySelectorAll('input, select, textarea')].every(el=>el.disabled)&&find('mgSave').disabled,'read-only: inputs and Save disabled');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack||String(e)});});
""")
        self.run_browser(script)


CATEGORY1_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "master_group_category1_form.json"

# Category I (P1-b3a) on the real page script with a mocked API; 422 for an out-of-range Display Order is the
# server's own message (master_group._int), so the field-error path is the real one.
CATEGORY1_MOCK_JS = r"""
window.requests=[];
window.bootstrap={Modal:class{show(){}hide(){}}};
window.AwsumConfirm={show:async()=>({confirmed:true,choice:null})};
const rows=[{id:1,code:'C100001',name:'Food',display_order:0,active:true,notes:'Main group',in_use:false},
            {id:2,code:'C100002',name:'Hardware',display_order:20,active:false,notes:'',in_use:false},
            {id:3,code:'C100003',name:'Locked',display_order:5,active:true,notes:'',in_use:false,read_only:true,read_only_message:'View only.'}];
window.fetch=async(path,options={})=>{
  const url=new URL(path,'http://127.0.0.1'),method=options.method||'GET';
  const payload=options.body?JSON.parse(options.body):null;
  requests.push({path:url.pathname,method,payload});
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({stores:[],taxes:[],category1:rows}));
  if(method==='PUT'||method==='POST'){
    const order=Number(payload&&payload.display_order);
    if(payload&&payload.display_order!==''&&(order<0||order>1000000))return new Response(JSON.stringify({detail:'Please check the highlighted fields.',
      errors:{display_order:'Display Order must be between 0 and 1000000.'}}),{status:422});
    const item={...(rows.find(r=>url.pathname.endsWith('/'+r.id))||{id:99,code:'C100099'}),...payload};
    return new Response(JSON.stringify({item}),{status:method==='POST'?201:200});
  }
  return new Response(JSON.stringify({items:rows,total:rows.length,page:1,pages:1,sort:'display_order',direction:'asc'}));
};
"""


def category1_page():
    js = "\n".join((browser.ROOT / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")
                   for name in ("money", "option-group", "master-detail", "master_group"))
    html = render_list("category1", "Category I")
    html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
    html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", toast_head(), 1)
    return html.replace("</body>", f"<script>{CATEGORY1_MOCK_JS}\n{js}</script></body>")


OPEN_CATEGORY1_JS = r"""
const tick=()=>new Promise(r=>setTimeout(r,40));
const wait=async(check,label)=>{for(let i=0;i<100;i++){try{if(check())return;}catch(e){}await tick();}throw Error('Timed out: '+label);};
async function openCategory1(){
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1280px;height:800px';frame.srcdoc=PAGE;
  const loaded=new Promise(r=>frame.addEventListener('load',r,{once:true}));document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),f=n=>find('mgf_'+n);
  await wait(()=>find('mgBody').textContent.includes('Food'),'list');
  find('mgAddBtn').click();
  await wait(()=>f('name')&&f('display_order')&&f('active').options.length,'add form');
  const create=canon(find('mgFields'));
  const addValues={name:f('name').value,display_order:f('display_order').value,active:f('active').value,notes:f('notes').value};
  doc.querySelector('#mgBody [data-row-select="1"]').click();
  await wait(()=>f('name').value==='Food','edit form');
  const edit=canon(find('mgFields'));
  const values={name:f('name').value,display_order:f('display_order').value,active:f('active').value,notes:f('notes').value};
  return {frame,doc,win,find,f,create,edit,addValues,values};
}
"""


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class Category1FormDefinitionBrowserTests(unittest.TestCase):
    """Common Framework P1-b3a: Category I from FORM_FIELDS.category1 keeps the earlier hand-written form's DOM
    (tools/fixtures/master_group_category1_form.json, captured before the change), values, payloads, the server's
    Display Order field error and the read-only state."""
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_category1_form_from_definition_keeps_dom_values_payload_errors_and_read_only(self):
        fixture = json.loads(CATEGORY1_FIXTURE.read_text(encoding="utf-8"))
        script = (f"const PAGE={json.dumps(category1_page())};\nconst FIXTURE={json.dumps(fixture)};\n".replace("</", "<\\/")
                  + CANON_JS + OPEN_CATEGORY1_JS + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
(async()=>{
  const {doc,win,find,f,create,edit,addValues,values}=await openCategory1();
  assert(create===FIXTURE.create,'Add form DOM differs:\n'+create+'\n---\n'+FIXTURE.create);
  assert(edit===FIXTURE.edit,'Edit form DOM differs:\n'+edit+'\n---\n'+FIXTURE.edit);
  assert(JSON.stringify(addValues)===JSON.stringify(FIXTURE.addValues),'Add values: '+JSON.stringify(addValues));
  assert(JSON.stringify(values)===JSON.stringify(FIXTURE.values),'Edit values (Display Order 0 shown as 0): '+JSON.stringify(values));
  const lastWrite=()=>win.requests.filter(r=>r.method==='PUT'||r.method==='POST').pop();
  // Update payload: the same keys; Display Order 0 is sent as typed.
  find('mgSave').click();
  await wait(()=>lastWrite(),'update');
  const put=lastWrite();
  assert(put.method==='PUT'&&put.path.endsWith('/category1/1')&&JSON.stringify(put.payload)===JSON.stringify({name:'Food',display_order:'0',active:true,notes:'Main group'}),'update payload: '+JSON.stringify(put));
  // Out-of-range Display Order: the server's field error in the Display Order slot.
  doc.querySelector('#mgBody [data-row-select="1"]').click();await wait(()=>f('display_order').value==='0','reopen');
  f('display_order').value='1000001';find('mgSave').click();
  await wait(()=>f('display_order').classList.contains('is-invalid'),'range error');
  assert(find('mgFields').querySelector('[data-error-for="display_order"]').textContent==='Display Order must be between 0 and 1000000.','range message in its slot');
  assert(!f('name').classList.contains('is-invalid'),'only Display Order marked');
  // Create payload: POST, no code, Display Order 0.
  find('mgAddBtn').click();await wait(()=>f('name')&&f('name').value==='','add again');
  f('name').value='Drinks';f('display_order').value='0';find('mgSave').click();
  await wait(()=>lastWrite().method==='POST','create');
  const post=lastWrite();
  assert(post.path.endsWith('/category1')&&JSON.stringify(post.payload)===JSON.stringify({name:'Drinks',display_order:'0',active:true,notes:''}),'create payload: '+JSON.stringify(post.payload));
  // Read-only row: inputs and Save disabled.
  doc.querySelector('#mgBody [data-row-select="3"]').click();
  await wait(()=>f('name')&&f('name').value==='Locked','read-only row');
  assert([...find('mgFields').querySelectorAll('input, select, textarea')].every(el=>el.disabled)&&find('mgSave').disabled,'read-only: inputs and Save disabled');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack||String(e)});});
""")
        self.run_browser(script)


CHILD_GRID_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "master_group_child_grid.json"
GRID_KINDS = ("history", "education", "experience", "certification", "skill")


def _fixed_timestamps(value):
    """Record lists with fixed timestamps, so the rendered rows (Created ... / Edited ...) are the same every run."""
    if isinstance(value, dict):
        return {k: ("2026-11-13T10:00:00" if k.endswith("_at") and v else _fixed_timestamps(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_fixed_timestamps(v) for v in value]
    return value


# Opens the Employee screen and returns, per Common Child Grid: the section DOM (rows), the DOM with the Add
# editor open, the DOM and values with the Edit editor open, and the payload of one Add save.
GRID_JS = r"""
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const waitFor=async(f,m)=>{for(let i=0;i<100;i++){try{if(f())return;}catch(e){}await pause(40);}throw Error('timeout: '+m);};
async function captureGrids(){
  const frame=document.createElement('iframe');frame.style.cssText='width:1400px;height:1000px;border:0';frame.srcdoc=PAGE;
  const loaded=new Promise(r=>frame.addEventListener('load',r,{once:true}));document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,$=id=>doc.getElementById(id);
  await waitFor(()=>doc.querySelector('#mgBody [data-row-select]'),'employees');
  doc.querySelector('#mgBody [data-row-select]').click();
  await waitFor(()=>doc.querySelector('#mgCghistory [data-cg-row]')&&doc.querySelector('#mgCgeducation [data-cg-row]'),'records');
  const out={};
  for(const kind of KINDS){
    const sec=$('mgCg'+kind),act=a=>sec.querySelector(`[data-cg-act="${a}"]`),editor=sec.querySelector('[data-cg-editor]');
    const values=()=>Object.fromEntries([...sec.querySelectorAll('[data-cg-field]')].map(el=>[el.dataset.cgField,el.value]));
    const r={list:canon(sec)};
    act('add').click();await waitFor(()=>!editor.classList.contains('d-none'),kind+' add');
    r.add=canon(sec);r.addValues=values();
    act('cancel').click();await waitFor(()=>editor.classList.contains('d-none'),kind+' cancel');
    const edit=sec.querySelector('[data-cg-act="edit"]');
    if(edit){edit.click();await waitFor(()=>!editor.classList.contains('d-none'),kind+' edit');
      r.edit=canon(sec);r.editValues=values();
      act('cancel').click();await waitFor(()=>editor.classList.contains('d-none'),kind+' cancel edit');}
    out[kind]=r;
  }
  // One Add save per grid kind with fields (the editor's collect + Common Date value -> ISO payload).
  const sec=$('mgCghistory');sec.querySelector('[data-cg-act="add"]').click();
  await waitFor(()=>!sec.querySelector('[data-cg-editor]').classList.contains('d-none'),'history add 2');
  const fld=n=>sec.querySelector(`[data-cg-field="${n}"]`);
  win.AwsumDateInput.setValue(fld('record_date'),'2026-03-01');fld('subject').value='Coaching';fld('description').value='Line one';
  const before=win.requests.length;sec.querySelector('[data-cg-act="save"]').click();
  await waitFor(()=>win.requests.slice(before).some(q=>q.method==='POST'),'history save');
  out.savedHistory=JSON.parse(win.requests.slice(before).find(q=>q.method==='POST').body);
  return out;
}
"""


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class ChildGridDomTests(employee_records.EmployeeRecordTestBase):
    """Common Child Grid (Employee HISTORY + 4 BACKGROUND lists): the same DOM, editor values and payload as the
    fixture tools/fixtures/master_group_child_grid.json, captured before the Common Control Renderer (P1-b1)."""

    run_browser = employee_master.EmployeeMasterTests.run_browser

    def grid_script(self, tail):
        response = self.client.post(f"{employee_records.COMMON}?group=JOB_SKILL", json={"name": "Barista"})
        self.assertEqual(response.status_code, 201, response.text)
        code = response.json()["item"]
        self.add("history", record_date="2026-02-01", subject="Older note", description="By this manager")
        self.add("education", school="State University", degree="BS", graduation_year=2010, major="Math", remark="Honors")
        self.add("experience", company="Acme", position="Clerk", start_date="2020-01-06", end_date="2021-02-01", description="Retail")
        self.add("certification", name="Food Safety", issuer="County", issued_date="2025-05-01", expiration_date="2027-05-01")
        self.add("skill", skill_code_id=code["id"], proficiency="EXPERT")
        lists = _fixed_timestamps({kind: self.items(kind) for kind in GRID_KINDS})
        current = self.client.get(employee_master.API, params={"active": "current"}).json()
        options = self.client.get("/merchant/api/master-group/options").json()
        mock = (f"window.__lists={json.dumps(lists)};window.__current={json.dumps(current)};window.__options={json.dumps(options)};" + r"""
window.requests=[];window.bootstrap=window.bootstrap||{};
const J=(b,s=200)=>new Response(JSON.stringify(b),{status:s,headers:{'Content-Type':'application/json'}});
window.fetch=async(path,options={})=>{const url=new URL(path,'http://x');const method=options.method||'GET';
 requests.push({path:url.pathname,method,body:options.body});
 if(url.pathname.endsWith('/options'))return J(__options);
 if(url.pathname.endsWith('/assignments'))return J({items:[]});
 const rec=/\/records\/(\w+)(?:\/(\d+))?$/.exec(url.pathname);
 if(rec){if(method==='GET')return J(__lists[rec[1]]);
   const item=Object.assign({id:9001,created_by:'Pat Merchant',created_at:'2026-11-13T10:00:00',updated_by:null,updated_at:null,edited:false,can_edit:true,can_delete:true},JSON.parse(options.body||'{}'));
   return J({item,message:'Saved.'},method==='POST'?201:200);}
 if(url.pathname==='/merchant/api/master-group/employee')return J(__current);
 return J({item:__current.items[0]});};""")
        page = store_management.inline_page(self.client.get("/merchant/employee-group/employee-master").text, mock)
        return (f"const PAGE={json.dumps(page)};\nconst KINDS={json.dumps(list(GRID_KINDS))};\n".replace("</", "<\\/")
                + CANON_JS + GRID_JS + tail)

    def test_child_grids_keep_dom_editor_values_and_payload(self):
        fixture = json.loads(CHILD_GRID_FIXTURE.read_text(encoding="utf-8"))
        self.run_browser(self.grid_script(f"const FIXTURE={json.dumps(fixture)};".replace("</", "<\\/") + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
(async()=>{
  const got=await captureGrids();
  // HISTORY Add defaults the Record Date to today: the fixture holds "{today}" there (MM-DD-YYYY of the run day).
  const d=new Date(),today=[d.getMonth()+1,d.getDate()].map(n=>String(n).padStart(2,'0')).join('-')+'-'+d.getFullYear();
  for(const kind of KINDS)for(const part of ['list','add','addValues','edit','editValues']){
    const a=JSON.stringify(got[kind][part]),b=JSON.stringify(FIXTURE[kind][part]).split('{today}').join(today);
    assert(a===b,`${kind} ${part} differs:\n${a}\n---\n${b}`);
  }
  assert(JSON.stringify(got.savedHistory)===JSON.stringify(FIXTURE.savedHistory),'history save payload: '+JSON.stringify(got.savedHistory));
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack||String(e)});});
"""))


def common_controls_source():
    """The shipped Common Control Renderer: the <common-controls> block of master_group.js plus the two constants it
    uses (esc, MONEY_KEYPAD_BUTTON). Taken by its markers, so the test always runs the code the page runs."""
    source = (browser.ROOT / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
    block = source[source.index("  // <common-controls>"):source.index("  // </common-controls>")]
    constants = [re.search(rf"^  const {name} = .*$", source, re.MULTILINE).group() for name in ("esc", "MONEY_KEYPAD_BUTTON")]
    return "\n".join(constants) + "\n" + block


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class FieldTypeRendererTests(unittest.TestCase):
    """Common Framework P1-b2: number / date / money / code / option from a Field Definition, in the main form identity
    (id + name) and the Common Child Grid identity (id + data-cg-field), initialized by bindControls() through the
    existing AwsumDateInput / AwsumMoneyInput / AwsumCodeInput / AwsumOptionGroup."""
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_field_types_render_bind_fill_collect_check_and_bind_once(self):
        components = "\n".join((browser.ROOT / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")
                               for name in ("money", "money-keypad", "date", "code-input", "option-group"))
        harness = ("const makeControls=(state)=>{" + common_controls_source()
                   + "\nreturn {controlHtml,bindControls,controlValue,setControlValue,setControlDisabled,checkControls,formIdent};};")
        script = components + "\n" + harness + "\n" + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
// Listener registrations per element: a second binding of the same control would show up here.
const listeners=new WeakMap();const addListener=EventTarget.prototype.addEventListener;
EventTarget.prototype.addEventListener=function(type,fn,opts){listeners.set(this,(listeners.get(this)||0)+1);return addListener.call(this,type,fn,opts);};
const count=el=>listeners.get(el)||0;
const OPTS=[{value:'A',label:'Alpha'},{value:'B',label:'Beta'},{value:'C',label:'Gamma'}];
const DEFS=[
  {name:'qty',label:'Quantity',type:'number',min:0,max:999,step:1},
  {name:'start_date',label:'Start Date',type:'date',required:true,max:'2030-12-31'},
  {name:'price',label:'Price',type:'money',required:true},
  {name:'zone',label:'Zone',type:'code',max:10},
  {name:'level',label:'Level',type:'option',options:(o)=>o.levels,clearable:true},
  {name:'tags',label:'Tags',type:'option',options:OPTS,multiple:true},
];
const C=makeControls({options:{levels:OPTS}});
const box=document.createElement('div');document.body.appendChild(box);
const identity=(mode,d)=>mode==='form'?C.formIdent(d.name):{id:`mgCg_t_${d.name}`,cgField:d.name};
const render=(mode,defs=DEFS)=>{box.innerHTML=defs.map(d=>`<div data-field-wrap="${d.name}">${C.controlHtml(d,identity(mode,d))}<div class="invalid-feedback" data-error-for="${d.name}"></div></div>`).join('');};
const get=(mode,name)=>mode==='form'?box.querySelector('#mgf_'+name):box.querySelector(`[data-cg-field="${name}"]`);
const values=mode=>Object.fromEntries(DEFS.map(d=>[d.name,C.controlValue(get(mode,d.name))]));
const type=(el,text)=>{el.value=text;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};
(async()=>{
 for(const mode of ['form','grid']){
  const g=n=>get(mode,n);
  // 1. Add rendering: the existing component markup and the identity rule of each place.
  render(mode);
  assert(g('qty').type==='number'&&g('qty').classList.contains('pm-num')&&g('qty').min==='0'&&g('qty').max==='999'&&g('qty').step==='1',mode+' number');
  assert(g('start_date').hasAttribute('data-awsum-date')&&g('start_date').dataset.dateRequired==='true'&&g('start_date').dataset.dateMax==='2030-12-31',mode+' date');
  assert(g('price').closest('.input-group').querySelector('[data-money-keypad]')&&g('price').dataset.moneyMin==='0'&&g('price').required,mode+' money with the keypad button');
  assert(g('zone').hasAttribute('data-awsum-code')&&g('zone').maxLength===10,mode+' code');
  assert(g('level').getAttribute('role')==='radiogroup'&&g('level').querySelectorAll('input[type=radio]').length===3&&g('level').querySelector('.awsum-option-clear'),mode+' option');
  assert(g('tags').hasAttribute('data-option-multiple')&&g('tags').querySelectorAll('input[type=checkbox]').length===3,mode+' multiple option');
  const named=[...box.querySelectorAll('[name]')].filter(el=>el.type!=='radio'&&el.type!=='checkbox');
  if(mode==='form')assert(named.map(el=>el.name).join()==='qty,start_date,price,zone'&&g('level').dataset.name==='level','form: id + name (collected by Save)');
  else assert(named.length===0&&g('level').dataset.cgField==='level'&&g('level').querySelector('input').name==='mgCg_t_level','grid: data-cg-field, no field name (never part of the main Save)');
  // 2. Initialization through the existing components (bindControls = their bindAll).
  C.bindControls(box);
  assert(g('start_date').closest('.awsum-date-group')&&g('start_date').closest('.awsum-date-group').querySelector('.awsum-date-trigger')&&g('start_date').placeholder==='MM-DD-YYYY',mode+' AwsumDateInput bound');
  assert(g('price').classList.contains('text-end')&&g('price').inputMode==='decimal',mode+' AwsumMoneyInput bound');
  assert(g('zone').autocapitalize==='characters',mode+' AwsumCodeInput bound');
  // 3. Edit: values shown through the components.
  const row={qty:5,start_date:'2026-03-01',price:'1234.5',zone:'AB-1',level:'B',tags:['A','C']};
  DEFS.forEach(d=>C.setControlValue(g(d.name),row[d.name]));
  assert(g('qty').value==='5'&&g('start_date').value==='03-01-2026'&&g('price').value==='1,234.50'&&g('zone').value==='AB-1',mode+' filled: '+[g('start_date').value,g('price').value]);
  assert(JSON.stringify(values(mode))==='{"qty":"5","start_date":"2026-03-01","price":"1234.50","zone":"AB-1","level":"B","tags":["A","C"]}',mode+' payload of the saved values: '+JSON.stringify(values(mode)));
  // 4. Changes: Code upper case while typing, money cents entry + grouping on blur, date mask + ISO, option clicks.
  type(g('zone'),'k01-s01');assert(g('zone').value==='K01-S01',mode+' code upper case');
  g('price').dispatchEvent(new Event('focus'));type(g('price'),'2500.5');g('price').dispatchEvent(new Event('blur'));
  assert(g('price').value==='2,500.50',mode+' money grouping: '+g('price').value);
  type(g('start_date'),'04152026');g('start_date').dispatchEvent(new Event('blur'));assert(g('start_date').value==='04-15-2026',mode+' date mask: '+g('start_date').value);
  g('level').querySelector('input[value="C"]').click();g('tags').querySelector('input[value="B"]').click();type(g('qty'),'7');
  assert(JSON.stringify(values(mode))==='{"qty":"7","start_date":"2026-04-15","price":"2500.50","zone":"K01-S01","level":"C","tags":["A","B","C"]}',mode+' changed payload: '+JSON.stringify(values(mode)));
  assert(Object.keys(C.checkControls(box)).length===0,mode+' valid values pass the checks');
  // 5. Validation: Common Date required / range and money format, keyed by the field name for the error display.
  C.setControlValue(g('start_date'),null);type(g('price'),'1,23');
  let errors=C.checkControls(box);
  assert(Object.keys(errors).sort().join()==='price,start_date'&&/Start Date/.test(errors.start_date)&&/monetary/.test(errors.price),mode+' errors: '+JSON.stringify(errors));
  let threw=false;try{C.controlValue(g('price'));}catch(e){threw=e instanceof TypeError;}assert(threw,mode+' invalid money never reaches the payload');
  C.setControlValue(g('start_date'),'2031-01-01');errors=C.checkControls(box);assert(errors.start_date,mode+' date after max refused');
  C.setControlValue(g('start_date'),'2030-12-31');C.setControlValue(g('price'),'0');assert(Object.keys(C.checkControls(box)).length===0,mode+' fixed');
  // 6. Reset: empty values through the components; the option group's own Clear button.
  DEFS.forEach(d=>C.setControlValue(g(d.name),null));
  // (Price is required: an empty required amount is invalid for AwsumMoneyInput, so only its display is read here.)
  const reset=Object.fromEntries(DEFS.filter(d=>d.name!=='price').map(d=>[d.name,C.controlValue(g(d.name))]));
  assert(JSON.stringify(reset)==='{"qty":"","start_date":"","zone":"","level":"","tags":[]}'&&g('price').value==='',mode+' reset: '+JSON.stringify(reset));
  C.setControlValue(g('level'),'A');g('level').querySelector('[data-option-clear]').click();assert(C.controlValue(g('level'))==='',mode+' option Clear');
  // 7. Bound once: repeated bindControls adds no listener; a re-render binds the new controls exactly once again.
  const ids=DEFS.map(d=>d.name),first=ids.map(n=>count(g(n)));
  C.bindControls(box);C.bindControls(box);
  assert(JSON.stringify(ids.map(n=>count(g(n))))===JSON.stringify(first),mode+' repeated bindControls: '+first+' / '+ids.map(n=>count(g(n))));
  const typing=[];g('zone').addEventListener('input',()=>typing.push(g('zone').value));type(g('zone'),'ab');
  assert(typing.length===1&&typing[0]==='AB',mode+' one Code handler: '+typing);
  render(mode);C.bindControls(box);C.bindControls(box);
  assert(JSON.stringify(ids.map(n=>count(g(n))))===JSON.stringify(first),mode+' re-rendered controls bound once: '+ids.map(n=>count(g(n))));
  // 8. Read only: inputs readonly (still sent), option groups disabled; the date picker does not open.
  render(mode,DEFS.map(d=>({...d,readonly:true})));C.bindControls(box);
  assert(['qty','start_date','price','zone'].every(n=>g(n).readOnly),mode+' readonly inputs');
  assert(['level','tags'].every(n=>[...g(n).querySelectorAll('input')].every(i=>i.disabled))&&g('level').classList.contains('is-disabled'),mode+' readonly options');
  g('start_date').closest('.awsum-date-group').querySelector('.awsum-date-trigger').click();
  const picker=document.querySelector('.awsum-date-picker');assert(!picker||picker.classList.contains('is-hidden'),mode+' no picker on a readonly date');
  C.setControlDisabled(g('level'),false);assert([...g('level').querySelectorAll('input')].every(i=>!i.disabled),mode+' option enabled again');
 }
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack||String(e)});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
