"""Location Master screen (browser, mocked API, real Bootstrap): registration guide when the selected Store has no
locations, "View Example" opening the same guide image in a Bootstrap modal, the Area-dependent Location Code
placeholder (example only), the read-only code on Edit, deactivate / delete confirmations, and the layout at 1400px
and 390px (no horizontal page scroll). Built-in guide asset served through builtin_asset_url()."""
import json
import re
import unittest

from tools import test_image_components as browser
from tools.test_master_group_ui import render_list, toast_head

GUIDE = browser.ROOT / "static" / "images" / "platform" / "ui" / "location-master-guide.svg"


class LocationGuideTemplateTests(unittest.TestCase):
    def test_guide_and_example_modal_only_on_location_master(self):
        html = render_list("location", "Location Master")
        self.assertEqual(html.count("/images/platform/ui/location-master-guide.svg?v="), 2)  # guide + modal, canonical URL
        self.assertNotIn("/static/images/", html)
        self.assertIn('id="mgLocationGuide" class="mg-location-guide d-none"', html)
        self.assertIn('id="mgExampleModal"', html)
        self.assertIn('id="mgGuideAdd">Add Location</button>', html)
        for entity, title in (("tax", "Tax"), ("md", "MD Master"), ("common_code", "Common Maintenance")):
            with self.subTest(entity=entity):
                other = render_list(entity, title)
                self.assertNotIn('id="mgLocationGuide"', other)
                self.assertNotIn('id="mgExampleModal"', other)
                self.assertNotIn("location-master-guide.svg", other)

    def test_guide_asset_explains_both_areas(self):
        svg = GUIDE.read_text(encoding="utf-8")
        self.assertTrue(svg.startswith("<svg") and 'viewBox="0 0 1600 1084"' in svg)
        # Supermarket aisle + warehouse rack with the three parts called out, the finished codes and the
        # AWSUM Location policy (one Sales / one Warehouse Location per product and Store, moves are Stock Transfers).
        for text in ("Sales Floor", "Supermarket aisle", "Warehouse", "Pallet rack storage", "ZONE", "ZONE W01",
                     ">S01<", ">B01<", ">C01<", ">W01<", ">R01<", "Zone", "Bay", "Shelf", "Rack", "Level",
                     "LOCATION CODE", "Products move. Locations stay.",
                     "Each product has one Sales Location per Store.", "Each product has one Warehouse Location per Store.",
                     "Moving it to another shelf or cooler keeps its Location.",
                     "Warehouse ↔ Sales Floor moves are Stock Transfers."):
            self.assertIn(text, svg)
        self.assertIn("S01-B01-C01", svg)  # in the accessible description (the range form's code format)
        self.assertIn("W01-R02-C01", svg)  # Rack R02 is the highlighted rack
        for old in ("A03-B01-C01", "W01-B02-C01", "Warehouse Area", ">A03<"):
            self.assertNotIn(old, svg)
        self.assertNotIn("<script", svg)

    def test_guide_asset_is_reproduced_by_its_build_script(self):
        """tools/build_location_guide.py is the source of the SVG: regenerating gives the committed file exactly."""
        import tempfile
        from pathlib import Path
        from tools import build_location_guide
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "guide.svg"
            build_location_guide.main([str(target)])
            self.assertEqual(target.read_bytes(), GUIDE.read_bytes())
        self.assertEqual(build_location_guide.DEFAULT_OUTPUT, GUIDE.resolve())


class LocationGuideBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def page(self):
        js = {name: (browser.ROOT / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")
              for name in ("money", "option-group", "master-detail", "master_group")}
        bootstrap_js = (browser.ROOT / "static" / "bootstrap" / "js" / "bootstrap.bundle.min.js").read_text(encoding="utf-8")
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8") + "\n" + "\n".join(
            (browser.ROOT / "static" / "css" / name).read_text(encoding="utf-8")
            for name in ("brand.css", "awsum-list.css", "awsum-master-detail.css", "platform-modal.css"))
        svg_uri = "data:image/svg+xml;base64," + __import__("base64").b64encode(GUIDE.read_bytes()).decode()
        mock = r"""
window.requests=[];window.commonConfirms=[];window.browserConfirms=0;
window.confirm=()=>{browserConfirms++;return true;};
window.AwsumConfirm={show:async(o)=>{commonConfirms.push(o);return {confirmed:true,choice:null};}};
const loc=(id,code,area,extra)=>({id,code,name:code+' name',store_id:2,location_area:area,
  location_area_name:area==='WAREHOUSE'?'Warehouse':'Sales Floor',display_order:10,active:true,in_use:false,notes:'',...extra});
const rows={1:[],2:[loc(31,'A03','SALES_FLOOR',{in_use:true}),loc(32,'B01','SALES_FLOOR'),loc(33,'S01-B01-C02','SALES_FLOOR')],
  // Store 3: two independent columns of different lengths (unsorted here; the server sorts by code)
  3:[loc(41,'A03-B02-C01','SALES_FLOOR'),loc(42,'A01-B01-C01','SALES_FLOOR'),loc(43,'A02-B01-C02','SALES_FLOOR',{active:false}),
     loc(51,'W01-B02-C01','WAREHOUSE'),loc(52,'COLD-ROOM-FREEZER-RACK-LEVEL-9','WAREHOUSE')]};
let nextId=90;
rows[4]=[];const initialized=new Set(['1']); // Store 1: initialized earlier, every Location deleted since
// Store 5: Inactive Store of a Merchant login: the server marks the Store and its rows read_only (view only).
const RO={read_only:true,read_only_message:'This Store is Inactive: view only. Platform Admin can edit and activate it.'};
rows[5]=[loc(61,'S01-B01-C01','SALES_FLOOR',RO),loc(62,'W01-R01-C01','WAREHOUSE',RO)];
window.fetch=async(path,options={})=>{
  const url=new URL(path,'http://127.0.0.1'),method=options.method||'GET';
  const payload=options.body?JSON.parse(options.body):null;
  requests.push({path:url.pathname,method,payload,store:url.searchParams.get('store_id'),q:url.searchParams.get('q'),area:url.searchParams.get('location_area'),size:url.searchParams.get('size')});
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({
    stores:[{id:1,code:'S1',name:'Empty Store',active:true,location_management:true},{id:2,code:'S2',name:'Daone',active:true,location_management:true},{id:3,code:'S3',name:'Columns',active:true,location_management:true},{id:4,code:'S4',name:'Fresh',active:true,location_management:true},
      {id:5,code:'S5',name:'Closed',active:false,location_management:true,...RO}],
    store_fixed:null,location_management:true,item_features:{},
    location_areas:[{code:'SALES_FLOOR',name:'Sales Floor'},{code:'WAREHOUSE',name:'Warehouse'}]}));
  // Writes behave like the server: an empty Location Name is saved as the Location Code.
  const list=rows[url.searchParams.get('store_id')]||[];
  // First visit: the server creates the 20 defaults only for a never-initialized Store without Locations.
  if(url.pathname.endsWith('/location/initialize')){
    const id=url.searchParams.get('store_id');
    if(initialized.has(id)||list.length){initialized.add(id);return new Response(JSON.stringify({initialized:false,created:0,reason:'already_initialized'}));}
    initialized.add(id);
    for(let n=1;n<=10;n++)list.push(loc(++nextId,`S01-B${String(n).padStart(2,'0')}-C01`,'SALES_FLOOR',{name:`S01-B${String(n).padStart(2,'0')}-C01`}));
    for(let n=1;n<=10;n++)list.push(loc(++nextId,`W01-R${String(n).padStart(2,'0')}-C01`,'WAREHOUSE',{name:`W01-R${String(n).padStart(2,'0')}-C01`}));
    return new Response(JSON.stringify({initialized:true,created:20,reason:'created'}));
  }
  // Range form: same codes, validation and skipping as the server (one request saves every new code).
  if(url.pathname.endsWith('/location/range/preview')||url.pathname.endsWith('/location/range')){
    const parts=payload.location_area==='WAREHOUSE'?[['rack','Rack','R'],['level','Level','C']]:[['bay','Bay','B'],['shelf','Shelf','C']];
    const bad=parts.find(([k])=>Number(payload[k+'_from'])>Number(payload[k+'_to']));
    if(bad)return new Response(JSON.stringify({detail:'Please correct the highlighted fields.',errors:{[bad[0]+'_to']:`${bad[1]} To must be ${bad[1]} From or greater.`}}),{status:422});
    const pad=n=>String(n).padStart(2,'0'),codes=[];
    for(let i=Number(payload[parts[0][0]+'_from']);i<=Number(payload[parts[0][0]+'_to']);i++)
      for(let j=Number(payload[parts[1][0]+'_from']);j<=Number(payload[parts[1][0]+'_to']);j++)
        codes.push(`${String(payload.zone).toUpperCase()}-${parts[0][2]}${pad(i)}-${parts[1][2]}${pad(j)}`);
    const have=new Set(list.map(r=>r.code)),fresh=codes.filter(c=>!have.has(c));
    if(url.pathname.endsWith('/preview'))return new Response(JSON.stringify({location_area:payload.location_area,
      codes:codes.map(code=>({code,exists:have.has(code)})),total:codes.length,duplicates:codes.length-fresh.length,new:fresh.length}));
    const items=fresh.map(code=>loc(++nextId,code,payload.location_area,{name:payload.name||code}));list.push(...items);
    return new Response(JSON.stringify({items,skipped:codes.filter(c=>have.has(c)),total:codes.length,created:items.length,duplicates:codes.length-items.length}),{status:201});
  }
  if(method==='POST'){
    const item=loc(++nextId,String(payload.code).toUpperCase(),payload.location_area,{name:payload.name||String(payload.code).toUpperCase()});
    list.push(item);return new Response(JSON.stringify({item}),{status:201});
  }
  if(method==='PUT'){
    const row=list.find(r=>r.id===Number(url.pathname.split('/').pop()));
    Object.assign(row,{active:payload.active,name:payload.name||row.code});
    return new Response(JSON.stringify({item:row}));
  }
  if(method==='DELETE')return new Response(JSON.stringify({message:'Deleted.'}));
  // Same paging as the server: one Area per request, Location Code ascending, page clamped to the last page.
  const q=(url.searchParams.get('q')||'').toUpperCase(),area=url.searchParams.get('location_area'),status=url.searchParams.get('active');
  const all=(rows[url.searchParams.get('store_id')]||[]).filter(r=>(!q||r.code.includes(q))&&(!area||r.location_area===area)
    &&(!status||(status==='active')===r.active)).sort((a,b)=>a.code.localeCompare(b.code));
  const size=Number(url.searchParams.get('size')||20),pages=Math.max(Math.ceil(all.length/size),1),page=Math.min(Number(url.searchParams.get('page')||1),pages);
  return new Response(JSON.stringify({items:all.slice((page-1)*size,page*size),total:all.length,page,pages,sort:'code',direction:'asc'}));
};
"""
        html = render_list("location", "Location Master")
        html = re.sub(r"/images/platform/ui/location-master-guide\.svg\?v=\d+", svg_uri, html)
        html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
        html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", toast_head(), 1)
        html = html.replace("</head>", f"<style>{css}</style><script>{bootstrap_js}</script></head>")
        html = html.replace("</body>", f"<script>{mock}\n{js['money']}\n{js['option-group']}\n{js['master-detail']}\n{js['master_group']}</script></body>")
        return json.dumps(html).replace("</", "<\\/")

    def run_page(self, width, body):
        script = f"const html={self.page()};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:""" + str(width) + r"""px;height:900px';frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),f=name=>find('mgf_'+name);
  const visible=id=>{const el=find(id);return !!el&&!el.classList.contains('d-none')&&el.getClientRects().length>0;};
  const noHorizontalScroll=()=>doc.documentElement.scrollWidth<=doc.documentElement.clientWidth+1;
""" + body + r"""
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)  # asserts {"ok": true} itself

    def test_empty_guide_example_modal_placeholder_and_read_only_code(self):
        self.run_page(1400, r"""
  // Empty Store: the guide (image + text + Add Location) fills the right side; nothing selected.
  await wait(()=>visible('mgLocationGuide'),'guide for the empty Store');
  const img=find('mgLocationGuide').querySelector('img');
  await wait(()=>img.complete&&img.naturalWidth>0,'guide image loaded');
  assert(img.getBoundingClientRect().width<=find('mgLocationGuide').getBoundingClientRect().width+1,'guide image fits the panel');
  assert(find('mgBody').textContent.includes('No records found.'),'empty grid');
  assert(noHorizontalScroll(),'no horizontal scroll at 1400px');
  // Add Location from the guide = the page Add action: guide hidden, the range form (Zone default by Area).
  find('mgGuideAdd').click();
  await wait(()=>f('zone')&&!visible('mgLocationGuide'),'create form');
  assert(f('zone').value==='S01'&&visible('mgf_bay_from')&&!visible('mgf_rack_from'),'Sales Floor: Zone S01, Bay / Shelf');
  doc.querySelector('#mgf_location_area input[value="WAREHOUSE"]').click();
  await wait(()=>f('zone').value==='W01'&&visible('mgf_level_to')&&!visible('mgf_shelf_to'),'Warehouse: Zone W01, Rack / Level');
  // View Example (next to the Zone label) opens the guide in a Bootstrap modal.
  const example=find('mgFields').querySelector('[data-location-example]');
  assert(example&&example.closest('.mg-label-row').querySelector('label[for="mgf_zone"]'),'View Example sits next to the Zone label');
  example.click();
  await wait(()=>find('mgExampleModal').classList.contains('show'),'example modal');
  const big=find('mgExampleModal').querySelector('.modal-body img');
  await wait(()=>big.complete&&big.naturalWidth>0,'modal image');
  assert(big.getBoundingClientRect().width>find('mgLocationGuide').querySelector('img').getBoundingClientRect().width,'modal shows it larger');
  find('mgExampleModal').querySelector('[data-bs-dismiss="modal"]').click();
  await wait(()=>!find('mgExampleModal').classList.contains('show'),'modal closed');
  // A Store with locations: no guide; a search without hits is not "empty Store".
  const store=find('mgStore');store.value='2';store.dispatchEvent(new win.Event('change'));
  await wait(()=>find('mgBody').querySelector('[data-row-select="31"]'),'Store 2 list');
  assert(!visible('mgLocationGuide'),'no guide when the Store has locations');
  const search=find('mgSearch');search.value='zzz';search.dispatchEvent(new win.Event('input'));
  await wait(()=>win.requests.some(r=>r.q==='zzz'),'search');await wait(()=>find('mgBody').textContent.includes('No records found.'),'no hits');
  assert(!visible('mgLocationGuide'),'no guide for an empty search result');
  search.value='';search.dispatchEvent(new win.Event('input'));
  await wait(()=>find('mgBody').querySelector('[data-row-select="31"]'),'list again');
  // Edit: the code is read-only text (never sent); View Example is still there.
  find('mgBody').querySelector('[data-row-select="31"]').click();
  await wait(()=>find('mgf_code_view'),'edit form');
  assert(!f('code')&&find('mgf_code_view').textContent==='A03'&&find('mgFields').querySelector('[data-location-example]'),'read-only code');
  // Deactivate: Common Confirm (warning; "in use" wording for a used location), then the same toggle payload.
  find('mgMdToggle').click();
  await wait(()=>win.requests.some(r=>r.method==='PUT'),'toggle request');
  const put=win.requests.find(r=>r.method==='PUT');
  const off=win.commonConfirms.find(c=>c.title==='Deactivate Location in Use');
  assert(off&&off.variant==='warning'&&win.browserConfirms===0,'deactivate uses the Common Confirm');
  assert(put.payload.code==='A03'&&put.payload.active===false&&!('parent_id' in put.payload)&&!('location_type' in put.payload),'toggle payload');
  await wait(()=>find('mgMdToggle').textContent==='Activate','deactivated');
  // Delete (a location without children): Common Confirm (danger), not the browser confirm.
  find('mgBody').querySelector('[data-row-select="32"]').click();
  await wait(()=>find('mgf_code_view')&&find('mgf_code_view').textContent==='B01','B01 form');
  find('mgMdDelete').click();
  await wait(()=>win.requests.some(r=>r.method==='DELETE'),'delete');
  const del=win.commonConfirms.find(c=>c.title==='Delete Location');
  assert(del&&del.variant==='danger'&&win.browserConfirms===0,'delete uses the Common Confirm');
""")

    def test_range_form_preview_skips_existing_and_saves_in_one_request(self):
        self.run_page(1400, r"""
  const shown=()=>[...find('mgPageActions').children].filter(el=>el.offsetParent!==null).map(el=>el.id||el.querySelector('.btn').id).join(',');
  const type=(name,value)=>{f(name).value=value;f(name).dispatchEvent(new win.InputEvent('input',{bubbles:true,inputType:'insertText'}));};
  const codes=()=>[...find('mgRangeCodes').querySelectorAll('.mg-range-code')].map(c=>c.textContent+(c.classList.contains('exists')?'(exists)':'')).join(',');
  const summary=()=>find('mgRangeSummary').textContent.replace(/\s+/g,' ').trim();
  const rangePosts=()=>win.requests.filter(r=>r.method==='POST'&&r.path.endsWith('/location/range'));
  await wait(()=>visible('mgLocationGuide'),'first Store');
  const store=find('mgStore');store.value='2';store.dispatchEvent(new win.Event('change'));
  await wait(()=>find('mgBody').querySelector('[data-row-select="31"]'),'Store 2 list');
  assert(shown()==='mgExportBtn,mgGuideBtn,mgAddBtn','nothing selected: '+shown());
  // Add: one range form (no tabs, no single Location Code input, no Type / Parent).
  find('mgAddBtn').click();
  await wait(()=>f('zone')&&codes()==='S01-B01-C01','create form with its first preview');
  const labels=[...find('mgFields').querySelectorAll('label.form-label')].filter(l=>l.offsetParent!==null).map(l=>l.textContent.replace(/[*\s]+$/,'').trim());
  assert(labels.join('|')==='Area|Zone|Bay From|Bay To|Shelf From|Shelf To|Location Name|Display Order|Status|Notes','fields: '+labels.join('|'));
  assert(!f('code')&&!f('location_type')&&!f('parent_id')&&!doc.querySelector('#mgFields [role="tablist"], #mgFields .nav-tabs'),'no Code input, Type, Parent or tabs');
  const req=name=>!!doc.querySelector(`label[for="mgf_${name}"] .pm-req`);
  assert(!req('name')&&['location_area','zone','bay_from','bay_to','shelf_from','shelf_to'].every(req),'Name optional; Area, Zone and ranges required');
  assert(find('mgFields').textContent.includes('Empty = each Location Code')&&summary()==='Total 1 Existing 0 (skipped) New 1','From = To: one code');
  assert(find('mgSave').textContent==='Save'&&shown()==='mgExportBtn,mgGuideBtn,mgCancelBtn,mgSave','create: Add hidden, Cancel + Save: '+shown());
  // Cancel (resetMdForm): back to nothing selected, nothing saved.
  find('mgCancelBtn').click();
  await wait(()=>!f('zone'),'form closed');
  assert(shown()==='mgExportBtn,mgGuideBtn,mgAddBtn'&&find('mgSave').disabled&&!rangePosts().length,'Cancel resets: '+shown());
  // A range: every combination in code order; S01-B01-C02 already exists in this Store (skipped).
  find('mgAddBtn').click();
  await wait(()=>f('zone')&&codes()==='S01-B01-C01','create form again');
  type('shelf_to','3');
  await wait(()=>codes()==='S01-B01-C01,S01-B01-C02(exists),S01-B01-C03','preview: '+codes());
  assert(summary()==='Total 3 Existing 1 (skipped) New 2','counts: '+summary());
  // Invalid range: explained in the preview; Save shows the field error and sends nothing.
  type('bay_from','3');
  await wait(()=>summary().includes('Bay To must be Bay From or greater.'),'range error in the preview: '+summary());
  find('mgSave').click();
  await wait(()=>f('bay_to').classList.contains('is-invalid'),'field error');
  assert(!rangePosts().length&&!win.commonConfirms.some(c=>c.title==='Create Locations'),'nothing saved for an invalid range');
  type('bay_from','1');
  await wait(()=>summary()==='Total 3 Existing 1 (skipped) New 2','valid again');
  // Save: Common Confirm with the counts, one request, only new codes; the form closes and the grid shows them.
  find('mgSave').click();
  await wait(()=>rangePosts().length===1,'range save');
  const confirm=win.commonConfirms.find(c=>c.title==='Create Locations');
  assert(confirm&&confirm.details.map(d=>d.join(':')).join('|')==='Area:Sales Floor|Codes:S01-B01-C01 … S01-B01-C03|Total:3|Existing (skipped):1|New:2','confirm: '+JSON.stringify(confirm&&confirm.details));
  const post=rangePosts()[0];
  assert(post.store==='2'&&post.payload.location_area==='SALES_FLOOR'&&post.payload.zone==='S01'&&post.payload.shelf_to==='3'&&post.payload.name===''
    &&!('rack_from' in post.payload)&&!('code' in post.payload),'range payload: '+JSON.stringify(post.payload));
  await wait(()=>!f('zone')&&find('mgBody').querySelector('.mg-loc-cell')&&[...find('mgBody').querySelectorAll('.mg-code')].some(c=>c.textContent==='S01-B01-C03'),'grid shows the new codes');
  await wait(()=>(find('awsumToastRegion')||{textContent:''}).textContent.includes('2 location(s) created. 1 existing code(s) skipped.'),'toast');
  assert(shown()==='mgExportBtn,mgGuideBtn,mgAddBtn'&&find('mgSave').disabled,'back to nothing selected');
  const named=find('mgBody').querySelector('.mg-loc-cell[title="S01-B01-C03"]');
  assert(named,'Name defaulted to the code');
  // The same range again: every code exists -> nothing to save (no request, no confirm).
  find('mgAddBtn').click();
  await wait(()=>f('zone')&&codes()==='S01-B01-C01(exists)','create form');
  type('shelf_to','3');
  await wait(()=>summary()==='Total 3 Existing 3 (skipped) New 0','all exist');
  const confirms=win.commonConfirms.length;
  find('mgSave').click();
  await wait(()=>(find('awsumToastRegion')||{textContent:''}).textContent.includes('already exist in this Store. Nothing was saved.'),'nothing to save');
  assert(rangePosts().length===1&&win.commonConfirms.length===confirms,'no second save');
  // Warehouse: Rack / Level, Zone W01, W01-R01-C01 format; the Sales Floor inputs are hidden and not sent.
  doc.querySelector('#mgf_location_area input[value="WAREHOUSE"]').click();
  await wait(()=>f('zone').value==='W01'&&visible('mgf_rack_from')&&!visible('mgf_bay_from')&&f('bay_from').disabled,'Warehouse ranges');
  type('rack_to','2');
  await wait(()=>codes()==='W01-R01-C01,W01-R02-C01','warehouse preview: '+codes());
  // Edit: an existing location is changed from the grid (the edit form, not the range form).
  find('mgBody').querySelector('[data-row-select="32"]').click();
  await wait(()=>find('mgf_code_view')&&find('mgf_code_view').textContent==='B01'&&!f('zone'),'edit form');
  assert(shown()==='mgExportBtn,mgGuideBtn,mgAddBtn,mgMdToggle,mgMdDelete,mgCancelBtn,mgSave','edit: '+shown());
  find('mgCancelBtn').click();
  await wait(()=>!find('mgf_code_view'),'edit closed');
  assert(!find('mgBody').querySelector('.selected')&&!win.requests.some(r=>r.method==='PUT'),'Cancel clears the selection, nothing saved');
  assert(noHorizontalScroll(),'no horizontal scroll at 1400px');
""")

    def test_first_visit_creates_defaults_once_and_shows_them_in_both_columns(self):
        self.run_page(1400, r"""
  const cells=()=>[...find('mgBody').querySelectorAll('tr.mg-loc-row')].map(tr=>[...tr.children].map(td=>td.querySelector('.mg-code')?.textContent||'').join('|'));
  const inits=id=>win.requests.filter(r=>r.method==='POST'&&r.path.endsWith('/location/initialize')&&r.store===id).length;
  // Store 1: initialized earlier, every Location deleted since -> nothing is recreated; the guide shows.
  await wait(()=>visible('mgLocationGuide'),'initialized empty Store keeps the guide');
  assert(inits('1')===1&&find('mgBody').textContent.includes('No records found.'),'asked once, nothing created');
  // Store 4: never initialized and empty -> 20 defaults, immediately in the Sales Floor | Warehouse columns.
  const store=find('mgStore');store.value='4';store.dispatchEvent(new win.Event('change'));
  await wait(()=>cells().length===10,'default locations listed');
  assert(cells()[0]==='S01-B01-C01|W01-R01-C01'&&cells()[9]==='S01-B10-C01|W01-R10-C01'&&find('mgSummary').textContent==='20 record(s)','defaults: '+cells().join('/'));
  await wait(()=>(find('awsumToastRegion')||{textContent:''}).textContent.includes('20 default Locations were created for this Store.'),'toast');
  const firstList=win.requests.findIndex(r=>r.method==='GET'&&r.path.endsWith('/location')&&r.store==='4');
  const init=win.requests.findIndex(r=>r.path.endsWith('/location/initialize')&&r.store==='4');
  assert(init>=0&&init<firstList&&!win.requests.some(r=>r.method==='POST'&&/\/location(\/range)?$/.test(r.path)),'initialize before the list; no other writes');
  assert(!find('mgPageActions').textContent.includes('Default'),'no Create Default Locations button');
  // Back and forth: asked once per Store per page; Store 2 (has data) gets nothing new.
  store.value='2';store.dispatchEvent(new win.Event('change'));
  await wait(()=>find('mgBody').querySelector('[data-row-select="31"]'),'Store 2');
  store.value='4';store.dispatchEvent(new win.Event('change'));
  await wait(()=>cells().length===10,'Store 4 again');
  assert(inits('4')===1&&inits('2')===1&&cells().length===10,'no second initialization');
  assert(win.requests.filter(r=>r.method==='GET'&&r.path.endsWith('/location')&&r.store==='2').length>=2&&!win.requests.some(r=>r.store==='2'&&r.method==='POST'&&!r.path.endsWith('/initialize')),'Store 2 untouched');
""")

    def test_inactive_store_of_a_merchant_is_view_only(self):
        self.run_page(1400, r"""
  const store=find('mgStore');
  await wait(()=>store.options.length===5,'stores');
  assert([...store.options].map(o=>o.textContent).includes('S5 · Closed [Inactive]'),'Inactive Store listed');
  assert(!visible('mgStoreNote')&&!find('mgAddBtn').disabled,'active Store: no note, Add enabled');
  store.value='5';store.dispatchEvent(new win.Event('change'));
  await wait(()=>find('mgBody').querySelector('[data-row-select="61"]'),'Inactive Store rows');
  // Inactive badge + view-only note; Add disabled; no default-Location initialization (a write) for it.
  assert(visible('mgStoreNote')&&find('mgStoreNote').querySelector('.mg-store-badge').textContent==='Inactive'
    &&find('mgStoreNote').textContent.includes('This Store is Inactive: view only. Platform Admin can edit and activate it.'),'badge and note: '+find('mgStoreNote').textContent);
  assert(find('mgAddBtn').disabled&&find('mgGuideAdd').disabled,'Add (and the guide Add) disabled');
  assert(!win.requests.some(r=>r.store==='5'&&r.method!=='GET'),'no write request for the Inactive Store');
  // A row opens view only (existing read-only mode): fields and Save disabled, no Deactivate, the note.
  find('mgBody').querySelector('[data-row-select="61"]').click();
  await wait(()=>f('name')&&find('mgReadOnlyNote'),'view-only form');
  assert([...find('mgFields').querySelectorAll('input,select,textarea')].every(el=>el.disabled)&&find('mgSave').disabled,'fields and Save disabled');
  assert(find('mgReadOnlyNote').textContent==='This Store is Inactive: view only. Platform Admin can edit and activate it.'&&find('mgMdToggle').classList.contains('d-none'),'note, no Deactivate');
  assert(!win.requests.some(r=>r.store==='5'&&r.method!=='GET'),'still no write');
  // Back to an active Store: normal.
  store.value='2';store.dispatchEvent(new win.Event('change'));
  await wait(()=>find('mgBody').querySelector('[data-row-select="31"]'),'Store 2');
  assert(!visible('mgStoreNote')&&!find('mgAddBtn').disabled,'active Store again: Add enabled');
""")

    def test_two_independent_area_columns_paging_search_and_selection(self):
        self.run_page(1400, r"""
  await wait(()=>visible('mgLocationGuide'),'first Store');
  const store=find('mgStore');store.value='3';store.dispatchEvent(new win.Event('change'));
  const cells=()=>[...find('mgBody').querySelectorAll('tr.mg-loc-row')].map(tr=>[...tr.children].map(td=>td.querySelector('.mg-code')?.textContent||'').join('|'));
  await wait(()=>cells().length===3,'two columns');
  assert([...find('mgHead').querySelectorAll('th')].map(th=>th.textContent).join('|')==='Sales Floor|Warehouse','Sales Floor | Warehouse headers');
  // Each column sorted by code on its own; rows pair nothing; the shorter column has empty cells.
  assert(cells().join('/')==='A01-B01-C01|COLD-ROOM-FREEZER-RACK-LEVEL-9/A02-B01-C02|W01-B02-C01/A03-B02-C01|',cells().join('/'));
  assert(find('mgBody').querySelectorAll('td.mg-loc-empty').length===1&&find('mgSummary').textContent==='5 record(s)','empty cell + total');
  assert(find('mgBody').querySelector('[data-row-select="43"]').textContent.includes('Inactive'),'inactive marked');
  const lists=win.requests.filter(r=>r.method==='GET'&&r.path.endsWith('/location')&&r.store==='3');
  assert(lists.map(r=>r.area).sort().join()==='SALES_FLOOR,WAREHOUSE','one request per Area');
  // Click a Warehouse code: the existing Edit form opens and only that cell is highlighted.
  find('mgBody').querySelector('[data-row-select="51"]').click();
  await wait(()=>find('mgf_code_view')&&find('mgf_code_view').textContent==='W01-B02-C01','edit form');
  assert(find('mgBody').querySelectorAll('.selected').length===1&&find('mgBody').querySelector('[data-row-select="51"]').classList.contains('selected'),'selected cell');
  // Keyboard: Enter on a focused code opens it too.
  const a01=find('mgBody').querySelector('[data-row-select="42"]');a01.focus();a01.dispatchEvent(new win.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  await wait(()=>find('mgf_code_view').textContent==='A01-B01-C01','keyboard open');
  // Search and Status apply to both Areas (one request per Area with the same filters).
  const search=find('mgSearch');search.value='B02';search.dispatchEvent(new win.Event('input'));
  await wait(()=>cells().join('/')==='A03-B02-C01|W01-B02-C01','search both columns: '+cells().join('/'));
  const last=win.requests.filter(r=>r.method==='GET'&&r.path.endsWith('/location')).slice(-2);
  assert(last.every(r=>r.q==='B02')&&last.map(r=>r.area).sort().join()==='SALES_FLOOR,WAREHOUSE','same filter for both Areas');
  search.value='';search.dispatchEvent(new win.Event('input'));
  await wait(()=>cells().length===3,'search cleared');
  const status=find('mgActiveFilter');status.value='inactive';status.dispatchEvent(new win.Event('change'));
  await wait(()=>cells().join('/')==='A02-B01-C02|','inactive only: '+cells().join('/'));
  status.value='';status.dispatchEvent(new win.Event('change'));
  await wait(()=>cells().length===3,'status cleared');
  // Paging per column: page size 10 -> one page; Previous / Next stay as before.
  const size=find('mgPageSize');size.value='10';size.dispatchEvent(new win.Event('change'));
  await wait(()=>win.requests.filter(r=>r.size==='10').length>=2,'page size');
  assert(find('mgPageLabel').textContent==='1 / 1'&&find('mgPrev').disabled&&find('mgNext').disabled,'pagination kept');
  assert(noHorizontalScroll(),'no horizontal scroll');
""")

    def test_location_guide_button_in_every_mode_opens_the_common_modal(self):
        self.run_page(1400, r"""
  const shown=()=>[...find('mgPageActions').children].filter(el=>el.offsetParent!==null).map(el=>el.id||el.querySelector('.btn').id).join(',');
  const modal=find('mgExampleModal'),img=modal.querySelector('.modal-body img'),pan=modal.querySelector('.mg-example-scroll');
  const open=async(button,label)=>{button.click();await wait(()=>modal.classList.contains('show')&&img.complete&&img.naturalWidth>0,label);await tick();await tick();};
  const close=async()=>{modal.querySelector('.modal-footer [data-bs-dismiss="modal"]').click();await wait(()=>!modal.classList.contains('show')&&!doc.querySelector('.modal-backdrop'),'closed');};
  await wait(()=>visible('mgLocationGuide'),'first Store');
  const store=find('mgStore');store.value='2';store.dispatchEvent(new win.Event('change'));
  await wait(()=>find('mgBody').querySelector('[data-row-select="31"]'),'Store 2 list');
  // Header button: Book icon + text, next to Export / Add, in view / create / edit modes.
  const btn=find('mgGuideBtn');
  assert(btn.querySelector('svg.mg-guide-icon')&&btn.textContent.trim()==='Location Guide'&&btn.classList.contains('btn-outline-secondary'),'Book icon + Location Guide');
  assert(shown()==='mgExportBtn,mgGuideBtn,mgAddBtn','view: '+shown());
  // The common Bootstrap modal: title, the built-in guide SVG, fitted (no scrolling needed), Close.
  await open(btn,'guide modal (view)');
  assert(find('mgExampleTitle').textContent==='Location Guide'&&/location-master-guide\.svg|^data:image\/svg/.test(img.getAttribute('src')),'title + guide image');
  assert(modal.querySelector('.modal-header').textContent.includes('S01-B01-C01')&&modal.querySelector('.modal-header').textContent.includes('W01-R02-C01')
    &&img.alt.includes('S01-B01-C01')&&img.alt.includes('W01-R02-C01'),'both code structures named');
  const box=img.getBoundingClientRect(),view=pan.getBoundingClientRect();
  assert(box.width>=900&&box.left>=view.left-1&&box.right<=view.right+1&&box.bottom<=view.bottom+1&&pan.scrollWidth<=pan.clientWidth+1&&pan.scrollHeight<=pan.clientHeight+1
    &&modal.querySelector('.modal-body').scrollHeight<=modal.querySelector('.modal-body').clientHeight+1,
    'fits the screen: '+JSON.stringify([box.width,box.height,view.width,view.height]));
  assert(!visible('mgExampleScrollHint')&&find('mgExampleZoom').textContent==='Zoom In','fit by default');
  // Zoom In: full size, scrolls inside the modal (nothing cut); Fit to Screen returns; reopening starts fitted.
  find('mgExampleZoom').click();await tick();
  assert(Math.round(img.getBoundingClientRect().width)===1600&&pan.scrollWidth>pan.clientWidth&&visible('mgExampleScrollHint')&&find('mgExampleZoom').getAttribute('aria-pressed')==='true','zoomed in');
  pan.scrollLeft=pan.scrollWidth;await tick();
  assert(Math.abs(img.getBoundingClientRect().right-pan.getBoundingClientRect().right)<=pan.offsetWidth-pan.clientWidth+2,'right edge reachable');
  find('mgExampleZoom').click();await tick();
  assert(pan.scrollWidth<=pan.clientWidth+1&&find('mgExampleZoom').textContent==='Zoom In','fit again');
  find('mgExampleZoom').click();await tick();
  await close();
  // Create mode: still shown; View Example in the form opens the same modal (fitted again).
  find('mgAddBtn').click();
  await wait(()=>f('zone'),'create form');
  assert(shown()==='mgExportBtn,mgGuideBtn,mgCancelBtn,mgSave','create: '+shown());
  await open(find('mgFields').querySelector('[data-location-example]'),'guide modal (View Example)');
  assert(doc.querySelectorAll('#mgExampleModal').length===1&&!pan.classList.contains('zoomed'),'same modal, fitted');
  await close();
  // Edit mode: still shown and working.
  find('mgBody').querySelector('[data-row-select="32"]').click();
  await wait(()=>find('mgf_code_view'),'edit form');
  assert(shown()==='mgExportBtn,mgGuideBtn,mgAddBtn,mgMdToggle,mgMdDelete,mgCancelBtn,mgSave','edit: '+shown());
  await open(btn,'guide modal (edit)');
  await close();
  assert(find('mgf_code_view').textContent==='B01'&&!win.requests.some(r=>r.method==='PUT'||r.method==='DELETE'),'form kept, nothing saved');
  assert(noHorizontalScroll(),'no horizontal scroll at 1400px');
""")

    def test_layout_at_390px(self):
        self.run_page(390, r"""
  await wait(()=>visible('mgLocationGuide'),'guide');
  const img=find('mgLocationGuide').querySelector('img');
  await wait(()=>img.complete&&img.naturalWidth>0,'guide image');
  assert(noHorizontalScroll(),'no horizontal scroll at 390px (guide)');
  {const store=find('mgStore');store.value='3';store.dispatchEvent(new win.Event('change'));
   await wait(()=>find('mgBody').querySelector('[data-row-select="52"]'),'Store 3 at 390px');
   const long=find('mgBody').querySelector('[data-row-select="52"]'),code=long.querySelector('.mg-code');
   assert(long.scrollWidth<=long.clientWidth+1&&code.getBoundingClientRect().right<=long.getBoundingClientRect().right+1,'long code wraps inside its cell');
   assert(getComputedStyle(code).textOverflow!=='ellipsis'&&code.textContent==='COLD-ROOM-FREEZER-RACK-LEVEL-9','full code shown');
   assert(noHorizontalScroll(),'no horizontal scroll at 390px (two columns)');
   store.value='1';store.dispatchEvent(new win.Event('change'));
   await wait(()=>visible('mgLocationGuide'),'back to the empty Store');}
  assert(img.getBoundingClientRect().right<=doc.documentElement.clientWidth+1,'guide image inside the viewport');
  find('mgGuideAdd').click();
  await wait(()=>f('zone')&&find('mgRangeCodes').textContent==='S01-B01-C01','create form');
  assert(noHorizontalScroll(),'no horizontal scroll at 390px (form)');
  {const box=id=>find(id).getBoundingClientRect();
   assert(Math.abs(box('mgf_bay_from').top-box('mgf_bay_to').top)<2&&box('mgf_bay_to').right<=doc.documentElement.clientWidth+1,'From / To side by side at 390px');
   assert(box('mgRangePreview').right<=doc.documentElement.clientWidth+1,'preview inside the viewport');}
  // Page Header at 390px: Cancel and Save stay visible (pinned); Add is hidden while creating; no Type / Parent.
  const on=id=>find(id).offsetParent!==null&&find(id).getBoundingClientRect().right<=doc.documentElement.clientWidth+1;
  assert(on('mgGuideBtn')&&on('mgCancelBtn')&&on('mgSave')&&find('mgSave').textContent==='Save'&&find('mgAddBtn').offsetParent===null,'390px create actions');
  assert(getComputedStyle(find('mgGuideBtn').querySelector('.mg-guide-text')).display==='none'&&find('mgGuideBtn').getAttribute('aria-label')==='Location Guide','390px: Book icon only, named for screen readers');
  assert(!f('location_type')&&!f('parent_id'),'no Type / Parent at 390px');
  find('mgFields').querySelector('[data-location-example]').click();
  await wait(()=>find('mgExampleModal').classList.contains('show'),'modal');
  const big=find('mgExampleModal').querySelector('.modal-body img');
  await wait(()=>big.complete&&big.naturalWidth>0,'modal image');
  // 390px: the whole guide fits the screen; Zoom In shows it at full size and scrolls inside the modal (never cut).
  await tick();await tick();
  const pan=find('mgExampleModal').querySelector('.mg-example-scroll'),bb=big.getBoundingClientRect();
  assert(bb.left>=0&&bb.right<=doc.documentElement.clientWidth+1&&pan.scrollWidth<=pan.clientWidth+1&&!visible('mgExampleScrollHint'),'fitted at 390px: '+JSON.stringify([bb.left,bb.right]));
  find('mgExampleZoom').click();await tick();
  assert(Math.round(big.getBoundingClientRect().width)===1600&&pan.scrollWidth>pan.clientWidth&&visible('mgExampleScrollHint'),'zoomed in at 390px');
  assert(noHorizontalScroll(),'no horizontal page scroll at 390px (modal)');
  const closeBtn=find('mgExampleModal').querySelector('.modal-footer [data-bs-dismiss="modal"]').getBoundingClientRect();
  assert(closeBtn.width>0&&closeBtn.right<=doc.documentElement.clientWidth+1,'Close button reachable at 390px');
  find('mgExampleModal').querySelector('[data-bs-dismiss="modal"]').click();
  await wait(()=>!find('mgExampleModal').classList.contains('show'),'modal closed');
  find('mgCancelBtn').click();
  await wait(()=>!f('zone')&&visible('mgLocationGuide'),'Cancel at 390px: back to the guide');
  assert(noHorizontalScroll(),'no horizontal scroll at 390px (after Cancel)');
""")


if __name__ == "__main__":
    unittest.main()
