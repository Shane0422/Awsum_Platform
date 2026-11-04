"""Merchant management in the Common Master-Detail layout (left Agency > Merchant Tree + right detail form).

Runs the real Master Management page script with mocked HTTP; no live API or DB access.
"""
import json
import re
import unittest
from types import SimpleNamespace

from tools import test_image_components as browser
from tools.test_platform_admin_ui import render_admin

ROOT = browser.ROOT
# Page scripts injected inline (web-session.js only pings the live session and is left out).
SCRIPTS = (
    "static/js/money.js", "static/js/money-keypad.js", "static/js/master-detail.js", "static/js/barcode-input.js",
    "static/js/option-group.js", "static/bootstrap/js/bootstrap.bundle.min.js",
    "static/components/image-uploader/image-uploader.js", "static/js/store_devices_panel.js",
)
STYLES = (
    "static/bootstrap/css/bootstrap.min.css", "static/css/brand.css", "static/css/platform-modal.css",
    "static/css/awsum-list.css", "static/css/awsum-master-detail.css",
    "static/components/image-uploader/image-uploader.css",
)


def merchant_page():
    agencies = [
        SimpleNamespace(i_agency_id=10, c_agency_code="AG0010", c_company_name="North Agency", c_agency_name="North"),
        SimpleNamespace(i_agency_id=11, c_agency_code="AG0011", c_company_name="South Agency", c_agency_name="South"),
    ]
    base_render = render_admin.__globals__["render_navigation"]
    base, context, _ = base_render()
    context.update(
        active_page="master", active_module="merchant", brand_logo_path="",
        roles=[], business_types=[SimpleNamespace(c_name="Retail"), SimpleNamespace(c_name="Deli")], merchants=[], agencies=agencies, agency_types=[],
        stores=[], pricing_plans=[], device_categories=[], device_types=[],
        filter={"status": "", "business_type": "", "channel_type": ""},
    )
    html = base.environment.get_template("platform/master_management.html").render(context)
    html = re.sub(r"<script\b[^>]*\bsrc=[^>]*>\s*</script>", "", html)
    html = re.sub(r"<link\b[^>]*>", "", html)
    css = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in STYLES)
    libs = "\n;\n".join((ROOT / path).read_text(encoding="utf-8") for path in SCRIPTS)
    mock = r"""
window.requests=[];
window.confirm=()=>true;
const merchants=[
  {id:1,merchant_code:'CLT0001',merchant_name:'Alpha Mart',primary_agency_id:10,primary_agency_name:'North Agency',business_type:'Retail',status:'active',zip:'30301',city:'Atlanta',state:'GA',country:'United States',notes:'',created_date:'2026-01-01'},
  {id:2,merchant_code:'CLT0002',merchant_name:'Beta Deli',primary_agency_id:10,primary_agency_name:'North Agency',business_type:'Retail',status:'active',zip:'',city:'',state:'',country:'United States',notes:'',created_date:'2026-01-02'},
  {id:3,merchant_code:'CLT0003',merchant_name:'Gamma Shop',primary_agency_id:11,primary_agency_name:'South Agency',business_type:'Retail',status:'inactive',zip:'',city:'',state:'',country:'United States',notes:'',created_date:'2026-01-03'},
];
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
window.fetch=async(path,options={})=>{
  const url=new URL(String(path),'http://127.0.0.1'),method=(options.method||'GET').toUpperCase();
  requests.push({path:url.pathname,method,body:options.body});
  if(url.pathname==='/platform/merchants')return json(merchants);
  if(url.pathname.startsWith('/platform/zip-lookup/'))return json({city:'Atlanta',state:'GA',country:'United States'});
  const saved=/\/platform\/merchant(?:\/(\d+))?(?:\/with-image)?$/.exec(url.pathname);
  if(saved&&(method==='PUT'||method==='POST')){
    const id=Number(saved[1]||4);const row=merchants.find(m=>m.id===id);
    let payload={};
    if(options.body instanceof FormData)payload=JSON.parse(options.body.get('payload')||'{}');
    else if(options.body)payload=JSON.parse(options.body);
    const item={...(row||{id,merchant_code:'CLT0004'}),...payload,id};
    if(row)Object.assign(row,item);else merchants.push(item);
    return json(item);
  }
  if(method==='DELETE'){const id=Number(url.pathname.split('/').pop());merchants.splice(merchants.findIndex(m=>m.id===id),1);return json({ok:true});}
  return json([]);
};
"""
    html = html.replace("<head>", f"<head><script>{mock}</script>", 1)
    html = html.replace("</head>", f"<style>{css}</style></head>", 1)
    # Libraries must exist before the page's own inline scripts run.
    first_inline = html.index("<script id=\"masterSeedData\"")
    return html[:first_inline] + f"<script>{libs}</script>" + html[first_inline:]


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class MerchantMasterDetailBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_merchant_uses_agency_tree_and_inline_existing_form(self):
        script = f"const page={json.dumps(merchant_page()).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=(ms=40)=>new Promise(resolve=>setTimeout(resolve,ms));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1440px;height:900px';frame.srcdoc=page;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),field=name=>find('field_'+name);
  const body=find('masterListBody');
  await wait(()=>body.textContent.includes('Alpha Mart'),'merchant list');
  // Layout: the Common Master-Detail workspace; the existing edit modal sits inline in the right panel.
  const workspace=doc.querySelector('.mg-md-workspace'),list=doc.querySelector('.mg-md-list-panel'),panel=doc.querySelector('.mg-md-form-panel');
  assert(workspace&&list&&panel&&panel.contains(find('masterEditModal')),'Common workspace with the existing form on the right');
  const lb=list.getBoundingClientRect(),pb=panel.getBoundingClientRect();
  assert(lb.right<=pb.left&&Math.abs(lb.top-pb.top)<2,'list left, detail right');
  assert(list.contains(find('masterSearch'))&&list.contains(find('storeAgencyCombo'))&&list.contains(find('masterStatusFilter'))&&list.contains(find('storeListPager'))&&find('storeListPager').contains(find('storePageSize')),'Search / Agency / Status above the list, Rows + paging below');
  assert(find('masterListCol').classList.contains('d-none')&&find('storeToolsArea').classList.contains('d-none'),'old full-width list and filter card are replaced');
  assert(doc.querySelector('.mg-md-form-title').textContent.trim()==='MERCHANT INFORMATION','panel title');
  assert(find('masterEditModal').hidden&&!find('masterDetailEmpty').classList.contains('d-none')&&find('masterDetailActions').classList.contains('d-none'),'empty state until a Merchant is selected');
  assert(find('masterListHeader').querySelectorAll('th').length===1&&find('masterListHeader').textContent.trim().startsWith('Agency / Merchant'),'single Agency / Merchant column: '+find('masterListHeader').innerHTML);
  // Tree: Agency > Merchant.
  const groups=()=>[...body.querySelectorAll('tr[data-tree-toggle]')];
  const groupText=()=>groups().map(tr=>tr.textContent.replace(/[\s−+]/g,'')).join('|');
  assert(groupText()==='NorthAgency2|SouthAgency1','Agency groups with counts: '+groupText());
  assert([...body.querySelectorAll('tr[data-row-select]')].map(tr=>tr.textContent.trim()).join('|')==='Alpha Mart|Beta Deli|Gamma Shop','merchant rows sorted inside agencies');
  groups()[0].click();await tick();
  assert(groups()[0].getAttribute('aria-expanded')==='false'&&!body.textContent.includes('Alpha Mart'),'collapse an Agency');
  groups()[0].click();await tick();
  // Select -> the existing form (tabs, values) shows immediately in the panel, no modal overlay.
  body.querySelector('tr[data-row-select="1"]').click();
  await wait(()=>!find('masterEditModal').hidden&&field('merchant_name')?.value==='Alpha Mart','selected merchant');
  const modal=find('masterEditModal');
  assert(win.getComputedStyle(modal).position==='static'&&!modal.classList.contains('show')&&!doc.querySelector('.modal-backdrop'),'inline, not a Bootstrap modal');
  assert(body.querySelector('tr[data-row-select="1"]').classList.contains('selected'),'selected row highlighted');
  assert(field('primary_agency_id').value==='10'&&field('zip').value==='30301'&&modal.querySelectorAll('.merchant-tab-btn').length>=4,'existing fields and tabs');
  const actions=find('masterDetailActions');
  assert(!actions.classList.contains('d-none')&&actions.contains(find('btnModalSave'))&&actions.contains(find('btnModalDelete'))&&actions.contains(find('btnModalCancel')),'Cancel / Delete / Save in the panel header');
  assert(find('btnModalSave').textContent.includes('Save Changes')&&find('btnModalSave').classList.contains('btn-sm'),'existing Save Changes action, MD button size');
  // ZIP auto lookup is still bound.
  field('zip').focus();field('zip').value='30303';field('zip').dispatchEvent(new win.Event('change'));
  await wait(()=>win.requests.some(r=>r.path==='/platform/zip-lookup/30303'),'ZIP lookup');
  // Validation still blocks the save.
  const name=field('merchant_name');
  name.value='';name.dispatchEvent(new win.Event('input',{bubbles:true}));
  const beforeInvalid=win.requests.length;
  find('btnModalSave').click();await tick(120);
  assert(!win.requests.slice(beforeInvalid).some(r=>r.method==='PUT')&&!find('masterModalError').classList.contains('d-none'),'existing validation blocks the save');
  // Save Changes: existing update API; the record stays on screen.
  name.value='Alpha Market';name.dispatchEvent(new win.Event('input',{bubbles:true}));
  find('btnModalSave').click();
  await wait(()=>win.requests.some(r=>r.method==='PUT'&&r.path.startsWith('/platform/merchant/1')),'update request');
  await wait(()=>body.textContent.includes('Alpha Market'),'list refreshed');
  assert(!modal.hidden&&field('merchant_name').value==='Alpha Market'&&body.querySelector('tr[data-row-select="1"]').classList.contains('selected'),'saved record stays selected in the panel');
  // Cancel reverts unsaved edits.
  field('merchant_name').value='Changed';find('btnModalCancel').click();await tick();
  assert(field('merchant_name').value==='Alpha Market'&&!modal.hidden,'Cancel reverts to the saved values');
  // Switching records loads the other Merchant.
  body.querySelector('tr[data-row-select="3"]').click();
  await wait(()=>field('merchant_name').value==='Gamma Shop','switch record');
  assert(field('primary_agency_id').value==='11'&&!body.querySelector('tr[data-row-select="1"]').classList.contains('selected'),'selection moves');
  // Add: empty create form on the right; Cancel returns to the empty state.
  find('btnListAdd').click();await tick();
  assert(!modal.hidden&&field('merchant_name').value===''&&find('btnModalSave').textContent.includes('Create Merchant')&&!body.querySelector('tr.selected'),'Add shows the create form');
  find('btnModalCancel').click();await tick();
  assert(modal.hidden&&!find('masterDetailEmpty').classList.contains('d-none'),'Cancel on a new Merchant closes the form');
  // Delete from the panel: existing delete API, then the empty state.
  body.querySelector('tr[data-row-select="2"]').click();
  await wait(()=>field('merchant_name').value==='Beta Deli','select for delete');
  find('btnModalDelete').click();
  await wait(()=>doc.querySelector('#appConfirmModal.show, .modal.show'),'confirm dialog');
  (doc.querySelector('#appConfirmModalOk')||doc.querySelector('.modal.show .btn-danger,.modal.show .btn-primary')).click();
  await wait(()=>win.requests.some(r=>r.method==='DELETE'&&r.path.endsWith('/2')),'delete request');
  await wait(()=>modal.hidden&&!body.textContent.includes('Beta Deli'),'empty state after delete');
  // Search / Agency filter / paging still drive the same list.
  find('storeAgencyCombo').value='11';find('storeAgencyCombo').dispatchEvent(new win.Event('change'));
  await wait(()=>!body.textContent.includes('Alpha Market')&&body.textContent.includes('Gamma Shop'),'agency filter');
  for(const width of [1440,768,375]){
    frame.style.width=width+'px';await tick();
    assert(doc.documentElement.scrollWidth<=width+1,'no page overflow at '+width);
  }
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
