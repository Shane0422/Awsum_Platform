"""Focused rendering and list-interaction checks without live API or DB access."""
import json
import re
import unittest

from tools import test_image_components as browser
from tools.test_platform_navigation import render_navigation


def toast_head():
    # Common Toast (platform_base.html) + Common Page Actions (master_group.html); the tests inline page assets.
    js = ";\n".join((browser.ROOT / "static" / "js" / name).read_text(encoding="utf-8") for name in ("awsum-toast.js", "page-actions.js", "code-input.js"))
    css = (browser.ROOT / "static" / "css" / "awsum-toast.css").read_text(encoding="utf-8")
    return f"<style>{css}</style><script>{js}</script></head>"



PAGES = [
    ("tax", "Tax"), ("category1", "Category I"), ("category2", "Category II"),
    ("category3", "Category III"), ("md", "MD Master"),
]
SPLIT = {"tax", "category1", "category2", "category3", "md", "item_kind"}  # Master-Detail layout (left Tree + right form)


def render_list(entity, title):
    base, context, _ = render_navigation()
    from backend.routers.master_group import RECORD_TITLES
    context.update(mg_entity=entity, mg_title=title, mg_record_title=RECORD_TITLES.get(entity, title), active_page="master-group")
    return base.environment.get_template("platform/master_group.html").render(context)


class MasterGroupListTemplateTests(unittest.TestCase):
    def test_location_master_add_button_uses_record_title(self):
        html = render_list("location", "Location Master")
        self.assertIn('id="mgAddBtn">Add Location</button>', html)
        self.assertNotIn("Add Location Master", html)
        self.assertIn('data-title="Location"', html)
        self.assertIn("LOCATION MASTER INFORMATION", html)  # page heading keeps the page title
        self.assertIn('id="mgAddBtn">Add Tax</button>', render_list("tax", "Tax"))  # other pages unchanged
        # Location only: Cancel in the Page Header and 'Save' for a new record; other screens keep 'Create', no Cancel.
        self.assertIn('id="mgCancelBtn">Cancel</button>', html)
        self.assertIn('form="mgForm" disabled>Save</button>', html)
        for entity, title in (("tax", "Tax"), ("md", "MD Master"), ("common_code", "Common Maintenance")):
            other = render_list(entity, title)
            self.assertNotIn("mgCancelBtn", other)
            self.assertIn('form="mgForm" disabled>Create</button>', other)

    def test_all_lists_share_styles_and_keep_existing_controls(self):
        for entity, title in PAGES:
            with self.subTest(entity=entity):
                html = render_list(entity, title)
                self.assertEqual(html.count('href="/static/css/awsum-list.css?v=8"'), 1)
                self.assertIn("mg-page awsum-list", html)
                self.assertIn(f'data-entity="{entity}"', html)
                self.assertIn(f"Add {title}</button>", html)
                self.assertIn('src="/static/js/master_group.js?v=95"', html)
                if entity in SPLIT:
                    self.assertIn('class="mg-md-workspace"', html)
                    self.assertIn('class="mg-md-form"', html)
                    self.assertNotIn('id="mgModal"', html)
                    self.assertNotIn("awsum-filter-card", html)
                    self.assertEqual(html.count('href="/static/css/awsum-master-detail.css?v=2"'), 1)
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
                html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", toast_head(), 1)
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
    const filter=doc.querySelector('.awsum-filter-card');
    const table=doc.querySelector('.awsum-table-card');
    assert(!doc.getElementById('mgMessage'),'no in-page success banner (Common Toast instead)');
    const filterTop=filter.getBoundingClientRect().top;
    win.AwsumToast.error('Sample error.');
    const region=doc.getElementById('awsumToastRegion');
    assert(win.getComputedStyle(region).position==='fixed'&&Math.abs(filter.getBoundingClientRect().top-filterTop)<1,'toast overlays without moving the layout');
    doc.getElementById('mgSubtitle').textContent='Common description';
    doc.getElementById('mgBody').innerHTML='<tr><td style="height:1800px">Scrollable records</td></tr>';
    for(const width of [1440,768,375,320]){
      frame.style.width=width+'px';frame.style.height='700px';win.scrollTo(0,0);await tick();
      assert(Math.abs(parseFloat(win.getComputedStyle(header).top)-nav.getBoundingClientRect().height)<1,'actual responsive navigation height');
      for(const node of [filter,table]){
        assert(!['sticky','fixed'].includes(win.getComputedStyle(node).position),'only page header is sticky');
      }
      assert(header.contains(doc.querySelector('.awsum-list-icon'))&&header.contains(doc.getElementById('mgSubtitle'))
        &&header.contains(doc.getElementById('mgExportBtn'))&&header.contains(doc.getElementById('mgAddBtn')),'complete header sticks together');
      win.scrollTo(0,500);await tick();
      assert(Math.abs(nav.getBoundingClientRect().top)<1,'navigation stays at top');
      assert(Math.abs(header.getBoundingClientRect().top-nav.getBoundingClientRect().bottom)<1,'no gap above sticky page header');
      assert(region.getBoundingClientRect().top>=0&&region.getBoundingClientRect().top<40,'toast stays at the top right while scrolling');
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
            html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", toast_head(), 1)
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

    def md_page_html(self, item_features="", md_extra=""):
        # MD Master page with a mocked API; item_features is JS text added to the /options response,
        # md_extra is JS text appended to the sample MD row (later keys override earlier ones).
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
        html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", toast_head(), 1)
        html = html.replace("</head>", f"<style>{css}</style></head>")
        mock = (r"""
window.requests=[];
window.details=[
  {id:1,detail_no:1,is_default:true,barcode:'BASE-1',unit:'EA',pk_size:'',size:'',cost:null,price:'95000.00',modifier:'',color_code_id:11},
  {id:2,detail_no:2,is_default:false,barcode:'BASE-2',unit:'CS',pk_size:'12',size:'12 oz',cost:'1.00',price:'12.00',modifier:'Case',color_code_id:13}
];
window.locationCodes={
  SALES_FLOOR:[{code:'A03-B02-S04',name:'Front aisle',stores:2,active_stores:2},{code:'S01-B01-C01',name:'S01-B01-C01',stores:3,active_stores:3},
    {code:'S01-B02-C01',name:'Old shelf',stores:1,active_stores:0}],
  WAREHOUSE:[{code:'W01-R02-C01',name:'Rack 2',stores:2,active_stores:1}]};
window.confirm=()=>true;
window.AwsumImages={createUploader:()=>({reset(){},destroy(){},setDisabled(){},formData:value=>value})};
window.fetch=async(path,options={})=>{
  const url=new URL(path,'http://127.0.0.1'),method=options.method||'GET';
  const request={path:url.pathname,method,payload:options.body?JSON.parse(options.body):null,query:url.search};requests.push(request);
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({
    can_view_cost:true,__ITEM_FEATURES__
    category1:[{id:1,name:'Food',code:'FOOD',active:true}],
    category2:[{id:2,name:'Beverage',code:'BEV',category1_id:1,active:true}],
    category3:[{id:3,name:'Soft Drink',code:'SOFT',category2_id:2,active:true,inventory_type:3,keep:'DRY'}],
    // MD Color = COLOR common codes (MD Detail color_code_id).
    color_codes:[{id:11,code:'CLR00001',name:'Black',swatch_hex:'#000000',active:true},{id:12,code:'CLR00002',name:'Navy',swatch_hex:null,active:true},
      {id:13,code:'CLR00003',name:'Old Red',swatch_hex:'#AA0000',active:false}],
    units:[{code:'EA',name:'Each',active:true},{code:'PK',name:'Pack',active:true},{code:'CS',name:'Case',active:true},{code:'BX',name:'Old Box',active:false}],
    taxes:[],item_kinds:[{code:'ITEM',name:'Item'},{code:'SERVICE',name:'Service'},{code:'GIFT_CARD',name:'Gift Card'}],default_item_kind:'ITEM',billing_cycles:['ONE_TIME','MONTHLY','ANNUAL'],
    license_types:['POS','KIOSK','CUSTOMER_ORDERING'],
    inventory_types:[
      {type_id:1,code:'NONE',name:'None',keep_required:false},{type_id:2,code:'STOCK',name:'Stock',keep_required:true},
      {type_id:3,code:'RECIPE',name:'Recipe',keep_required:false},{type_id:4,code:'EXPENSE_ON_RECEIPT',name:'Consume on Receipt',keep_required:false},
      {type_id:5,code:'EXPENSE_ON_ISSUE',name:'Consume on Issue',keep_required:false}],
    default_inventory_type:1,default_keep:'DRY',
    keep_types:[{code:'DRY',name:'Dry'},{code:'COLD',name:'Cold'},{code:'FROZEN',name:'Frozen'}],
    location_areas:[{code:'SALES_FLOOR',name:'Sales Floor'},{code:'WAREHOUSE',name:'Warehouse'}],
  }));
  // Common Location Selector (/location/codes): each code once with its Store counts; Area / Status / search / paging.
  if(url.pathname.endsWith('/location/codes')){
    const p=url.searchParams,q=(p.get('q')||'').toUpperCase(),st=p.get('active');
    const all=((window.locationCodes||{})[p.get('location_area')]||[]).filter(i=>(!q||i.code.includes(q)||i.name.toUpperCase().includes(q))
      &&(st==='active'?i.active_stores>0:st==='inactive'?i.active_stores===0:true)).map(i=>({...i,active:i.active_stores>0}));
    const size=Number(p.get('size')||20),pages=Math.max(Math.ceil(all.length/size),1),page=Math.min(Number(p.get('page')||1),pages);
    return new Response(JSON.stringify({items:all.slice((page-1)*size,page*size),total:all.length,page,pages,store_total:3,location_area:p.get('location_area')}));
  }
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
  // Store Shelf Location links (window.mdLocByStore[storeId] = { available, details }).
  if(/\/md\/7\/locations$/.test(url.pathname)){
    const sid=url.searchParams.get('store_id'),data=(window.mdLocByStore||{})[sid]||{available:[],details:{}};
    return new Response(JSON.stringify({store_id:Number(sid),...data}));
  }
  const locMatch=/\/details\/(\d+)\/locations$/.exec(url.pathname);
  if(locMatch&&method==='PUT'){
    const data=window.mdLocByStore[url.searchParams.get('store_id')],det=data.details[locMatch[1]];
    Object.entries(request.payload.areas).forEach(([area,spec])=>{
      det.links=det.links.filter(l=>l.location_area!==area).concat(spec.location_ids.map(id=>({...data.available.find(a=>a.id===id),primary:id===spec.primary_location_id})));
      const own=det.links.filter(l=>l.location_area===area);
      det.effective[area]=own.length?{source:'item',locations:own}:{source:'none',locations:[]};
    });
    return new Response(JSON.stringify({item:det,message:'Saved.'}));
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
    description:'Saved desc',second_name:'Alt One',item_kind:'SERVICE',unit:'EA',retail_price:'95000.00',cost:null,
    p1_mode:null,p2_mode:null,p3_mode:null,default_tax_id:null,billing_cycle:'ONE_TIME',
    inventory_type:2,keep:'COLD',output_devices:[2,5],
    license_required:false,license_type:null,licenses_per_unit:null,serial_required:false__MD_EXTRA__
  }],total:1,page:1,pages:1,sort:'name',direction:'asc'}));
  if(/\/md\/7(\/with-image)?$/.test(url.pathname)&&method==='PUT')return new Response(JSON.stringify({item:{...request.payload,id:7,code:'MD00007'}}));
  if(/\/md\/7$/.test(url.pathname)&&method==='DELETE')return new Response(JSON.stringify({message:'Deleted.'}));
  throw Error('Unexpected request: '+method+' '+url.pathname);
};
""").replace("__ITEM_FEATURES__", item_features).replace("__MD_EXTRA__", md_extra)
        html = html.replace("</body>", f"<script>{mock}\n{money}\n{keypad}\n{tree}\n{barcode}\n{option_group}\n{client}</script></body>")
        return html

    def test_md_split_form_and_detail_crud_use_existing_api(self):
        html = self.md_page_html()
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
  assert(tabs.join('|')==='GENERAL|PRICE LEVEL|LICENSE','GENERAL, PRICE LEVEL and LICENSE tabs exist');
  const activeTab=win.getComputedStyle(find('mgMdTabGeneral'));
  assert(activeTab.backgroundColor==='rgb(255, 255, 255)'&&activeTab.color==='rgb(16, 46, 104)'&&activeTab.boxShadow.includes('rgb(18, 99, 235)'),'selected tab uses common navy/blue accent');
  assert(win.getComputedStyle(doc.querySelector('.mg-md-form-panel')).backgroundColor==='rgb(255, 255, 255)','form card is white');
  const req=find('mgFields').querySelector('.pm-req'),reqLabel=req.closest('.form-label');
  assert(win.getComputedStyle(req).color==='rgb(220, 38, 38)'&&win.getComputedStyle(reqLabel).color==='rgb(73, 100, 134)'&&win.getComputedStyle(req).fontSize===win.getComputedStyle(reqLabel).fontSize,'only the required * is red');
  const header=doc.querySelector('.mg-md-form-header'),fields=find('mgFields');
  assert(header.querySelector('.mg-md-form-title').textContent.trim()==='MD INFORMATION'&&header.getBoundingClientRect().bottom<=find('mgf_record_code').getBoundingClientRect().top,'MD INFORMATION header sits above Code');
  assert(header.getBoundingClientRect().height<=40,'form header is thin');
  const pageHeader=doc.querySelector('.mg-page > .awsum-page-header');
  assert(['mgMdToggle','mgMdDelete','mgSave'].every(id=>find(id).closest('.awsum-page-header')===pageHeader)&&!header.querySelector('button'),'Deactivate/Delete/Save live in the Page Header (none left in the form header)');
  assert(find('mgSave').getAttribute('form')==='mgForm'&&Math.abs(pageHeader.getBoundingClientRect().right-find('mgSave').getBoundingClientRect().right)<24,'Save submits the form and sits at the right of the Page Header');
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
  assert(find('mgf_notes').type==='hidden'&&!doc.querySelector('label[for="mgf_notes"]'),'MD Notes is hidden (value kept, no label)');
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
  const order=['record_code','category1_id','category2_id','category3_id','category_tax_display','name','second_name','default_barcode','unit','default_pk_size','default_size','retail_price'].map(name=>find('mgf_'+name));
  assert(order.every((el,i)=>i===0||(order[i-1].compareDocumentPosition(el)&win.Node.DOCUMENT_POSITION_FOLLOWING)),'basic info order: Code, Category/Tax, Item Name/Second Name, Barcode row');
  // GENERAL tab order: MD Detail -> Options -> Description (full width).
  const descEl=find('mgf_description'),descWrap=descEl.closest('[data-field-wrap]');
  const generalOrder=[find('mgDetailSection'),find('mgf_output_devices'),descEl];
  assert(general.contains(descEl)&&!doc.querySelector('.mg-md-top').contains(descEl)&&generalOrder.every((el,i)=>i===0||(generalOrder[i-1].compareDocumentPosition(el)&win.Node.DOCUMENT_POSITION_FOLLOWING)),'Description is in GENERAL after MD Detail and Options');
  assert(descWrap.classList.contains('col-12')&&Math.abs(descWrap.getBoundingClientRect().width-descWrap.parentElement.getBoundingClientRect().width)<2,'Description is full width');
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
  // Output Device: same Common Option Group in multiple mode (#1-#8 checkboxes); none selected = not output.
  const out=find('mgf_output_devices'),boxes=[...out.querySelectorAll('input.awsum-option-input')];
  assert(general.contains(out)&&out.matches('[data-awsum-option-group][data-option-multiple][role="group"]')&&out.classList.contains('is-multiple'),'Output Device is a Common Option Group (multiple) in GENERAL Options');
  assert(boxes.length===8&&boxes.every(b=>b.type==='checkbox')&&boxes.map(b=>b.value+'='+b.closest('label').textContent).join('|')==='1=#1|2=#2|3=#3|4=#4|5=#5|6=#6|7=#7|8=#8','Output Device: #1-#8 checkboxes');
  assert(JSON.stringify(OG.getValue(out))==='["2","5"]','saved Output Devices load as checked boxes');
  boxes[6].closest('label').click();
  assert(JSON.stringify(OG.getValue(out))==='["2","5","7"]'&&OG.getValue(inv)==='2'&&OG.getValue(keep)==='FROZEN','multiple selection; radio groups unaffected');
  boxes[1].closest('label').click();
  assert(JSON.stringify(OG.getValue(out))==='["5","7"]','clicking a checked box clears only that box');
  out.querySelector('[data-option-clear]').click();
  assert(JSON.stringify(OG.getValue(out))==='[]'&&!out.classList.contains('has-value'),'Clear selects nothing (not output)');
  boxes[0].closest('label').click();boxes[2].closest('label').click();
  await wait(()=>win.getComputedStyle(boxes[0].nextElementSibling).borderColor==='rgb(18, 99, 235)'); // after the CSS transition
  assert(win.getComputedStyle(boxes[1].nextElementSibling).borderColor!=='rgb(18, 99, 235)','checked Output Device is emphasized');
  // Duplicate Barcode: blocked before anything is saved, with the owning Item Code on the Barcode field.
  scan.value='TAKEN';
  const beforeDuplicate=win.requests.length;
  find('mgForm').requestSubmit();
  await wait(()=>!find('mgFormError').classList.contains('d-none'));
  assert(find('mgFormError').textContent.includes('MD00009')&&find('mgf_default_barcode').classList.contains('is-invalid')
    &&find('mgFields').querySelector('[data-error-for="default_barcode"]').textContent.includes('MD00009'),'duplicate Barcode shows the existing Item Code');
  assert(win.requests.slice(beforeDuplicate).every(r=>r.method==='GET')&&win.requests.slice(beforeDuplicate).some(r=>r.path.endsWith('/md/barcode-check')),'duplicate Barcode saves nothing');
  assert(find('mgf_description').value==='Saved desc','saved Description loads in GENERAL');
  scan.value='NEW-BC';find('mgf_second_name').value='Alt Two';find('mgf_description').value='New desc';find('mgf_default_pk_size').value='12';find('mgf_default_size').value='500ML';
  find('mgForm').requestSubmit();
  await wait(()=>find('mgf_default_barcode').value==='NEW-BC'&&win.requests.some(request=>request.method==='PUT'&&request.path.endsWith('/details/1')));
  const mdSave=win.requests.find(request=>request.method==='PUT'&&request.path.endsWith('/md/7/with-image'));
  assert(mdSave&&!('barcode' in mdSave.payload)&&!('default_barcode' in mdSave.payload)&&!('pk_size' in mdSave.payload)&&!('size' in mdSave.payload)&&mdSave.payload.item_kind==='GIFT_CARD'&&mdSave.payload.second_name==='Alt Two','MD payload saves Second Name');
  assert(mdSave.payload.inventory_type==='2'&&mdSave.payload.keep==='FROZEN'&&!('inventory_option' in mdSave.payload),'MD payload saves Inventory Type number / Keep code');
  assert(JSON.stringify(mdSave.payload.output_devices)==='["1","3"]','MD payload saves Output Devices as a list');
  assert(mdSave.payload.description==='New desc','MD payload saves Description from GENERAL');
  assert(JSON.stringify(OG.getValue(find('mgf_output_devices')))==='["1","3"]','saved Output Devices reload into the form');
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

    def test_md_store_item_features_off_hide_fields_and_keep_saved_values(self):
        # Store Options > Item Features OFF (Store scope): fields are hidden and left out of the save payload.
        html = self.md_page_html("item_features:{color:false,modifier:false,bottle_deposit:false,sugar_tax:false,output_device:false},location_management:false,")
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
  await wait(()=>find('mgBody').querySelector('[data-row-select="7"]'));
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>find('mgf_output_devices')&&find('mgDetailBody').textContent.includes('BASE-2'));
  const outWrap=find('mgf_output_devices').closest('[data-field-wrap]');
  assert(outWrap.classList.contains('d-none')&&win.getComputedStyle(outWrap).display==='none','Output Device is hidden');
  assert(JSON.stringify(win.AwsumOptionGroup.getValue(find('mgf_output_devices')))==='["2","5"]','hidden Output Device still holds the saved value');
  const heads=[...find('mgDetailSection').querySelectorAll('thead th')].filter(th=>win.getComputedStyle(th).display!=='none').map(th=>th.textContent.trim());
  assert(heads.join('|')==='Default|Barcode|Unit|PK/Size|Size|Cost|Price|Actions','Modifier column is hidden');
  assert([...find('mgDetailBody').querySelectorAll('tr')].every(tr=>tr.children.length===8)&&!find('mgDetailBody').textContent.includes('Case'),'detail rows omit Modifier');
  assert(find('mgf_inventory_type')&&win.getComputedStyle(find('mgf_inventory_type').closest('[data-field-wrap]')).display!=='none','other Options stay visible');
  // Use Color OFF: top Color, Detail Color column and editor Color are hidden; the saved Color is never sent.
  assert(win.getComputedStyle(find('mgf_default_color').closest('[data-field-wrap]')).display==='none'&&find('mgf_default_color').value==='11','top Color is hidden but holds the Default Detail Color');
  assert(!find('mgDetailBody').textContent.includes('Black')&&!find('mgDetailBody').textContent.includes('Old Red'),'detail rows omit Color');
  // Use Location Management OFF: Store selector, Location column and editor block are hidden; no link requests.
  assert(win.getComputedStyle(find('mgf_sales_location').closest('[data-item-feature]')).display==='none','Location section is hidden');
  assert(!win.requests.some(r=>r.path.endsWith('/locations')),'no Location requests while OFF');
  find('mgf_default_pk_size').value='6';
  find('mgForm').requestSubmit();
  await wait(()=>win.requests.some(r=>r.method==='PUT'&&r.path.endsWith('/md/7/with-image')));
  const mdSave=win.requests.find(r=>r.method==='PUT'&&r.path.endsWith('/md/7/with-image'));
  assert(!('output_devices' in mdSave.payload)&&mdSave.payload.name==='Sample Item'&&mdSave.payload.keep==='COLD','MD save leaves out Output Device (saved value kept by the server)');
  assert(!('sales_location' in mdSave.payload)&&!('warehouse_location' in mdSave.payload),'Location OFF: the common positions are not sent (kept by the server)');
  try{await wait(()=>win.requests.some(r=>r.method==='PUT'&&r.path.endsWith('/details/1')));}
  catch(e){throw Error('no default sync; form error='+find('mgFormError').textContent+' | message='+(find('awsumToastRegion')||{textContent:''}).textContent+' | reqs='+win.requests.slice(-4).map(r=>r.method+' '+r.path).join(','));}
  const defaultSync=win.requests.find(r=>r.method==='PUT'&&r.path.endsWith('/details/1'));
  assert(defaultSync.payload.pk_size==='6'&&!('color_id' in defaultSync.payload)&&!('color_code_id' in defaultSync.payload),'Default Detail sync leaves out Color (saved Color kept by the server)');
  await wait(()=>find('mgDetailBody').querySelector('[data-detail-act="edit"][data-id="2"]'));
  find('mgDetailBody').querySelector('[data-detail-act="edit"][data-id="2"]').click();
  assert(win.getComputedStyle(find('mgDetailModifier').parentElement).display==='none','Modifier input is hidden in the Detail editor');
  assert(win.getComputedStyle(find('mgDetailColor').parentElement).display==='none','Color select is hidden in the Detail editor');
  find('mgDetailSave').click();
  await wait(()=>win.requests.some(r=>r.method==='PUT'&&r.path.endsWith('/details/2')));
  const detailSave=win.requests.find(r=>r.method==='PUT'&&r.path.endsWith('/details/2'));
  assert(!('modifier' in detailSave.payload)&&!('color_id' in detailSave.payload)&&!('color_code_id' in detailSave.payload)&&detailSave.payload.unit==='CS','Detail save leaves out Modifier and Color (saved values kept by the server)');
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_md_inventory_type_and_keep_show_inherited_checked_and_keep_null(self):
        # MD row inherits both (NULL); mock Category III 3 = Recipe (3) / Keep Dry. System default = None (1) / Dry.
        html = self.md_page_html(md_extra=",inventory_type:null,keep:null")
        html = html.replace("inventory_type:3,keep:'DRY'}", "inventory_type:3,keep:'COLD'}")  # Category III Keep = Cold
        serialized = json.dumps(html).replace("</", "<\\/")
        script = f"const html={serialized};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+(label||''));};
(async()=>{
  const frame=document.createElement('iframe');frame.style.border='0';frame.style.width='1400px';frame.style.height='800px';frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),OG=()=>win.AwsumOptionGroup;
  const inv=()=>find('mgf_inventory_type'),keep=()=>find('mgf_keep');
  const checked=g=>[...g.querySelectorAll('input:checked')].map(i=>i.value).join();
  const source=g=>g.querySelector('.awsum-option-source').textContent;
  const label=(g,v)=>[...g.querySelectorAll('input')].find(i=>i.value===v).closest('label');
  const body=(g,v)=>win.getComputedStyle(label(g,v).querySelector('.awsum-option-body'));
  const clearShown=g=>win.getComputedStyle(g.querySelector('[data-option-clear]')).display!=='none';
  const state=g=>[checked(g),OG().getValue(g),source(g),g.classList.contains('is-inheriting')?'inheriting':(g.classList.contains('has-value')?'override':'none')].join('|');
  const saves=()=>win.requests.filter(r=>r.method==='PUT'&&r.path.endsWith('/md/7/with-image'));
  await wait(()=>find('mgBody').querySelector('[data-row-select="7"]'),'list');
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>inv()&&find('mgDetailBody').textContent.includes('BASE-1'),'form');

  // 1) Inherited values are CHECKED, marked as inherited, and read as '' (NULL).
  assert(state(inv())==='3||Inherited from Category III|inheriting','Inventory Type inherits Recipe checked: '+state(inv()));
  assert(state(keep())==='COLD||Inherited from Category III|inheriting','Keep inherits Cold checked: '+state(keep()));
  assert(body(keep(),'COLD').borderStyle==='dashed'&&body(keep(),'COLD').boxShadow==='none','inherited selection is drawn dashed (not a solid pick)');
  assert(!clearShown(inv())&&!clearShown(keep()),'Reset is hidden while inheriting');

  // 2) Saving other fields keeps NULL (nothing inherited becomes explicit).
  find('mgf_second_name').value='Alt Changed';
  find('mgForm').requestSubmit();
  await wait(()=>saves().length===1,'save 1');
  let p=saves()[0].payload;
  assert(p.inventory_type===''&&p.keep===''&&p.second_name==='Alt Changed','other-field save sends blank (inherit) for both: '+JSON.stringify([p.inventory_type,p.keep]));
  await wait(()=>!find('mgSave').disabled&&find('mgSave').textContent==='Save Changes','saved');
  assert(state(inv())==='3||Inherited from Category III|inheriting'&&state(keep())==='COLD||Inherited from Category III|inheriting','still inheriting after save and reload');

  // 3) Category III change: inherited values follow; no Category III -> system default None / Dry.
  find('mgf_category3_id').value='';find('mgf_category3_id').dispatchEvent(new win.Event('change'));
  assert(state(inv())==='1||System default|inheriting'&&state(keep())==='DRY||System default|inheriting','system default None / Dry is checked');
  find('mgf_category3_id').value='3';find('mgf_category3_id').dispatchEvent(new win.Event('change'));
  assert(state(keep())==='COLD||Inherited from Category III|inheriting','back to Category III Cold');

  // 4) A user pick is an Override, even the same value as inherited.
  label(keep(),'COLD').click();
  assert(state(keep())==='COLD|COLD|Override|override'&&clearShown(keep()),'clicking the inherited Cold makes an Override: '+state(keep()));
  assert(body(keep(),'COLD').borderStyle==='solid'&&body(keep(),'COLD').boxShadow!=='none','Override is a solid selection');
  label(inv(),'2').click();
  assert(state(inv())==='2|2|Override|override','Inventory Type Override Stock');
  assert(!find('mgKeepReq').classList.contains('d-none'),'Stock override makes Keep required (Keep has a value)');
  // Keyboard arrow selection is also an Override.
  label(keep(),'FROZEN').querySelector('input').click();
  assert(state(keep())==='FROZEN|FROZEN|Override|override','keyboard/radio selection is an Override');
  // Category III change does not move an Override.
  find('mgf_category3_id').value='';find('mgf_category3_id').dispatchEvent(new win.Event('change'));
  assert(state(keep())==='FROZEN|FROZEN|Override|override'&&state(inv())==='2|2|Override|override','Overrides stay when the inherited value changes');
  find('mgf_category3_id').value='3';find('mgf_category3_id').dispatchEvent(new win.Event('change'));
  find('mgForm').requestSubmit();
  await wait(()=>saves().length===2,'save 2');
  p=saves()[1].payload;
  assert(p.inventory_type==='2'&&p.keep==='FROZEN','Overrides are saved explicitly');
  await wait(()=>!find('mgSave').disabled,'saved 2');
  assert(state(keep())==='FROZEN|FROZEN|Override|override','saved Override reloads as Override');

  // 5) Reset returns to inheritance: inherited value checked again, saved as NULL.
  keep().querySelector('[data-option-clear]').click();
  inv().querySelector('[data-option-clear]').click();
  assert(state(keep())==='COLD||Inherited from Category III|inheriting'&&state(inv())==='3||Inherited from Category III|inheriting','Reset inherits again (checked)');
  find('mgForm').requestSubmit();
  await wait(()=>saves().length===3,'save 3');
  p=saves()[2].payload;
  assert(p.inventory_type===''&&p.keep==='','Reset saves NULL');

  // 6) Category III changed elsewhere (Keep -> Frozen, Inventory Type -> Stock): re-selecting the MD shows it.
  const baseFetch=win.fetch;
  let savedKeep=null;
  win.fetch=async(path,options={})=>{
    const res=await baseFetch(path,options);
    const url=new URL(path,'http://127.0.0.1');
    if(url.pathname.endsWith('/options')){
      const data=await res.json();data.category3=data.category3.map(c=>c.id===3?{...c,inventory_type:2,keep:'FROZEN'}:c);
      return new Response(JSON.stringify(data));
    }
    if(url.pathname.endsWith('/md')&&(options.method||'GET')==='GET'){
      const data=await res.json();data.items=data.items.map(r=>({...r,keep:savedKeep}));
      return new Response(JSON.stringify(data));
    }
    return res;
  };
  const optionCalls=()=>win.requests.filter(r=>r.path.endsWith('/options')).length;
  let before=optionCalls();
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>optionCalls()>before&&keep()&&checked(keep())==='FROZEN','fresh Category III');
  assert(state(keep())==='FROZEN||Inherited from Category III|inheriting'&&state(inv())==='2||Inherited from Category III|inheriting','re-selected MD shows the updated inherited values');
  // A Keep Override saved elsewhere appears as an Override; once cleared (e.g. bulk change) it inherits again.
  savedKeep='COLD';before=optionCalls();
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>optionCalls()>before&&checked(keep())==='COLD'&&keep().classList.contains('has-value'),'fresh override');
  assert(state(keep())==='COLD|COLD|Override|override','fresh saved Keep Override is shown as Override');
  savedKeep=null;before=optionCalls();
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>optionCalls()>before&&keep().classList.contains('is-inheriting'),'fresh inherit');
  assert(state(keep())==='FROZEN||Inherited from Category III|inheriting','cleared Override inherits again');
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_md_location_common_fields_inherit_override_reset(self):
        # MD Master > GENERAL > Options > Location: two common fields; empty = Category III default (hint), typed = Override.
        html = self.md_page_html(md_extra=",sales_location:null,warehouse_location:'W01-R02'")
        html = html.replace("inventory_type:3,keep:'DRY'}", "inventory_type:3,keep:'DRY',sales_location:'A03',warehouse_location:'W99'}")
        # The Common Lookup Modal (real Bootstrap Modal) as the page loads it for MD Master.
        extra_css = "\n".join((browser.ROOT / "static" / "css" / n).read_text(encoding="utf-8") for n in ("platform-modal.css", "awsum-lookup.css"))
        extra_js = "\n".join((browser.ROOT / "static" / n).read_text(encoding="utf-8") for n in ("bootstrap/js/bootstrap.bundle.min.js", "js/lookup-modal.js"))
        html = html.replace("</head>", f"<style>{extra_css}</style></head>").replace("</body>", f"<script>{extra_js}</script></body>")
        html = html.replace("category3:[{id:3,", "category3:[{id:4,name:'Cooler',code:'COOL',category2_id:2,active:true,sales_location:'C01',warehouse_location:null},{id:3,")
        serialized = json.dumps(html).replace("</", "<\\/")
        script = f"const html={serialized};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+(label||''));};
const open=async(width)=>{
  const frame=document.createElement('iframe');frame.style.border='0';frame.style.width=width+'px';frame.style.height='800px';frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id);
  await wait(()=>find('mgBody').querySelector('[data-row-select="7"]'),'list');
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>find('mgf_sales_location')&&find('mgDetailBody').textContent.includes('BASE-2'),'form');
  return {frame,doc,win,find};
};
(async()=>{
  let {frame,doc,win,find}=await open(1400);
  const reset=key=>doc.querySelector(`[data-loc-reset="${key}"]`);
  const st=key=>[find('mgf_'+key).value,find('mgf_'+key).placeholder,find('mgLocSource_'+key).textContent,reset(key).classList.contains('d-none')?'-':'reset'].join('|');
  // Placement: its own Common Section right under Keep, before Output Device; no Store / Detail / Save Locations.
  const sec=find('mgLocSection'),keepSec=find('mgf_keep').closest('section'),outSec=find('mgf_output_devices').closest('section');
  assert(keepSec.nextElementSibling===sec&&sec.nextElementSibling===outSec&&sec.querySelector('.pm-section-head h3').textContent==='Location','Options (Keep) > Location > Output Device');
  assert(!find('mgf_loc_store')&&!find('mgf_loc_detail')&&!find('mgLocSave')&&!sec.querySelector('input[type="checkbox"]'),'no Store / Detail selector, checkboxes or Save Locations');
  assert([...find('mgDetailSection').querySelectorAll('thead th')].every(th=>th.textContent.trim()!=='Location'),'MD Detail table has no Location column');
  // Inherit / Override.
  assert(st('sales_location')==='|A03 (Category III)|Inherited from Category III|-','empty field inherits the Category III default');
  assert(st('warehouse_location')==='W01-R02|W99 (Category III)|Override|reset','saved value is an Override');
  const box=k=>find('mgf_'+k).closest('[data-field-wrap]').getBoundingClientRect();
  assert(Math.abs(box('sales_location').top-box('warehouse_location').top)<2,'two fields side by side on a wide screen');
  // Common Location Selector: read-only code + Search / Clear; Reset stays the Common text link under the field.
  const keepReset=find('mgf_keep').closest('[data-field-wrap]').querySelector('.awsum-option-clear');
  ['sales_location','warehouse_location'].forEach(key=>{
    const link=reset(key),input=find('mgf_'+key),cs=win.getComputedStyle(link);
    assert(link.classList.contains('awsum-option-clear')&&!link.classList.contains('btn')&&link.closest('.form-text'),key+': Reset is the Common text link under the field');
    const search=doc.querySelector(`[data-loc-search="${key}"]`),label=key==='sales_location'?'Sales Floor Location':'Warehouse Location';
    assert(input.readOnly&&!input.hasAttribute('data-awsum-code')&&input.closest('.input-group')&&search.querySelector('svg')&&search.textContent.trim()===''
      &&search.title==='Search '+label&&search.getAttribute('aria-label')==='Search '+label,key+': read-only + magnifier icon button (title / aria-label)');
    assert(!doc.querySelector(`[data-loc-clear="${key}"]`),key+': MD has no Clear button (Reset returns to Category III)');
    if(keepReset){const ks=win.getComputedStyle(keepReset);
      assert(['fontSize','color','textDecorationLine','backgroundColor','borderTopWidth'].every(p=>cs[p]===ks[p]),key+': same style as the Keep Reset link');}
    else assert(cs.textDecorationLine==='underline'&&cs.fontSize==='11px'&&cs.borderTopWidth==='0px','Common Reset link style');
  });
  // Search from the keyboard (Enter on the read-only field): the Common Lookup, Sales Floor only, Active by default.
  const modal=()=>find('awsumLookupModal'),shown=()=>!!modal()&&modal().classList.contains('show')&&!!doc.querySelector('.modal-backdrop');
  const gone=()=>!modal().classList.contains('show')&&!doc.querySelector('.modal-backdrop');
  const rows=()=>[...find('awsumLookupBody').querySelectorAll('tr[data-index]')].map(tr=>tr.firstChild.textContent+(tr.classList.contains('disabled')?'(x)':''));
  const codesReq=()=>win.requests.filter(r=>r.path.endsWith('/location/codes'));
  const sales=find('mgf_sales_location');
  sales.focus();sales.dispatchEvent(new win.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  await wait(()=>shown()&&rows().length===2,'lookup open');
  assert(find('awsumLookupTitle').textContent==='Select Sales Floor Location'&&find('awsumLookupFixed').textContent==='Area: Sales Floor','title + fixed Area');
  assert(codesReq().every(r=>new URLSearchParams(r.query).get('location_area')==='SALES_FLOOR')&&new URLSearchParams(codesReq()[0].query).get('active')==='active'
    &&!new URLSearchParams(codesReq()[0].query).has('store_id'),'Sales Floor only, Active, All Stores by default');
  assert(rows().join()==='A03-B02-S04,S01-B01-C01'&&[...find('awsumLookupHead').children].map(th=>th.textContent).join('|')==='Location Code|Name|Stores|Active Stores','columns: '+rows().join());
  assert([...find('awsumLookupBody').querySelector('tr').children].slice(2).map(td=>td.textContent).join('/')==='2/2'&&find('awsumLookupSummary').textContent==='2 code(s) · 3 Store(s) in scope','Store counts');
  // All status: the inactive code is listed but can never be chosen (double-click / Select do nothing).
  const status=[...find('awsumLookupFilters').querySelectorAll('select')].find(s=>s.dataset.key==='active');
  status.value='';status.dispatchEvent(new win.Event('change',{bubbles:true}));
  await wait(()=>rows().length===3,'all status');
  assert(rows()[2]==='S01-B02-C01Inactive(x)'&&new URLSearchParams(codesReq().pop().query).get('active')==='','inactive listed and disabled: '+rows()[2]);
  find('awsumLookupBody').querySelectorAll('tr')[2].dispatchEvent(new win.MouseEvent('dblclick',{bubbles:true}));
  find('awsumLookupBody').querySelectorAll('tr')[2].click();await tick();
  assert(shown()&&find('awsumLookupSelect').disabled&&sales.value==='','inactive cannot be selected');
  // Keyboard: type to search, Arrow Down into the list, Enter selects.
  const search=find('awsumLookupSearch');
  await wait(()=>doc.activeElement===search,'search box focused');
  search.value='b02-s';search.dispatchEvent(new win.Event('input',{bubbles:true}));
  await wait(()=>rows().join()==='A03-B02-S04','search: '+rows().join());
  search.dispatchEvent(new win.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
  assert(doc.activeElement===find('awsumLookupBody').querySelector('tr')&&!find('awsumLookupSelect').disabled,'Arrow Down moves into the list');
  doc.activeElement.dispatchEvent(new win.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  await wait(()=>gone()&&sales.value==='A03-B02-S04','Enter selects');
  assert(st('sales_location')==='A03-B02-S04|A03 (Category III)|Override|reset','selection makes an Override (Reset appears)');
  assert(doc.activeElement===doc.querySelector('[data-loc-search="sales_location"]'),'focus returns to Search');
  // Reset still works; the mouse path (click a row + Select) picks it again, the current code is marked.
  reset('sales_location').click();
  assert(st('sales_location')==='|A03 (Category III)|Inherited from Category III|-','Reset returns to the Category III default');
  doc.querySelector('[data-loc-search="sales_location"]').click();
  await wait(()=>shown()&&rows().length===2,'lookup again (fresh search, Active)');
  find('awsumLookupBody').querySelector('tr').click();
  assert(find('awsumLookupBody').querySelector('tr').classList.contains('selected'),'row highlighted');
  find('awsumLookupSelect').click();
  await wait(()=>gone()&&sales.value==='A03-B02-S04','Select picks the highlighted row');
  doc.querySelector('[data-loc-search="sales_location"]').click();
  await wait(()=>shown()&&rows().length===2,'lookup with a current value');
  assert(rows()[0].includes('Current')&&find('awsumLookupBody').querySelector('tr').classList.contains('selected'),'the current code is marked');
  modal().querySelector('.modal-footer [data-bs-dismiss]').click();
  await wait(gone,'Cancel');
  assert(sales.value==='A03-B02-S04','Cancel keeps the value');
  // Reset on the saved Override: the MD value is released and Category III is inherited (Reset hides again).
  reset('warehouse_location').click();
  assert(st('warehouse_location')==='|W99 (Category III)|Inherited from Category III|-','Reset returns the saved Override to the Category III default');
  assert(doc.activeElement===doc.querySelector('[data-loc-search="warehouse_location"]'),'focus moves to the Search icon');
  // Category III change: the inherited hint follows; the Override stays.
  find('mgf_category3_id').value='';find('mgf_category3_id').dispatchEvent(new win.Event('change'));
  assert(st('warehouse_location')==='|Not set|Not set|-'&&st('sales_location').startsWith('A03-B02-S04|Not set|Override'),'no Category III: Not set');
  find('mgf_category3_id').value='3';find('mgf_category3_id').dispatchEvent(new win.Event('change'));
  find('mgForm').requestSubmit();
  await wait(()=>win.requests.some(r=>r.method==='PUT'&&r.path.endsWith('/md/7/with-image')),'save');
  const md=win.requests.find(r=>r.method==='PUT'&&r.path.endsWith('/md/7/with-image')).payload;
  assert(md.sales_location==='A03-B02-S04'&&md.warehouse_location===''&&!win.requests.some(r=>r.path.includes('/locations')),'Save Changes sends the Override and a blank (= inherit); no separate location API');
  frame.remove();
  // Narrow screen: the two fields stack.
  ({frame,doc,win,find}=await open(560));
  const nb=k=>find('mgf_'+k).closest('[data-field-wrap]').getBoundingClientRect();
  assert(nb('warehouse_location').top>=nb('sales_location').bottom-1,'fields stack on a narrow screen');
  frame.remove();
  // 390px: Search / Clear stay inside the field; the lookup fits (Name column hidden), Select / Cancel reachable.
  ({frame,doc,win,find}=await open(390));
  // (The MD Detail table already makes the MD pane wider than 390px; the Location section must not add to it.)
  const pane=()=>find('mgMdPaneGeneral').getBoundingClientRect().width;
  find('mgLocSection').style.display='none';await tick();const paneWithout=pane();find('mgLocSection').style.display='';await tick();
  assert(Math.abs(pane()-paneWithout)<1,'the Location section does not widen the page at 390px');
  ['sales_location','warehouse_location'].forEach(key=>{const wrap=find('mgf_'+key).closest('[data-field-wrap]').getBoundingClientRect();
    const r=doc.querySelector(`[data-loc-search="${key}"]`).getBoundingClientRect(),inp=find('mgf_'+key).getBoundingClientRect();
    assert(r.width>0&&r.width<=48&&r.left>=wrap.left-1&&r.right<=wrap.right+1&&inp.width>=wrap.width-r.width-32,key+' icon inside its field, the code keeps the width at 390px');
    assert(Math.abs(r.left-inp.right-6)<=0.5&&Math.abs(r.width-r.height)<=0.5&&Math.abs(r.height-inp.height)<=0.5&&Math.abs(r.top-inp.top)<=0.5,key+': 6px gap, square icon as tall as the field at 390px');
    assert(!doc.querySelector(`[data-loc-clear="${key}"]`)&&!doc.querySelector(`[data-loc-reset="${key}"]`).closest('.input-group'),key+': Reset link stays under the field');});
  const vw=doc.documentElement.clientWidth,inside=el=>{const r=el.getBoundingClientRect();return r.width>0&&r.left>=-1&&r.right<=vw+1;};
  doc.querySelector('[data-loc-search="warehouse_location"]').click();
  await wait(()=>find('awsumLookupModal')&&find('awsumLookupModal').classList.contains('show')&&find('awsumLookupBody').querySelector('tr[data-index]'),'lookup at 390px');
  await tick();await tick();
  const m=find('awsumLookupModal');
  assert(inside(m.querySelector('.modal-content'))&&inside(find('awsumLookupSelect'))&&inside(m.querySelector('.modal-footer [data-bs-dismiss]')),'modal and buttons inside at 390px');
  const tableBox=m.querySelector('.awsum-lookup-table');
  assert(win.getComputedStyle(find('awsumLookupHead').children[1]).display==='none'&&tableBox.scrollWidth<=tableBox.clientWidth+1,'Name column hidden; the list fits the modal');
  const mbody=m.querySelector('.modal-body');
  assert(mbody.scrollHeight<=mbody.clientHeight+1&&find('awsumLookupNext').getBoundingClientRect().bottom<=m.querySelector('.modal-footer').getBoundingClientRect().top+1,'pager visible above the footer (no modal-body scroll)');
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_md_color_platform_always_shows_default_and_detail_color(self):
        # Platform: Color always shown. Top Color = Default Detail Color; each Detail has its own Color.
        html = self.md_page_html()
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
  const visible=el=>!!el&&win.getComputedStyle(el).display!=='none'&&el.getClientRects().length>0;
  const opts=el=>[...el.options].map(o=>o.value+'='+o.textContent).join('|');
  await wait(()=>find('mgBody').querySelector('[data-row-select="7"]'));
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>find('mgDetailBody').textContent.includes('BASE-2')&&find('mgf_default_color').value==='11');
  const top=find('mgf_default_color');
  assert(visible(top)&&top.tagName==='SELECT'&&top.classList.contains('form-select')&&!top.name,'top Color is a Common Select without an MD field name');
  assert(opts(top)==='=None|11=Black|12=Navy','top Color lists active Colors (inactive only when saved)');
  const nameBox=find('mgf_name').getBoundingClientRect(),colorBox=top.getBoundingClientRect();
  assert(Math.abs(colorBox.top-nameBox.top)<1&&colorBox.left>find('mgf_second_name').getBoundingClientRect().right,'Color sits on the Item Name / Second Name line');
  const rowEls=['default_barcode','unit','default_pk_size','default_size','retail_price'].map(name=>find('mgf_'+name));
  assert(rowEls.every(el=>Math.abs(el.getBoundingClientRect().top-rowEls[0].getBoundingClientRect().top)<1),'Barcode row is unchanged');
  const heads=[...find('mgDetailSection').querySelectorAll('thead th')].filter(visible).map(th=>th.textContent.trim());
  assert(heads.join('|')==='Default|Barcode|Color|Unit|PK/Size|Size|Cost|Price|Modifier|Actions','Detail grid shows Color after Barcode (no Location column)');
  const cells=[...find('mgDetailBody').querySelectorAll('tr')].map(tr=>tr.children[2].textContent.trim());
  assert(cells.join('|')==='Black|Old Red'&&find('mgDetailBody').querySelector('.mg-color-swatch'),'Detail rows show the Color name with swatch (inactive name kept)');
  find('mgDetailBody').querySelector('[data-detail-act="edit"][data-id="2"]').click();
  const sel=find('mgDetailColor');
  assert(visible(sel)&&sel.value==='13'&&opts(sel)==='=None|11=Black|12=Navy|13=Old Red (Inactive)','editor keeps the saved inactive Color selectable');
  const editorInputs=[...find('mgDetailEditor').querySelectorAll('input,select')];
  assert(editorInputs[0]===find('mgDetailBarcode')&&editorInputs[1]===sel,'editor Color follows Barcode');
  sel.value='12';find('mgDetailSave').click();
  await wait(()=>win.requests.some(r=>r.method==='PUT'&&r.path.endsWith('/details/2')));
  assert(win.requests.find(r=>r.method==='PUT'&&r.path.endsWith('/details/2')).payload.color_code_id===12&&!('color_id' in win.requests.find(r=>r.method==='PUT'&&r.path.endsWith('/details/2')).payload),'Detail save sends only the COLOR code id (color_id is removed)');
  await wait(()=>[...find('mgDetailBody').querySelectorAll('tr')].map(tr=>tr.children[2].textContent.trim()).join('|')==='Black|Navy');
  find('mgDetailAdd').click();
  assert(find('mgDetailColor').value===''&&opts(find('mgDetailColor'))==='=None|11=Black|12=Navy','Add Detail starts with no Color');
  find('mgDetailCancel').click();
  top.value='12';
  find('mgForm').requestSubmit();
  await wait(()=>win.requests.some(r=>r.method==='PUT'&&r.path.endsWith('/details/1')));
  const mdSave=win.requests.find(r=>r.method==='PUT'&&r.path.endsWith('/md/7/with-image'));
  assert(!('color_id' in mdSave.payload)&&!('color_code_id' in mdSave.payload)&&!('default_color' in mdSave.payload)&&!('color' in mdSave.payload),'MD Master payload never carries Color');
  const sync=win.requests.find(r=>r.method==='PUT'&&r.path.endsWith('/details/1')).payload;
  assert(sync.color_code_id===12&&sync.is_default===true&&sync.barcode==='BASE-1'&&sync.unit==='EA','top Color saves to the Default Detail via the Detail API');
  await wait(()=>find('mgf_default_color').value==='12');
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
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
window.confirm=()=>{window.browserConfirms=(window.browserConfirms||0)+1;return true;};
window.AwsumConfirm={show:async(o)=>{(window.commonConfirms=window.commonConfirms||[]).push(o);return {confirmed:true,choice:null};}};
const entity=document.getElementById('mgPage').dataset.entity;
const rows={
  tax:[{id:1,code:'TAX00001',name:'State',rate:'7.0000',active:true,notes:''},
       {id:2,code:'TAX00002',name:'City',rate:'1.5000',active:true,notes:''}],
  item_kind:[{id:3,code:'IKC00003',system_code:'HARDWARE',name:'Hardware',display_order:1,active:true,system:true,active_locked:false,in_use:false,notes:''},
             {id:4,code:'IKC00004',system_code:'SERVICE',name:'Service',display_order:2,active:true,system:true,active_locked:false,in_use:false,notes:''}],
  location:[{id:21,code:'A-01',name:'Aisle 1',store_id:5,location_area:'SALES_FLOOR',location_area_name:'Sales Floor',display_order:10,active:true,in_use:false,notes:''},
            {id:22,code:'W-1',name:'Warehouse 1',store_id:5,location_area:'WAREHOUSE',location_area_name:'Warehouse',display_order:20,active:true,in_use:false,notes:''},
            {id:23,code:'B-02',name:'Bay 2',store_id:5,location_area:'SALES_FLOOR',location_area_name:'Sales Floor',display_order:10,active:true,in_use:false,notes:''}],
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
  requests.push({path:url.pathname,method,payload,query:url.search});
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({
    stores:[{id:5,code:'ST5',name:'Main',merchant_name:'Mart',active:true},{id:6,code:'ST6',name:'Closed',merchant_name:'Mart',active:false}],store_fixed:null,
    location_areas:[{code:'SALES_FLOOR',name:'Sales Floor'},{code:'WAREHOUSE',name:'Warehouse'}],
    category1:[{id:1,name:'Food',active:true},{id:2,name:'Hardware',active:true}],
    category2:[{id:7,name:'Drinks',category1_id:1,active:true},{id:8,name:'Tools',category1_id:2,active:true}],
    category3:[],taxes:[{id:1,name:'State',rate:'7.0000',active:true}],
    inventory_types:[{type_id:1,name:'None',keep_required:false},{type_id:2,name:'Stock',keep_required:true}],
    default_inventory_type:1,default_keep:'DRY',keep_types:[{code:'DRY',name:'Dry'},{code:'COLD',name:'Cold'}]}));
  if(method==='DELETE')return new Response(JSON.stringify({message:'Deleted.'}));
  if(url.pathname.endsWith('/location/initialize'))return new Response(JSON.stringify({initialized:false,created:0,reason:'already_initialized'}));
  // Location Master range form: preview counts, then the range save (new codes only).
  if(url.pathname.endsWith('/location/range/preview'))return new Response(JSON.stringify({location_area:payload.location_area,
    codes:[{code:'S01-B01-C01',exists:false}],total:1,duplicates:0,new:1}));
  if(url.pathname.endsWith('/location/range'))return new Response(JSON.stringify({items:[],skipped:[],total:1,created:1,duplicates:0}),{status:201});
  if(method==='PUT'||method==='POST'){
    const item={...(rows.find(r=>url.pathname.endsWith('/'+r.id))||{id:99,code:'NEW00099'}),...payload};
    return new Response(JSON.stringify({item}));
  }
  // Location Master asks one Area per request (two-column list); other masters get all rows.
  const area=url.searchParams.get('location_area');
  const listed=area?rows.filter(r=>r.location_area===area).sort((a,b)=>a.code.localeCompare(b.code)):rows;
  return new Response(JSON.stringify({items:listed,total:listed.length,page:1,pages:1,sort:area?'code':'name',direction:'asc'}));
};
"""
        pages = []
        for entity, title in (("tax", "Tax"), ("category1", "Category I"), ("category2", "Category II"),
                              ("category3", "Category III"), ("item_kind", "Item Kind"),
                              ("location", "Location Master")):
            html = render_list(entity, title)
            html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
            html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", toast_head(), 1)
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
      // Item Kind (Super Admin System Master): auto Code (IKC) plus the protected System Code; Add is available,
      // required System Codes cannot be deleted.
      item_kind:{label:'Item Kind',item:'Hardware',id:3,code:'IKC00003',header:'Display Name',flat:true,system:true,
        fields:()=>f('display_order').value==='1'&&/System Code\s*HARDWARE/.test(find('mgFields').textContent),payload:p=>p.display_order==='1'},
      // Shelf Location: Store-scoped (Store selector, store_id on every request); the code is an editable field.
      // Location Master: Store-scoped, grouped by Area, children indented under their parent; Parent = same Area, never itself/children.
      // Two independent code columns (Sales Floor | Warehouse), Location Code ascending; a code cell is the item.
      // Form: Location Code (fixed after create), optional Name, Area, Display Order, Active, Notes (no Type / Parent).
      // Page Header: Cancel in create / edit, Add hidden while creating, 'Save' for a new record.
      location:{label:'Location Master',item:'A-01',formName:'Aisle 1',id:21,code:'A-01',header:'Sales FloorWarehouse',twoColumns:'A-01|W-1/B-02|',storeScoped:true,
        cancel:true,createLabel:'Save',rangeCreate:true,guide:true,
        // Location Code is fixed after create: Edit shows it read-only (not sent), with View Example next to it.
        fields:()=>!f('code')&&find('mgf_code_view').textContent==='A-01'&&!!find('mgFields').querySelector('[data-location-example]')&&win.AwsumOptionGroup.getValue(f('location_area'))==='SALES_FLOOR'
          &&!f('parent_id')&&!f('location_type')&&!/Parent|Location Type/.test(find('mgFields').textContent)
          &&!find('mgFields').textContent.includes('auto-generated'),
        payload:p=>!('code' in p)&&p.location_area==='SALES_FLOOR'&&!('location_type' in p)&&!('parent_id' in p),
        toggle:p=>p.code==='A-01'&&p.location_area==='SALES_FLOOR'&&!('parent_id' in p)&&!('location_type' in p),
        create:()=>{assert(!f('code')&&f('zone').value==='S01'&&f('bay_from').value==='1'&&f('shelf_to').value==='1','range form defaults');}},
    };
    const entity=find('mgPage').dataset.entity,spec=SPEC[entity],item=spec.item,itemId=spec.id;
    await wait(()=>find('mgBody').textContent.includes(item),'list');
    if(spec.storeScoped){
      const store=find('mgStore');
      assert(store&&!find('mgStoreWrap').classList.contains('d-none')&&store.value==='5'&&[...store.options].map(o=>o.textContent).join('|')==='ST5 · Main (Mart)|ST6 · Closed (Mart) [Inactive]','Store selector lists Stores (active first, inactive marked)');
      assert(win.requests.filter(r=>r.path.endsWith('/location')).every(r=>r.query.includes('store_id=5')),'list requests carry the selected Store');
    }else{
      assert(!find('mgStore'),'no Store selector on non-Store masters');
    }
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
    // Common Page Header actions: visible buttons follow none / create / edit (same nodes, same order).
    const shown=()=>[...find('mgPageActions').children].filter(el=>el.offsetParent!==null).map(el=>el.id||el.querySelector('.btn').id).join(',');
    const noneSet=spec.noAdd?'mgExportBtn':spec.guide?'mgExportBtn,mgGuideBtn,mgAddBtn':'mgExportBtn,mgAddBtn';  // Location: Location Guide in every mode
    assert(!!find('mgGuideBtn')===!!spec.guide,'Location Guide only on Location Master');
    assert(shown()===noneSet,'nothing selected: '+shown());
    assert(doc.querySelectorAll('#mgSave').length===1&&doc.querySelectorAll('#mgMdToggle').length===1&&doc.querySelectorAll('#mgMdDelete').length===1,'no duplicate action buttons');
    assert(!find('mgBody').querySelector('[data-act]')&&find('mgHead').textContent.trim()===spec.header,'single Name column: '+find('mgHead').textContent);
    const groups=()=>[...find('mgBody').querySelectorAll('tr[data-tree-toggle]')];
    const groupText=()=>groups().map(tr=>tr.textContent.replace(/[\s−+]/g,'')).join('|');
    if(spec.twoColumns){
      const cells=[...find('mgBody').querySelectorAll('tr.mg-loc-row')].map(tr=>[...tr.children].map(td=>td.querySelector('.mg-code')?.textContent||'').join('|')).join('/');
      assert(cells===spec.twoColumns,'Sales Floor | Warehouse columns, code ascending, empty cell when shorter: '+cells);
      assert(win.requests.filter(r=>r.path.endsWith('/location')&&r.method==='GET').map(r=>new URLSearchParams(r.query).get('location_area')).sort().join()==='SALES_FLOOR,WAREHOUSE','one request per Area');
      assert(groups().length===0,'no tree groups');
    }else if(spec.flat){
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
    find('mgBody').querySelector(`[data-row-select="${itemId}"]`).click();
    await wait(()=>f('name')&&f('name').value===(spec.formName||item),'form');
    assert(find('mgBody').querySelector(`[data-row-select="${itemId}"]`).classList.contains('selected'),'selected row highlighted');
    assert((spec.storeScoped||find('mgFields').textContent.includes(spec.code))&&spec.fields(),entity+' fields loaded'); // a Location Code is an input value
    assert(!find('mgSave').disabled&&find('mgSave').textContent==='Save Changes'&&find('mgMdToggle').textContent==='Deactivate','Save Changes / Deactivate for the selected record');
    assert(find('mgMdDelete').classList.contains('d-none')===!!spec.system,'Delete shown unless the record is a System Code');
    assert(shown()===noneSet+',mgMdToggle'+(spec.system?'':',mgMdDelete')+(spec.cancel?',mgCancelBtn':'')+',mgSave','edit: '+shown());
    f('name').value=item+' 2';
    find('mgSave').click(); // Page Header Save submits #mgForm (form attribute): same validation and save
    await wait(()=>win.requests.some(r=>r.method==='PUT'),'save');
    await tick();await tick();
    assert(win.requests.filter(r=>r.method==='PUT').length===1,'Save runs exactly once');
    const put=win.requests.find(r=>r.method==='PUT');
    assert(put.path.endsWith(`/${entity}/${itemId}`)&&put.payload.name===item+' 2'&&spec.payload(put.payload),'Save Changes uses the existing update API');
    if(spec.storeScoped)assert(win.requests.filter(r=>r.method!=='GET').every(r=>r.query.includes('store_id=5')),'writes carry the selected Store');
    await wait(()=>/ (updated|created)\./.test((find('awsumToastRegion')||{textContent:''}).textContent),'saved message');
    assert(find('mgSave').textContent==='Save Changes'&&f('name').value===item+' 2','form stays on the saved record');
    // Deactivate from the form header (existing toggle payload; unsent keys such as Tax / Inventory are kept).
    const before=win.requests.length;
    find('mgMdToggle').click();
    await wait(()=>win.requests.slice(before).some(r=>r.method==='PUT'),'toggle');
    const toggle=win.requests.slice(before).find(r=>r.method==='PUT');
    assert(toggle.payload.active===false&&!('tax1_id' in toggle.payload)&&(!spec.toggle||spec.toggle(toggle.payload)),'Deactivate uses the existing toggle payload');
    await wait(()=>find('mgMdToggle').textContent==='Activate','toggle label');
    assert(win.requests.slice(before).filter(r=>r.method==='PUT').length===1,'Deactivate runs exactly once');
    // Location: Deactivate asks through the Common Confirm (warning); other screens are unchanged (no confirm when unused).
    const deactivateConfirm=(win.commonConfirms||[]).find(c=>c.title==='Deactivate Location');
    assert(entity==='location'?(!!deactivateConfirm&&deactivateConfirm.variant==='warning'):!deactivateConfirm,entity+': deactivate confirm');
    if(!spec.system){
      find('mgMdDelete').click();
      await wait(()=>win.requests.some(r=>r.method==='DELETE'&&r.path.endsWith(`/${entity}/${itemId}`)),'delete');
      await wait(()=>find('mgSave').disabled&&!f('name'),'form reset after delete');
      assert(win.requests.filter(r=>r.method==='DELETE').length===1&&shown()===noneSet,'Delete runs once; back to nothing selected: '+shown());
      // Location: Common Confirm (danger); other Master Group screens keep the browser confirm.
      const deleteConfirm=(win.commonConfirms||[]).find(c=>c.title==='Delete Location');
      assert(entity==='location'?(!!deleteConfirm&&deleteConfirm.variant==='danger'&&!win.browserConfirms):(!deleteConfirm&&win.browserConfirms===1),entity+': delete confirm');
    }
    if(spec.noAdd){
      assert(find('mgAddBtn').classList.contains('d-none'),'Add stays hidden for '+entity);
    }else{
      // Add -> empty create form on the right.
      find('mgAddBtn').click();await tick();
      assert(f('name').value===''&&find('mgSave').textContent===(spec.createLabel||'Create')&&find('mgMdToggle').classList.contains('d-none'),'Add shows an empty create form');
      assert(shown()===(spec.cancel?'mgExportBtn,mgGuideBtn,mgCancelBtn,mgSave':'mgExportBtn,mgAddBtn,mgSave'),'create: '+shown());
      if(spec.create)spec.create();
      if(entity==='tax')f('rate').value='2.5';
      f('name').value='New';find('mgForm').requestSubmit();
      if(spec.rangeCreate){
        // Location Master: preview -> Common Confirm -> one range save; then back to nothing selected.
        await wait(()=>win.requests.some(r=>r.method==='POST'&&r.path.endsWith('/location/range')),'range create');
        const post=win.requests.find(r=>r.method==='POST'&&r.path.endsWith('/location/range'));
        assert(post.payload.name==='New'&&post.payload.zone==='S01'&&post.query.includes('store_id=5')&&!win.requests.some(r=>r.method==='POST'&&r.path.endsWith('/location')),'range save');
        assert((win.commonConfirms||[]).some(c=>c.title==='Create Locations'),'Common Confirm before saving');
        await wait(()=>!f('zone')&&shown()===noneSet&&find('mgSave').disabled,'form closed after the range save');
      }else{
      await wait(()=>win.requests.some(r=>r.method==='POST'),'create');
      const post=win.requests.find(r=>r.method==='POST');
      assert(post.path.endsWith(`/${entity}`)&&post.payload.name==='New','Create uses the existing create API');
      await wait(()=>find('mgSave').textContent==='Save Changes','created record stays selected');
      }
    }
    frame.remove();
  }
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_category3_inventory_change_asks_scope_with_common_confirm_modal(self):
        # Real Bootstrap Modal + Common Confirm Modal (static/js/confirm-modal.js) on the Category III screen.
        js = {name: (browser.ROOT / "static" / path).read_text(encoding="utf-8") for name, path in (
            ("bootstrap", "bootstrap/js/bootstrap.bundle.min.js"), ("money", "js/money.js"), ("option-group", "js/option-group.js"),
            ("master-detail", "js/master-detail.js"), ("confirm", "js/confirm-modal.js"), ("lookup", "js/lookup-modal.js"),
            ("master_group", "js/master_group.js"))}
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8") + "\n" + "\n".join(
            (browser.ROOT / "static" / "css" / name).read_text(encoding="utf-8")
            for name in ("brand.css", "awsum-list.css", "awsum-master-detail.css", "awsum-option-group.css", "awsum-confirm-modal.css",
                         "platform-modal.css", "awsum-lookup.css"))
        mock = r"""
window.requests=[];
window.impact={id:5,name:'Soda',inventory_type:2,md_total:3,override_total:2,can_apply_to_md:true};
window.locationCodes={
  SALES_FLOOR:[{code:'A03-B02-S04',name:'Front aisle',stores:2,active_stores:2},{code:'S01-B01-C01',name:'S01-B01-C01',stores:3,active_stores:3},
    {code:'S01-B02-C01',name:'Old shelf',stores:1,active_stores:0}],
  WAREHOUSE:[{code:'W01-R02-C01',name:'Rack 2',stores:2,active_stores:1}]};

let row={id:5,code:'C300005',name:'Soda',category1_id:1,category1_name:'Food',category2_id:7,category2_name:'Drinks',
  tax1_id:null,tax2_id:null,tax3_id:null,inventory_type:2,keep:'COLD',display_order:1,active:true,notes:''};
window.fetch=async(path,options={})=>{
  const url=new URL(path,'http://127.0.0.1'),method=options.method||'GET';
  const payload=options.body?JSON.parse(options.body):null;
  requests.push({path:url.pathname,method,payload,query:url.search});
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({
    category1:[{id:1,name:'Food',active:true}],category2:[{id:7,name:'Drinks',category1_id:1,active:true}],category3:[],taxes:[],
    inventory_types:[{type_id:1,name:'None',keep_required:false},{type_id:2,name:'Stock',keep_required:true},{type_id:3,name:'Recipe',keep_required:false}],
    default_inventory_type:1,default_keep:'DRY',keep_types:[{code:'DRY',name:'Dry'},{code:'COLD',name:'Cold'}]}));
  // Common Location Selector (/location/codes): each code once with its Store counts; Area / Status / search / paging.
  if(url.pathname.endsWith('/location/codes')){
    const p=url.searchParams,q=(p.get('q')||'').toUpperCase(),st=p.get('active');
    const all=((window.locationCodes||{})[p.get('location_area')]||[]).filter(i=>(!q||i.code.includes(q)||i.name.toUpperCase().includes(q))
      &&(st==='active'?i.active_stores>0:st==='inactive'?i.active_stores===0:true)).map(i=>({...i,active:i.active_stores>0}));
    const size=Number(p.get('size')||20),pages=Math.max(Math.ceil(all.length/size),1),page=Math.min(Number(p.get('page')||1),pages);
    return new Response(JSON.stringify({items:all.slice((page-1)*size,page*size),total:all.length,page,pages,store_total:3,location_area:p.get('location_area')}));
  }
  if(url.pathname.endsWith('/inventory-impact'))return new Response(JSON.stringify(impact));
  if(method==='PUT'){
    row={...row,...payload,inventory_type:payload.inventory_type===''?null:Number(payload.inventory_type)};
    const item={...row};delete item.apply_inventory_to_md;
    if(payload.apply_inventory_to_md)item.md_inventory_reset=impact.override_total;
    return new Response(JSON.stringify({item}));
  }
  return new Response(JSON.stringify({items:[row],total:1,page:1,pages:1,sort:'name',direction:'asc'}));
};
"""
        html = render_list("category3", "Category III")
        html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
        html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", toast_head(), 1)
        html = html.replace("</head>", f"<style>{css}</style></head>")
        html = html.replace("</body>", "<script>" + mock + "\n" + "\n".join(
            js[k] for k in ("bootstrap", "money", "option-group", "master-detail", "confirm", "lookup", "master_group")) + "</script></body>")
        serialized = json.dumps(html).replace("</", "<\\/")
        script = f"const html={serialized};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1280px;height:800px';frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),f=name=>find('mgf_'+name);
  const modalEl=()=>find('awsumConfirmModal');
  const open=()=>!!modalEl()&&modalEl().dataset.state==='shown'&&win.getComputedStyle(modalEl()).display==='block';
  const closed=()=>!modalEl()||(modalEl().dataset.state==='hidden'&&win.getComputedStyle(modalEl()).display==='none');
  const pick=value=>[...f('inventory_type').querySelectorAll('input[type="radio"]')].find(r=>r.value===value).closest('label').click();
  const puts=()=>win.requests.filter(r=>r.method==='PUT');
  const impactCalls=()=>win.requests.filter(r=>r.path.endsWith('/category3/5/inventory-impact')).length;
  await wait(()=>find('mgBody').querySelector('tr[data-row-select="5"]'),'list');
  find('mgBody').querySelector('tr[data-row-select="5"]').click();
  await wait(()=>f('name')&&f('name').value==='Soda','form');
  assert(!find('mgCat3Locations')&&!find('mgFields').textContent.includes('Default Locations')&&!win.requests.some(r=>r.path.includes('/locations')),
    'Category III has no Store default-location section');
  assert(f('sales_location')&&f('warehouse_location')&&!doc.querySelector('#mgFields select[id*="store" i]')
    &&f('sales_location').closest('[data-field-wrap]').querySelector('label').textContent.trim()==='Sales Floor Location','Category III: two common fields, no Store selector');
  // Same Common Location Selector as MD Master: read-only; Search lists Warehouse codes only; a row + Select picks it.
  assert(f('warehouse_location').readOnly&&f('sales_location').readOnly&&!f('warehouse_location').hasAttribute('data-awsum-code'),'read-only Location fields');
  ['sales_location','warehouse_location'].forEach(key=>{const s=doc.querySelector(`[data-loc-search="${key}"]`),c=doc.querySelector(`[data-loc-clear="${key}"]`);
    const label=key==='sales_location'?'Sales Floor Location':'Warehouse Location';
    assert(s.querySelector('svg')&&s.title==='Search '+label&&s.getAttribute('aria-label')==='Search '+label,key+': magnifier icon button');
    assert(c&&c.textContent==='Clear'&&c.getAttribute('aria-label')==='Clear '+label&&!doc.querySelector(`[data-loc-reset="${key}"]`),key+': Category III keeps Clear (no Reset)');
    const ir=f(key).getBoundingClientRect(),sr=doc.querySelector(`[data-loc-search="${key}"]`).getBoundingClientRect();
    assert(Math.abs(sr.left-ir.right-6)<=0.5&&Math.abs(sr.width-sr.height)<=0.5&&Math.abs(sr.height-ir.height)<=0.5&&Math.abs(sr.top-ir.top)<=0.5,
      key+': 6px gap, square icon as tall as the field');
    const cr=c.getBoundingClientRect();assert(Math.abs(cr.left-sr.right-6)<=0.5&&Math.abs(cr.height-ir.height)<=0.5,key+': Clear keeps the same gap and height');});
  doc.querySelector('[data-loc-search="warehouse_location"]').click();
  const lrows=()=>find('awsumLookupBody')?[...find('awsumLookupBody').querySelectorAll('tr[data-index]')]:[];
  await wait(()=>find('awsumLookupModal')&&find('awsumLookupModal').classList.contains('show')&&lrows().length===1,'Common Lookup');
  assert(new URLSearchParams(win.requests.filter(r=>r.path.endsWith('/location/codes')).pop().query).get('location_area')==='WAREHOUSE'
    &&find('awsumLookupTitle').textContent==='Select Warehouse Location','Warehouse only');
  lrows()[0].click();find('awsumLookupSelect').click();
  await wait(()=>!find('awsumLookupModal').classList.contains('show')&&!doc.querySelector('.modal-backdrop')&&f('warehouse_location').value==='W01-R02-C01','selected');
  doc.querySelector('[data-loc-clear="sales_location"]').click();
  assert(f('sales_location').value==='','Clear = Not set');

  // 1) No Inventory Type change: plain save, no impact lookup, no modal.
  find('mgForm').requestSubmit();
  await wait(()=>puts().length===1,'plain save');
  assert(impactCalls()===0&&!modalEl()&&!('apply_inventory_to_md' in puts()[0].payload),'unchanged Inventory Type saves without confirmation');
  assert(puts()[0].payload.sales_location===''&&puts()[0].payload.warehouse_location==='W01-R02-C01','Category III Save Changes carries the two positions');

  // 2) Change -> modal with details; default "Category III only" -> OK saves without the flag.
  pick('3');find('mgForm').requestSubmit();
  await wait(open,'modal open');
  const details=[...modalEl().querySelectorAll('.awsum-confirm-details dt')].map(dt=>dt.textContent+'='+dt.nextElementSibling.textContent).join('|');
  assert(details==='Category III=Soda|Inventory Type=Stock → Recipe|MD Master items=3|Item overrides=2','modal shows name, before/after, counts: '+details);
  const radios=[...modalEl().querySelectorAll('.awsum-confirm-choices input[type="radio"]')];
  assert(radios.map(r=>r.value).join()==='category,all'&&radios[0].checked&&!radios[1].checked,'default choice is Category III only');
  assert(modalEl().querySelector('.modal-title').textContent==='Inventory Type Change'&&modalEl().querySelector('.awsum-confirm-ok').textContent==='Save Changes','title and OK label');
  assert(modalEl().dataset.variant==='warning'&&win.getComputedStyle(modalEl().querySelector('.modal-content'),'::before').height==='4px','Common Confirm Modal design (variant accent bar)');
  assert(puts().length===1,'nothing is saved while the modal is open');
  modalEl().querySelector('.awsum-confirm-ok').click();
  await wait(()=>puts().length===2&&closed(),'save category only');
  // OK pressed during the open animation still closes the modal once it has opened.
  let stateAtClick='';
  modalEl().addEventListener('show.bs.modal',()=>setTimeout(()=>{stateAtClick=modalEl().dataset.state;modalEl().querySelector('.awsum-confirm-ok').click();},0),{once:true});
  pick('2');find('mgForm').requestSubmit();
  await wait(()=>puts().length===3&&closed(),'OK during the open animation');
  assert(stateAtClick==='opening','OK was pressed while the modal was still opening');
  pick('3');find('mgForm').requestSubmit();
  await wait(open,'modal open again');
  modalEl().querySelector('.awsum-confirm-ok').click();
  await wait(()=>puts().length===4&&closed(),'save category only (again)');
  assert(puts()[3].payload.inventory_type==='3'&&!('apply_inventory_to_md' in puts()[3].payload),'Category III only: no MD flag');
  await wait(()=>/ (updated|created)\./.test((find('awsumToastRegion')||{textContent:''}).textContent),'saved message');
  assert(!(find('awsumToastRegion')||{textContent:''}).textContent.includes('override'),'no MD reset message for Category III only');

  // 3) Cancel: nothing saved, form keeps the edit.
  pick('1');find('mgForm').requestSubmit();
  await wait(open,'modal open (cancel)');
  assert(modalEl().querySelector('.awsum-confirm-details').textContent.includes('Recipe → None'),'before value follows the saved record');
  modalEl().querySelector('.awsum-confirm-cancel').click();
  await wait(()=>closed()&&(find('awsumToastRegion')||{textContent:''}).textContent.includes('Not saved'),'cancel');
  assert(puts().length===4&&!find('mgSave').disabled&&find('mgSave').textContent==='Save Changes','Cancel saves nothing and re-enables Save');
  assert(win.AwsumOptionGroup.getValue(f('inventory_type'))==='1','the edited value stays in the form after Cancel');

  // 4) Apply to all MD Master -> flag sent, reset count reported.
  find('mgForm').requestSubmit();
  await wait(open,'modal open (all)');
  modalEl().querySelectorAll('.awsum-confirm-choices input')[1].closest('label').click();
  assert(modalEl().querySelectorAll('.awsum-confirm-choices input')[1].checked,'Apply to all can be selected');
  modalEl().querySelector('.awsum-confirm-ok').click();
  await wait(()=>puts().length===5&&closed(),'save all');
  assert(puts()[4].payload.apply_inventory_to_md===true&&puts()[4].payload.inventory_type==='1','Apply to all sends the flag');
  await wait(()=>(find('awsumToastRegion')||{textContent:''}).textContent.includes('2 MD item override(s) now inherit'),'reset message');

  // 5) Not set (Common Reset) also asks; label shows the system default.
  f('inventory_type').querySelector('[data-option-clear]').click();
  find('mgForm').requestSubmit();
  await wait(open,'modal open (not set)');
  assert(modalEl().querySelector('.awsum-confirm-details').textContent.includes('None → Not set (System default: None)'),'Not set is confirmed with the system default');
  assert(modalEl().querySelectorAll('.awsum-confirm-choices input')[0].checked,'default resets to Category III only on every open');
  modalEl().querySelector('.awsum-confirm-ok').click();
  await wait(()=>puts().length===6&&closed(),'save not set');
  assert(puts()[5].payload.inventory_type===''&&!('apply_inventory_to_md' in puts()[5].payload),'Not set saved as Category III only');

  // 6) No MD items (or a scope that cannot apply): plain save.
  win.impact={...win.impact,md_total:0,override_total:0};
  pick('2');find('mgForm').requestSubmit();
  await wait(()=>puts().length===7,'save without items');
  assert(!open()&&!('apply_inventory_to_md' in puts()[6].payload),'no modal when the Category III has no MD items');
  win.impact={...win.impact,md_total:3,override_total:1,can_apply_to_md:false};
  pick('3');find('mgForm').requestSubmit();
  await wait(()=>puts().length===8,'save merchant scope');
  assert(!open(),'no modal when the scope cannot apply to MD Master');
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
