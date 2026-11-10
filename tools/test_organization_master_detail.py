"""Merchant and Agency management in the Common Master-Detail layout (left Tree / list + right detail form).

Runs the real Master Management page script with mocked HTTP; no live API or DB access.
"""
import json
import re
import unittest
from types import SimpleNamespace

from tools import test_image_components as browser
from tools.test_platform_admin_ui import render_admin

ROOT = browser.ROOT
# Geometry of the Master-Detail panel with a record open, checked at several window sizes and form scroll
# positions: nothing overlaps the panel header / actions, the form stays inside its panel (which scrolls on its
# own), and the page itself does not scroll under the navigation and Page Header.
LAYOUT_CHECKS = r"""
const checkLayout=async(frame,doc,win,label)=>{
  const box=el=>el.getBoundingClientRect();
  const overlap=(a,b)=>a.left<b.right-1&&b.left<a.right-1&&a.top<b.bottom-1&&b.top<a.bottom-1;
  const nav=doc.querySelector('.platform-header'),pageHeader=doc.querySelector('.awsum-page-header');
  const list=doc.querySelector('.mg-md-list-panel'),panel=doc.querySelector('.mg-md-form-panel');
  const header=panel.querySelector('.mg-md-form-header'),body=panel.querySelector('.mg-md-form-body');
  for(const [width,height] of [[1440,900],[1280,720],[1024,768]]){
    frame.style.width=width+'px';frame.style.height=height+'px';win.scrollTo(0,0);await tick(80);
    const where=label+' '+width+'x'+height;
    assert(doc.documentElement.scrollWidth<=width+1,'no horizontal page overflow: '+where);
    assert(doc.documentElement.scrollHeight<=height+40,'the page itself does not scroll (panels scroll inside): '+where+' '+doc.documentElement.scrollHeight);
    const lb=box(list),pb=box(panel);
    assert(Math.abs(lb.height-pb.height)<2&&Math.abs(lb.top-pb.top)<2&&lb.right<=pb.left,'list and form panels side by side, same height: '+where);
    assert(box(pageHeader).bottom<=pb.top&&box(nav).bottom<=box(pageHeader).top+1,'navigation, Page Header, then panels: '+where);
    const buttons=[...header.querySelectorAll('.btn')].filter(b=>b.offsetParent);
    buttons.forEach((a,i)=>buttons.slice(i+1).forEach(b=>assert(!overlap(box(a),box(b)),'header buttons overlap: '+a.textContent+' / '+b.textContent+' '+where)));
    buttons.forEach(b=>assert(box(b).right<=pb.right+1&&box(b).top>=pb.top,'button inside the panel: '+b.textContent+' '+where));
    for(const position of [0,0.5,1]){
      body.scrollTop=(body.scrollHeight-body.clientHeight)*position;await tick(30);
      const hb=box(header),bb=box(body);
      assert(Math.abs(hb.top-(pb.top+parseFloat(win.getComputedStyle(panel).paddingTop)))<2,'panel header stays on top: '+where+' @'+position);
      assert(bb.top>=hb.bottom-1&&bb.bottom<=pb.bottom+1,'form body sits below the header inside the panel: '+where);
      assert(['auto','scroll'].includes(win.getComputedStyle(body).overflowY),'form body scrolls and clips its content: '+where);
      // The whole form is reachable: its top at the start of the scroll, its bottom at the end.
      const content=box(body.querySelector('.modal-content'));
      if(position===0)assert(Math.abs(content.top-bb.top)<2,'form starts at the top of its scroll area (nothing cut off above): '+where+' '+Math.round(content.top)+'/'+Math.round(bb.top));
      if(position===1)assert(content.bottom<=bb.bottom+1,'form end is reachable by scrolling: '+where);
      buttons.forEach(b=>{const r=box(b),hit=doc.elementFromPoint((r.left+r.right)/2,(r.top+r.bottom)/2);
        assert(hit&&b.contains(hit),'nothing is drawn over '+b.textContent.trim()+' '+where+' @'+position+' (hit '+(hit&&(hit.id||hit.className))+')');});
      const logo=panel.querySelector('.awsum-image-form-image');
      if(logo){const lr=box(logo);
        [...body.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(el=>el.offsetParent).forEach(el=>assert(!overlap(lr,box(el)),'logo overlaps '+el.id+' '+where));}
      assert(Math.abs(box(nav).top)<1&&box(pageHeader).bottom<=pb.top,'navigation and Page Header unchanged while the form scrolls: '+where);
    }
    body.scrollTop=0;
  }
  frame.style.width='1440px';frame.style.height='900px';await tick(80);
};
"""
# Page scripts injected inline (web-session.js only pings the live session and is left out).
SCRIPTS = (
    "static/js/awsum-toast.js", "static/js/money.js", "static/js/money-keypad.js", "static/js/master-detail.js", "static/js/barcode-input.js",
    "static/js/option-group.js", "static/bootstrap/js/bootstrap.bundle.min.js",
    "static/components/image-uploader/image-uploader.js", "static/js/store_devices_panel.js",
)
STYLES = (
    "static/bootstrap/css/bootstrap.min.css", "static/css/brand.css", "static/css/platform-modal.css",
    "static/css/awsum-list.css", "static/css/awsum-master-detail.css",
    "static/css/awsum-toast.css",
    "static/components/image-uploader/image-uploader.css",
)


def module_page(module, external_libs=False):
    """Rendered page with mocked HTTP. external_libs=True loads the libraries as file:// <script src> (top-level pages)."""
    agencies = [
        SimpleNamespace(i_agency_id=10, c_agency_code="AG0010", c_company_name="North Agency", c_agency_name="North"),
        SimpleNamespace(i_agency_id=11, c_agency_code="AG0011", c_company_name="South Agency", c_agency_name="South"),
    ]
    base_render = render_admin.__globals__["render_navigation"]
    base, context, _ = base_render()
    context.update(
        active_page="master", active_module=module, brand_logo_path="",
        roles=[], business_types=[SimpleNamespace(c_name="Retail"), SimpleNamespace(c_name="Deli")], merchants=[], agencies=agencies,
        agency_types=[SimpleNamespace(i_agency_type_id=1, c_agency_type_name="Dealer"),
                      SimpleNamespace(i_agency_type_id=2, c_agency_type_name="Installer")],
        stores=[], pricing_plans=[], device_categories=[], device_types=[],
        filter={"status": "", "business_type": "", "channel_type": ""},
    )
    html = base.environment.get_template("platform/master_management.html").render(context)
    html = re.sub(r"<script\b[^>]*\bsrc=[^>]*>\s*</script>", "", html)
    html = re.sub(r"<link\b[^>]*>", "", html)
    css = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in STYLES)
    if external_libs:
        libs_html = "".join(f'<script src="{(ROOT / path).as_uri()}"></script>' for path in SCRIPTS)
    else:
        libs_html = "<script>" + "\n;\n".join((ROOT / path).read_text(encoding="utf-8") for path in SCRIPTS) + "</script>"
    mock = r"""
window.requests=[];
window.confirm=()=>true;
const merchants=[
  {id:1,merchant_code:'CLT0001',merchant_name:'Alpha Mart',primary_agency_id:10,primary_agency_name:'North Agency',business_type:'Retail',status:'active',zip:'30301',city:'Atlanta',state:'GA',country:'United States',notes:'',created_date:'2026-01-01'},
  {id:2,merchant_code:'CLT0002',merchant_name:'Beta Deli',primary_agency_id:10,primary_agency_name:'North Agency',business_type:'Retail',status:'active',zip:'',city:'',state:'',country:'United States',notes:'',created_date:'2026-01-02'},
  {id:3,merchant_code:'CLT0003',merchant_name:'Gamma Shop',primary_agency_id:11,primary_agency_name:'South Agency',business_type:'Retail',status:'inactive',zip:'',city:'',state:'',country:'United States',notes:'',created_date:'2026-01-03'},
];
const agencies=[
  {id:10,agency_code:'AG0010',agency_type_id:1,agency_type_name:'Dealer',company_name:'North Agency',contact_name:'Ann Lee',phone:'404-555-0100',commission_rate:5,status:'active',zip:'30301',city:'Atlanta',state:'GA',address_line1:'1 Main St',address_line2:'',country:'United States',memo:'Top dealer',created_at:'2026-01-01'},
  {id:11,agency_code:'AG0011',agency_type_id:2,agency_type_name:'Installer',company_name:'South Agency',contact_name:'Bo Kim',phone:'',commission_rate:'',status:'inactive',zip:'',city:'',state:'',address_line1:'',address_line2:'',country:'United States',memo:'',created_at:'2026-01-02'},
  {id:12,agency_code:'AG0012',agency_type_id:1,agency_type_name:'Dealer',company_name:'East Agency',contact_name:'',phone:'',commission_rate:'',status:'active',zip:'',city:'',state:'',address_line1:'',address_line2:'',country:'United States',memo:'',created_at:'2026-01-03'},
];
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
window.fetch=async(path,options={})=>{
  const url=new URL(String(path),'http://127.0.0.1'),method=(options.method||'GET').toUpperCase();
  requests.push({path:url.pathname,method,body:options.body});
  if(url.pathname==='/platform/merchants')return json(merchants);
  if(url.pathname==='/platform/agencies')return json(agencies);
  if(url.pathname.startsWith('/platform/zip-lookup/'))return json({city:'Atlanta',state:'GA',country:'United States'});
  const saved=/\/platform\/(merchant|agency)(?:\/(\d+))?(?:\/with-image)?$/.exec(url.pathname);
  const table=saved&&saved[1]==='agency'?agencies:merchants;
  if(saved&&(method==='PUT'||method==='POST')){
    const id=Number(saved[2]||(saved[1]==='agency'?13:4));const row=table.find(m=>m.id===id);
    let payload={};
    if(options.body instanceof FormData)payload=JSON.parse(options.body.get('payload')||'{}');
    else if(options.body)payload=JSON.parse(options.body);
    const item={...(row||{id,merchant_code:'CLT0004',agency_code:'AG0013'}),...payload,id};
    if(row)Object.assign(row,item);else table.push(item);
    return json(item);
  }
  if(method==='DELETE'){const list=url.pathname.includes('/agency/')?agencies:merchants;const id=Number(url.pathname.split('/').pop());list.splice(list.findIndex(m=>m.id===id),1);return json({ok:true});}
  return json([]);
};
"""
    html = html.replace("<head>", f"<head><script>{mock}</script>", 1)
    html = html.replace("</head>", f"<style>{css}</style></head>", 1)
    # Libraries must exist before the page's own inline scripts run.
    first_inline = html.index("<script id=\"masterSeedData\"")
    return html[:first_inline] + libs_html + html[first_inline:]


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class OrganizationMasterDetailBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_merchant_uses_agency_tree_and_inline_existing_form(self):
        script = f"const page={json.dumps(module_page('merchant')).replace('</', '<' + chr(92) + '/')};\n" + LAYOUT_CHECKS + r"""
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
  await checkLayout(frame,doc,win,'merchant');
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

    def test_agency_uses_single_list_and_inline_existing_form(self):
        script = f"const page={json.dumps(module_page('agency')).replace('</', '<' + chr(92) + '/')};\n" + LAYOUT_CHECKS + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=(ms=40)=>new Promise(resolve=>setTimeout(resolve,ms));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1440px;height:900px';frame.srcdoc=page;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),field=name=>find('field_'+name);
  const body=find('masterListBody'),modal=find('masterEditModal'),actions=find('masterDetailActions');
  await wait(()=>body.textContent.includes('North Agency'),'agency list');
  // Same Common workspace as Merchant: filters, single list and pager on the left; the existing form on the right.
  const list=doc.querySelector('.mg-md-list-panel'),panel=doc.querySelector('.mg-md-form-panel');
  assert(list&&panel&&panel.contains(modal)&&list.getBoundingClientRect().right<=panel.getBoundingClientRect().left,'list left, form right');
  assert(list.contains(find('masterSearch'))&&list.contains(find('masterStatusFilter'))&&list.contains(find('storeListPager'))&&!find('storeListPager').classList.contains('d-none'),'Search / Status / list / Pagination');
  assert(doc.querySelector('.mg-md-form-title').textContent.trim()==='AGENCY INFORMATION','panel title');
  assert(modal.hidden&&find('masterDetailEmpty').textContent.includes('Select an Agency'),'empty state');
  assert(find('masterListHeader').querySelectorAll('th').length===1&&find('masterListHeader').textContent.trim().startsWith('Agency'),'single Agency column');
  assert(!body.querySelector('tr[data-tree-toggle]'),'no parent groups');
  assert([...body.querySelectorAll('tr[data-row-select]')].map(tr=>tr.textContent.trim()).join('|')==='East Agency|North Agency|South Agency','sorted by company');
  assert(find('storePagerInfo').textContent==='3 agencies','pager summary');
  // Select -> every existing field and the Registration Email action, Save Changes / Deactivate / Delete on top.
  body.querySelector('tr[data-row-select="10"]').click();
  await wait(()=>!modal.hidden&&field('company_name')?.value==='North Agency','selected agency');
  await checkLayout(frame,doc,win,'agency');
  assert(win.getComputedStyle(modal).position==='static'&&!doc.querySelector('.modal-backdrop'),'inline form');
  assert(field('agency_type_id').value==='1'&&field('agency_code').value==='AG0010'&&field('status').value==='active'&&String(field('commission_rate').value)==='5'
    &&field('zip').value==='30301'&&field('address_line1').value==='1 Main St'&&field('memo').value==='Top dealer','existing Agency Type / Company / Commission / Address / Notes fields');
  assert(actions.contains(find('btnModalSave'))&&actions.contains(find('btnModalDelete'))&&actions.contains(find('masterDetailStatusToggle'))&&actions.contains(find('btnAgencyRegistrationEmailAction')),'header actions incl. Registration Email');
  const toggle=find('masterDetailStatusToggle');
  assert(!toggle.classList.contains('d-none')&&toggle.textContent==='Deactivate'&&find('btnModalSave').textContent.includes('Save Changes'),'Save Changes / Deactivate / Delete');
  const order=[...actions.querySelectorAll('.btn')].map(b=>b.id).filter(Boolean);
  assert(order.indexOf('masterDetailStatusToggle')<order.indexOf('btnModalDelete')&&order.indexOf('btnModalDelete')<order.indexOf('btnModalSave'),'Deactivate, Delete, Save order: '+order.join(','));
  // ZIP lookup and validation are the existing ones.
  field('zip').value='30303';field('zip').dispatchEvent(new win.Event('change'));
  await wait(()=>win.requests.some(r=>r.path==='/platform/zip-lookup/30303'),'ZIP lookup');
  field('commission_rate').value='150';field('commission_rate').dispatchEvent(new win.Event('input',{bubbles:true}));
  const beforeInvalid=win.requests.length;
  find('btnModalSave').click();await tick(120);
  assert(!win.requests.slice(beforeInvalid).some(r=>r.method==='PUT')&&find('masterModalError').textContent.includes('Commission Rate'),'existing validation blocks the save');
  // Deactivate refuses to mix with unsaved edits, then (after Cancel) saves only the Status.
  toggle.click();await tick();
  assert(find('masterModalError').textContent.includes('Save or cancel'),'unsaved edits are not saved by Deactivate');
  find('btnModalCancel').click();await tick();
  assert(String(field('commission_rate').value)==='5'&&field('zip').value==='30301','Cancel reverts');
  toggle.click();
  await wait(()=>win.requests.some(r=>r.method==='PUT'&&r.path.startsWith('/platform/agency/10')),'deactivate request');
  await wait(()=>toggle.textContent==='Activate'&&field('status').value==='inactive','status toggled');
  const put=win.requests.find(r=>r.method==='PUT');
  const sent=put.body instanceof win.FormData?JSON.parse(put.body.get('payload')):JSON.parse(put.body);
  assert(sent.status==='inactive'&&sent.company_name==='North Agency'&&String(sent.commission_rate)==='5','Deactivate saves the existing record with Status inactive');
  assert(!modal.hidden&&body.querySelector('tr[data-row-select="10"]').classList.contains('selected'),'record stays selected');
  // Save Changes.
  field('company_name').value='North Agency LLC';field('company_name').dispatchEvent(new win.Event('input',{bubbles:true}));
  find('btnModalSave').click();
  await wait(()=>body.textContent.includes('North Agency LLC'),'saved name in list');
  // Add Agency -> empty create form on the right.
  find('btnListAdd').click();await tick();
  assert(!modal.hidden&&field('company_name').value===''&&find('btnModalSave').textContent.includes('Create Agency')&&toggle.classList.contains('d-none'),'Add shows the create form without Deactivate');
  find('btnModalCancel').click();await tick();
  assert(modal.hidden,'Cancel closes a new Agency');
  // Delete -> existing API, empty state.
  body.querySelector('tr[data-row-select="12"]').click();
  await wait(()=>field('company_name').value==='East Agency','select for delete');
  find('btnModalDelete').click();
  await wait(()=>doc.querySelector('.modal.show'),'confirm dialog');
  (doc.querySelector('#appConfirmModalOk')||doc.querySelector('.modal.show .btn-danger,.modal.show .btn-primary')).click();
  await wait(()=>win.requests.some(r=>r.method==='DELETE'&&r.path.endsWith('/agency/12')),'delete request');
  await wait(()=>modal.hidden&&!body.textContent.includes('East Agency'),'empty state after delete');
  // Status filter / paging.
  find('masterStatusFilter').value='inactive';find('masterStatusFilter').dispatchEvent(new win.Event('change'));
  await wait(()=>win.requests.some(r=>r.path==='/platform/agencies'&&r.method==='GET')&&find('storePagerPage').textContent.includes('1 /'),'status filter reloads');
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
