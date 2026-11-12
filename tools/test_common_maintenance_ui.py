"""Common Maintenance screen (browser, mocked API): Group dropdown + Code grid + Code form on the existing
Master Group split layout, the view-only group mechanism (COLOR mocked view-only here; it is editable since
COLOR step 5-2, see test_color_switch), UNIT CRUD rules (fixed Code, EA locked, in-use deactivate warning)
and the Value input switching by Value Type (TEXT / NUMBER decimal / CURRENCY money / COLOR)."""
import json
import re
import unittest

from tools import test_image_components as browser
from tools.test_master_group_ui import render_list, toast_head


class CommonMaintenanceTemplateTests(unittest.TestCase):
    def test_page_reuses_split_layout_with_group_selector(self):
        html = render_list("common_code", "Common Maintenance")
        self.assertIn('class="mg-md-workspace"', html)
        self.assertIn('id="mgGroup"', html)
        self.assertIn('id="mgAddBtn">Add Code</button>', html)
        self.assertIn("COMMON MAINTENANCE INFORMATION", html)
        self.assertNotIn('id="mgModal"', html)
        self.assertNotIn('id="mgGroup"', render_list("color", "Color"))  # other screens unchanged
        self.assertIn('href="/platform/master-group/common-maintenance"', html)  # Master Group menu entry
        self.assertNotIn('href="/platform/master-group/color"', html)  # COLOR step 5-2: the Color menu is gone


class CommonMaintenanceBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_group_switch_view_only_color_unit_rules_and_value_inputs(self):
        js = {name: (browser.ROOT / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")
              for name in ("money", "option-group", "master-detail", "master_group")}
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8") + "\n" + "\n".join(
            (browser.ROOT / "static" / "css" / name).read_text(encoding="utf-8")
            for name in ("brand.css", "awsum-list.css", "awsum-master-detail.css"))
        mock = r"""
window.requests=[];window.confirms=[];window.confirmAnswer=false;
window.bootstrap={Modal:class{show(){}hide(){}}};
window.confirm=()=>true;
window.AwsumConfirm={show:async(o)=>{confirms.push(o);return {confirmed:window.confirmAnswer};}};
const group=(code,extra)=>({code,name:code,code_mode:'MANUAL',code_max_length:30,value_type:null,value_label:null,
  value_required:false,value_scale:null,value_min:null,value_max:null,text_max_length:null,read_only:false,read_only_message:null,...extra});
const groups=[
  group('COLOR',{name:'Color',code_mode:'AUTO',code_prefix:'CLR',code_max_length:20,value_type:'COLOR',value_label:'Swatch HEX',
    read_only:true,read_only_message:'COLOR is read-only here until the Color switch. Edit colors in Master Group > Color.'}),
  group('UNIT',{name:'Unit'}),
  group('NUM',{value_type:'NUMBER',value_label:'Tare Weight',value_scale:3,value_min:'0'}),
  group('CUR',{value_type:'CURRENCY',value_label:'Deposit',value_scale:2,value_min:'0'}),
  group('TXT',{value_type:'TEXT',value_label:'Label',text_max_length:20}),
];
const rows={
  COLOR:[{id:1,code:'CLR00001',name:'Black',group:'COLOR',value_type:'COLOR',value:'#000000',legacy_id:1,display_order:10,active:true,in_use:true,read_only:true,protected:false,active_locked:false,system:false,notes:null,description:null}],
  UNIT:[{id:21,code:'EA',name:'Each',group:'UNIT',value_type:null,value:null,display_order:10,active:true,in_use:true,read_only:false,protected:true,active_locked:true,system:true,notes:null,description:null},
        {id:22,code:'BOX',name:'Box',group:'UNIT',value_type:null,value:null,display_order:30,active:true,in_use:true,read_only:false,protected:false,active_locked:false,system:false,notes:null,description:null}],
  NUM:[],CUR:[{id:41,code:'BTL',name:'Bottle',group:'CUR',value_type:'CURRENCY',value:'2500.50',display_order:10,active:true,in_use:false,read_only:false,protected:false,active_locked:false,system:false,notes:null,description:null}],TXT:[],
};
window.fetch=async(path,options={})=>{
  const url=new URL(path,'http://127.0.0.1'),method=options.method||'GET';
  const payload=options.body?JSON.parse(options.body):null;
  requests.push({path:url.pathname,method,payload,group:url.searchParams.get('group')});
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({common_groups:groups,colors:[]}));
  const list=rows[url.searchParams.get('group')]||[];
  if(method==='DELETE')return new Response(JSON.stringify({message:'Deleted.'}));
  if(method==='PUT'||method==='POST'){
    const item={...(list.find(r=>url.pathname.endsWith('/'+r.id))||{id:99,group:url.searchParams.get('group')}),...payload};
    if(method==='POST')item.code=(payload.code||'NEW').toUpperCase();
    return new Response(JSON.stringify({item}),{status:method==='POST'?201:200});
  }
  return new Response(JSON.stringify({items:list,total:list.length,page:1,pages:1,sort:'display_order',direction:'asc'}));
};
"""
        html = render_list("common_code", "Common Maintenance")
        html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
        html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", toast_head(), 1)
        html = html.replace("</head>", f"<style>{css}</style></head>")
        html = html.replace("</body>", f"<script>{mock}\n{js['money']}\n{js['option-group']}\n{js['master-detail']}\n{js['master_group']}</script></body>")
        script = f"const html={json.dumps(html).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
const wait=async(check,label)=>{for(let i=0;i<100;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1280px;height:800px';frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id),f=name=>find('mgf_'+name);
  const visible=id=>{const el=find(id);return !!el&&!el.classList.contains('d-none')&&el.offsetParent!==null;};
  const heads=()=>[...find('mgHead').querySelectorAll('th')].map(th=>th.textContent.trim()).join('|');
  const select=async(code)=>{const g=find('mgGroup');g.value=code;g.dispatchEvent(new win.Event('change'));
    await wait(()=>win.requests.some(r=>r.method==='GET'&&r.group===code&&r.path.endsWith('/common_code')),'list '+code);await tick();};
  const openRow=async(id)=>{find('mgBody').querySelector(`tr[data-row-select="${id}"]`).click();await wait(()=>f('name'),'form '+id);};
  await wait(()=>find('mgBody').querySelector('tr[data-row-select="1"]'),'COLOR list');

  // Group dropdown: every Common group from /options; COLOR first (mocked view only: the read_only mechanism).
  assert([...find('mgGroup').options].map(o=>o.value).join()==='COLOR,UNIT,NUM,CUR,TXT','group options');
  assert(find('mgGroup').options[0].textContent.includes('(view only)'),'COLOR marked view only');
  assert(win.requests.filter(r=>r.path.endsWith('/common_code')).every(r=>r.group==='COLOR'),'list names the group');
  assert(heads()==='Code|Name|Swatch HEX|Status','COLOR columns: '+heads());
  assert(find('mgBody').querySelector('.mg-color-swatch'),'swatch in grid');
  assert(find('mgAddBtn').disabled,'COLOR: Add disabled');
  assert(/read-only here/.test(find('mgGroupNote').textContent)&&visible('mgGroupNote'),'read-only note');
  await openRow(1);
  assert([...find('mgFields').querySelectorAll('input,select,textarea')].every(el=>el.disabled),'COLOR form disabled');
  assert(find('mgReadOnlyNote')&&find('mgSave').disabled,'read-only note, Save disabled');
  assert(!visible('mgMdToggle')&&!visible('mgMdDelete')&&!visible('mgSave'),'no record actions for COLOR');
  assert(f('value').value==='#000000'&&find('mgValueSwatch').querySelector('.mg-color-swatch'),'HEX + swatch preview');

  // UNIT: no Value column / field; Add with a typed upper-case Code; Code fixed after save.
  await select('UNIT');
  assert(!f('name'),'form reset on group change');
  assert(heads()==='Code|Name|Status','UNIT columns: '+heads());
  assert(!find('mgAddBtn').disabled,'UNIT: Add enabled');
  find('mgAddBtn').click();await wait(()=>f('code'),'create form');
  assert(f('code').hasAttribute('data-awsum-code')&&!f('value'),'code input, no value field');
  f('code').focus();doc.execCommand('insertText',false,'ctn');
  assert(f('code').value==='CTN','upper case while typing');
  f('name').value='Carton';
  find('mgSave').click();
  await wait(()=>win.requests.some(r=>r.method==='POST'),'create');
  const created=win.requests.find(r=>r.method==='POST');
  assert(created.group==='UNIT'&&created.payload.code==='CTN'&&created.payload.name==='Carton'&&!('value' in created.payload),'create payload '+JSON.stringify(created));
  await wait(()=>!f('code')&&/Code\s*CTN\s*\(cannot be changed\)/.test(find('mgFields').textContent),'code fixed after save');

  // EA (default Unit): status locked, no Deactivate; Delete shown disabled with the reason.
  await openRow(21);
  assert(f('active').disabled&&!visible('mgMdToggle'),'EA protected');
  assert(visible('mgMdDelete')&&find('mgMdDelete').disabled&&/protected default code/.test(find('mgMdDelete').title),'EA: Delete disabled with reason');
  // In-use code: Delete disabled with the reason; Deactivate asks first (Common Confirm); Cancel sends nothing, OK deactivates.
  await openRow(22);
  assert(visible('mgMdToggle')&&visible('mgMdDelete'),'BOX actions');
  assert(find('mgMdDelete').disabled&&/in use/.test(find('mgMdDelete').title),'BOX in use: Delete disabled with reason');
  const puts=()=>win.requests.filter(r=>r.method==='PUT').length;
  const before=puts();
  find('mgMdToggle').click();await wait(()=>win.confirms.length===1,'confirm');
  await tick();
  assert(puts()===before&&win.confirms[0].variant==='warning'&&/used by MD/.test(win.confirms[0].message),'cancel keeps it active');
  win.confirmAnswer=true;find('mgMdToggle').click();
  await wait(()=>puts()===before+1,'deactivate');
  const put=win.requests.filter(r=>r.method==='PUT').pop();
  assert(put.group==='UNIT'&&put.payload.active===false&&put.payload.name==='Box','deactivate payload');

  // Value input follows the group's Value Type.
  await select('NUM');
  assert(heads()==='Code|Name|Tare Weight|Status','NUMBER value column');
  find('mgAddBtn').click();await wait(()=>f('value'),'NUM form');
  assert(f('value').hasAttribute('data-awsum-decimal')&&f('value').dataset.decimalScale==='3'&&!f('value').hasAttribute('data-awsum-money'),'decimal input');
  f('value').focus();doc.execCommand('insertText',false,'1,2a3.45678');
  assert(f('value').value==='123.456','decimal typing: '+f('value').value);
  await select('CUR');
  assert(find('mgBody').textContent.includes('$2,500.50'),'currency grid uses the common money format');
  await openRow(41);
  assert(f('value').hasAttribute('data-awsum-money')&&f('value').value==='2,500.50','money input');
  find('mgSave').click();
  await wait(()=>win.requests.filter(r=>r.method==='PUT').length===before+2,'save currency');
  assert(win.requests.filter(r=>r.method==='PUT').pop().payload.value==='2500.50','money payload is comma-free');
  // A code not in use: Delete enabled; Common Confirm (Group / Code / Name) first, Cancel sends nothing, OK deletes.
  await openRow(41);
  assert(visible('mgMdDelete')&&!find('mgMdDelete').disabled&&!find('mgMdDelete').title,'BTL: Delete enabled');
  const deletes=()=>win.requests.filter(r=>r.method==='DELETE').length;
  win.confirmAnswer=false;const asked=win.confirms.length;
  find('mgMdDelete').click();await wait(()=>win.confirms.length===asked+1,'delete confirm');await tick();
  const ask=win.confirms[win.confirms.length-1];
  assert(deletes()===0&&ask.title==='Delete Code'&&ask.variant==='danger'&&JSON.stringify(ask.details)===JSON.stringify([['Group','CUR'],['Code','BTL'],['Name','Bottle']]),'cancel sends nothing: '+JSON.stringify(ask));
  win.confirmAnswer=true;find('mgMdDelete').click();
  await wait(()=>deletes()===1,'delete');
  const del=win.requests.find(r=>r.method==='DELETE');
  assert(del.group==='CUR'&&del.path.endsWith('/common_code/41'),'delete request '+JSON.stringify(del));
  await select('TXT');
  find('mgAddBtn').click();await wait(()=>f('value'),'TXT form');
  assert(f('value').maxLength===20&&!f('value').hasAttribute('data-awsum-decimal'),'text input');
  assert(/Label/.test(find('mgFields').textContent),'value label');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)



class MdUnitSelectorBrowserTests(unittest.TestCase):
    """Step 4: MD Master and MD Detail use the same Common UNIT dropdown ("EA · Each"), active codes only for new
    choices; a saved inactive / unlisted Unit stays shown and selected; new records default to EA."""
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_md_master_and_detail_share_the_unit_dropdown(self):
        from tools.test_master_group_ui import MasterGroupListBrowserTests
        html = MasterGroupListBrowserTests.md_page_html(self)
        # Saved values from before step 4: Default Detail = unlisted BOTTLE, Detail 2 = inactive BX.
        html = html.replace("barcode:'BASE-1',unit:'EA'", "barcode:'BASE-1',unit:'BOTTLE'", 1)
        html = html.replace("barcode:'BASE-2',unit:'CS'", "barcode:'BASE-2',unit:'BX'", 1)
        assert "unit:'BOTTLE'" in html and "unit:'BX'" in html
        script = f"const html={json.dumps(html).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1400px;height:800px';frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id);
  const labels=el=>[...el.options].map(o=>o.textContent).join('|');
  const ACTIVE='EA · Each|PK · Pack|CS · Case';
  await wait(()=>find('mgBody').querySelector('[data-row-select="7"]'),'list');
  find('mgBody').querySelector('[data-row-select="7"]').click();
  await wait(()=>find('mgf_unit')&&find('mgDetailBody').textContent.includes('BASE-2')&&find('mgf_unit').value==='BOTTLE','MD + details');
  const mdUnit=find('mgf_unit');
  assert(mdUnit.tagName==='SELECT','MD Unit is a dropdown');
  assert(labels(mdUnit)===ACTIVE+'|BOTTLE (not in Unit list)','MD: active codes + saved unlisted value: '+labels(mdUnit));
  // MD Detail editor: the same selector logic.
  find('mgDetailBody').querySelector('[data-detail-act="edit"][data-id="2"]').click();
  await wait(()=>!find('mgDetailEditor').classList.contains('d-none'),'detail editor');
  const detailUnit=find('mgDetailUnit');
  assert(detailUnit.tagName==='SELECT'&&detailUnit.value==='BX','Detail Unit dropdown keeps the saved inactive code');
  assert(labels(detailUnit)==='EA · Each|PK · Pack|CS · Case|BX · Old Box (Inactive)','Detail: '+labels(detailUnit));
  assert(doc.activeElement===detailUnit,'Edit Detail keeps focusing Unit');
  find('mgDetailAdd').click();
  await wait(()=>find('mgDetailUnit').value==='EA','new detail default');
  assert(labels(find('mgDetailUnit'))===ACTIVE,'new Detail: active codes only');
  assert(doc.activeElement===find('mgDetailBarcode'),'Add Detail starts at Barcode');
  find('mgDetailUnit').value='PK';find('mgDetailPrice').value='3.00';
  find('mgDetailSave').click();
  await wait(()=>win.requests.some(r=>r.method==='POST'&&/\/details$/.test(r.path)),'detail create');
  assert(win.requests.find(r=>r.method==='POST'&&/\/details$/.test(r.path)).payload.unit==='PK','Detail payload: unit code');
  // Saving the MD unchanged keeps the unlisted value (same API field, server allows the unchanged value).
  await wait(()=>find('mgDetailEditor').classList.contains('d-none'),'editor closed');
  find('mgSave').click();
  await wait(()=>win.requests.some(r=>r.method==='PUT'&&/\/md\/7(\/with-image)?$/.test(r.path)),'md save');
  const mdPut=win.requests.find(r=>r.method==='PUT'&&/\/md\/7(\/with-image)?$/.test(r.path));
  const unitSent=mdPut.payload?mdPut.payload.unit:(mdPut.form||{}).unit;
  assert(unitSent==='BOTTLE','MD save keeps BOTTLE: '+JSON.stringify(mdPut).slice(0,300));
  // New MD: default EA, active codes only.
  find('mgAddBtn').click();
  await wait(()=>find('mgf_unit')&&!find('mgf_record_code').value,'new MD form');
  assert(find('mgf_unit').value==='EA'&&labels(find('mgf_unit'))===ACTIVE,'new MD defaults to EA: '+labels(find('mgf_unit')));
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
