"""Focused DOM regression checks for the reused License/Registration email dialog."""
import json
import re
import unittest

from jinja2 import Environment, FileSystemLoader

from tools import test_image_components as browser


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class AgencyActivationBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_registration_reuses_email_preview_recipient_edit_and_retry(self):
        component = (browser.ROOT / "static" / "js" / "store_devices_panel.js").read_text(encoding="utf-8")
        template = (browser.ROOT / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        function = re.search(r"  async function openAgencyRegistrationEmail\(.*?\n  }\n", template, re.DOTALL).group()
        script = (
            "window.bootstrap={Modal:class{show(){}dispose(){}}};\n"
            f"(0,eval)({json.dumps(component)});\n"
            "const apiBase='/platform';const parseApiError=text=>JSON.parse(text).detail;const masterEditModalElement=null;const agencyRegistrationPasswords=new Map();\n"
            f"{function}\n"
        )
        script += r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const find=id=>document.getElementById(id);
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
const wait=async(check)=>{for(let n=0;n<100;n++){if(check())return;await tick();}throw Error('Email dialog timed out');};
let requests=[],sends=0,failSend=true;
window.fetch=async(path,options)=>{
  const payload=JSON.parse(options.body);requests.push({path,payload});
  if(!path.endsWith('/preview')){
    sends++;
    if(failSend)return new Response(JSON.stringify({detail:'SMTP unavailable'}),{status:503});
    return new Response(JSON.stringify({accepted:true,recipient:payload.recipient,message_id:'mock-message'}));
  }
  return new Response(JSON.stringify({html:'<p>Registration completed</p>',subject:'Registration',
    recipient:payload.recipient||'primary@example.test',message:'Registration completed\nLogin Email: primary@example.test\nInitial Password: Awsum@5858'}));
};
(async()=>{
  await openAgencyRegistrationEmail(1);
  await wait(()=>!find('sdEmailSend').disabled);
  assert(find('sdEmailTo').value==='primary@example.test','default To Email is Primary Contact');
  assert(find('sdEmailSubject').readOnly&&find('sdEmailMessage').readOnly,'required content cannot be removed');
  assert(sends===0,'preview never sends');
  find('sdEmailTo').value='alternate@example.test';
  find('sdEmailTo').dispatchEvent(new Event('input'));
  assert(find('sdPvTo').textContent==='alternate@example.test','edited recipient displayed');
  assert(find('sdEmailSend').disabled,'wait for updated preview');
  await wait(()=>!find('sdEmailSend').disabled);
  find('sdEmailSend').click();find('sdEmailSend').click();
  await wait(()=>!find('sdEmailSend').disabled);
  assert(sends===1,'duplicate clicks do not send twice');
  assert(find('sdModalError').textContent==='SMTP unavailable','SMTP failure shown explicitly');
  const sendRequest=requests.find(r=>!r.path.endsWith('/preview'));
  assert(sendRequest.payload.recipient==='alternate@example.test','changed To affects only send payload');
  assert(sendRequest.payload.temporary_password==='','resend resolves initial password server-side without a save');
  assert(!('email' in sendRequest.payload)&&!('login_email' in sendRequest.payload),'contact/login email is never submitted for update');
  assert(requests.every(r=>r.path.includes('/registration-email')),'no Contact/User update request');
  failSend=false;find('sdEmailSend').click();
  await wait(()=>find('sdEmailBody').textContent.includes('alternate@example.test'));
  assert(sends===2,'explicit retry after failure');
  find('sdEmailModal').dispatchEvent(new Event('hidden.bs.modal'));
  assert(!find('sdEmailModal'),'password-bearing dialog removed on close');
  await openAgencyRegistrationEmail(1);
  await wait(()=>!find('sdEmailSend').disabled);
  assert(find('sdEmailTo').value==='primary@example.test','reopening resets To to Primary Contact');
  assert(sends===2,'reopening for resend does not send');
  find('sdEmailSend').click();
  await wait(()=>find('sdEmailBody').textContent.includes('primary@example.test'));
  assert(sends===3,'same dialog supports a later resend');
  find('sdEmailModal').dispatchEvent(new Event('hidden.bs.modal'));
  find('result').textContent=JSON.stringify({ok:true,networkCalls:0,sends,previews:requests.length-sends});
})().catch(error=>{find('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_agency_form_shows_email_button_only_for_saved_admin(self):
        template = (browser.ROOT / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        rendered_template = Environment(loader=FileSystemLoader(browser.ROOT / "templates")).get_template(
            "platform/master_management.html")
        layout = rendered_template.environment.get_template("platform/components/management_layout.html").module
        context = rendered_template.new_context({
            "active_module": "agency", "brand_icon_path": "",
            "page_header": layout.page_header, "surface": layout.surface,
            "form_modal": rendered_template.environment.get_template("platform/components/form_modal.html").module.form_modal,
        })
        rendered = "".join(rendered_template.blocks["content"](context))
        scripts = [body for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", rendered, re.DOTALL)
                   if "application/json" not in attrs]
        footer = re.search(
            r'<div class="modal-footer">\s*<div class="awsum-modal-left-actions">.*?</div>\s*</div>\s*</div>',
            rendered, re.DOTALL,
        ).group()
        styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", rendered, re.DOTALL))
        bootstrap_css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8")
        bootstrap_css += (browser.ROOT / "static" / "css" / "platform-modal.css").read_text(encoding="utf-8")
        functions = "\n".join(re.search(
            rf"  function {name}\([^\n]*\) \{{.*?\n  \}}", template, re.DOTALL).group()
            for name in ("createField", "createContactManagerPanel", "createImageFormLayout",
                         "renderForm", "clearForm", "syncAgencyEmailButton"))
        config = re.search(r"    agency: (\{.*?\n    \}),\n    license:", template, re.DOTALL).group(1)
        script = (
            f"const style=document.createElement('style');style.textContent={json.dumps(bootstrap_css + styles)};document.head.appendChild(style);\n"
            "document.body.className='page-master';const edit=document.createElement('div');edit.id='masterEditModal';edit.className='agency-modal pm-modal awsum-form-modal';\n"
            f"edit.innerHTML={json.dumps(footer)};document.body.appendChild(edit);\n"
            f"const renderedScripts={json.dumps(scripts)};\n"
            "const activeModule='agency',isListCentricModule=true,FIELD_ICONS={},masterAgencyTypeOptions=[];\n"
            f"const config={config};\n"
            "const masterFormModal=document.getElementById('uploader'),masterForm=null;\n"
            "const selectedMasterId={value:''};let selectedOriginalRecord=null;\n"
            "const agencyRegistrationPasswords=new Map();let previewOpens=0;const openAgencyRegistrationEmail=async()=>{previewOpens++;};\n"
            "const CONTACT_ROLE_OPTIONS=[['PRIMARY','Primary Contact']];\n"
            "const noop=()=>{},resetContactDrafts=noop;\n"
            "const bindUsDatePickers=noop,bindBrandLogoUploadHandlers=noop,refreshOrgLogoPreview=noop,\n"
            "refreshContactManager=noop,bindScopedStoreSelect=noop,applyEditFieldRules=noop,\n"
            "syncScopedStoreOptions=noop,updateSectionTitles=noop,renderMerchantDetailHero=noop,\n"
            "renderStoreDetailHero=noop,renderModuleDetailHero=noop,refreshMerchantLogoPreview=noop,\n"
            "refreshStoreLogoPreview=noop,updateListCentricActionButtons=noop,syncStoreOptionScopes=noop,\n"
            "setBaselineSnapshot=noop,openContactEditor=noop,closeContactEditor=noop,saveBusinessContact=noop;\n"
            "const createIdField=()=>document.createElement('div'),createBrandLogoUploadPanel=createIdField;\n"
            f"{functions}\n"
        )
        script += r"""
(async()=>{
  const assert=(ok,message)=>{if(!ok)throw Error(message);};
  renderedScripts.forEach(code=>new Function(code));
  renderForm();clearForm();
  const status=document.getElementById('field_status'),button=document.getElementById('btnAgencyRegistrationEmail');
  assert(status.value==='inactive','new Agency starts Inactive for contact registration');
  assert(!document.getElementById('sendRegistrationEmail'),'old send checkbox removed');
  assert(button.closest('.awsum-modal-action').classList.contains('d-none'),'unsaved Agency has no email button');
  assert(!!document.getElementById('businessContactManager'),'existing Contacts component reused');
  assert(!!document.getElementById('bc_email'),'existing Business Contact email editor reused');
  selectedMasterId.value='1';selectedOriginalRecord={has_agency_admin:false};syncAgencyEmailButton();
  assert(!button.closest('.awsum-modal-action').classList.contains('d-none')&&!button.disabled,'saved Agency without Admin keeps action enabled');
  assert(document.getElementById(button.id+'Reason').textContent==='','no stale disabled reason');
  button.click();await new Promise(resolve=>setTimeout(resolve,20));
  assert(previewOpens===1,'missing Admin metadata still requests current account check');
  selectedOriginalRecord.has_agency_admin=true;syncAgencyEmailButton();
  assert(!button.disabled&&button.textContent==='Registration Email','saved Admin shows Registration Email');
  assert(button.closest('.modal-footer'),'Registration Email is in actual Edit footer, not the scrollable form');
  const cancel=document.getElementById('btnModalCancel'),del=document.getElementById('btnModalDelete'),save=document.getElementById('btnModalSave');
  assert(button.getBoundingClientRect().width>0,'Registration Email visibly rendered');
  const positions=[button,cancel,del,save].map(el=>el.getBoundingClientRect().x);
  assert(positions.every((x,i)=>!i||x>positions[i-1]),'footer order: Registration Email | Cancel | Delete | Save');
  button.click();await new Promise(resolve=>setTimeout(resolve,20));
  assert(previewOpens===2,'button opens Preview independently');
  button.click();await new Promise(resolve=>setTimeout(resolve,20));
  assert(previewOpens===3,'button can reopen Preview later');
  selectedOriginalRecord={};syncAgencyEmailButton();assert(!button.disabled,'missing list metadata does not disable Edit action');
  clearForm();assert(button.closest('.awsum-modal-action').classList.contains('d-none'),'new record clears email availability');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_agency_save_never_opens_preview_or_displays_password(self):
        template = (browser.ROOT / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        function = re.search(r"  async function saveItem\(.*?\n  }\n", template, re.DOTALL).group()
        script = (
            "const activeModule='agency',isListCentricModule=true,agencyModalMode='edit',toastLabel='Agency';\n"
            "const selectedMasterId={value:'1'},agencyRegistrationPasswords=new Map();let lastSavedId=null;\n"
            "let savingStoreImage=false;const getStoreLogoUploader=()=>null,getOrganizationLogoUploader=()=>null;\n"
            "const noop=()=>{},isFormDirty=()=>true,usesPremiumModal=()=>true,getFormData=()=>({status:'active'}),\n"
            "validateAgencyForm=()=>[],showMasterModalError=noop,getItemUrl=id=>'/platform/agency/'+id,\n"
            "loadList=async()=>{},setBaselineSnapshot=noop,updateListCentricActionButtons=noop,notifyStorageWarning=noop;\n"
            f"{function}\n"
        )
        script += r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
let requests=[],alerts=[],previewOpens=0;
const openAgencyRegistrationEmail=async()=>{previewOpens++;};
window.AwsumToast={success:m=>alerts.push(m),warning:m=>alerts.push(m),error:m=>alerts.push(m),message:(action,label)=>label+' '+action+'.'};
window.fetch=async(path,options)=>{
  requests.push({path,options});
  return new Response(JSON.stringify({id:1,agency_user_created:true,temporary_password:'RandomTest!LongPassword'}));
};
(async()=>{
  assert(await saveItem(),'Save succeeds');
  assert(requests.length===1&&requests[0].path==='/platform/agency/1','Save only writes Agency');
  assert(previewOpens===0,'Save never opens Email Preview');
  assert(alerts.join('')==='Agency updated. Agency Admin User created.','Save result (Common Toast) contains no password');
  assert(agencyRegistrationPasswords.get('1')==='RandomTest!LongPassword','random fallback kept only in page memory for later Preview');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_license_dialog_preserves_selected_license_preview_and_send(self):
        component = (browser.ROOT / "static" / "js" / "store_devices_panel.js").read_text(encoding="utf-8")
        script = (
            "window.bootstrap={Modal:class{show(){}dispose(){}}};\n"
            f"(0,eval)({json.dumps(component)});\n"
        )
        script += r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const find=id=>document.getElementById(id);
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
const wait=async(check)=>{for(let n=0;n<100;n++){if(check())return;await tick();}throw Error('License dialog timed out');};
let writes=[];
find('uploader').innerHTML='<div id="storeDevicesPanel"></div>';
window.fetch=async(path,options={})=>{
  if(options.method==='POST'){
    const payload=JSON.parse(options.body);writes.push({path,payload});
    return new Response(JSON.stringify(path.endsWith('/preview')?{html:'<p>License KEY-1</p>'}
      :{accepted:true,recipient:payload.recipient,message_id:'mock-license'}));
  }
  return new Response(JSON.stringify({
    store:{store_name:'Store',merchant_name:'Merchant',agency_name:'Agency',agency_email:'license@example.test'},
    licenses:[{license_id:1,name:'POS',type:'POS',status:'issued',license_key:'KEY-1'}],
    devices:[],summary:{POS:{contracted:1,activated:0,issued:1},KIOSK:{contracted:0,activated:0,issued:0},
      CUSTOMER_ORDERING:{enabled:false}},entitlements:[],
  }));
};
(async()=>{
  AwsumStoreDevices.init();AwsumStoreDevices.show({id:1});
  await wait(()=>!!document.querySelector('[data-sd-license-select]'));
  const checkbox=document.querySelector('[data-sd-license-select]');checkbox.checked=true;
  checkbox.dispatchEvent(new Event('change',{bubbles:true}));
  find('sdSendEmail').click();
  await wait(()=>!find('sdEmailSend').disabled);
  assert(find('sdEmailTo').value==='license@example.test','license recipient unchanged');
  assert(!find('sdEmailMessage').readOnly,'license custom message stays editable');
  assert(writes[0].path==='/platform/store/1/licenses/email/preview','license preview endpoint preserved');
  assert(writes[0].payload.license_ids.join(',')==='1','selected IDs previewed');
  find('sdEmailTo').value='other@example.test';find('sdEmailTo').dispatchEvent(new Event('input'));
  find('sdEmailSend').click();
  await wait(()=>find('sdEmailBody').textContent.includes('other@example.test'));
  assert(writes[1].path==='/platform/store/1/licenses/email','license send endpoint preserved');
  assert(writes[1].payload.license_ids.join(',')==='1','selected IDs sent');
  assert(writes[1].payload.recipient==='other@example.test','license To remains editable');
  find('sdEmailModal').dispatchEvent(new Event('hidden.bs.modal'));
  find('result').textContent=JSON.stringify({ok:true,networkCalls:0,writes:writes.length});
})().catch(error=>{find('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
