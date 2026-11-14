"""Focused common Master modal layout and existing handler regression checks."""
import json
import re
import unittest

from tools import test_image_components as browser
from tools.test_platform_admin_ui import render_admin


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class MasterFormModalTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_shared_image_layout_keeps_fields_left_and_uploader_top_right(self):
        source = (browser.ROOT / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        functions = "\n".join(re.search(
            rf"  (?:async )?function {name}\([^\n]*\) \{{.*?\n  \}}", source, re.DOTALL,
        ).group() for name in (
            "createBrandLogoUploadPanel", "createImageFormLayout", "createMerchantTabs", "renderForm",
        ))
        css = "\n".join((browser.ROOT / path).read_text(encoding="utf-8") for path in (
            r"static\bootstrap\css\bootstrap.min.css", r"static\css\platform-modal.css",
            r"static\components\image-uploader\image-uploader.css",
        ))
        pages = []
        for module in ("agency", "merchant", "store"):
            html = render_admin("master_management.html", "master", module)
            html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", f"<style>{css}</style></head>")
            pages.append({"module": module, "html": html})
        setup = r"""
const activeModule=MODULE,isListCentricModule=true;
const masterFormModal=document.getElementById('masterFormModal'),masterForm=null;
const fields=Array.from({length:6},(_,i)=>({name:'input'+i}));
fields.splice(4,0,{type:'section',name:'contact',label:'Primary Contact'});
if(activeModule==='agency')fields.unshift({type:'section',name:'information',label:'Information'});
const config={fields};
if(activeModule!=='agency')config.tabs=[{id:'overview',title:'Basic Information',fields:fields.map(f=>f.name)}];
const CONTACT_OWNER_TYPES={};
const noop=()=>{},bindUsDatePickers=noop,refreshMerchantLogoPreview=noop,refreshStoreLogoPreview=noop,
refreshContactManager=noop,initMerchantStorePanelHandlers=noop,loadMerchantStoresForSelectedMerchant=noop,
syncRegistrationEmailButtons=noop,bindRegistrationEmailButton=noop,refreshOrgLogoPreview=noop,bindScopedStoreSelect=noop,applyEditFieldRules=noop;
const createIdField=()=>{const el=document.createElement('input');el.type='hidden';return el;};
const createContactManagerPanel=()=>{const el=document.createElement('div');el.className='col-12';el.textContent='Contact manager';return el;};
const createField=field=>{
 if(field.type==='section'){
  const section=document.createElement('div');section.className='col-12';
  section.innerHTML='<div class="master-form-section-title">'+field.label+'</div>';return section;
 }
 const el=document.createElement('div');el.className='col-12 col-md-6';
 el.innerHTML='<label class="form-label">Information</label><input class="form-control" id="field_'+field.name+'">';
 return el;
};
"""
        setup += functions + r"""
renderForm();
const modal=document.getElementById('masterEditModal');
modal.style.display='block';
modal.classList.add('show','agency-modal');
if(activeModule!=='agency')modal.classList.add(activeModule+'-modal');
window.imageLayouts=[masterFormModal.querySelector('.awsum-image-form')];
if(activeModule==='merchant'){
 const linked=document.getElementById('merchantStoreModal');linked.style.display='block';linked.classList.add('show');
 window.imageLayouts.push(linked.querySelector('.awsum-image-form'));
}
"""
        script = "const pages=" + json.dumps(pages).replace("</", "<\\/") + ";const setup=" + json.dumps(setup) + ";" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
(async()=>{
 for(const item of pages){
  const frame=document.createElement('iframe');frame.srcdoc=item.html;
  const loaded=new Promise(resolve=>frame.onload=resolve);document.body.append(frame);await loaded;
  const win=frame.contentWindow,doc=frame.contentDocument;
  win.eval('const MODULE='+JSON.stringify(item.module)+';\n'+setup);
  for(const layout of win.imageLayouts){
   assert(layout,'all image forms share layout');
   const target=layout.querySelector('.awsum-image-form-image > div');
   const uploader=AwsumImages.createUploader(target,{idPrefix:target.id+'Layout',label:'Logo',previewSize:120,fit:'contain'});
   uploader.reset(document.createElement('canvas').toDataURL(),true);
  }
  for(const width of [1440,1024,768,375,320]){
   frame.style.width=width+'px';frame.style.height='800px';await tick();
   for(const layout of win.imageLayouts){
    const fields=layout.querySelector('.awsum-image-form-fields'),image=layout.querySelector('.awsum-image-form-image');
    const left=fields.getBoundingClientRect(),right=image.getBoundingClientRect();
    assert(left.width>0&&right.width>0,'fields and image visible');
    assert(layout.scrollWidth<=layout.clientWidth+1,'layout fits viewport: '+JSON.stringify({module:item.module,width,scroll:layout.scrollWidth,client:layout.clientWidth,fields:left.toJSON(),image:right.toJSON()}));
    if(width>=768){
     assert(right.left>=left.right,'image stays right of information');
     assert(Math.abs(right.top-left.top)<2,'image does not push inputs down');
     assert(right.width<=154,'compact image column');
    }else{
     assert(right.bottom<=left.top+1,'mobile stacks image and fields');
    }
    if(layout.closest('#masterFormModal')){
     const full=layout.querySelector('.awsum-image-form-full');
     assert(full,'sections after Information have shared full-width layout');
     const rect=full.getBoundingClientRect();
     assert(rect.top>=Math.max(left.bottom,right.bottom),'later sections follow Information and image');
     assert(rect.width>left.width||width<768,'later sections reclaim image column width');
     assert(full.querySelector('.master-form-section-title').textContent==='Primary Contact','Primary Contact starts full-width area');
     assert(!fields.textContent.includes('Primary Contact'),'contact no longer constrained beside image');
    }
    assert([...image.querySelectorAll('button')].some(b=>b.textContent.includes('Replace')),'Replace retained');
    assert([...image.querySelectorAll('button')].some(b=>b.textContent.includes('Remove')),'Remove retained');
   }
  }
  frame.remove();
 }
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack}));
"""
        self.run_browser(script)

    def test_module_modes_scroll_and_existing_actions(self):
        source = (browser.ROOT / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        functions = "\n".join(re.search(
            rf"  (?:async )?function {name}\([^\n]*\) \{{.*?\n  \}}", source, re.DOTALL,
        ).group() for name in (
            "usesPremiumModal", "applyAgencyModalState", "showMasterModalError",
            "getMasterEditModalInstance", "openMasterEditModal", "closeMasterEditModal",
            "syncRegistrationEmailButtons", "contactOwner", "saveItem", "deleteItem",
        ))
        handlers = "\n".join(re.search(
            rf"  if \({name}\) \{{\n    {name}\.addEventListener.*?\n  \}}", source, re.DOTALL,
        ).group() for name in ("btnModalSave", "btnModalDelete"))
        css = "\n".join((browser.ROOT / path).read_text(encoding="utf-8") for path in (
            r"static\bootstrap\css\bootstrap.min.css", r"static\css\brand.css",
            r"static\css\platform-modal.css", r"static\css\awsum-list.css",
        ))
        bootstrap = (browser.ROOT / "static" / "bootstrap" / "js" / "bootstrap.bundle.min.js").read_text(encoding="utf-8")
        pages = []
        for module in ("agency", "merchant", "store"):
            html = render_admin("master_management.html", "master", module)
            html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            html = re.sub(r"<link\b[^>]*>", "", html).replace("</head>", f"<style>{css}</style></head>")
            pages.append({"module": module, "html": html})
        setup = r"""
const activeModule=MODULE,isListCentricModule=true,config={};
const activeLabel={singular:MODULE[0].toUpperCase()+MODULE.slice(1)};
const toastLabel=activeLabel.singular;
const masterEditModalElement=document.getElementById('masterEditModal');
const masterEditModalTitle=document.getElementById('masterEditModalTitle');
const masterFormModal=document.getElementById('masterFormModal');
const btnModalSave=document.getElementById('btnModalSave'),btnModalDelete=document.getElementById('btnModalDelete'),btnModalCancel=document.getElementById('btnModalCancel');
const selectedMasterId={value:''};let selectedOriginalRecord=null,agencyModalMode='create',lastSavedId=null;
const AGENCY_PLUS_ICON='',AGENCY_CHECK_ICON='',FIELD_ICONS={building:'',store:''};
const registrationPasswords=new Map(),pendingContactActivations=new Set(),noop=()=>{};
const CONTACT_OWNER_TYPES={agency:'AGENCY',merchant:'MERCHANT',store:'STORE'};
// The Common modal path (inlineMasterEditor = null): Store and the other list-centric modules edit in this modal.
// Agency / Merchant now edit in the inline Master-Detail panel (MASTER_DETAIL), which this test does not cover.
const inlineMasterEditor=null;
let validationErrors=[],confirmDelete=true,requests=[];
let savingStoreImage=false;
const getStoreLogoUploader=()=>null;
const getOrganizationLogoUploader=()=>null;
const isFormDirty=()=>true,getFormData=()=>({name:'Existing form data'}),
validateAgencyForm=()=>validationErrors,validateMerchantForm=()=>validationErrors,validateStoreForm=()=>validationErrors,
jumpToStoreErrorTab=noop,loadList=async()=>{},setBaselineSnapshot=noop,updateListCentricActionButtons=noop,notifyStorageWarning=noop,
getItemUrl=id=>'/existing/'+MODULE+(id?'/'+id:''),parseApiError=text=>text,
showCenteredConfirm=async()=>confirmDelete,clearForm=()=>{selectedMasterId.value='';};
window.fetch=async(path,options)=>{requests.push({path,method:options.method,body:options.body});return new Response(JSON.stringify({id:1}));};
window.alert=message=>{throw Error('Unexpected alert: '+message);};
// Save / Delete results go through the Common Toast: success is expected, a warning / error is not.
window.AwsumToast={success:noop,info:noop,done:noop,message:(action,label)=>label+' '+action+'.',
  warning:message=>{throw Error('Unexpected warning: '+message);},error:message=>{throw Error('Unexpected error: '+message);}};
"""
        setup += functions + "\n" + handlers + r"""
window.probe={
  open(mode){
    selectedMasterId.value=mode==='edit'?'1':'';
    selectedOriginalRecord={has_agency_admin:true};
    masterFormModal.innerHTML='<div style="height:1800px">Existing module form content</div>';
    openMasterEditModal(mode);
    if(activeModule==='agency')syncRegistrationEmailButtons();
  },
  errors(value){validationErrors=value;},confirm(value){confirmDelete=value;},
  requests,select(){selectedMasterId.value='1';}
};
"""
        encoded = lambda value: json.dumps(value).replace("</", "<\\/")
        script = f"const pages={encoded(pages)},bootstrapCode={encoded(bootstrap)},setup={encoded(setup)};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
(async()=>{
 for(const item of pages){
  const frame=document.createElement('iframe');frame.style.border='0';frame.srcdoc=item.html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const win=frame.contentWindow,doc=frame.contentDocument;win.eval(bootstrapCode);
  win.eval('const MODULE='+JSON.stringify(item.module)+';\n'+setup);
  const modal=doc.getElementById('masterEditModal'),body=modal.querySelector('.modal-body'),footer=modal.querySelector('.modal-footer');
  modal.classList.remove('fade');
  assert(modal.querySelectorAll('.modal-footer').length===1,'one shared footer');
  for(const width of [1440,768,375,320]){
   const height=width===320?568:width===375?667:800;
   frame.style.width=width+'px';frame.style.height=height+'px';
   for(const mode of ['create','edit']){
    win.probe.open(mode);await tick();
    assert(doc.getElementById('masterEditModalTitle').textContent===(mode==='create'?'Add ':'Edit ')+item.module[0].toUpperCase()+item.module.slice(1),'title preserved');
    assert(doc.getElementById('btnModalSave').textContent===(mode==='create'?'Create '+item.module[0].toUpperCase()+item.module.slice(1):'Save Changes'),'save label');
    const del=doc.getElementById('btnModalDelete');
    assert((win.getComputedStyle(del).display!=='none')===(item.module==='agency'&&mode==='edit'),'existing Delete availability preserved');
    const email=doc.getElementById('btnAgencyRegistrationEmail');
    if(item.module==='agency')assert((email.getBoundingClientRect().width>0)===(mode==='edit'),'email is Edit-only');
    else assert(!email,'no Agency optional action in other modules');
    const initial=footer.getBoundingClientRect(),header=modal.querySelector('.modal-header').getBoundingClientRect();
    assert(initial.bottom<=height&&initial.left>=0&&initial.right<=width,'footer fits viewport');
    assert(footer.scrollWidth<=footer.clientWidth+1,'footer buttons not clipped');
    for(const fraction of [0,0.5,1]){
     body.scrollTop=(body.scrollHeight-body.clientHeight)*fraction;await tick();
     assert(Math.abs(footer.getBoundingClientRect().top-initial.top)<1,'footer fixed during body scroll');
     assert(Math.abs(modal.querySelector('.modal-header').getBoundingClientRect().top-header.top)<1,'header fixed during scroll');
     assert(doc.getElementById('btnModalSave').getBoundingClientRect().bottom<=initial.bottom,'save remains visible');
     const buttons=[...footer.querySelectorAll('.awsum-modal-primary-actions button')].filter(el=>win.getComputedStyle(el).display!=='none');
     assert(buttons.every((el,i)=>!i||el.getBoundingClientRect().left>buttons[i-1].getBoundingClientRect().left),'Cancel Delete Save order never changes');
    }
    doc.getElementById('btnModalCancel').click();await tick();
    assert(!modal.classList.contains('show'),'Cancel closes through existing Bootstrap dismiss');
   }
  }
  frame.style.width='1440px';
  for(const mode of ['create','edit']){
   win.probe.open(mode);await tick();
   win.probe.errors(['Validation test']);
   const before=win.probe.requests.length;
   doc.getElementById('btnModalSave').click();await tick();
   assert(win.probe.requests.length===before,'validation prevents API write');
   assert(modal.classList.contains('show'),'validation keeps modal open');
   win.probe.errors([]);
   doc.getElementById('btnModalSave').click();await tick();
   assert(win.probe.requests.at(-1).method===(mode==='create'?'POST':'PUT'),'Create/Edit existing API method preserved');
   assert(!modal.classList.contains('show'),'successful save closes');
  }
  if(item.module==='agency'){
   win.probe.open('edit');await tick();win.probe.confirm(false);
   let before=win.probe.requests.length;doc.getElementById('btnModalDelete').click();await tick();
   assert(win.probe.requests.length===before&&modal.classList.contains('show'),'cancel delete confirmation has no write');
   win.probe.confirm(true);doc.getElementById('btnModalDelete').click();await tick();
   assert(win.probe.requests.at(-1).method==='DELETE'&&!modal.classList.contains('show'),'confirmed Delete preserved');
  }
  frame.remove();
 }
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0,modules:3,widths:4,scrollPositions:3});
})().catch(error=>document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack}));
"""
        self.run_browser(script)
