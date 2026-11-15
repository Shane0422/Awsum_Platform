"""Platform Store Management as Common Master-Detail (S2-b PHASE 1): Agency > Merchant > Store Organization Tree on the
left, the existing 8-tab Store form inline on the right.

Uses the tools.test_merchant_store_management fixture (isolated SQLite app) to render /platform/master/store; the
Tree / detail APIs are mocked in the page so the screen runs offline in headless Chrome. Covers the Tree (Merchant's
Agency, No Agency, a Merchant without Stores, more than 100 Stores), Search / Status, the hidden Agency / Merchant
filters / Sort / Rows / Pager, the fixed header, selection through the existing detail API (latest answer only), the 8
tabs and the layout widths. The Common treeHtml countItem option is checked directly (default unchanged).
"""
import json
import unittest

from tools import test_image_components as browser
from tools import test_merchant_store_management as store_tests

MASTER_DETAIL_JS = (browser.ROOT / "static" / "js" / "master-detail.js").read_text(encoding="utf-8")


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class StoreMasterDetailBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def setUp(self):
        store_tests.MerchantStoreManagementTests.setUp(self)

    def page(self):
        detail = self.client.get("/platform/store/10").json()
        # 2 Agencies (the Merchant's), a Merchant without Stores, No Agency with 120 Stores (more than a list page).
        mock = ("window.__errors=[];addEventListener('error',e=>__errors.push(String(e.message)));"
                f"window.__detail={json.dumps(detail)};" + r"""
window.requests=[];window.__delay={};
const bulk=[...Array(120).keys()].map(n=>({store_id:2000+n,store_code:'STR26M'+String(n).padStart(3,'0'),store_name:'Bulk '+String(n).padStart(3,'0'),status:n%3?'active':'inactive'}));
window.__tree={agencies:[
 {agency_id:1,agency_code:'AGT00001',agency_name:'Agency One',merchants:[
  {merchant_id:10,merchant_code:'MER26ABCD',merchant_name:'Merchant One',merchant_status:'active',stores:[
   {store_id:12,store_code:'STR26INAC',store_name:'Inactive One',status:'inactive'},
   {store_id:10,store_code:'STR26ABCD',store_name:'Store One',status:'active'}]}]},
 {agency_id:2,agency_code:'AGT00002',agency_name:'Beta Agency',merchants:[
  {merchant_id:12,merchant_code:'MER26EMPT',merchant_name:'Empty Merchant',merchant_status:'active',stores:[]}]},
 {agency_id:null,agency_code:null,agency_name:'No Agency',merchants:[
  {merchant_id:11,merchant_code:'MER26EFGH',merchant_name:'Other Merchant',merchant_status:'active',stores:bulk}]}],
 counts:{agencies:2,merchants:3,stores:122}};
const J=b=>new Response(JSON.stringify(b),{headers:{'Content-Type':'application/json'}});
window.fetch=async(path,options={})=>{const url=new URL(path,'http://x');requests.push({path:url.pathname,search:url.search});
 if(url.pathname==='/platform/api/store-tree')return J(__tree);
 const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;
 const lic=url.pathname.match(/^\/platform\/store\/(\d+)\/(devices-licenses|entitlement|licenses\/issue|licenses\/email\/preview|licenses\/email)$/);
 if(lic){const id=Number(lic[1]);requests[requests.length-1].method=method;requests[requests.length-1].body=body;
  const ws={store:{store_id:id,store_name:'Detail '+id,merchant_name:'Merchant One',agency_name:'Agency One',agency_email:'license@example.test'},
   licenses:[{license_id:id*10+1,name:'POS-01',type:'POS',status:'issued',status_label:'Not Activated',license_key:'AWS-POS-AAAA-BBBB-CCCC-'+id,issued_at:'2026-11-01T10:00:00',valid_until:'2027-10-31T00:00:00',issued_by:'admin@example.test',issue_request_id:'req-aaaa-0001',email_sent_count:0,email_last_sent_at:null},
    {license_id:id*10+3,name:'KIOSK-01',type:'KIOSK',status:'activated',status_label:'Activated',license_key:'AWS-KSK-GGGG-HHHH-JJJJ-'+id,issued_at:'2026-10-20T09:00:00',device_id:77,device_name:'Front Kiosk',os:'Windows 11',activated_at:'2026-10-21T08:00:00',last_seen_at:'2026-11-14T16:05:00',last_ip:'203.0.113.7',valid_until:null,issued_by:null,issue_request_id:null,email_sent_count:2,email_last_sent_at:'2026-10-22T11:00:00'}],
   devices:[],entitlements:[],summary:{POS:{contracted:3,issued:1,activated:0,remaining:2},KIOSK:{contracted:1,issued:0,activated:0,remaining:1},
    CUSTOMER_ORDERING:{enabled:false,remaining:0}}};
  if(lic[2]==='licenses/issue')return J({replayed:false,issued:[id*10+2],workspace:{...ws,summary:{...ws.summary,POS:{contracted:3,issued:2,activated:0,remaining:1}},licenses:[...ws.licenses,{license_id:id*10+2,name:'POS-02',type:'POS',status:'issued',status_label:'Not Activated',license_key:'AWS-POS-DDDD-EEEE-FFFF-'+id,issued_at:'2026-11-15T13:30:00',valid_until:null,issued_by:'admin@example.test',issue_request_id:'req-bbbb-0002',email_sent_count:0,email_last_sent_at:null}]}});
  if(lic[2]==='licenses/email/preview')return J({html:'<p>Your License Information AWS-POS-AAAA-BBBB-CCCC-'+id+'</p>',subject:'Your License Information',recipient:body.recipient});
  if(lic[2]==='licenses/email')return J({accepted:true,recipient:body.recipient,message_id:'mock-license'});
  return J(ws);}
 const det=url.pathname.match(/^\/platform\/store\/(\d+)\/licenses\/(\d+)$/);
 if(det)return J({license:{license_id:Number(det[2]),name:'KIOSK-01',type:'KIOSK',status:'activated',status_label:'Activated',license_key:'AWS-KSK-GGGG-HHHH-JJJJ-'+det[1],issued_at:'2026-10-20T09:00:00',email_sent_count:2,email_last_sent_at:'2026-10-22T11:00:00'},
  device:{device_id:77,device_code:'DEV-77',name:'Front Kiosk',os:'Windows 11',last_seen:'2026-11-14T16:05:00',last_ip:'203.0.113.7',first_activated_at:'2026-10-21T08:00:00',status:'activated'},history:[{device_id:76,device_code:'DEV-76',linked_at:'2026-10-01T10:15:00',unlinked_at:'2026-10-19T17:40:00',unlinked_by:'admin@example.test',unlink_reason:'Replaced'},{device_id:77,device_code:'DEV-77',linked_at:'2026-10-21T08:00:00',unlinked_at:null}],logs:[]});
 const m=url.pathname.match(/^\/platform\/store\/(\d+)$/);
 if(m){const id=Number(m[1]);if(__delay[id])await new Promise(r=>setTimeout(r,__delay[id]));
  return J({...__detail,store_id:id,id,store_name:id===10?__detail.store_name:'Detail '+id,store_code:id===10?__detail.store_code:'DET'+id});}
 return J('[]');};""")
        return store_tests.inline_page(self.client.get("/platform/master/store").text, mock)

    def run_page(self, body):
        script = "const page=" + json.dumps(self.page()).replace("</", "<\\/") + ";\n" + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const wait=async(f,m)=>{for(let i=0;i<150;i++){try{if(f())return;}catch(e){}await pause(40);}throw Error('timeout: '+m);};
(async()=>{
 const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1440px;height:900px';frame.srcdoc=page;
 const loaded=new Promise(r=>frame.addEventListener('load',r,{once:true}));document.body.appendChild(frame);await loaded;
 const doc=frame.contentDocument,win=frame.contentWindow,shown=el=>!!el&&el.getClientRects().length>0;
 const find=id=>doc.getElementById(id),f=n=>find('field_'+n),body=find('masterListBody');
 const rows=()=>[...body.querySelectorAll('tr[data-row-select]')].map(tr=>tr.dataset.rowSelect);
 const groups=()=>[...body.querySelectorAll('tr[data-tree-toggle]')].map(tr=>tr.textContent.replace(/[−+]/g,'').replace(/\s+/g,' ').trim());
 await wait(()=>rows().length===122,'every Store in the Tree');
""" + body + r"""
 assert(!win.__errors.length,'no script errors: '+win.__errors);
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack});});
"""
        self.run_browser(script)

    def test_tree_groups_filters_and_layout(self):
        self.run_page(r"""
 // Agency (the Merchant's) > Merchant > Store, API order; No Agency last; counts = Stores only.
 assert(JSON.stringify(groups())===JSON.stringify(['Agency One2','Merchant One2','Beta Agency0','Empty Merchant0','No Agency120','Other Merchant120']),'groups: '+groups());
 const empty=body.querySelector('tr.mg-md-empty-row');
 assert(empty&&empty.textContent.trim()==='No stores'&&!empty.hasAttribute('data-row-select')&&!empty.hasAttribute('tabindex'),'a Merchant without Stores shows a "No stores" row (not selectable)');
 assert(rows().length===122&&rows().includes('2119'),'all 120 Stores of one Merchant (no 100-row page)');
 const inactive=body.querySelector('tr[data-row-select="12"]');
 assert(inactive.classList.contains('mg-md-row-inactive')&&/Inactive One \(STR26INAC\)\s*inactive/i.test(inactive.textContent),'Inactive Store kept with its status: '+inactive.textContent);
 assert(body.querySelector('tr[data-row-select="10"]').textContent.trim()==='Store One (STR26ABCD)','row label');
 // Fixed header (no sort).
 const th=find('masterListHeader').querySelector('th');
 assert(th.textContent.trim()==='Agency / Merchant / Store'&&!th.classList.contains('sortable-col')&&!th.hasAttribute('role'),'fixed Tree header');
 const before=JSON.stringify(groups());th.click();await pause(60);
 assert(JSON.stringify(groups())===before,'header click does not reorder');
 // Search / Status kept; Agency / Merchant filters, Sort, Rows and Pager hidden; count text = Stores.
 for(const id of ['masterSearch','masterStatusFilter'])assert(shown(find(id)),id+' shown');
 for(const id of ['storeAgencyCombo','storeMerchantCombo','storeSortSelect','storePageSize','storePagerPrev','storePagerNext','storePagerPage'])assert(!shown(find(id)),id+' hidden');
 assert(find('storePagerInfo').textContent.trim()==='122 stores','count: '+find('storePagerInfo').textContent);
 assert(!win.requests.some(r=>r.path==='/platform/stores'),'no paged list API');
 // Search: Merchant / Agency / Store name or code; the matched path opens.
 const search=v=>{find('masterSearch').value=v;find('masterSearch').dispatchEvent(new win.KeyboardEvent('keyup',{key:'Enter',bubbles:true}));};
 search('bulk 11');await wait(()=>rows().length===10,'search Store name');
 assert(rows().every(id=>Number(id)>=2110&&Number(id)<=2119)&&JSON.stringify(groups())===JSON.stringify(['No Agency10','Other Merchant10']),'search result: '+groups());
 search('empty');await wait(()=>rows().length===0&&groups().includes('Empty Merchant0'),'search a Merchant without Stores');
 search('agency one');await wait(()=>rows().length===2,'search Agency name');
 search('STR26ABCD');await wait(()=>rows().length===1&&rows()[0]==='10','search Store code');
 search('');await wait(()=>rows().length===122,'search cleared');
 // Status: Inactive only; the "No stores" row is hidden while a Status is set.
 find('masterStatusFilter').value='inactive';find('masterStatusFilter').dispatchEvent(new win.Event('change'));
 await wait(()=>rows().length===41,'Inactive filter');
 assert(!body.querySelector('tr.mg-md-empty-row')&&!groups().some(g=>g.startsWith('Beta Agency')),'no empty Merchant under a Status filter');
 find('masterStatusFilter').value='';find('masterStatusFilter').dispatchEvent(new win.Event('change'));
 await wait(()=>rows().length===122,'Status cleared');
 // Collapse / expand keeps working.
 body.querySelector('tr[data-tree-toggle="agency:none"]').click();await wait(()=>rows().length===2,'collapse No Agency');
 body.querySelector('tr[data-tree-toggle="agency:none"]').click();await wait(()=>rows().length===122,'expand No Agency');
 // Layout: the common workspace (list | form), no page overflow; the list scrolls inside its panel.
 assert(find('masterDetailList')&&find('masterDetailForm').contains(find('masterEditModal')),'Tree left, form panel right');
 assert(!find('masterRoleView').classList.contains('store-split')&&!shown(find('storeDevicesCol'))&&!shown(find('storeSplitter')),'old split list / Devices column not used');
 const wrap=find('masterListScroll');
 assert(wrap.scrollHeight>wrap.clientHeight&&win.getComputedStyle(wrap).overflowY==='auto','long Tree scrolls inside the list panel');
 for(const width of [1440,768,375]){frame.style.width=width+'px';await pause(80);
  assert(doc.documentElement.scrollWidth<=width+1,'no page overflow at '+width+': '+doc.documentElement.scrollWidth);}
 frame.style.width='1440px';
""")

    def test_selection_loads_the_detail_into_the_existing_form(self):
        self.run_page(r"""
 assert(find('masterEditModal').hidden&&shown(find('masterDetailEmpty')),'empty state before a selection');
 body.querySelector('tr[data-row-select="10"]').click();
 await wait(()=>!find('masterEditModal').hidden&&f('store_code').value==='STR26ABCD','the form shows the selected Store');
 assert(win.requests.filter(r=>r.path==='/platform/store/10').length===1,'existing detail API, once');
 assert(f('store_name').value===win.__detail.store_name&&f('merchant_id').value==='10'&&f('id').value==='10','detail values in the form');
 assert(f('status').value===win.__detail.store_status&&f('status').value!=='','Status from the detail store_status: '+f('status').value);
 assert(body.querySelector('tr[data-row-select="10"]').classList.contains('selected'),'selected row');
 const formBody=find('masterEditModal');
 for(const width of [1440,1100]){frame.style.width=width+'px';await pause(80);
  assert(formBody.scrollWidth<=formBody.clientWidth+1,'no horizontal scroll in the form at '+width+': '+formBody.scrollWidth+'/'+formBody.clientWidth);}
 frame.style.width='1440px';
 // The 8 tabs of the existing Store form (Platform scope: Notes included).
 const tabs=[...find('masterEditModal').querySelectorAll('.merchant-tab-btn')].map(b=>b.textContent.trim());
 assert(JSON.stringify(tabs)===JSON.stringify(['Overview','Contact','Address','Receipt Info','License','Business Options','Item Features','Notes']),'tabs: '+tabs);
 for(const btn of find('masterEditModal').querySelectorAll('.merchant-tab-btn')){btn.click();await pause(20);
  const panel=find('masterEditModal').querySelector('.merchant-tab-panel.active');
  assert(panel&&panel.dataset.tabPanel===btn.dataset.tabTarget&&shown(panel),'tab shows its panel: '+btn.textContent.trim());}
 assert(f('notes').value===win.__detail.notes,'Notes (Platform only) filled');
 // A slower answer for an earlier click is dropped: the latest selection wins.
 win.__delay[2001]=400;
 body.querySelector('tr[data-row-select="2001"]').click();await pause(30);
 body.querySelector('tr[data-row-select="2002"]').click();
 await wait(()=>f('store_code').value==='DET2002','latest selection');
 await pause(500);
 assert(f('store_code').value==='DET2002'&&f('id').value==='2002','the earlier (slower) answer did not replace the form');
 assert(body.querySelector('tr[data-row-select="2002"]').classList.contains('selected')&&!body.querySelector('tr[data-row-select="2001"]').classList.contains('selected'),'only the latest row selected');
 // The "No stores" row does nothing.
 const calls=win.requests.length;body.querySelector('tr.mg-md-empty-row').click();await pause(60);
 assert(win.requests.length===calls&&f('id').value==='2002','"No stores" row is not selectable');
""")

    def test_license_tab_shows_the_existing_devices_and_licenses_panel(self):
        # The existing panel (store_devices_panel.js) inside the License tab: follows the selected Store; Contract
        # Quantities, Issue Licenses and Email Preview / Send call the existing license APIs of that Store.
        self.run_page(r"""
 const panel=find('storeDevicesPanel'),license=find('masterEditModal').querySelector('.merchant-tab-panel[data-tab-panel="license"]');
 assert(license&&license.contains(panel),'Devices & Licenses panel is inside the License tab');
 assert(!find('storeDevicesCol').contains(panel),'not in the old side column');
 for(const n of ['store_license_key','store_license_status','store_license_start_date','license_expire_date','license_count'])assert(license.contains(f(n)),n+' kept in the License tab');
 body.querySelector('tr[data-row-select="10"]').click();
 await wait(()=>f('store_code').value==='STR26ABCD','Store 10');
 find('masterEditModal').querySelector('.merchant-tab-btn[data-tab-target="license"]').click();
 await wait(()=>panel.textContent.includes('AWS-POS-AAAA-BBBB-CCCC-10'),'Store 10 licenses');
 assert(win.requests.some(r=>r.path==='/platform/store/10/devices-licenses'),'Store 10 workspace');
 assert(shown(find('sdBtnIssue'))&&shown(find('sdBtnQty'))&&shown(find('sdSendEmail'))&&shown(find('sdPreviewEmail')),'Issue / Quantities / Preview / Send shown');
 // Layout: Store License fields (form) > Contract Quantities > Device Licenses.
 const qtyTable=find('sdQtyTable'),licTable=find('sdLicenseTable');
 assert(f('license_count').compareDocumentPosition(qtyTable)&4&&qtyTable.compareDocumentPosition(licTable)&4,'section order');
 const rowText=tr=>[...tr.children].map(td=>td.textContent.replace(/\s+/g,' ').trim()).join(' ');
 const qrows=[...qtyTable.querySelectorAll('tbody tr')].map(rowText);
 assert(qrows[0]==='POS 3 1 0 2'&&qrows[1]==='KIOSK 1 0 0 1'&&/^Customer Ordering Not enabled/.test(qrows[2]),'Contract Quantities: '+JSON.stringify(qrows));
 const heads=[...licTable.querySelectorAll('thead th')].map(th=>th.textContent.trim());
 assert(JSON.stringify(heads)==='["","Device Name","Type","License Key","Issued Date","Valid Until","Status","Activated / Linked Device","Last Seen (Server)","Email",""]','columns: '+heads);
 const licWrap=licTable.closest('.sd-table-wrap');
 assert(licWrap.scrollWidth<=licWrap.clientWidth+1,'Device Licenses fit the form at 1440 (Copy / Details visible): '+licWrap.scrollWidth+'/'+licWrap.clientWidth);
 const cells=id=>[...panel.querySelector('[data-sd-license-select="'+id+'"]').closest('tr').children].map(td=>td.textContent.replace(/\s+/g,' ').trim());
 assert(cells(101)[4]==='11-01-2026'&&cells(101)[5]==='10-31-2027'&&cells(101)[7]==='—'&&cells(101)[8]==='Not Recorded'&&cells(101)[9]==='Not sent','issued / valid until / no device / last seen / not sent: '+cells(101));
 assert(cells(103)[4]==='10-20-2026'&&cells(103)[5]==='—'&&/Front Kiosk · Windows 11\s*Activated 10-21-2026/.test(cells(103)[7])&&cells(103)[8]==='11-14-2026 04:05 PM'&&cells(103)[9]==='Sent 10-22-2026 (2x)','linked device / last seen / email sent: '+cells(103));
 // Accordion (Common AwsumCollapsible): Contract Quantities and Device Licenses open, Issue History closed.
 const sec=id=>panel.querySelector('[data-awsum-collapsible="'+id+'"]'),tog=id=>sec(id).querySelector('.awsum-collapsible-toggle');
 const open=id=>tog(id).getAttribute('aria-expanded')==='true',bodyShown=id=>shown(sec(id).querySelector('.awsum-collapsible-body'));
 assert(open('sdQty')&&open('sdLicenses')&&!open('sdHistory'),'default: Quantities / Licenses open, History closed');
 await wait(()=>bodyShown('sdQty')&&bodyShown('sdLicenses')&&!bodyShown('sdHistory'),'default bodies');
 assert(tog('sdHistory').tagName==='BUTTON'&&tog('sdHistory').getAttribute('aria-controls')===sec('sdHistory').querySelector('.awsum-collapsible-body').id,'keyboard: a real button with aria-controls');
 const summary=id=>sec(id).querySelector('.awsum-collapsible-summary').textContent;
 assert(summary('sdQty')==='POS 1/3 · KIOSK 0/1 · Ordering Off'&&summary('sdLicenses')==='2 licenses · 1 activated'&&summary('sdHistory')==='2 issues','summaries: '+[summary('sdQty'),summary('sdLicenses'),summary('sdHistory')]);
 tog('sdHistory').click();await wait(()=>bodyShown('sdHistory')&&open('sdHistory'),'History opened');
 tog('sdQty').click();await wait(()=>!bodyShown('sdQty')&&!open('sdQty'),'Quantities closed');
 assert(shown(tog('sdQty'))&&summary('sdQty').length>0&&shown(find('sdBtnQty')),'collapsed: title, summary and action stay visible');
 panel.querySelector('[data-awsum-collapsible-all="collapse"]').click();
 await wait(()=>!bodyShown('sdQty')&&!bodyShown('sdLicenses')&&!bodyShown('sdHistory'),'Collapse All');
 assert(shown(find('sdBtnIssue'))&&shown(find('sdSendEmail')),'Device License actions visible while collapsed');
 panel.querySelector('[data-awsum-collapsible-all="expand"]').click();
 await wait(()=>bodyShown('sdQty')&&bodyShown('sdLicenses')&&bodyShown('sdHistory'),'Expand All');
 tog('sdHistory').click();await wait(()=>!bodyShown('sdHistory'),'History closed again');
 // The Store / Merchant / Agency heading of the panel repeats the form: hidden inside the Store form only.
 assert(!shown(panel.querySelector('.sd-title'))&&!shown(panel.querySelector('.sd-sub')),'panel heading hidden in the License tab');
 // Issue History: one entry per issue request ID; licenses without a request ID are "Not recorded" (never by time).
 const hist=()=>[...find('sdHistoryTable').querySelectorAll('tbody tr')].map(rowText);
 assert(JSON.stringify(hist())===JSON.stringify(['11-01-2026 admin@example.test req-aaaa-0001 POS-01 POS x1 Not sent','10-20-2026 — Not recorded KIOSK-01 KIOSK x1 1 / 1 sent']),'history: '+JSON.stringify(hist()));
 // Selection: none -> Preview / Send disabled; one; all; deselect all.
 const sel=id=>{const b=panel.querySelector('[data-sd-license-select="'+id+'"]');b.checked=!b.checked;b.dispatchEvent(new win.Event('change',{bubbles:true}));};
 const state=()=>[find('sdSelectedCount').textContent,find('sdPreviewEmail').disabled,find('sdSendEmail').disabled].join('|');
 assert(state()==='0 selected|true|true','nothing selected: '+state());
 sel(101);await wait(()=>state()==='1 selected|false|false','one selected');
 find('sdSelectAll').click();await wait(()=>state()==='2 selected|false|false','all selected');
 find('sdDeselectAll').click();await wait(()=>state()==='0 selected|true|true','deselected');
 panel.querySelector('[data-sd-detail="103"]').click();
 await wait(()=>shown(find('sdModalBody'))&&find('sdModalBody').textContent.includes('Last seen (server)'),'details');
 const det=find('sdModalBody').textContent.replace(/\s+/g,' ');
 assert(/Name\s*Front Kiosk/.test(det)&&/Last seen \(server\)\s*11-14-2026 04:05 PM/.test(det)&&/Last IP \(server\)\s*203\.0\.113\.7/.test(det),'details: device name / last seen / last IP: '+det.slice(0,400));
 // Details dates: the Common formatter (MM-DD-YYYY hh:mm AM/PM) for Issued / Activated at / Linked / Unlinked.
 assert(/Issued\s*10-20-2026 09:00 AM/.test(det)&&/Activated at\s*10-21-2026 08:00 AM/.test(det),'details issued / activated: '+det.slice(0,500));
 assert(det.includes('10-01-2026 10:15 AM')&&det.includes('10-19-2026 05:40 PM by admin@example.test')&&det.includes('10-21-2026 08:00 AM'),'details link history dates: '+det);
 // Details Email = the list's Email for the same license (same aggregation and emailText).
 assert(/Email\s*Sent 10-22-2026 \(2x\)/.test(det)&&cells(103)[9]==='Sent 10-22-2026 (2x)','details email = list email: '+det.slice(0,300));
 doc.querySelector('#sdModal [data-bs-dismiss="modal"]').click();await wait(()=>!shown(find('sdModalBody')),'details closed');await pause(300);
 // Another Store: the panel reloads for it.
 body.querySelector('tr[data-row-select="2003"]').click();
 await wait(()=>f('store_code').value==='DET2003'&&panel.textContent.includes('AWS-POS-AAAA-BBBB-CCCC-2003'),'Store 2003 licenses');
 assert(!panel.textContent.includes('CCCC-10'),'previous Store licenses replaced');
 // Contract Quantities -> PUT entitlement of the selected Store.
 find('sdBtnQty').click();await wait(()=>shown(find('sdQtySave')),'quantity dialog');
 assert(find('sdQtyPos').value==='3'&&find('sdQtyKiosk').value==='1','current contracted quantities');
 find('sdQtyPos').value='4';find('sdQtySave').click();
 await wait(()=>win.requests.some(r=>r.method==='PUT'&&r.path==='/platform/store/2003/entitlement'),'entitlement saved');
 const qty=win.requests.find(r=>r.path==='/platform/store/2003/entitlement');
 assert(qty.body.pos_qty===4&&qty.body.kiosk_qty===1&&qty.body.customer_ordering===false,'quantities payload: '+JSON.stringify(qty.body));
 await wait(()=>!shown(find('sdQtySave')),'quantity dialog closed');await pause(400);
 // Issue Licenses -> POST issue (type, quantity, request_id) of the selected Store; the new key is listed.
 find('sdBtnIssue').click();await wait(()=>shown(find('sdIssueGo'))&&!find('sdIssueGo').disabled,'issue dialog');
 find('sdIssueGo').click();
 await wait(()=>win.requests.some(r=>r.method==='POST'&&r.path==='/platform/store/2003/licenses/issue'),'issued');
 const issue=win.requests.find(r=>r.path==='/platform/store/2003/licenses/issue');
 assert(issue.body.license_type==='POS'&&issue.body.quantity===1&&/^[A-Za-z0-9_-]{8,64}$/.test(issue.body.request_id),'issue payload: '+JSON.stringify(issue.body));
 await wait(()=>panel.textContent.includes('AWS-POS-DDDD-EEEE-FFFF-2003'),'issued key listed');
 const keys=[...find('sdLicenseTable').querySelectorAll('.sd-key')].map(k=>k.textContent);
 assert(keys.includes('AWS-POS-AAAA-BBBB-CCCC-2003')&&keys.includes('AWS-KSK-GGGG-HHHH-JJJJ-2003')&&keys.length===3,'existing licenses kept: '+keys);
 const newRow=panel.querySelector('[data-sd-license-select="20032"]').closest('tr');
 assert(/POS-02\s*New/.test(newRow.textContent)&&newRow.textContent.includes('11-15-2026'),'new license marked New with its issued date');
 assert(!/New/.test(panel.querySelector('[data-sd-license-select="20031"]').closest('tr').children[1].textContent),'existing license not marked New');
 assert(rowText(find('sdQtyTable').querySelector('tbody tr'))==='POS 3 2 0 1','quantities refreshed');
 assert(hist()[0]==='11-15-2026 admin@example.test req-bbbb-0002 POS-02 POS x1 Not sent'&&hist().length===3,'new issue on top of the history: '+JSON.stringify(hist()));
 assert(!open('sdHistory')&&open('sdLicenses'),'section state kept after the re-render');
 await wait(()=>!shown(find('sdIssueGo')),'issue dialog closed');await pause(400);
 // Email Preview / Send -> the existing email APIs with the selected license IDs.
 const box=panel.querySelector('[data-sd-license-select="20031"]');box.checked=true;box.dispatchEvent(new win.Event('change',{bubbles:true}));
 await wait(()=>!find('sdPreviewEmail').disabled,'license selected');
 find('sdPreviewEmail').click();
 await wait(()=>win.requests.some(r=>r.path==='/platform/store/2003/licenses/email/preview')&&!find('sdEmailSend').disabled,'preview');
 const preview=win.requests.find(r=>r.path==='/platform/store/2003/licenses/email/preview');
 assert(preview.body.license_ids.join(',')==='20031','preview license IDs: '+JSON.stringify(preview.body));
 assert(find('sdEmailTo').value==='license@example.test','recipient from the workspace');
 const loadsBefore=win.requests.filter(r=>r.path==='/platform/store/2003/devices-licenses').length;
 find('sdEmailSend').click();
 await wait(()=>win.requests.some(r=>r.method==='POST'&&r.path==='/platform/store/2003/licenses/email'),'sent');
 await wait(()=>win.requests.filter(r=>r.path==='/platform/store/2003/devices-licenses').length>loadsBefore,'workspace reloaded after the accepted send (Email status)');
 const sent=win.requests.find(r=>r.path==='/platform/store/2003/licenses/email');
 assert(sent.body.license_ids.join(',')==='20031'&&sent.body.recipient==='license@example.test','send payload: '+JSON.stringify(sent.body));
 assert(JSON.stringify(preview.body.license_ids)===JSON.stringify(sent.body.license_ids),'preview and send use the same licenses');
""")

    def test_deep_link_selects_the_store(self):
        # srcdoc pages cannot change their URL: the page's own query read is given ?store_id=2050.
        page = self.page()
        self.assertEqual(page.count("new URLSearchParams(window.location.search)"), 1)
        page = page.replace("new URLSearchParams(window.location.search)", "new URLSearchParams('?store_id=2050')")
        self.page = lambda: page
        self.run_page(r"""
 await wait(()=>f('store_code').value==='DET2050'&&!find('masterEditModal').hidden,'?store_id= opens the Store');
 assert(body.querySelector('tr[data-row-select="2050"]').classList.contains('selected'),'deep-linked row selected');
""")


class TreeCountItemTests(unittest.TestCase):
    """The Common treeHtml countItem option (static/js/master-detail.js): default counts every row."""

    def test_count_item_option_and_default(self):
        import subprocess
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed.")
        script = MASTER_DETAIL_JS + r"""
const api=module.exports;
const items=[{id:1,g:'A'},{id:2,g:'A',placeholder:true},{id:3,g:'B'}];
const levels=[{key:i=>'g:'+i.g,name:i=>i.g}];
const rowHtml=i=>`<tr data-id="${i.id}"></tr>`;
const counts=h=>[...h.matchAll(/mg-tree-count">(\d+)</g)].map(m=>Number(m[1]));
const plain=api.treeHtml({items,levels,collapsed:new Set(),colspan:1,rowHtml});
const counted=api.treeHtml({items,levels,collapsed:new Set(),colspan:1,rowHtml,countItem:i=>!i.placeholder});
console.log(JSON.stringify({plain:counts(plain),counted:counts(counted),rows:(counted.match(/data-id=/g)||[]).length}));
"""
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), {"plain": [2, 1], "counted": [1, 1], "rows": 3})


if __name__ == "__main__":
    unittest.main()
