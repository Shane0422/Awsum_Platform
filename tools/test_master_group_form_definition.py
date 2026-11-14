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
  for(const kind of KINDS)for(const part of ['list','add','addValues','edit','editValues']){
    const a=JSON.stringify(got[kind][part]),b=JSON.stringify(FIXTURE[kind][part]);
    assert(a===b,`${kind} ${part} differs:\n${a}\n---\n${b}`);
  }
  assert(JSON.stringify(got.savedHistory)===JSON.stringify(FIXTURE.savedHistory),'history save payload: '+JSON.stringify(got.savedHistory));
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack||String(e)});});
"""))


if __name__ == "__main__":
    unittest.main()
