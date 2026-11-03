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
                self.assertIn('src="/static/js/master_group.js?v=29"', html)
                if entity == "md":
                    self.assertIn('class="mg-md-workspace"', html)
                    self.assertIn('class="mg-md-form"', html)
                    self.assertNotIn('id="mgModal"', html)
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
            for entity, title in PAGES[:4]:
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
            for name in ("brand.css", "awsum-list.css"))
        client = (browser.ROOT / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
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
            html = html.replace("</body>", f"<script>{mock}\n{money}\n{client}</script></body>")
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
    const md= find('mgPage').dataset.entity==='md';
    assert(find('mgBody').querySelectorAll('[data-act]').length===(md?0:3),'row actions are correct');
    assert(win.getComputedStyle(doc.querySelector('.awsum-table-card')).borderRadius==='12px','common card styling');
    const statusBadge=doc.querySelector('.text-bg-success');
    if(find('mgPage').dataset.entity==='md'){
      assert(!statusBadge,'MD list does not render columns outside Category and Name');
      assert(doc.querySelector('.mg-md-workspace .mg-md-list-panel')&&doc.querySelector('.mg-md-workspace .mg-md-form-panel'),'MD uses the split list and form layout');
      assert(!find('mgBody').querySelector('[data-act="edit"]'),'MD Edit action is replaced by row selection');
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
        money = (browser.ROOT / "static" / "js" / "money.js").read_text(encoding="utf-8")
        barcode = (browser.ROOT / "static" / "js" / "barcode-input.js").read_text(encoding="utf-8")
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8") + "\n" + "\n".join(
            (browser.ROOT / "static" / "css" / name).read_text(encoding="utf-8")
            for name in ("brand.css", "awsum-list.css"))
        html = render_list("md", "MD Master")
        html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
        html = re.sub(r"<link\b[^>]*>", "", html)
        html = html.replace("</head>", f"<style>{css}</style></head>")
        mock = r"""
window.requests=[];
window.details=[
  {id:1,detail_no:1,is_default:true,barcode:'BASE-1',unit:'EA',pk_size:'',size:'',cost:null,price:'3.25',modifier:''},
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
    category3:[{id:3,name:'Soft Drink',code:'SOFT',category2_id:2,active:true}],
    taxes:[],item_kinds:[{code:'ITEM',name:'Item'},{code:'SERVICE',name:'Service'},{code:'GIFT_CARD',name:'Gift Card'}],default_item_kind:'ITEM',billing_cycles:['ONE_TIME','MONTHLY','ANNUAL'],
    license_types:['POS','KIOSK','CUSTOMER_ORDERING']
  }));
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
    description:'',second_name:'Alt One',item_kind:'SERVICE',unit:'EA',retail_price:'3.25',cost:null,
    p1_mode:null,p2_mode:null,p3_mode:null,default_tax_id:null,billing_cycle:'ONE_TIME',
    license_required:false,license_type:null,licenses_per_unit:null,serial_required:false
  }],total:1,page:1,pages:1,sort:'name',direction:'asc'}));
  if(/\/md\/7(\/with-image)?$/.test(url.pathname)&&method==='PUT')return new Response(JSON.stringify({item:{...request.payload,id:7,code:'MD00007'}}));
  if(/\/md\/7$/.test(url.pathname)&&method==='DELETE')return new Response(JSON.stringify({message:'Deleted.'}));
  throw Error('Unexpected request: '+method+' '+url.pathname);
};
"""
        html = html.replace("</body>", f"<script>{mock}\n{money}\n{barcode}\n{client}</script></body>")
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
  assert([...find('mgHead').querySelectorAll('th')].map(th=>th.textContent.trim()).join('|')==='Category|Name','MD list has Category and Name only');
  const listPanel=doc.querySelector('.mg-md-list-panel');
  assert(!doc.querySelector('.awsum-filter-card'),'MD has no separate filter card');
  assert(listPanel.querySelector('.mg-md-filter').contains(find('mgSearch'))&&listPanel.querySelector('.mg-md-filter').contains(find('mgActiveFilter')),'Search/Status sit above the left grid');
  assert(listPanel.querySelector('.mg-md-pager').contains(find('mgPageSize')),'Rows per page sits in the pagination footer');
  assert(find('mgMdToggle').classList.contains('d-none')&&find('mgMdDelete').classList.contains('d-none'),'form actions hidden until a record is selected');
  assert(doc.querySelector('.mg-md-workspace .mg-md-list-panel')&&doc.querySelector('.mg-md-workspace .mg-md-form-panel'),'MD uses split workspace');
  assert(!find('mgBody').querySelector('[data-act="edit"]'),'MD row has no Edit button');
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>find('mgDetailBody').textContent.includes('BASE-1'));
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
  find('mgDetailUnit').value='PK';find('mgDetailPrice').value='18.50';
  find('mgDetailSave').click();
  await wait(()=>find('mgDetailBody').textContent.includes('PK'));
  assert(win.requests.some(request=>request.method==='POST'&&request.path.endsWith('/md/7/details')
    &&request.payload.unit==='PK'&&request.payload.price==='18.50'),'Add uses existing MD Detail API');
  find('mgDetailBody').querySelector('[data-detail-act="edit"][data-id="3"]').click();
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
  find('mgDetailCancel').click();
  scan.value='NEW-BC';find('mgf_second_name').value='Alt Two';find('mgf_default_pk_size').value='12';find('mgf_default_size').value='500ML';
  find('mgForm').requestSubmit();
  await wait(()=>find('mgf_default_barcode').value==='NEW-BC'&&win.requests.some(request=>request.method==='PUT'&&request.path.endsWith('/details/1')));
  const mdSave=win.requests.find(request=>request.method==='PUT'&&request.path.endsWith('/md/7/with-image'));
  assert(mdSave&&!('barcode' in mdSave.payload)&&!('default_barcode' in mdSave.payload)&&!('pk_size' in mdSave.payload)&&!('size' in mdSave.payload)&&mdSave.payload.item_kind==='GIFT_CARD'&&mdSave.payload.second_name==='Alt Two','MD payload saves Second Name');
  const barcodeSync=win.requests.find(request=>request.method==='PUT'&&request.path.endsWith('/details/1'));
  assert(barcodeSync.payload.barcode==='NEW-BC'&&barcodeSync.payload.pk_size==='12'&&barcodeSync.payload.size==='500ML'&&barcodeSync.payload.is_default===true&&barcodeSync.payload.unit==='EA'&&barcodeSync.payload.price==='3.25','Barcode/PK/Size/Size sync to Default Detail via existing API');
  await wait(()=>find('mgDetailBody').textContent.includes('NEW-BC'));
  find('mgMdToggle').click();
  await wait(()=>find('mgMdToggle').textContent==='Activate');
  assert(win.requests.some(request=>request.method==='PUT'&&request.path.endsWith('/md/7')&&request.payload.active===false),'Deactivate reuses existing row action');
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


if __name__ == "__main__":
    unittest.main()
