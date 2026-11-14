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
    "static/components/image-uploader/image-uploader.js", "static/js/store_devices_panel.js", "static/js/awsum-address.js",
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
  // One-shot server failure for the next Agency / Merchant save: window.__failNext={status,body,type}.
  if(window.__failNext&&(method==='PUT'||method==='POST')&&/^\/platform\/(merchant|agency)(\/|$)/.test(url.pathname)){
    const fail=window.__failNext;window.__failNext=null;
    return new Response(fail.body,{status:fail.status,headers:{'Content-Type':fail.type||'application/json'}});
  }
  // Business Contacts (Common contact service API): window.__contacts['MERCHANT:1']=[...].
  const contactsOf=/^\/platform\/business-contacts\/(AGENCY|MERCHANT)\/(\d+)$/.exec(url.pathname);
  if(contactsOf){
    window.__contacts=window.__contacts||{};const list=window.__contacts[contactsOf[1]+':'+contactsOf[2]]||(window.__contacts[contactsOf[1]+':'+contactsOf[2]]=[]);
    if(method==='POST'){const p=JSON.parse(options.body);if(p.is_primary)list.forEach(c=>{c.is_primary=false;});
      const c={...p,contact_id:900+list.length,status:'active'};list.push(c);return json(c);}
    return json(list);
  }
  // Registration Email: preview, then send (mail-server acceptance only).
  const email=/^\/platform\/registration-email\/(AGENCY|MERCHANT)\/(\d+)(\/preview)?$/.exec(url.pathname);
  if(email){const p=options.body?JSON.parse(options.body):{};const recipient=p.recipient||'owner@example.test';
    return json(email[3]?{html:'<p>Registration completed</p>',subject:'Registration',recipient,message:'Registration completed'}
      :{accepted:true,recipient,message_id:'mock-registration'});}
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

    def test_inline_create_server_errors_unsaved_switch_contacts_email_and_narrow_layout(self):
        """The inline Agency / Merchant panel beyond the basic flow: POST create keeps the new record selected; server
        errors (400 / 409 / 500) stay in the panel; switching rows with unsaved edits (current behaviour: discarded,
        nothing sent); Business Contacts and Registration Email in the panel; below 992px the panels stack and the
        actions stay usable; the existing panel geometry (header actions, own scroll, equal height) still holds."""
        pages = {module: module_page(module) for module in ("merchant", "agency")}
        script = f"const pages={json.dumps(pages).replace('</', '<' + chr(92) + '/')};\n" + LAYOUT_CHECKS + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=(ms=40)=>new Promise(resolve=>setTimeout(resolve,ms));
const wait=async(check,label)=>{for(let i=0;i<150;i++){try{if(check())return;}catch(e){}await tick();}throw Error('Timed out: '+label);};
const open=async(module)=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1440px;height:900px';frame.srcdoc=pages[module];
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),field=name=>find('field_'+name);
  return {frame,doc,win,find,field,body:find('masterListBody'),modal:find('masterEditModal')};
};
const set=(win,el,value)=>{el.value=value;el.dispatchEvent(new win.Event('input',{bubbles:true}));el.dispatchEvent(new win.Event('change',{bubbles:true}));};
const payloadOf=(win,request)=>request.body instanceof win.FormData?JSON.parse(request.body.get('payload')||'{}'):JSON.parse(request.body||'{}');
const errorText=find=>find('masterModalError').classList.contains('d-none')?'':find('masterModalError').textContent.trim();
(async()=>{
  // ---------------- Merchant ----------------
  let {frame,doc,win,find,field,body,modal}=await open('merchant');
  win.__contacts={'MERCHANT:1':[{contact_id:801,contact_role:'PRIMARY',first_name:'Jane',last_name:'Primary',email:'jane@example.test',phone:'404-555-0101',is_primary:true,status:'active'}]};
  await wait(()=>body.textContent.includes('Alpha Mart'),'merchant list');
  body.querySelector('tr[data-row-select="1"]').click();
  await wait(()=>field('merchant_name').value==='Alpha Mart','merchant 1');
  // Server errors stay in the panel; nothing else changes (400 rule text, 409 duplicate, 500 non-JSON).
  for(const [status,bodyText,type,expected] of [
    [400,JSON.stringify({detail:'Save the Merchant as Inactive, add an active Primary Contact, then activate it.'}),'application/json','Save the Merchant as Inactive, add an active Primary Contact, then activate it.'],
    [409,JSON.stringify({detail:'A merchant with this name already exists.'}),'application/json','A merchant with this name already exists.'],
    [500,'Internal Server Error','text/plain','Something went wrong. Please try again.']]){
    win.__failNext={status,body:bodyText,type};
    set(win,field('merchant_name'),'Alpha Mart '+status);
    const before=win.requests.length;find('btnModalSave').click();
    await wait(()=>errorText(find)!=='','error '+status);
    assert(errorText(find)===expected,status+' shown in the panel: '+errorText(find));
    assert(!modal.hidden&&field('merchant_name').value==='Alpha Mart '+status&&body.querySelector('tr[data-row-select="1"]').classList.contains('selected'),status+': form and selection kept');
    assert(win.requests.slice(before).filter(r=>r.method==='PUT').length===1&&!body.textContent.includes('Alpha Mart '+status),status+': one request, list unchanged');
    find('btnModalCancel').click();await tick();
    assert(field('merchant_name').value==='Alpha Mart',status+': Cancel restores the saved value');
  }
  // Unsaved edits + another row (current behaviour): the other record opens, nothing is sent, the edit is discarded.
  set(win,field('merchant_name'),'Unsaved Name');
  const beforeSwitch=win.requests.length;
  body.querySelector('tr[data-row-select="3"]').click();
  await wait(()=>field('merchant_name').value==='Gamma Shop','switched with unsaved edits');
  assert(!win.requests.slice(beforeSwitch).some(r=>r.method==='PUT'||r.method==='POST'),'unsaved edits are not saved by switching');
  body.querySelector('tr[data-row-select="1"]').click();
  await wait(()=>field('merchant_name').value==='Alpha Mart','back: saved value, edit discarded');
  // Business Contacts in the panel (Contact tab): list, add through the Common contact API, refreshed list.
  [...modal.querySelectorAll('.merchant-tab-btn')].find(b=>b.textContent.trim()==='Contact').click();await tick();
  await wait(()=>find('businessContactList').textContent.includes('Jane Primary'),'contacts listed');
  assert(find('businessContactList').getBoundingClientRect().width>0,'Contact tab shows the contact list');
  assert(find('businessContactList').querySelector('.badge')&&find('businessContactList').textContent.includes('Primary'),'primary contact marked');
  find('btnAddBusinessContact').click();await tick();
  assert(!find('businessContactEditor').classList.contains('d-none'),'contact editor in the panel');
  set(win,find('bc_first_name'),'Sam');set(win,find('bc_last_name'),'Second');set(win,find('bc_email'),'sam@example.test');
  find('bc_is_primary').checked=false;const beforeContact=win.requests.length;find('btnSaveBusinessContact').click();
  await wait(()=>win.requests.some(r=>r.method==='POST'&&r.path==='/platform/business-contacts/MERCHANT/1'),'contact POST');
  const contactPost=payloadOf(win,win.requests.find(r=>r.method==='POST'&&r.path==='/platform/business-contacts/MERCHANT/1'));
  assert(contactPost.first_name==='Sam'&&contactPost.email==='sam@example.test'&&contactPost.is_primary===false,'contact payload: '+JSON.stringify(contactPost));
  await wait(()=>find('businessContactList').textContent.includes('Sam Second'),'contacts refreshed');
  assert(!win.requests.slice(beforeContact).some(r=>(r.method==='PUT'||r.method==='POST')&&r.path.startsWith('/platform/merchant')),'the contact save writes no Merchant record');
  // Registration Email from the panel: preview first (never sends), then an explicit send.
  const emailButton=find('btnContactRegistrationEmail');
  assert(emailButton&&!emailButton.disabled&&emailButton.getBoundingClientRect().width>0,'Registration Email available for the saved Merchant');
  emailButton.click();
  await wait(()=>find('sdEmailSend')&&!find('sdEmailSend').disabled,'email preview');
  assert(win.requests.some(r=>r.path==='/platform/registration-email/MERCHANT/1/preview')&&!win.requests.some(r=>r.path==='/platform/registration-email/MERCHANT/1'),'preview only');
  assert(find('sdEmailTo').value==='owner@example.test','recipient from the preview');
  find('sdEmailSend').click();
  await wait(()=>win.requests.some(r=>r.path==='/platform/registration-email/MERCHANT/1'),'email send');
  find('sdEmailModal').dispatchEvent(new win.Event('hidden.bs.modal'));await tick();
  // POST create: the new Merchant is saved through the existing create API and stays selected.
  find('btnListAdd').click();await wait(()=>field('merchant_name').value==='','create form');
  set(win,field('merchant_name'),'Delta Market');set(win,field('primary_agency_id'),'10');set(win,field('business_type'),'Retail');
  set(win,field('status'),'inactive');
  find('btnModalSave').click();
  await wait(()=>win.requests.some(r=>r.method==='POST'&&/^\/platform\/merchant(\/with-image)?$/.test(r.path)),'create POST');
  const created=payloadOf(win,win.requests.find(r=>r.method==='POST'&&/^\/platform\/merchant(\/with-image)?$/.test(r.path)));
  assert(created.merchant_name==='Delta Market'&&created.primary_agency_id==='10'&&!('id' in created&&created.id),'create payload: '+JSON.stringify(created));
  await wait(()=>body.querySelector('tr[data-row-select="4"]'),'new row listed');
  await wait(()=>body.querySelector('tr[data-row-select="4"]').classList.contains('selected')&&field('merchant_name').value==='Delta Market','new Merchant selected after save');
  // The new record stays open; the next save updates it (PUT). (Current behaviour: the header still reads
  // "Add Merchant" / "Create Merchant" until another record is opened; reported, not changed here.)
  assert(!modal.hidden,'the new record stays open');
  set(win,field('merchant_name'),'Delta Market 2');const beforeEdit=win.requests.length;find('btnModalSave').click();
  await wait(()=>win.requests.slice(beforeEdit).some(r=>r.method==='PUT'&&/^\/platform\/merchant\/4(\/with-image)?$/.test(r.path)),'next save updates the new record');
  assert(!win.requests.slice(beforeEdit).some(r=>r.method==='POST'&&r.path.startsWith('/platform/merchant')),'no second create');
  await wait(()=>body.textContent.includes('Delta Market 2'),'list refreshed after the update');
  // The existing panel geometry still holds after these flows (header actions, own scroll, equal height).
  await checkLayout(frame,doc,win,'merchant after create');
  // Below 992px: list and panel stack, the panel actions stay visible, inside the screen and clickable.
  for(const width of [991,768,420]){
    frame.style.width=width+'px';frame.style.height='900px';win.scrollTo(0,0);await tick(120);
    const list=doc.querySelector('.mg-md-list-panel').getBoundingClientRect(),panel=doc.querySelector('.mg-md-form-panel').getBoundingClientRect();
    assert(panel.top>=list.bottom-1&&Math.abs(panel.left-list.left)<2,width+'px: panels stacked: '+[list.bottom,panel.top]);
    assert(doc.documentElement.scrollWidth<=width+1,width+'px: no sideways scroll');
    for(const id of ['btnModalCancel','btnModalSave']){
      const button=find(id);button.scrollIntoView({block:'center'});await tick(60);
      const r=button.getBoundingClientRect(),hit=doc.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
      assert(r.width>0&&r.left>=0&&r.right<=width+1&&(hit===button||button.contains(hit)),width+'px: '+id+' visible and clickable');
    }
  }
  frame.remove();
  // ---------------- Agency ----------------
  ({frame,doc,win,find,field,body,modal}=await open('agency'));
  await wait(()=>body.textContent.includes('North Agency'),'agency list');
  body.querySelector('tr[data-row-select="10"]').click();
  await wait(()=>field('company_name').value==='North Agency','agency 10');
  win.__failNext={status:409,body:JSON.stringify({detail:'An agency with this company name already exists.'})};
  set(win,field('company_name'),'North Agency Copy');find('btnModalSave').click();
  await wait(()=>errorText(find)!=='','agency 409');
  assert(errorText(find)==='An agency with this company name already exists.'&&!modal.hidden,'agency server error in the panel');
  find('btnModalCancel').click();await tick();
  find('btnListAdd').click();await wait(()=>field('company_name').value==='','agency create form');
  set(win,field('company_name'),'West Agency');set(win,field('agency_type_id'),'1');set(win,field('status'),'inactive');
  find('btnModalSave').click();
  await wait(()=>win.requests.some(r=>r.method==='POST'&&/^\/platform\/agency(\/with-image)?$/.test(r.path)),'agency POST');
  await wait(()=>body.querySelector('tr[data-row-select="13"]')&&body.querySelector('tr[data-row-select="13"]').classList.contains('selected')&&field('company_name').value==='West Agency','new Agency selected after save');
  await checkLayout(frame,doc,win,'agency after create');
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

    def test_merchant_code_is_shown_read_only_with_copy(self):
        script = f"const page={json.dumps(module_page('merchant')).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=(ms=40)=>new Promise(resolve=>setTimeout(resolve,ms));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1440px;height:900px';frame.srcdoc=page;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),field=name=>find('field_'+name);
  const body=find('masterListBody');
  await wait(()=>body.querySelector('tr[data-row-select="1"]'),'merchant list');
  body.querySelector('tr[data-row-select="1"]').click();
  await wait(()=>!find('masterEditModal').hidden&&field('merchant_name')?.value==='Alpha Mart','selected merchant');
  const code=field('merchant_code'),copy=find('field_merchant_code_copy');
  // OVERVIEW: Merchant Code read-only with Copy (common field option); the internal Merchant ID stays hidden.
  assert(code.getClientRects().length>0&&code.readOnly&&code.value==='CLT0001','Merchant Code shown read-only: '+code.value);
  assert(code.closest('[class*="col-"]').querySelector('label').textContent.trim()==='Merchant Code','label');
  assert(copy&&copy.textContent==='Copy'&&code.parentElement===copy.parentElement&&code.parentElement.classList.contains('input-group'),'Copy button');
  assert(field('id').closest('.d-none'),'Merchant ID stays internal (hidden)');
  // Layout: Merchant Code | Primary Agency share the first row; the other fields keep their order.
  const top=name=>Math.round(field(name).getBoundingClientRect().top);
  assert(top('merchant_code')===top('primary_agency_id')&&top('merchant_name')===top('business_type')&&top('merchant_code')<top('merchant_name'),'2-column layout');
  // Headless Chromium has no clipboard permission: stub it (and never block on the prompt fallback).
  const copied=[];win.prompt=()=>null;
  Object.defineProperty(win.navigator,'clipboard',{configurable:true,value:{writeText:async(v)=>{copied.push(v);}}});
  copy.click();
  await wait(()=>copy.textContent==='Copied','copied');
  assert(copied.join()==='CLT0001'||copied.length===0,'copied value: '+copied.join());
  // Saving never sends the code (server-generated, immutable).
  field('merchant_name').value='Alpha Market';field('merchant_name').dispatchEvent(new win.Event('input',{bubbles:true}));
  const before=win.requests.length;
  find('btnModalSave').click();
  await wait(()=>win.requests.slice(before).some(r=>r.method==='PUT'),'save');
  const put=win.requests.slice(before).find(r=>r.method==='PUT');
  const sent=put.body instanceof win.FormData?JSON.parse(put.body.get('payload')):JSON.parse(put.body);
  assert(!('merchant_code' in sent)&&!('id' in sent),'code / ID not sent: '+Object.keys(sent).join(','));
  // Create form: empty code with the placeholder; Copy explains it is generated on save.
  find('btnListAdd').click();
  await wait(()=>find('btnModalSave').textContent.includes('Create Merchant'),'create form');
  assert(field('merchant_code').value===''&&field('merchant_code').placeholder.startsWith('Generated automatically'),'create: placeholder');
  find('field_merchant_code_copy').click();
  await wait(()=>(find('awsumToastRegion')||{textContent:''}).textContent.includes('Merchant Code is generated when the record is saved.'),'create: copy explains');
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_merchant_business_options_tab_saves_with_save_changes(self):
        # Server shape of /platform/merchants: business_options = registry rows with enabled / saved (like the API).
        bo_mock = r"""<script>(()=>{const base=window.fetch;
const defs=[['customer','Customer Management',true],['loyalty','Loyalty / Points',true],['gift_card','Gift Card',false],
  ['time_attendance','Time & Attendance',true],['inventory','Inventory Management',true],
  ['location_management','Use Location Management',true],['accounting','Accounting',true]];
const saved={1:{customer:true},2:{loyalty:true}};
const rowsFor=id=>defs.map(([key,label,available])=>({key,label,tier:'ADDON',available,
  enabled:available&&!!(saved[id]||{})[key],saved:key in (saved[id]||{})}));
window.fetch=async(path,options={})=>{const url=new URL(String(path),'http://127.0.0.1'),method=(options.method||'GET').toUpperCase();
  const put=/^\/platform\/merchant\/(\d+)(?:\/with-image)?$/.exec(url.pathname);
  if(put&&method==='PUT'){const body=options.body instanceof FormData?JSON.parse(options.body.get('payload')||'{}'):JSON.parse(options.body||'{}');
    Object.entries(body.business_options||{}).forEach(([k,v])=>{const cur=(saved[put[1]]||{})[k]??false;if(cur!==v)(saved[put[1]]=saved[put[1]]||{})[k]=v;});
    return base(path,options);}
  const res=await base(path,options);
  if(url.pathname==='/platform/merchants'&&method==='GET'){const list=await res.json();
    return new Response(JSON.stringify(list.map(m=>({...m,business_options:rowsFor(m.id)}))),{headers:{'Content-Type':'application/json'}});}
  return res;};})();</script></head>"""
        page = module_page("merchant").replace("</head>", bo_mock, 1)
        defs = [{"key": k, "label": l, "tier": "ADDON", "available": a} for k, l, a in (
            ("customer", "Customer Management", True), ("loyalty", "Loyalty / Points", True), ("gift_card", "Gift Card", False),
            ("time_attendance", "Time & Attendance", True), ("inventory", "Inventory Management", True),
            ("location_management", "Use Location Management", True), ("accounting", "Accounting", True))]
        page = page.replace('"businessOptions": []', '"businessOptions": ' + json.dumps(defs), 1)
        script = f"const page={json.dumps(page).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=(ms=40)=>new Promise(resolve=>setTimeout(resolve,ms));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1440px;height:900px';frame.srcdoc=page;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),field=name=>find('field_'+name);
  const body=find('masterListBody');
  await wait(()=>body.querySelector('tr[data-row-select="1"]'),'merchant list');
  body.querySelector('tr[data-row-select="1"]').click();
  await wait(()=>!find('masterEditModal').hidden&&field('merchant_name')?.value==='Alpha Mart','selected merchant');
  // Tab order, one row.
  const tabs=[...find('masterEditModal').querySelectorAll('.merchant-detail-tabs .merchant-tab-btn')];
  assert(tabs.map(t=>t.textContent).join('|')==='Overview|Contact|Address|Business Options|Feature Entitlements|Stores|Notes','tab order: '+tabs.map(t=>t.textContent).join('|'));
  assert(new Set(tabs.map(t=>Math.round(t.getBoundingClientRect().top))).size===1,'one tab row');
  tabs[3].click();await tick();
  const keys=['customer','loyalty','gift_card','time_attendance','inventory','location_management','accounting'];
  const selects=keys.map(k=>field('bo_'+k));
  // The Store form's Off / On select (same Common field type, form-select), 7 fields visible.
  assert(selects.every(s=>s&&s.tagName==='SELECT'&&s.classList.contains('form-select')&&s.getClientRects().length>0),'Store-style selects');
  assert(!find('masterEditModal').querySelector('[data-tab-panel="business_options"] [data-awsum-option-group]'),'no option group on BUSINESS OPTIONS');
  const labels=selects.map(s=>s.closest('[class*="col-"]').querySelector('label').textContent.trim());
  assert(labels.join('|')==='Customer Management|Loyalty / Points|Gift Card|Time & Attendance|Inventory Management|Use Location Management|Accounting','registry labels: '+labels.join('|'));
  // Two-column grid like the Store form: pairs share a row at 1440px.
  const tops=selects.map(s=>Math.round(s.getBoundingClientRect().top)),lefts=selects.map(s=>Math.round(s.getBoundingClientRect().left));
  assert(tops[0]===tops[1]&&tops[2]===tops[3]&&tops[1]<tops[2]&&lefts[0]<lefts[1]&&lefts[0]===lefts[2],'2-column grid: '+tops.join(',')+' / '+lefts.join(','));
  assert([...selects[0].options].map(o=>o.value+':'+o.textContent).join(',')==='off:Off,on:On','Off / On options');
  assert(field('bo_customer').value==='on'&&field('bo_inventory').value==='off'&&field('bo_loyalty').value==='off','saved On / default Off');
  const gift=field('bo_gift_card');
  assert(gift.disabled&&gift.value==='off'&&gift.options[gift.selectedIndex].textContent==='Coming soon','Gift Card: Coming soon, disabled');
  // Change only one Business Option -> Save Changes sends business_options (no Gift Card) through the existing Merchant save.
  field('bo_inventory').value='on';field('bo_inventory').dispatchEvent(new win.Event('change',{bubbles:true}));await tick();
  const before=win.requests.length;
  find('btnModalSave').click();
  await wait(()=>win.requests.slice(before).some(r=>r.method==='PUT'&&r.path.startsWith('/platform/merchant/1')),'Save Changes');
  const put=win.requests.slice(before).find(r=>r.method==='PUT'&&r.path.startsWith('/platform/merchant/1'));
  const sent=put.body instanceof win.FormData?JSON.parse(put.body.get('payload')):JSON.parse(put.body);
  assert(JSON.stringify(sent.business_options)==='{"customer":true,"loyalty":false,"time_attendance":false,"inventory":true,"location_management":false,"accounting":false}','payload: '+JSON.stringify(sent.business_options));
  assert(!Object.keys(sent).some(k=>k.startsWith('bo_')),'no raw option fields in the payload');
  // Reload the record: the saved state comes back from the list.
  await wait(()=>win.requests.slice(before).filter(r=>r.path==='/platform/merchants').length>0,'list reloaded');
  body.querySelector('tr[data-row-select="2"]').click();
  await wait(()=>field('merchant_name').value==='Beta Deli','other merchant');
  assert(field('bo_inventory').value==='off'&&field('bo_loyalty').value==='on','other Merchant unaffected');
  body.querySelector('tr[data-row-select="1"]').click();
  await wait(()=>field('merchant_name').value==='Alpha Mart'&&field('bo_inventory').value==='on','saved value after reload');
  // Add Merchant: registry defaults (all Off), Gift Card still disabled.
  find('btnListAdd').click();
  await wait(()=>field('merchant_name').value===''&&find('btnModalSave').textContent.includes('Create Merchant'),'create form');
  assert(keys.every(k=>field('bo_'+k).value==='off')&&field('bo_gift_card').disabled&&!field('bo_customer').disabled,'new Merchant: registry defaults');
  body.querySelector('tr[data-row-select="1"]').click();
  await wait(()=>field('merchant_name').value==='Alpha Mart','back to Alpha Mart');
  // Narrow screen: one tab row, scrolls sideways; the page itself does not.
  frame.style.width='390px';await tick(120);
  const bar=find('masterEditModal').querySelector('.merchant-detail-tabs');
  assert(new Set(tabs.map(t=>Math.round(t.getBoundingClientRect().top))).size===1&&bar.scrollWidth>bar.clientWidth
    &&win.getComputedStyle(bar).overflowX==='auto','390px: one row, horizontal tab scroll');
  assert(doc.documentElement.scrollWidth<=390+1,'no page overflow at 390px');
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_store_option_off_keeps_scope_and_on_restores_it(self):
        # One Store with Customer ON (Scope MERCHANT) and Loyalty OFF with a saved Scope MERCHANT.
        store_mock = r"""<script>(()=>{const base=window.fetch;
const stores=[{id:7,store_code:'ST0007',store_name:'Scope Store',merchant_id:1,merchant_name:'Alpha Mart',installed_by_agency_id:10,
  agency_name:'North Agency',business_type:'Retail',status:'active',opt_customer:'on',customer_scope:'MERCHANT',
  opt_loyalty:'off',loyalty_scope:'MERCHANT',country:'United States',created_at:'2026-01-01'}];
window.fetch=async(path,options={})=>{const url=new URL(String(path),'http://127.0.0.1');
  if(url.pathname==='/platform/stores'){requests.push({path:url.pathname,method:'GET'});
    return new Response(JSON.stringify({items:stores,total:1,total_pages:1,page:1,page_size:20}),{headers:{'Content-Type':'application/json'}});}
  return base(path,options);};})();</script></head>"""
        page = module_page("store").replace("</head>", store_mock, 1)
        script = f"const page={json.dumps(page).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=(ms=40)=>new Promise(resolve=>setTimeout(resolve,ms));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1440px;height:900px';frame.srcdoc=page;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),field=name=>find('field_'+name);
  await wait(()=>find('masterListBody').querySelector('tr[data-item-id="7"]'),'store list');
  find('masterListBody').querySelector('tr[data-item-id="7"]').click();
  await wait(()=>field('customer_scope')&&field('opt_customer').value==='on','store form');
  const set=(name,value)=>{field(name).value=value;field(name).dispatchEvent(new win.Event('change',{bubbles:true}));};
  const st=key=>`${field('opt_'+key).value}|${field(key+'_scope').value}|${field(key+'_scope').disabled}`;
  // Loaded: OFF shows the saved Scope disabled (no longer cleared); ON keeps its Scope enabled.
  assert(st('customer')==='on|MERCHANT|false','Customer ON: '+st('customer'));
  assert(st('loyalty')==='off|MERCHANT|true','Loyalty OFF keeps the saved Scope (disabled): '+st('loyalty'));
  // OFF -> the Scope stays (disabled); ON again -> the same Scope.
  set('opt_customer','off');
  assert(st('customer')==='off|MERCHANT|true','OFF keeps MERCHANT: '+st('customer'));
  set('opt_customer','on');
  assert(st('customer')==='on|MERCHANT|false','ON restores MERCHANT: '+st('customer'));
  set('opt_loyalty','on');
  assert(st('loyalty')==='on|MERCHANT|false','Loyalty ON restores its saved Scope: '+st('loyalty'));
  // ON without any saved Scope: STORE (unchanged).
  set('opt_loyalty','off');field('loyalty_scope').value='';set('opt_loyalty','on');
  assert(st('loyalty')==='on|STORE|false','no saved Scope -> STORE: '+st('loyalty'));
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
