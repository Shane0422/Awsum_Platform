"""Focused rendering and list-interaction checks without live API or DB access."""
import json
import re
import unittest

from tools import test_image_components as browser
from tools.test_platform_navigation import render_navigation


PAGES = [
    ("tax", "Tax"), ("category1", "Category I"), ("category2", "Category II"),
    ("category3", "Category III"), ("md", "MD Master"),
]
SPLIT = {"tax", "category1", "category2", "category3", "md", "item_kind"}  # Master-Detail layout (left Tree + right form)


def render_list(entity, title):
    base, context, _ = render_navigation()
    context.update(mg_entity=entity, mg_title=title, active_page="master-group")
    return base.environment.get_template("platform/master_group.html").render(context)


class MasterGroupListTemplateTests(unittest.TestCase):
    def test_all_lists_share_styles_and_keep_existing_controls(self):
        for entity, title in PAGES:
            with self.subTest(entity=entity):
                html = render_list(entity, title)
                self.assertEqual(html.count('href="/static/css/awsum-list.css?v=5"'), 1)
                self.assertIn("mg-page awsum-list", html)
                self.assertIn(f'data-entity="{entity}"', html)
                self.assertIn(f"Add {title}</button>", html)
                self.assertIn('src="/static/js/master_group.js?v=45"', html)
                if entity in SPLIT:
                    self.assertIn('class="mg-md-workspace"', html)
                    self.assertIn('class="mg-md-form"', html)
                    self.assertNotIn('id="mgModal"', html)
                    self.assertNotIn("awsum-filter-card", html)
                    self.assertEqual(html.count('href="/static/css/awsum-master-detail.css?v=1"'), 1)
                else:
                    self.assertIn('id="mgModal"', html)
                for control in ("mgSearch", "mgActiveFilter", "mgPageSize", "mgExportBtn",
                                "mgAddBtn", "mgHead", "mgBody", "mgPrev", "mgNext",
                                "mgForm", "mgSave"):
                    self.assertEqual(html.count(f'id="{control}"'), 1)
                for control in ("mgSearch", "mgActiveFilter", "mgPageSize"):
                    self.assertIn(f'for="{control}"', html)
                self.assertNotIn(".mg-page .mg-table", html)


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class MasterGroupListBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_common_headers_stick_without_fixing_message_filter_or_table(self):
        from backend.services.app_navigation import AppScope

        css = "\n".join(
            (browser.ROOT / "static" / path).read_text(encoding="utf-8")
            for path in ("bootstrap/css/bootstrap.min.css", "css/brand.css", "css/awsum-list.css")
        )
        pages = []
        for scope in ("PLATFORM", "MERCHANT"):
            for entity, title in [page for page in PAGES if page[0] not in SPLIT]:
                base, context, _ = render_navigation()
                context.update(mg_entity=entity, mg_title=title, active_page="master-group",
                               mg_api_base=f"/{scope.lower()}/api/master-group",
                               app_navigation=AppScope("MERCHANT", 20, "Merchant Admin", True).navigation()
                               if scope == "MERCHANT" else None)
                html = base.environment.get_template("platform/master_group.html").render(context)
                html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
                html = re.sub(r"<link\b[^>]*>", "", html)
                html = html.replace("</head>", f"<style>{css}</style></head>")
                pages.append(html)
        if not pages:
            self.skipTest("Every Master Group list uses the Master-Detail layout; no full-width list remains.")
        serialized = json.dumps(pages).replace("</", "<\\/")
        script = f"const pages={serialized};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,60));
(async()=>{
  for(const html of pages){
    const frame=document.createElement('iframe');frame.style.border='0';frame.srcdoc=html;
    const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
    document.body.appendChild(frame);await loaded;
    const doc=frame.contentDocument,win=frame.contentWindow;
    const nav=doc.querySelector('.platform-header'),header=doc.querySelector('.mg-page > .awsum-page-header');
    const message=doc.getElementById('mgMessage'),filter=doc.querySelector('.awsum-filter-card');
    const table=doc.querySelector('.awsum-table-card');
    message.className='alert alert-success';message.textContent='Saved.';
    doc.getElementById('mgSubtitle').textContent='Common description';
    doc.getElementById('mgBody').innerHTML='<tr><td style="height:1800px">Scrollable records</td></tr>';
    for(const width of [1440,768,375,320]){
      frame.style.width=width+'px';frame.style.height='700px';win.scrollTo(0,0);await tick();
      assert(Math.abs(parseFloat(win.getComputedStyle(header).top)-nav.getBoundingClientRect().height)<1,'actual responsive navigation height');
      for(const node of [message,filter,table]){
        assert(!['sticky','fixed'].includes(win.getComputedStyle(node).position),'only page header is sticky');
      }
      assert(header.contains(doc.querySelector('.awsum-list-icon'))&&header.contains(doc.getElementById('mgSubtitle'))
        &&header.contains(doc.getElementById('mgExportBtn'))&&header.contains(doc.getElementById('mgAddBtn')),'complete header sticks together');
      win.scrollTo(0,500);await tick();
      assert(Math.abs(nav.getBoundingClientRect().top)<1,'navigation stays at top');
      assert(Math.abs(header.getBoundingClientRect().top-nav.getBoundingClientRect().bottom)<1,'no gap above sticky page header');
      assert(message.getBoundingClientRect().bottom<header.getBoundingClientRect().bottom,'message scrolls out');
      assert(filter.getBoundingClientRect().bottom<header.getBoundingClientRect().bottom,'filter scrolls out');
      assert(table.getBoundingClientRect().top<header.getBoundingClientRect().bottom,'table scrolls under header without spacer');
      const previous=table.getBoundingClientRect().top;
      win.scrollTo(0,600);await tick();
      assert(Math.abs(previous-table.getBoundingClientRect().top-100)<1,'table scrolls normally');
    }
    doc.getElementById('mgPage').classList.remove('mg-page');
    assert(win.getComputedStyle(header).position==='static','other management headers are unchanged');
    frame.remove();
  }
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0,pages:8,viewports:4});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:error.stack});});
"""
        self.run_browser(script)

    def test_all_list_styles_responsive_and_existing_interactions(self):
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8") + "\n" + "\n".join(
            (browser.ROOT / "static" / "css" / name).read_text(encoding="utf-8")
            for name in ("brand.css", "awsum-list.css", "awsum-master-detail.css"))
        client = (browser.ROOT / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
        tree = (browser.ROOT / "static" / "js" / "master-detail.js").read_text(encoding="utf-8")
        money = (browser.ROOT / "static" / "js" / "money.js").read_text(encoding="utf-8")
        mock = r"""
window.requests=[];
window.bootstrap={Modal:class{show(){}hide(){}}};
window.fetch=async(path,options)=>{
  const url=new URL(path,'http://127.0.0.1');requests.push({path,method:options.method});
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({can_view_cost:true}));
  const q=url.searchParams;
  const item={id:1,code:'TEST00001',name:'Sample Record',rate:'5.00',display_order:1,
    active:true,category1_name:'Food',category2_name:'Beverage',category3_name:'Soft Drink',
    retail_price:'2.50',cost:'1.20',item_kind:'HARDWARE',billing_cycle:'ONE_TIME'};
  return new Response(JSON.stringify({items:q.get('q')==='empty'?[]:[item],
    total:40,page:Number(q.get('page')||1),pages:2,sort:q.get('sort')||'code',direction:q.get('direction')||'asc'}));
};
"""
        pages = []
        for entity, title in PAGES:
            html = render_list(entity, title)
            html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
            html = re.sub(r"<link\b[^>]*>", "", html)
            html = html.replace("</head>", f"<style>{css}</style></head>")
            html = html.replace("</body>", f"<script>{mock}\n{money}\n{tree}\n{client}</script></body>")
            pages.append(html)
        serialized = json.dumps(pages).replace("</", "<\\/")
        script = f"const pages={serialized};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
const wait=async(check)=>{for(let i=0;i<100;i++){if(check())return;await tick();}throw Error('List timed out');};
(async()=>{
  for(const html of pages){
    const frame=document.createElement('iframe');frame.style.border='0';frame.srcdoc=html;
    const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
    document.body.appendChild(frame);await loaded;
    const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id);
    await wait(()=>find('mgBody').textContent.includes('Sample Record'));
    assert(find('mgSubtitle').textContent.length>0,'existing subtitle populated');
    const split=['tax','category1','category2','category3','md','item_kind'].includes(find('mgPage').dataset.entity);
    assert(find('mgBody').querySelectorAll('[data-act]').length===(split?0:3),'row actions are correct');
    assert(win.getComputedStyle(doc.querySelector('.awsum-table-card')).borderRadius==='12px','common card styling');
    const statusBadge=doc.querySelector('.text-bg-success');
    if(split){
      assert(!statusBadge,'Master-Detail list does not render columns outside Category and Name');
      assert(doc.querySelector('.mg-md-workspace .mg-md-list-panel')&&doc.querySelector('.mg-md-workspace .mg-md-form-panel'),'split list and form layout');
      assert(!find('mgBody').querySelector('[data-act="edit"]'),'Edit action is replaced by row selection');
      assert(find('mgHead').querySelectorAll('th').length===1,'only the Name column is shown');
    }else{
      assert(statusBadge&&win.getComputedStyle(statusBadge).backgroundColor==='rgb(220, 248, 232)','common status badge');
    }
    for(const width of [1440,768,375,320]){
      frame.style.width=width+'px';frame.style.height='800px';await tick();
      assert(doc.documentElement.scrollWidth<=width+1,'no whole-page horizontal overflow: '+width);
      const wrap=doc.querySelector('.awsum-list-table-wrap');
      assert(wrap.getBoundingClientRect().right<=width,'wide table remains inside responsive wrapper');
      for(const id of ['mgSearch','mgActiveFilter','mgPageSize','mgAddBtn']){
        const r=find(id).getBoundingClientRect();assert(r.left>=0&&r.right<=width+1,'control fits: '+id);
      }
    }
    find('mgSearch').value='sample';find('mgSearch').dispatchEvent(new win.Event('input'));
    await wait(()=>win.requests.some(r=>r.path.includes('q=sample')));
    find('mgActiveFilter').value='active';find('mgActiveFilter').dispatchEvent(new win.Event('change'));
    await wait(()=>win.requests.some(r=>r.path.includes('active=active')));
    find('mgPageSize').value='10';find('mgPageSize').dispatchEvent(new win.Event('change'));
    await wait(()=>win.requests.some(r=>r.path.includes('size=10')));
    find('mgHead').querySelector('[data-sort="name"]').click();
    await wait(()=>find('mgHead').querySelector('[data-sort="name"]').classList.contains('sorted'));
    find('mgHead').querySelector('[data-sort="name"]').dispatchEvent(new win.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
    await wait(()=>find('mgHead').querySelector('[data-sort="name"]').classList.contains('desc'));
    find('mgNext').click();await wait(()=>find('mgPageLabel').textContent==='2 / 2');
    find('mgPrev').click();await wait(()=>find('mgPageLabel').textContent==='1 / 2');
    find('mgSearch').value='empty';find('mgSearch').dispatchEvent(new win.Event('input'));
    await wait(()=>find('mgBody').textContent.includes('No records found.'));
    assert(win.requests.every(r=>r.method==='GET'&&r.path.startsWith('/platform/api/master-group/')),'existing read-only endpoints');
    frame.remove();
  }
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0,pages:5,viewports:4});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_md_split_form_and_detail_crud_use_existing_api(self):
        client = (browser.ROOT / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
        tree = (browser.ROOT / "static" / "js" / "master-detail.js").read_text(encoding="utf-8")
        money = (browser.ROOT / "static" / "js" / "money.js").read_text(encoding="utf-8")
        barcode = (browser.ROOT / "static" / "js" / "barcode-input.js").read_text(encoding="utf-8")
        option_group = (browser.ROOT / "static" / "js" / "option-group.js").read_text(encoding="utf-8")
        keypad = (browser.ROOT / "static" / "js" / "money-keypad.js").read_text(encoding="utf-8")
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8") + "\n" + "\n".join(
            (browser.ROOT / "static" / "css" / name).read_text(encoding="utf-8")
            for name in ("brand.css", "awsum-list.css", "awsum-master-detail.css", "awsum-option-group.css", "awsum-money-keypad.css"))
        html = render_list("md", "MD Master")
        html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
        html = re.sub(r"<link\b[^>]*>", "", html)
        html = html.replace("</head>", f"<style>{css}</style></head>")
        mock = r"""
window.requests=[];
window.details=[
  {id:1,detail_no:1,is_default:true,barcode:'BASE-1',unit:'EA',pk_size:'',size:'',cost:null,price:'95000.00',modifier:''},
  {id:2,detail_no:2,is_default:false,barcode:'BASE-2',unit:'CS',pk_size:'12',size:'12 oz',cost:'1.00',price:'12.00',modifier:'Case'}
];
window.confirm=()=>true;
window.AwsumImages={createUploader:()=>({reset(){},destroy(){},setDisabled(){},formData:value=>value})};
window.fetch=async(path,options={})=>{
  const url=new URL(path,'http://127.0.0.1'),method=options.method||'GET';
  const request={path:url.pathname,method,payload:options.body?JSON.parse(options.body):null};requests.push(request);
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({
    can_view_cost:true,
    category1:[{id:1,name:'Food',code:'FOOD',active:true}],
    category2:[{id:2,name:'Beverage',code:'BEV',category1_id:1,active:true}],
    category3:[{id:3,name:'Soft Drink',code:'SOFT',category2_id:2,active:true,inventory_type:3,keep:'DRY'}],
    taxes:[],item_kinds:[{code:'ITEM',name:'Item'},{code:'SERVICE',name:'Service'},{code:'GIFT_CARD',name:'Gift Card'}],default_item_kind:'ITEM',billing_cycles:['ONE_TIME','MONTHLY','ANNUAL'],
    license_types:['POS','KIOSK','CUSTOMER_ORDERING'],
    inventory_types:[
      {type_id:1,code:'NONE',name:'None',keep_required:false},{type_id:2,code:'STOCK',name:'Stock',keep_required:true},
      {type_id:3,code:'RECIPE',name:'Recipe',keep_required:false},{type_id:4,code:'EXPENSE_ON_RECEIPT',name:'Consume on Receipt',keep_required:false},
      {type_id:5,code:'EXPENSE_ON_ISSUE',name:'Consume on Issue',keep_required:false}],
    default_inventory_type:1,default_keep:'DRY',
    keep_types:[{code:'DRY',name:'Dry'},{code:'COLD',name:'Cold'},{code:'FROZEN',name:'Frozen'}],
  }));
  if(url.pathname.endsWith('/md/barcode-check')){
    const code=url.searchParams.get('barcode');
    return new Response(JSON.stringify(code==='TAKEN'
      ?{barcode:code,available:false,message:'Barcode TAKEN is already used by MD00009 (Detail 1).'}
      :{barcode:code,available:true,message:null}));
  }
  if(url.pathname.endsWith('/details')){
    if(method==='GET')return new Response(JSON.stringify({items:details}));
    if(method==='POST'){
      const item={...request.payload,id:3,detail_no:3,is_default:false};details.push(item);
      return new Response(JSON.stringify({item,message:'Saved.'}),{status:201});
    }
  }
  const detailMatch=/\/details\/(\d+)$/.exec(url.pathname);
  if(detailMatch&&method==='PUT'){
    details=details.map(item=>item.id===Number(detailMatch[1])?{...item,...request.payload}:item);
    return new Response(JSON.stringify({item:details.find(item=>item.id===Number(detailMatch[1]))}));
  }
  if(detailMatch&&method==='DELETE'){
    details=details.filter(item=>item.id!==Number(detailMatch[1]));
    return new Response(JSON.stringify({message:'Deleted.'}));
  }
  if(url.pathname.endsWith('/md'))return new Response(JSON.stringify({items:[{
    id:7,code:'MD00007',name:'Sample Item',active:true,category1_id:1,category2_id:2,category3_id:3,
    category1_name:'Food',category2_name:'Beverage',category3_name:'Soft Drink',
    description:'',second_name:'Alt One',item_kind:'SERVICE',unit:'EA',retail_price:'95000.00',cost:null,
    p1_mode:null,p2_mode:null,p3_mode:null,default_tax_id:null,billing_cycle:'ONE_TIME',
    inventory_type:2,keep:'COLD',
    license_required:false,license_type:null,licenses_per_unit:null,serial_required:false
  }],total:1,page:1,pages:1,sort:'name',direction:'asc'}));
  if(/\/md\/7(\/with-image)?$/.test(url.pathname)&&method==='PUT')return new Response(JSON.stringify({item:{...request.payload,id:7,code:'MD00007'}}));
  if(/\/md\/7$/.test(url.pathname)&&method==='DELETE')return new Response(JSON.stringify({message:'Deleted.'}));
  throw Error('Unexpected request: '+method+' '+url.pathname);
};
"""
        html = html.replace("</body>", f"<script>{mock}\n{money}\n{keypad}\n{tree}\n{barcode}\n{option_group}\n{client}</script></body>")
        serialized = json.dumps(html).replace("</", "<\\/")
        script = f"const html={serialized};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
const wait=async(check)=>{for(let i=0;i<100;i++){if(check())return;await tick();}throw Error('Timed out waiting for UI update');};
(async()=>{
  const frame=document.createElement('iframe');frame.style.border='0';frame.style.width='1400px';frame.style.height='800px';frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id);
  await wait(()=>find('mgBody').textContent.includes('Sample Item'));
  assert([...find('mgHead').querySelectorAll('th')].map(th=>th.textContent.trim()).join('|')==='Category / Name','MD list is one Category / Name tree column');
  // Category I > II > III group rows with +/- above the item; the item row keeps row selection.
  const groupRows=()=>[...find('mgBody').querySelectorAll('tr[data-tree-toggle]')];
  const itemRow=()=>find('mgBody').querySelector('[data-row-select="7"]');
  assert(groupRows().map(tr=>tr.querySelector('td').childNodes[1].textContent).join('>')==='Food>Beverage>Soft Drink','Category I > II > III groups in order');
  assert(groupRows().every(tr=>tr.getAttribute('aria-expanded')==='true'),'groups start expanded');
  assert(groupRows()[2].nextElementSibling===itemRow(),'item row sits under its Category III');
  assert(itemRow().textContent.trim()==='Sample Item','item row shows the Name');
  const depth=tr=>parseFloat(win.getComputedStyle(tr.querySelector('td')).paddingLeft);
  assert(depth(groupRows()[0])<depth(groupRows()[1])&&depth(groupRows()[1])<depth(groupRows()[2])&&depth(groupRows()[2])<depth(itemRow()),'levels are indented');
  groupRows()[0].click();
  await wait(()=>!itemRow()&&groupRows().length===1);
  assert(groupRows()[0].getAttribute('aria-expanded')==='false'&&groupRows()[0].textContent.includes('+'),'Category I collapses its whole branch');
  groupRows()[0].click();
  await wait(()=>!!itemRow()&&groupRows().length===3);
  groupRows()[1].dispatchEvent(new win.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  await wait(()=>!itemRow()&&groupRows().length===2);
  assert(doc.activeElement===groupRows()[1],'keyboard toggle keeps focus on the group');
  find('mgSearch').value='sample';find('mgSearch').dispatchEvent(new win.Event('input'));
  await wait(()=>!!itemRow()&&groupRows().every(tr=>tr.getAttribute('aria-expanded')==='true'));
  assert(groupRows().map(tr=>[1,2,3].find(n=>tr.classList.contains('mg-tree-l'+n))).join('')==='123','group rows carry their Category level');
  const fontWeight=tr=>Number(win.getComputedStyle(tr.querySelector('td')).fontWeight);
  assert(fontWeight(groupRows()[0])>fontWeight(groupRows()[2])&&fontWeight(groupRows()[2])>fontWeight(itemRow()),'Category I > III > Item typography weight');
  const count=groupRows()[0].querySelector('.mg-tree-count');
  assert(count.textContent==='1'&&win.getComputedStyle(count).borderRadius==='999px'&&parseFloat(win.getComputedStyle(count).fontSize)<=10,'item count is a small pill');
  const listPanel=doc.querySelector('.mg-md-list-panel');
  assert(!doc.querySelector('.awsum-filter-card'),'MD has no separate filter card');
  assert(listPanel.querySelector('.mg-md-filter').contains(find('mgSearch'))&&listPanel.querySelector('.mg-md-filter').contains(find('mgActiveFilter')),'Search/Status sit above the left grid');
  assert(listPanel.querySelector('.mg-md-pager').contains(find('mgPageSize')),'Rows per page sits in the pagination footer');
  assert(find('mgMdToggle').classList.contains('d-none')&&find('mgMdDelete').classList.contains('d-none'),'form actions hidden until a record is selected');
  assert(doc.querySelector('.mg-md-workspace .mg-md-list-panel')&&doc.querySelector('.mg-md-workspace .mg-md-form-panel'),'MD uses split workspace');
  assert(!find('mgBody').querySelector('[data-act="edit"]'),'MD row has no Edit button');
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>find('mgDetailBody').textContent.includes('BASE-1'));
  const selectedCell=itemRow().querySelector('td');
  assert(itemRow().classList.contains('selected')&&win.getComputedStyle(selectedCell).backgroundColor==='rgb(231, 240, 254)','selected item uses the Common active blue tint');
  const accent=win.getComputedStyle(selectedCell,'::before');
  assert(accent.width==='3px'&&accent.backgroundColor==='rgb(18, 99, 235)','selected item has a blue accent bar');
  const tracks=win.getComputedStyle(doc.querySelector('.mg-md-workspace')).gridTemplateColumns.split(' ').map(parseFloat);
  assert(tracks[0]/tracks.reduce((sum,value)=>sum+value,0)>=.24&&tracks[0]/tracks.reduce((sum,value)=>sum+value,0)<=.28,'left list uses about 25-27% of the workspace');
  const detailBounds=find('mgDetailSection').getBoundingClientRect();
  assert(detailBounds.top<frame.clientHeight,
    `Detail grid starts in the initial viewport (${detailBounds.top}/${frame.clientHeight})`);
  const tabs=[...find('mgMdTabs').querySelectorAll('[role="tab"]')].map(tab=>tab.textContent.trim());
  assert(tabs.join('|')==='GENERAL|OPTION 1|OPTION 2','GENERAL, OPTION 1 and OPTION 2 tabs exist');
  const activeTab=win.getComputedStyle(find('mgMdTabGeneral'));
  assert(activeTab.backgroundColor==='rgb(255, 255, 255)'&&activeTab.color==='rgb(16, 46, 104)'&&activeTab.boxShadow.includes('rgb(18, 99, 235)'),'selected tab uses common navy/blue accent');
  assert(win.getComputedStyle(doc.querySelector('.mg-md-form-panel')).backgroundColor==='rgb(255, 255, 255)','form card is white');
  const req=find('mgFields').querySelector('.pm-req'),reqLabel=req.closest('.form-label');
  assert(win.getComputedStyle(req).color==='rgb(220, 38, 38)'&&win.getComputedStyle(reqLabel).color==='rgb(73, 100, 134)'&&win.getComputedStyle(req).fontSize===win.getComputedStyle(reqLabel).fontSize,'only the required * is red');
  const header=doc.querySelector('.mg-md-form-header'),fields=find('mgFields');
  assert(header.querySelector('.mg-md-form-title').textContent.trim()==='MD INFORMATION'&&header.getBoundingClientRect().bottom<=find('mgf_record_code').getBoundingClientRect().top,'MD INFORMATION header sits above Code');
  assert(header.getBoundingClientRect().height<=40,'form header is thin');
  assert(find('mgSave').getBoundingClientRect().left>header.querySelector('.mg-md-form-title').getBoundingClientRect().right&&Math.abs(header.getBoundingClientRect().right-find('mgSave').getBoundingClientRect().right)<16,'Save/Deactivate/Delete aligned right in header');
  const panelBox=listPanel.getBoundingClientRect(),pagerBox=listPanel.querySelector('.mg-md-pager').getBoundingClientRect(),wrap=listPanel.querySelector('.awsum-list-table-wrap');
  assert(Math.abs(panelBox.bottom-(win.innerHeight-12))<=2,'left grid card fits the viewport height ('+panelBox.bottom+'/'+win.innerHeight+')');
  assert(Math.abs(listPanel.querySelector('.mg-md-filter').getBoundingClientRect().top-panelBox.top)<20&&Math.abs(panelBox.bottom-pagerBox.bottom)<20,'Search/Status top and Pagination bottom stay fixed');
  assert(['auto','scroll'].includes(win.getComputedStyle(wrap).overflowY)&&wrap.getBoundingClientRect().bottom<=pagerBox.top+1,'only the list scrolls inside the card');
  const tabContent=doc.querySelector('.mg-md-pageframe .tab-content'),panes=[...tabContent.querySelectorAll(':scope > .tab-pane')];
  const frameHeight=tabContent.getBoundingClientRect().height;
  assert(frameHeight+1>=Math.max(...panes.map(pane=>pane.scrollHeight)),'PageFrame uses the tallest tab height');
  panes.forEach(pane=>{panes.forEach(other=>other.classList.toggle('active',other===pane));
    assert(Math.abs(tabContent.getBoundingClientRect().height-frameHeight)<1,'PageFrame height is stable for '+pane.id);});
  panes.forEach(pane=>pane.classList.toggle('active',pane.id==='mgMdPaneGeneral'));
  const general=find('mgMdPaneGeneral'),option1=find('mgMdPaneOption1'),option2=find('mgMdPaneOption2');
  const licenseFields=['license_required','license_type','licenses_per_unit','serial_required'];
  assert(licenseFields.every(name=>option2.contains(find('mgf_'+name))),'OPTION 2 holds License fields');
  assert(licenseFields.every(name=>!general.contains(find('mgf_'+name)))&&!general.contains(find('mgf_active'))&&general.contains(find('mgf_notes')),'GENERAL keeps non-License options only');
  const status=find('mgf_active'),codeEl=find('mgf_record_code');
  assert(doc.querySelector('.mg-md-top').contains(status)&&Math.abs(status.getBoundingClientRect().top-codeEl.getBoundingClientRect().top)<1&&status.getBoundingClientRect().left>codeEl.getBoundingClientRect().right,'Status sits right of Code on the top line');
  find('mgf_license_required').value='true';find('mgf_license_required').dispatchEvent(new win.Event('change',{bubbles:true}));
  assert(!find('mgf_license_type').disabled,'existing License toggle logic still works');
  assert(!general.querySelector('[name^="p1_"],[name^="p2_"],[name^="p3_"],[name="retail_price"]'),'GENERAL has no Pricing or P1/P2/P3');
  assert(['p1_mode','p2_mode','p3_mode','p1_price','p3_discount_pct'].every(name=>option1.contains(find('mgf_'+name))),'OPTION 1 holds P1/P2/P3 price levels');
  const top=doc.querySelector('.mg-md-top');
  assert(top.contains(find('mgf_retail_price'))&&!top.querySelector('[name="cost"]:not([type="hidden"]),#mgf_cost:not([type="hidden"])'),'top shows Price; Cost only in Detail');
  assert(find('mgModalTitle').closest('.visually-hidden')&&find('mgModalSub').closest('.visually-hidden')&&![...top.querySelectorAll('h3')].some(h=>h.textContent.trim()==='Item Information'),'title/description and Item Information heading removed');
  assert(top.querySelector('input:not([type="hidden"])')===find('mgf_record_code'),'Code is at the top of the form');
  const second=find('mgf_second_name');
  assert(!second.disabled&&second.name==='second_name'&&second.value==='Alt One'&&Math.abs(second.getBoundingClientRect().top-find('mgf_name').getBoundingClientRect().top)<1&&second.getBoundingClientRect().left>find('mgf_name').getBoundingClientRect().right,'Item Name + Second Name share one line');
  const rowEls=['default_barcode','unit','default_pk_size','default_size','retail_price'].map(name=>find('mgf_'+name));
  assert(rowEls.every((el,i)=>i===0||(Math.abs(el.getBoundingClientRect().top-rowEls[0].getBoundingClientRect().top)<1&&el.getBoundingClientRect().left>rowEls[i-1].getBoundingClientRect().left)),'Barcode | Unit | PK/Size | Size | Price on one line');
  assert(!find('mgf_default_pk_size').name&&!find('mgf_default_size').name,'PK/Size and Size reuse Default Detail without MD field names');
  assert(!find('mgMdToggle').classList.contains('d-none')&&find('mgMdToggle').textContent==='Deactivate'&&!find('mgMdDelete').classList.contains('d-none'),'form header shows Deactivate/Delete');
  const tallInputs=[...doc.querySelectorAll('.mg-md-form input.form-control:not([type="hidden"]),.mg-md-form select.form-select')]
    .filter(el=>el.offsetParent&&el.getBoundingClientRect().height>28.5);
  assert(tallInputs.length===0,`compact MD inputs/selects are <=28px (${tallInputs.map(el=>el.name||el.id).join(',')})`);
  assert(general.classList.contains('active')&&general.querySelector('.pm-section')===find('mgDetailSection'),'MD Detail is the first GENERAL area');
  assert(doc.querySelector('.mg-md-top').compareDocumentPosition(find('mgMdTabs'))&win.Node.DOCUMENT_POSITION_FOLLOWING,'basic info stays above the PageFrame');
  assert(!general.contains(doc.querySelector('.mg-md-top')),'basic info is outside GENERAL');
  assert(find('mgDetailBody').querySelector('[data-detail-act="delete"]').disabled,'Default Detail cannot be deleted');
  find('mgDetailAdd').click();
  const barcodeField=find('mgDetailBarcode'),firstDetailInput=find('mgDetailEditor').querySelector('input');
  assert(firstDetailInput===barcodeField&&barcodeField.value===''&&doc.activeElement===barcodeField,'Add Detail focuses the empty Barcode (first field)');
  find('mgDetailUnit').value='PK';find('mgDetailPrice').value='18.50';
  find('mgDetailSave').click();
  await wait(()=>find('mgDetailBody').textContent.includes('PK'));
  assert(win.requests.some(request=>request.method==='POST'&&request.path.endsWith('/md/7/details')
    &&request.payload.unit==='PK'&&request.payload.price==='18.50'),'Add uses existing MD Detail API');
  find('mgDetailBody').querySelector('[data-detail-act="edit"][data-id="3"]').click();
  assert(doc.activeElement===find('mgDetailUnit'),'Edit Detail keeps focusing Unit');
  find('mgDetailPrice').value='19.00';find('mgDetailSave').click();
  await wait(()=>win.requests.some(request=>request.method==='PUT'&&request.path.endsWith('/details/3')));
  find('mgDetailBody').querySelector('[data-detail-act="delete"][data-id="3"]').click();
  await wait(()=>!find('mgDetailBody').textContent.includes('PK'));
  assert(win.requests.some(request=>request.method==='DELETE'&&request.path.endsWith('/details/3')),'Delete uses existing MD Detail API');
  const order=['record_code','category1_id','category2_id','category3_id','category_tax_display','name','second_name','default_barcode','unit','default_pk_size','default_size','retail_price','description'].map(name=>find('mgf_'+name));
  assert(order.every((el,i)=>i===0||(order[i-1].compareDocumentPosition(el)&win.Node.DOCUMENT_POSITION_FOLLOWING)),'basic info order: Code, Category/Tax, Item Name/Second Name, Barcode row, Description');
  const taxLabel=doc.querySelector('label[for="mgf_category_tax_display"]'),taxValue=find('mgf_category_tax_display');
  assert(Math.abs(taxLabel.getBoundingClientRect().bottom-taxValue.getBoundingClientRect().bottom)<4&&taxValue.getBoundingClientRect().left>taxLabel.getBoundingClientRect().right&&win.getComputedStyle(taxLabel,'::after').content.includes(':'),'Tax label and value share one line');
  const kind=find('mgf_item_kind'),kindStatus=find('mgf_active');
  assert(kind.value==='SERVICE'&&[...kind.options].map(o=>o.value).join()==='ITEM,SERVICE,GIFT_CARD'&&kind.options[2].textContent==='Gift Card','Item Kind uses active System Master rows');
  assert(Math.abs(kind.getBoundingClientRect().top-kindStatus.getBoundingClientRect().top)<1&&kind.getBoundingClientRect().left>kindStatus.getBoundingClientRect().right,'Item Kind sits right next to Status');
  kind.value='GIFT_CARD';
  assert(![...doc.querySelectorAll('.mg-md-top h3')].some(h=>h.textContent.trim()==='Classification'),'Classification heading removed');
  const code=find('mgf_record_code');
  assert(code.readOnly&&!code.name&&code.value==='MD00007'&&!find('mgFields').textContent.includes('auto-generated'),'Code is small readonly without long note');
  assert(option2.contains(find('mgf_billing_cycle'))&&!doc.querySelector('.mg-md-top').contains(find('mgf_billing_cycle')),'Billing Cycle moved to OPTION 2');
  assert(find('mgf_retail_price').value==='95,000.00'&&doc.activeElement!==find('mgf_retail_price'),'loaded Price is grouped immediately, without focus/blur ('+find('mgf_retail_price').value+')');
  assert(find('mgf_default_barcode').value==='BASE-1'&&!find('mgf_default_barcode').name,'Barcode shows Default Detail barcode without its own field name');
  find('mgf_default_barcode').value='NEW-BC';
  const scan=find('mgf_default_barcode');
  const before=win.requests.length;let scanned=null,submitted=false;
  scan.addEventListener('awsum:barcode',event=>{scanned=event.detail.value;});
  find('mgForm').addEventListener('submit',()=>{submitted=true;},{once:true});
  scan.focus();scan.value='  SCAN-123  ';
  const enter=new win.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});
  scan.dispatchEvent(enter);await tick();
  assert(enter.defaultPrevented&&!submitted&&win.requests.length===before,'scanner Enter does not submit the form');
  assert(doc.activeElement===scan&&scanned==='SCAN-123'&&scan.value==='SCAN-123','scanner Enter completes barcode and keeps focus');
  const typed=new win.KeyboardEvent('keydown',{key:'A',bubbles:true,cancelable:true});
  scan.dispatchEvent(typed);
  assert(!typed.defaultPrevented,'manual typing is unchanged');
  find('mgDetailAdd').click();
  assert(find('mgDetailBarcode').hasAttribute('data-awsum-barcode'),'Detail Barcode reuses Common Barcode Input');
  // Cents-based entry through the Common money input: 1 -> 0.01, 123 -> 1.23, 12345 -> 123.45.
  const keyIn=(input,data,inputType='insertText')=>input.dispatchEvent(new win.InputEvent('beforeinput',{inputType,data,cancelable:true,bubbles:true}));
  const cents=(input,digits)=>{input.focus();input.select();for(const d of digits)keyIn(input,d);const shown=input.value;input.blur();return [shown,input.value];};
  ['mgDetailCost','mgDetailPrice'].forEach(id=>assert(find(id).hasAttribute('data-awsum-money')&&find(id).type==='text'&&find(id).dataset.moneyMin==='0','MD Detail '+id+' uses Common money input'));
  for(const [digits,expected] of [['1','0.01'],['123','1.23'],['12345','123.45'],['1234567','12,345.67']]){
    const [shown,blurred]=cents(find('mgDetailPrice'),digits);
    assert(blurred===expected&&shown===expected.replace(/,/g,''),'Detail Price cents entry '+digits+' -> '+shown+'/'+blurred);
  }
  const retailEl=find('mgf_retail_price'),retailBefore=retailEl.value;
  assert(cents(retailEl,'12345')[1]==='123.45','Price cents entry');
  retailEl.focus();keyIn(retailEl,null,'deleteContentBackward');retailEl.blur();
  assert(retailEl.value==='12.34','backspace removes the last cent digit');
  win.AwsumMoneyInput.setValue(retailEl,win.AwsumMoneyInput.normalize(retailBefore));
  ['mgDetailCost','mgDetailPrice'].forEach(id=>assert(find(id).closest('.input-group').querySelector('button[data-money-keypad]'),'MD Detail '+id+' has the keypad $ button'));
  find('mgDetailCancel').click();
  // Common Money Keypad: "$" opens it with the current amount; cents-based; C / Backspace / OK; nothing changes until OK.
  const KP=win.AwsumMoneyKeypad,dollar=retailEl.closest('.input-group').querySelector('[data-money-keypad]');
  assert(dollar.tagName==='BUTTON'&&dollar.type==='button'&&dollar.textContent==='$'&&dollar.classList.contains('input-group-text'),'Price $ prefix is the keypad button');
  const priceBefore=retailEl.value;let priceInputs=0;retailEl.addEventListener('input',()=>{priceInputs+=1;});
  dollar.click();
  const pad=KP.element,shown=()=>pad.querySelector('.awsum-money-keypad-value').textContent,press=k=>pad.querySelector(`[data-key="${k}"]`).click();
  assert(KP.isOpen()&&!pad.hidden&&shown()===priceBefore&&priceBefore==='95,000.00'&&dollar.getAttribute('aria-expanded')==='true','keypad loads the current amount');
  const padBox=pad.getBoundingClientRect(),groupBox=retailEl.closest('.input-group').getBoundingClientRect();
  assert(padBox.left>=8&&padBox.right<=win.innerWidth-8&&padBox.top>=8&&padBox.bottom<=win.innerHeight-8,'keypad stays inside the viewport');
  assert(Math.abs(padBox.top-(groupBox.bottom+4))<2||Math.abs(padBox.bottom-(groupBox.top-4))<2,'keypad sits next to the field');
  press('1');assert(shown()==='0.01','first digit starts a new cents-based amount');
  press('2');press('3');assert(shown()==='1.23','123 -> 1.23');
  press('back');assert(shown()==='0.12','Backspace');
  press('clear');assert(shown()==='0.00'&&pad.querySelector('.awsum-money-keypad-value').classList.contains('is-blank'),'C clears everything');
  for(const k of '12345')pad.dispatchEvent(new win.KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true}));
  assert(shown()==='123.45','physical keys work on the keypad');
  press('00');assert(shown()==='12,345.00','00 key');
  assert(retailEl.value===priceBefore&&priceInputs===0,'field unchanged until OK');
  press('ok');
  assert(!KP.isOpen()&&retailEl.value==='12,345.00'&&win.AwsumMoneyInput.getValue(retailEl)==='12345.00'&&priceInputs===1,'OK writes the formatted amount through the Common input');
  assert(doc.activeElement===dollar&&dollar.getAttribute('aria-expanded')==='false','focus returns to $');
  dollar.click();press('9');pad.dispatchEvent(new win.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  assert(!KP.isOpen()&&retailEl.value==='12,345.00','Escape cancels without changes');
  dollar.click();press('clear');press('ok');
  assert(KP.isOpen()&&pad.querySelector('.awsum-money-keypad-error').textContent.includes('Enter a monetary amount')&&retailEl.value==='12,345.00','field validation (required) blocks OK');
  doc.body.dispatchEvent(new win.PointerEvent('pointerdown',{bubbles:true}));
  assert(!KP.isOpen()&&retailEl.value==='12,345.00','outside click cancels');
  const lockedPrice=[1,2,3].map(n=>find(`mgf_p${n}_price`)).find(el=>el.disabled||el.readOnly);
  assert(lockedPrice,'a disabled/read-only level price exists in the mock');
  lockedPrice.closest('.input-group').querySelector('[data-money-keypad]').click();
  assert(!KP.isOpen(),'read-only/disabled amounts do not open the keypad');
  win.AwsumMoneyInput.setValue(retailEl,win.AwsumMoneyInput.normalize(priceBefore));
  // Inventory Type / Keep: Common Option Group (all choices visible), number/code values, server names as labels.
  const inv=find('mgf_inventory_type'),keep=find('mgf_keep'),OG=win.AwsumOptionGroup;
  const radios=group=>[...group.querySelectorAll('input[type="radio"]')];
  const pick=(group,value)=>radios(group).find(r=>r.value===value).closest('label').click();
  assert(general.contains(inv)&&general.contains(keep)&&inv.matches('[data-awsum-option-group][role="radiogroup"]')&&keep.matches('[data-awsum-option-group]'),'Inventory Type and Keep are Common Option Groups in GENERAL');
  assert(!general.querySelector('select[name="inventory_type"],select[name="keep"],select[name="inventory_option"]'),'no dropdowns for Inventory Type / Keep');
  // MD values are overrides; no "Category III (...)" choice. Common Reset returns to NULL = inherit.
  assert(radios(inv).map(r=>r.value+'='+r.closest('label').textContent).join('|')==='1=None|2=Stock|3=Recipe|4=Consume on Receipt|5=Consume on Issue','Inventory Type: numbers 1-5 with names only');
  assert(radios(keep).map(r=>r.value+'='+r.closest('label').textContent).join('|')==='DRY=Dry|COLD=Cold|FROZEN=Frozen','Keep: codes with names only');
  assert(!find('mgFields').textContent.includes('Category III (')&&inv.querySelector('[data-option-clear]').textContent==='Reset'&&keep.querySelector('[data-option-clear]').textContent==='Reset','inherit buttons removed; Common Reset kept');
  assert(OG.getValue(inv)==='2'&&OG.getValue(keep)==='COLD','saved overrides load into the groups');
  const body=(group,value)=>win.getComputedStyle(radios(group).find(r=>r.value===value).nextElementSibling);
  assert(body(inv,'2').borderColor==='rgb(18, 99, 235)'&&body(inv,'2').backgroundColor==='rgb(231, 240, 254)'&&body(inv,'1').borderColor!=='rgb(18, 99, 235)','selected option is clearly emphasized');
  assert(new Set(radios(inv).map(r=>Math.round(r.closest('label').getBoundingClientRect().top))).size===1,'all Inventory Type choices fit on one compact row');
  assert(!find('mgKeepReq').classList.contains('d-none'),'Stock override makes Keep required');
  const reset=group=>group.querySelector('[data-option-clear]').click();
  const inherited=group=>radios(group).filter(r=>r.closest('label').classList.contains('is-inherited')).map(r=>r.value+':'+r.closest('label').title).join();
  assert(win.getComputedStyle(keep.querySelector('[data-option-clear]')).display!=='none','Reset stays available while Keep is required');
  reset(inv);
  assert(OG.getValue(inv)===''&&find('mgKeepReq').classList.contains('d-none'),'inheriting Recipe from Category III makes Keep optional');
  assert(inherited(inv)==='3:Inherited from Category III'&&win.getComputedStyle(radios(inv)[2].nextElementSibling).borderStyle==='dashed','inherited Category III value is marked');
  reset(keep);
  assert(OG.getValue(keep)===''&&inherited(keep)==='DRY:Inherited from Category III','Keep can inherit from Category III');
  find('mgf_category3_id').value='';find('mgf_category3_id').dispatchEvent(new win.Event('change'));
  assert(inherited(inv)==='1:System default'&&inherited(keep)==='DRY:System default','without Category III the system default None / Dry applies');
  find('mgf_category3_id').value='3';find('mgf_category3_id').dispatchEvent(new win.Event('change'));
  assert(inherited(inv)==='3:Inherited from Category III','inherited mark restored');
  pick(inv,'2');pick(keep,'FROZEN');
  assert(OG.getValue(inv)==='2'&&OG.getValue(keep)==='FROZEN','clicking an option selects it');
  // Duplicate Barcode: blocked before anything is saved, with the owning Item Code on the Barcode field.
  scan.value='TAKEN';
  const beforeDuplicate=win.requests.length;
  find('mgForm').requestSubmit();
  await wait(()=>!find('mgFormError').classList.contains('d-none'));
  assert(find('mgFormError').textContent.includes('MD00009')&&find('mgf_default_barcode').classList.contains('is-invalid')
    &&find('mgFields').querySelector('[data-error-for="default_barcode"]').textContent.includes('MD00009'),'duplicate Barcode shows the existing Item Code');
  assert(win.requests.slice(beforeDuplicate).every(r=>r.method==='GET')&&win.requests.slice(beforeDuplicate).some(r=>r.path.endsWith('/md/barcode-check')),'duplicate Barcode saves nothing');
  scan.value='NEW-BC';find('mgf_second_name').value='Alt Two';find('mgf_default_pk_size').value='12';find('mgf_default_size').value='500ML';
  find('mgForm').requestSubmit();
  await wait(()=>find('mgf_default_barcode').value==='NEW-BC'&&win.requests.some(request=>request.method==='PUT'&&request.path.endsWith('/details/1')));
  const mdSave=win.requests.find(request=>request.method==='PUT'&&request.path.endsWith('/md/7/with-image'));
  assert(mdSave&&!('barcode' in mdSave.payload)&&!('default_barcode' in mdSave.payload)&&!('pk_size' in mdSave.payload)&&!('size' in mdSave.payload)&&mdSave.payload.item_kind==='GIFT_CARD'&&mdSave.payload.second_name==='Alt Two','MD payload saves Second Name');
  assert(mdSave.payload.inventory_type==='2'&&mdSave.payload.keep==='FROZEN'&&!('inventory_option' in mdSave.payload),'MD payload saves Inventory Type number / Keep code');
  const barcodeSync=win.requests.find(request=>request.method==='PUT'&&request.path.endsWith('/details/1'));
  assert(barcodeSync.payload.barcode==='NEW-BC'&&barcodeSync.payload.pk_size==='12'&&barcodeSync.payload.size==='500ML'&&barcodeSync.payload.is_default===true&&barcodeSync.payload.unit==='EA'&&barcodeSync.payload.price==='95000.00','Barcode/PK/Size/Size sync to Default Detail via existing API');
  await wait(()=>find('mgDetailBody').textContent.includes('NEW-BC'));
  find('mgMdToggle').click();
  await wait(()=>find('mgMdToggle').textContent==='Activate');
  assert(win.requests.some(request=>request.method==='PUT'&&request.path.endsWith('/md/7')&&request.payload.active===false),'Deactivate reuses existing row action');
  assert(win.requests.some(request=>request.method==='PUT'&&request.path.endsWith('/md/7')&&request.payload.inventory_type===2&&request.payload.keep==='COLD'),'Deactivate resends the row\'s saved Inventory Type / Keep');
  assert(find('mgf_active').value==='false','form status follows Deactivate');
  find('mgMdDelete').click();
  await wait(()=>find('mgMdDelete').classList.contains('d-none'));
  assert(win.requests.some(request=>request.method==='DELETE'&&request.path.endsWith('/md/7')),'Delete reuses existing row action');
  assert(!find('mgFields').children.length&&find('mgSave').disabled,'form resets after delete');
  find('mgAddBtn').click();
  await wait(()=>find('mgf_item_kind'));
  assert(find('mgf_item_kind').value==='ITEM','new MD defaults to ITEM');
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0,detailActions:['GET','POST','PUT','DELETE']});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_tax_categories_and_item_kind_use_master_detail_tree_and_form(self):
        js = {name: (browser.ROOT / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")
              for name in ("money", "option-group", "master-detail", "master_group")}
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8") + "\n" + "\n".join(
            (browser.ROOT / "static" / "css" / name).read_text(encoding="utf-8")
            for name in ("brand.css", "awsum-list.css", "awsum-master-detail.css", "awsum-option-group.css"))
        mock = r"""
window.requests=[];
window.bootstrap={Modal:class{show(){}hide(){}}};
window.confirm=()=>true;
const entity=document.getElementById('mgPage').dataset.entity;
const rows={
  tax:[{id:1,code:'TAX00001',name:'State',rate:'7.0000',active:true,notes:''},
       {id:2,code:'TAX00002',name:'City',rate:'1.5000',active:true,notes:''}],
  item_kind:[{id:3,code:'HARDWARE',name:'Hardware',display_order:1,active:true,system:true,notes:''},
             {id:4,code:'SERVICE',name:'Service',display_order:2,active:true,system:true,notes:''}],
  category1:[{id:1,code:'C100001',name:'Food',display_order:1,active:true,notes:''},
             {id:2,code:'C100002',name:'Hardware',display_order:2,active:true,notes:''}],
  category2:[{id:7,code:'C200007',name:'Drinks',category1_id:1,category1_name:'Food',display_order:1,active:true,notes:''},
             {id:8,code:'C200008',name:'Tools',category1_id:2,category1_name:'Hardware',display_order:2,active:true,notes:''}],
  category3:[{id:5,code:'C300005',name:'Soda',category1_id:1,category1_name:'Food',category2_id:7,category2_name:'Drinks',
              tax1_id:1,tax2_id:null,tax3_id:null,inventory_type:2,keep:'COLD',display_order:1,active:true,notes:''}],
}[entity];
window.fetch=async(path,options={})=>{
  const url=new URL(path,'http://127.0.0.1'),method=options.method||'GET';
  const payload=options.body?JSON.parse(options.body):null;
  requests.push({path:url.pathname,method,payload});
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({
    category1:[{id:1,name:'Food',active:true},{id:2,name:'Hardware',active:true}],
    category2:[{id:7,name:'Drinks',category1_id:1,active:true},{id:8,name:'Tools',category1_id:2,active:true}],
    category3:[],taxes:[{id:1,name:'State',rate:'7.0000',active:true}],
    inventory_types:[{type_id:1,name:'None',keep_required:false},{type_id:2,name:'Stock',keep_required:true}],
    default_inventory_type:1,default_keep:'DRY',keep_types:[{code:'DRY',name:'Dry'},{code:'COLD',name:'Cold'}]}));
  if(method==='DELETE')return new Response(JSON.stringify({message:'Deleted.'}));
  if(method==='PUT'||method==='POST'){
    const item={...(rows.find(r=>url.pathname.endsWith('/'+r.id))||{id:99,code:'NEW00099'}),...payload};
    return new Response(JSON.stringify({item}));
  }
  return new Response(JSON.stringify({items:rows,total:rows.length,page:1,pages:1,sort:'name',direction:'asc'}));
};
"""
        pages = []
        for entity, title in (("tax", "Tax"), ("category1", "Category I"), ("category2", "Category II"),
                              ("category3", "Category III"), ("item_kind", "Item Kind")):
            html = render_list(entity, title)
            html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
            html = re.sub(r"<link\b[^>]*>", "", html)
            html = html.replace("</head>", f"<style>{css}</style></head>")
            html = html.replace("</body>", f"<script>{mock}\n{js['money']}\n{js['option-group']}\n{js['master-detail']}\n{js['master_group']}</script></body>")
            pages.append(html)
        serialized = json.dumps(pages).replace("</", "<\\/")
        script = f"const pages={serialized};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
const wait=async(check,label)=>{for(let i=0;i<100;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  for(const html of pages){
    const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1280px;height:800px';frame.srcdoc=html;
    const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
    document.body.appendChild(frame);await loaded;
    const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),f=name=>find('mgf_'+name);
    // Same layout and flow for every screen; only the tree levels and form fields differ.
    const SPEC={
      tax:{label:'Tax',item:'State',id:1,code:'TAX00001',header:'Tax Name',flat:true,
        fields:()=>f('rate').value==='7.0000'&&!f('display_order')&&!f('category1_id'),payload:p=>p.rate==='7.0000'},
      category1:{label:'Category I',item:'Food',id:1,code:'C100001',header:'Category / Name',flat:true,
        fields:()=>!f('category1_id')&&f('display_order').value==='1'&&f('active').value==='true',payload:p=>p.display_order==='1'},
      category2:{label:'Category II',item:'Drinks',id:7,code:'C200007',header:'Category / Name',groups:'Food1|Hardware1',
        fields:()=>f('category1_id').value==='1',payload:p=>p.category1_id==='1'&&p.display_order==='1',
        create:()=>{f('category1_id').value='1';f('category1_id').dispatchEvent(new win.Event('change'));}},
      category3:{label:'Category III',item:'Soda',id:5,code:'C300005',header:'Category / Name',groups:'Food1|Drinks1',
        fields:()=>f('category1_id').value==='1'&&f('category2_id').value==='7'&&f('tax1_id').value==='1'
          &&win.AwsumOptionGroup.getValue(f('inventory_type'))==='2'&&win.AwsumOptionGroup.getValue(f('keep'))==='COLD',
        payload:p=>p.category1_id==='1'&&p.tax1_id==='1'&&p.inventory_type==='2'&&p.display_order==='1',
        create:()=>{f('category1_id').value='1';f('category1_id').dispatchEvent(new win.Event('change'));f('category2_id').value='7';}},
      // Item Kind keeps its existing rules: no Add, System Codes cannot be deleted.
      item_kind:{label:'Item Kind',item:'Hardware',id:3,code:'HARDWARE',header:'Display Name',flat:true,system:true,noAdd:true,
        fields:()=>f('display_order').value==='1'&&find('mgFields').textContent.includes('System Code'),payload:p=>p.display_order==='1'},
    };
    const entity=find('mgPage').dataset.entity,spec=SPEC[entity],item=spec.item,itemId=spec.id;
    await wait(()=>find('mgBody').textContent.includes(item),'list');
    // Layout: the Common Master-Detail workspace, no modal, no row buttons.
    assert(doc.querySelector('.mg-md-workspace .mg-md-list-panel')&&doc.querySelector('.mg-md-workspace .mg-md-form-panel')&&!find('mgModal'),entity+' uses the Master-Detail workspace');
    const listBox=doc.querySelector('.mg-md-list-panel').getBoundingClientRect(),formBox=doc.querySelector('.mg-md-form-panel').getBoundingClientRect();
    assert(listBox.right<=formBox.left&&Math.abs(listBox.top-formBox.top)<2,'list on the left, form on the right');
    window.__panel=window.__panel||{width:Math.round(listBox.width),top:Math.round(listBox.top),formLeft:Math.round(formBox.left)};
    assert(Math.round(listBox.width)===window.__panel.width&&Math.round(listBox.top)===window.__panel.top&&Math.round(formBox.left)===window.__panel.formLeft,'same panel size and position on every screen: '+entity);
    const list=doc.querySelector('.mg-md-list-panel');
    assert(list.contains(find('mgSearch'))&&list.contains(find('mgActiveFilter'))&&list.contains(find('mgPageSize'))&&list.contains(find('mgNext')),'Search / Status / list / Pagination on the left');
    assert(doc.querySelector('.mg-md-form-title').textContent.trim()===spec.label.toUpperCase()+' INFORMATION','form header title');
    assert(find('mgSave').disabled&&find('mgMdToggle').classList.contains('d-none')&&find('mgMdDelete').classList.contains('d-none'),'actions wait for a selection');
    assert(!find('mgBody').querySelector('[data-act]')&&find('mgHead').textContent.trim()===spec.header,'single Name column: '+find('mgHead').textContent);
    const groups=()=>[...find('mgBody').querySelectorAll('tr[data-tree-toggle]')];
    const groupText=()=>groups().map(tr=>tr.textContent.replace(/[\s−+]/g,'')).join('|');
    if(spec.flat){
      // No parent: a flat list in the same tree component (no group rows).
      assert(groups().length===0&&find('mgBody').querySelectorAll('tr.mg-md-row').length===2,entity+' shows only its own list');
      assert(find('mgBody').querySelector('tr.mg-md-row').style.getPropertyValue('--mg-depth')==='0','list rows sit at the top level');
    }else{
      assert(groupText()===spec.groups,'tree groups (name + count): '+groupText());
      assert(groups().every(tr=>tr.getAttribute('aria-expanded')==='true'),'groups start open');
      groups()[0].click();await tick();
      assert(groups()[0].getAttribute('aria-expanded')==='false'&&!find('mgBody').textContent.includes(item),'collapse hides the group');
      groups()[0].click();await tick();
      assert(find('mgBody').textContent.includes(item),'expand shows it again');
    }
    // Select -> details show immediately on the right.
    find('mgBody').querySelector(`tr[data-row-select="${itemId}"]`).click();
    await wait(()=>f('name')&&f('name').value===item,'form');
    assert(find('mgBody').querySelector(`tr[data-row-select="${itemId}"]`).classList.contains('selected'),'selected row highlighted');
    assert(find('mgFields').textContent.includes(spec.code)&&spec.fields(),entity+' fields loaded');
    assert(!find('mgSave').disabled&&find('mgSave').textContent==='Save Changes'&&find('mgMdToggle').textContent==='Deactivate','Save Changes / Deactivate for the selected record');
    assert(find('mgMdDelete').classList.contains('d-none')===!!spec.system,'Delete shown unless the record is a System Code');
    f('name').value=item+' 2';
    find('mgForm').requestSubmit();
    await wait(()=>win.requests.some(r=>r.method==='PUT'),'save');
    const put=win.requests.find(r=>r.method==='PUT');
    assert(put.path.endsWith(`/${entity}/${itemId}`)&&put.payload.name===item+' 2'&&spec.payload(put.payload),'Save Changes uses the existing update API');
    await wait(()=>find('mgMessage').textContent.includes('saved'),'saved message');
    assert(find('mgSave').textContent==='Save Changes'&&f('name').value===item+' 2','form stays on the saved record');
    // Deactivate from the form header (existing toggle payload; unsent keys such as Tax / Inventory are kept).
    const before=win.requests.length;
    find('mgMdToggle').click();
    await wait(()=>win.requests.slice(before).some(r=>r.method==='PUT'),'toggle');
    const toggle=win.requests.slice(before).find(r=>r.method==='PUT');
    assert(toggle.payload.active===false&&!('tax1_id' in toggle.payload),'Deactivate uses the existing toggle payload');
    await wait(()=>find('mgMdToggle').textContent==='Activate','toggle label');
    if(!spec.system){
      find('mgMdDelete').click();
      await wait(()=>win.requests.some(r=>r.method==='DELETE'&&r.path.endsWith(`/${entity}/${itemId}`)),'delete');
      await wait(()=>find('mgSave').disabled&&!f('name'),'form reset after delete');
    }
    if(spec.noAdd){
      assert(find('mgAddBtn').classList.contains('d-none'),'Add stays hidden for '+entity);
    }else{
      // Add -> empty create form on the right.
      find('mgAddBtn').click();await tick();
      assert(f('name').value===''&&find('mgSave').textContent==='Create'&&find('mgMdToggle').classList.contains('d-none'),'Add shows an empty create form');
      if(spec.create)spec.create();
      if(entity==='tax')f('rate').value='2.5';
      f('name').value='New';find('mgForm').requestSubmit();
      await wait(()=>win.requests.some(r=>r.method==='POST'),'create');
      const post=win.requests.find(r=>r.method==='POST');
      assert(post.path.endsWith(`/${entity}`)&&post.payload.name==='New','Create uses the existing create API');
      await wait(()=>find('mgSave').textContent==='Save Changes','created record stays selected');
    }
    frame.remove();
  }
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
