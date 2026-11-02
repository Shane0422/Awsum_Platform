"""Shared baseline User screen behavior for Platform and Agency."""
import json
import re
import unittest

from tools import test_image_components as browser
from tools.test_agency_portal_ui import portal_fixture
from tools.test_platform_admin_ui import render_admin


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class CommonUserManagementTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_same_component_preserves_platform_actions_and_agency_scope(self):
        pages = []
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8")
        image_assets = browser.ROOT / "static" / "components" / "image-uploader"
        css += (image_assets / "image-uploader.css").read_text(encoding="utf-8")
        for scope, html in (
            ("PLATFORM", render_admin("users.html", "master", "user")),
            ("AGENCY", portal_fixture("users")),
        ):
            scripts = [body for body in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.DOTALL)
                       if "const usersBody" in body]
            self.assertEqual(len(scripts), 1)
            html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            html = re.sub(r"<link\b[^>]*>", "", html)
            pages.append({"scope": scope, "html": html.replace("</head>", f"<style>{css}</style></head>"),
                          "script": scripts[0]})
        bootstrap = (browser.ROOT / "static" / "bootstrap" / "js" / "bootstrap.bundle.min.js").read_text(encoding="utf-8")
        image_script = (image_assets / "image-uploader.js").read_text(encoding="utf-8")
        encoded = lambda value: json.dumps(value).replace("</", "<\\/")
        script = f"const pages={encoded(pages)},bootstrapCode={encoded(bootstrap)},imageCode={encoded(image_script)};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,35));
(async()=>{
 for(const item of pages){
  const frame=document.createElement('iframe');frame.srcdoc=item.html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const win=frame.contentWindow,doc=frame.contentDocument,agency=item.scope==='AGENCY';
  frame.style.width='1440px';frame.style.height='900px';
  doc.querySelectorAll('.modal').forEach(el=>el.classList.remove('fade'));
  win.eval(bootstrapCode);
  win.eval(imageCode);
  let calls=[],photoUpdates=0,failNext=false,hold=false,release;
  win.confirm=()=>true;
  const updateAvatar=win.AwsumImages.updateCurrentAvatar;
  win.AwsumImages.updateCurrentAvatar=(...args)=>{photoUpdates++;return updateAvatar(...args);};
  const user={id:2,name:'Existing Admin',first_name:'Existing',last_name:'Admin',email:'existing@example.test',phone:'555-1234',role:'Admin',role_id:1,status:'active',must_change_password:false};
  win.fetch=async(path,options={})=>{
    const body=options.body instanceof win.FormData?JSON.parse(options.body.get('payload')):options.body?JSON.parse(options.body):null;
    calls.push({path,method:options.method||'GET',body});
    if(hold&&options.method){await new Promise(resolve=>release=resolve);}
    if(failNext&&options.method){failNext=false;return new win.Response(JSON.stringify({detail:'Duplicate Email'}),{status:409});}
    if(path.startsWith('/platform/roles'))return new win.Response(JSON.stringify([{id:1,name:'Admin'}]));
    if(!options.method)return new win.Response(JSON.stringify(agency?{
      users:[{...user,...(path.includes('page=2')?{id:1,name:'My Account'}:{}),role:'Agency Admin'}],pages:2
    }:[user]));
    return new win.Response(JSON.stringify({success:true,id:2,image_url:''}));
  };
  win.eval(item.script);await tick();
  const rows=()=>doc.querySelector('#platformUsersTableBody').textContent;
  assert(rows().includes('Existing Admin'),'common list loads');
  assert(rows().includes('Reset Password'),'same Reset Password action');
  if(agency){
    const selfRow=[...doc.querySelectorAll('#platformUsersTableBody tr')].find(row=>row.textContent.includes('My Account'));
    assert(selfRow&&!selfRow.textContent.includes('Delete'),'all Agency pages load and self-delete is suppressed');
    assert(doc.getElementById('platformUserResetModal'),'Agency uses common Reset Password form');
  }
  assert(calls.every(call=>agency?!call.path.startsWith('/platform/'):!call.path.startsWith('/agency/')),'scope-specific transport only');
  doc.getElementById('addPlatformUserButton').click();await tick();
  for(const width of [1440,375,320]){
    frame.style.width=width+'px';await tick();
    for(const id of ['platformUserFirstName','platformUserLastName','platformUserEmail','platformUserRole','platformUserStatus',...(agency?['platformUserPhone']:[])]){
      const rect=doc.getElementById(id).getBoundingClientRect();
      assert(rect.width>0&&rect.left>=0&&rect.right<=width,`${item.scope} ${id} fits ${width}px`);
    }
    assert(win.getComputedStyle(doc.getElementById('platformUserImageUploader')).display!=='none','same photo uploader');
    assert(doc.getElementById('platformUserImageUploader').textContent.includes('Profile Photo'),'real shared photo component mounted');
    assert(doc.getElementById('platformUserMustChange').getBoundingClientRect().width>0,'same Must Change Password control');
  }
  const form=doc.getElementById('platformUserForm');
  doc.getElementById('platformUserFirstName').value='New';
  doc.getElementById('platformUserLastName').value='Admin';
  doc.getElementById('platformUserEmail').value='new@example.test';
  doc.getElementById('platformUserRole').value=agency?'agency-admin':'1';
  doc.getElementById('platformUserPassword').value='Secret!42';
  doc.getElementById('platformUserConfirmPassword').value='Secret!42';
  if(agency)doc.getElementById('platformUserPhone').value='123';
  doc.getElementById('platformUserMustChange').checked=true;
  hold=true;
  form.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));
  form.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));
  await tick();
  let writes=calls.filter(call=>call.method!=='GET');
  assert(writes.length===1,'common form blocks duplicate submits');
  assert(writes[0].path===(agency?'/agency/users/with-image':'/platform/user/with-image'),'scoped photo endpoint');
  assert(agency?!('role_id'in writes[0].body)&&writes[0].body.must_change_password===true:writes[0].body.role_id==='1','scope payload preserves role policy');
  hold=false;release();await tick();
  const actions=()=>[...doc.querySelectorAll('#platformUsersTableBody button')];
  actions().find(button=>button.textContent==='Edit').click();
  assert(doc.getElementById('platformUserModalTitle').textContent==='Edit User','same edit modal');
  assert(doc.getElementById('platformUserCreatePasswordFields').classList.contains('d-none'),'edit never recreates password');
  failNext=true;form.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));await tick();
  assert(doc.getElementById('platformUserFormError').textContent==='Duplicate Email','server validation surfaced');
  form.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));await tick();
  const edit=calls.filter(call=>call.method==='PUT').at(-1);
  assert(edit.path===(agency?'/agency/users/2/with-image':'/platform/user/2/with-image'),'scoped photo edit endpoint');
  assert(!('password'in edit.body),'edit preserves existing password');
  actions().find(button=>button.textContent==='Deactivate').click();await tick();
  const status=calls.filter(call=>call.method!=='GET').at(-1);
  assert(status.method===(agency?'PUT':'PATCH')&&status.body.status==='inactive','same status action scoped');
  actions().find(button=>button.textContent==='Reset Password').click();
  doc.getElementById('platformUserResetPassword').value='Reset!42';
  doc.getElementById('platformUserResetConfirm').value='Reset!42';
  doc.getElementById('platformUserResetForm').dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));await tick();
  assert(calls.filter(call=>call.method==='POST').at(-1).path===(agency?'/agency/users/2/reset-password':'/platform/user/2/reset-password'),'scoped common Reset Password endpoint');
  assert(photoUpdates===2,'shared photo update flow retained');
  if(agency){
    actions().find(button=>button.textContent==='Delete').click();await tick();
    assert(calls.filter(call=>call.method==='DELETE').at(-1).path==='/agency/users/2','Agency soft delete retained');
  }
  frame.remove();
 }
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack}));
"""
        self.run_browser(script)
