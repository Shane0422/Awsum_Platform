"""Common Page Header actions (Stage 1: Master Group): template placement, sticky header, mode switching,
small-screen More menu, permissions / enabled options and single execution of the existing events."""
import json
import re
import unittest

from tools import test_image_components as browser
from tools import test_master_group_ui as mgui  # module import: its tests are not collected twice

render_list = mgui.render_list
# All 8 Master Group screens (Platform / Merchant / Store share the same template).
PAGES = [("tax", "Tax"), ("category1", "Category I"), ("category2", "Category II"), ("category3", "Category III"),
         ("md", "MD Master"), ("item_kind", "Item Kind"), ("location", "Location Master")]

ROOT = browser.ROOT


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class PageActionsTemplateTests(unittest.TestCase):
    def test_master_group_record_actions_live_once_in_the_sticky_page_header(self):
        for entity, title in PAGES:
            with self.subTest(entity=entity):
                html = render_list(entity, title)
                start = html.index('<header class="awsum-list-header awsum-page-header')
                header = html[start:html.index("</header>", start)]
                self.assertIn("awsum-page-header-sticky", header)
                self.assertIn('id="mgPageActions" data-page-actions', header)
                for button in ('id="mgExportBtn"', 'id="mgAddBtn"', 'id="mgMdToggle"', 'id="mgMdDelete"', 'id="mgSave"'):
                    self.assertEqual(html.count(button), 1, button)  # no duplicate in the form header / footer
                    self.assertIn(button, header)
                self.assertIn('id="mgSave" form="mgForm"', header)  # submits the form: same validation and save
                form_header = html[html.index('<div class="mg-md-form-header'):html.index('id="mgFormError"')]
                self.assertNotIn("<button", form_header)
                self.assertIn('src="/static/js/page-actions.js?v=1"', html)

    def test_page_header_options_are_optional_and_other_screens_are_unchanged(self):
        from backend.config.templates import templates
        env = templates.env
        macro = env.from_string('{% from "platform/components/management_layout.html" import page_header %}'
                                '{% call page_header("Users", "Sub") %}<button>Add</button>{% endcall %}').render()
        self.assertIn('<header class="awsum-list-header awsum-page-header">', macro)
        self.assertNotIn("data-page-actions", macro)
        self.assertNotIn("awsum-page-header-sticky", macro)
        # Stage 1 only: no other screen passes the new options.
        for path in ("templates/platform/master_management.html", "templates/platform/components/user_management.html",
                     "templates/platform/sessions.html", "templates/platform/system_settings.html",
                     "templates/platform/platform_stores.html", "templates/agency/portal.html"):
            source = read(path)
            self.assertNotIn("actions_id=", source, path)
            self.assertNotIn("page-actions.js", source, path)


class PageActionsBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def md_html(self):
        html = mgui.MasterGroupListBrowserTests.md_page_html(None)
        bootstrap = read("static/bootstrap/js/bootstrap.bundle.min.js")  # real dropdown for More
        assert "<script>\nwindow.requests=[];" in html
        return html.replace("<script>\nwindow.requests=[];", f"<script>{bootstrap}</script><script>\nwindow.requests=[];", 1)

    def test_md_header_sticky_more_menu_and_single_execution(self):
        html = self.md_html()
        self.assertIn("Dropdown", html)  # real Bootstrap dropdown is on the page
        script = f"const html={json.dumps(html).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(check())return;await tick();}throw Error('Timed out: '+label);};
const open=async(width)=>{
  const frame=document.createElement('iframe');frame.style.cssText=`border:0;width:${width}px;height:760px`;frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id);
  await wait(()=>find('mgBody').querySelector('[data-row-select="7"]'),'list');
  return {frame,doc,win,find};
};
const select=async(find)=>{find('mgBody').querySelector('[data-row-select="7"]').click();await wait(()=>find('mgf_name')&&find('mgf_name').value==='Sample Item'&&find('mgDetailBody').textContent.includes('BASE-2'),'form');};
const shown=find=>[...find('mgPageActions').children].filter(el=>el.offsetParent!==null).map(el=>el.id||(el.classList.contains('awsum-page-more')?'more':el.querySelector('.btn').id)).join(',');
(async()=>{
  // ---- wide: all actions in the header; sticky under the navigation while scrolling ----
  let {frame,doc,win,find}=await open(1400);
  assert(shown(find)==='mgExportBtn,mgAddBtn','nothing selected: '+shown(find));
  await select(find);
  assert(shown(find)==='mgExportBtn,mgAddBtn,mgMdToggle,mgMdDelete,mgSave','edit: '+shown(find));
  const nav=doc.querySelector('.platform-header'),header=doc.querySelector('.mg-page > .awsum-page-header');
  doc.getElementById('mgPage').insertAdjacentHTML('beforeend','<div style="height:2400px"></div>');
  win.scrollTo(0,900);await tick();
  const navBox=nav.getBoundingClientRect(),headBox=header.getBoundingClientRect(),saveBox=find('mgSave').getBoundingClientRect();
  assert(win.scrollY>800&&Math.abs(navBox.top)<1&&Math.abs(headBox.top-navBox.bottom)<1.5,'header sticks right under the navigation (no overlap): '+[navBox.bottom,headBox.top]);
  assert(saveBox.top>=navBox.bottom&&saveBox.bottom<=headBox.bottom&&find('mgSave').offsetParent!==null,'Save Changes stays visible while scrolling');
  frame.remove();

  // ---- small screen: Save stays, the rest goes to More ----
  ({frame,doc,win,find}=await open(375));
  const header2=()=>doc.querySelector('.mg-page > .awsum-page-header');
  assert(shown(find)==='more,mgAddBtn','narrow, nothing selected: Add stays, Export in More: '+shown(find));
  await select(find);
  assert(shown(find)==='more,mgSave'&&find('mgSave').textContent==='Save Changes','narrow edit: More + Save Changes: '+shown(find));
  const acts=find('mgPageActions'),more=acts.querySelector('.awsum-page-more');
  const mb=more.getBoundingClientRect(),sb=find('mgSave').getBoundingClientRect();
  assert(Math.abs(mb.top-sb.top)<2&&mb.right<=sb.left&&sb.right<=doc.documentElement.clientWidth&&acts.scrollWidth<=acts.clientWidth+1,'More and Save on one line, not overlapping or cut');
  const hb=header2().getBoundingClientRect();
  assert(header2().scrollWidth<=header2().clientWidth+1&&hb.right<=doc.documentElement.clientWidth+1,'header fits the small screen (no clipped actions)');
  more.querySelector('.dropdown-toggle').click();await tick();
  const menu=more.querySelector('.dropdown-menu');
  const items=()=>[...menu.children].filter(li=>!li.hidden).map(li=>li.textContent.trim()).join('|');
  assert(menu.classList.contains('show'),'More opens');
  assert(items()==='Export|Export Excel|Export CSV|Export PDF|Add MD Master|Deactivate|Delete','More lists the secondary actions: '+items());
  assert(find('mgMdDelete').classList.contains('text-danger')&&find('mgMdDelete').closest('.dropdown-menu')===menu,'Delete keeps its danger style in More');
  // The same button node runs its existing listener exactly once.
  const before=win.requests.length;
  find('mgMdToggle').click();
  await wait(()=>find('mgMdToggle').textContent==='Activate','toggle');
  await tick();
  const puts=win.requests.slice(before).filter(r=>r.method==='PUT');
  assert(puts.length===1&&puts[0].payload.active===false,'Deactivate from More runs once');
  await wait(()=>(find('awsumToastRegion')||{textContent:''}).textContent.includes('deactivated'),'toast');
  // Add from More -> create: More (Export, Add) + Create.
  find('mgAddBtn').click();
  await wait(()=>find('mgSave').textContent==='Create','create');
  assert(shown(find)==='more,mgSave'&&items()==='Export|Export Excel|Export CSV|Export PDF|Add MD Master','narrow create: '+shown(find)+' / '+items());
  // Back to a wide screen: the same buttons return to the header, styles restored.
  frame.style.width='1400px';
  await wait(()=>shown(find)==='mgExportBtn,mgAddBtn,mgSave','wide again: '+shown(find));
  assert(find('mgAddBtn').className==='btn btn-primary'&&find('mgMdDelete').className.startsWith('btn btn-outline-danger')&&more.classList.contains('d-none'),'header styles restored, More hidden');
  assert(doc.querySelectorAll('#mgSave,#mgAddBtn,#mgMdToggle,#mgMdDelete,#mgExportBtn').length===5,'no duplicates after moving');
  frame.remove();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_component_modes_permissions_enabled_and_screen_state(self):
        css = read("static/bootstrap/css/bootstrap.min.css") + read("static/css/awsum-list.css")
        page = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
                f"<style>{css}</style><script>{read('static/bootstrap/js/bootstrap.bundle.min.js')}</script>"
                f"<script>{read('static/js/page-actions.js')}</script></head><body class='awsum-list'>"
                "<header class='awsum-list-header awsum-page-header'><div><h1>T</h1></div><div class='awsum-list-actions' id='acts'>"
                "<button class='btn btn-outline-secondary' id='a'>Archive</button><button class='btn btn-outline-danger' id='d'>Delete</button>"
                "<button class='btn btn-primary' id='s'>Save</button></div></header></body></html>")
        script = f"const html={json.dumps(page).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
(async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1000px;height:400px';frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id);
  let clicks=0;find('s').addEventListener('click',()=>clicks++);
  const pa=win.AwsumPageActions.mount(find('acts'),{mode:'none',permissions:{delete:false},actions:[
    {id:'s',modes:['create','edit'],pin:true},{id:'a',modes:['edit']},{id:'d',modes:['edit'],permission:'delete',danger:true}]});
  const shown=()=>[...find('acts').children].filter(el=>el.offsetParent!==null).map(el=>el.id||'more').join(',');
  assert(shown()==='','none: nothing');
  assert([...find('acts').children].map(el=>el.id||'more').join(',')==='more,s,a,d','configured order (existing nodes moved, not copied)');
  pa.setMode('edit');assert(shown()==='s,a','edit, no delete permission: '+shown());
  pa.setPermissions({delete:true});assert(shown()==='s,a,d','permission granted: '+shown());
  pa.setEnabled('s',false);assert(find('s').disabled,'setEnabled');pa.setEnabled('s',true);
  find('s').click();assert(clicks===1,'existing listener runs once');
  // Narrow: the screen's own d-none is followed inside More.
  frame.style.width='360px';await tick();await tick();
  assert(shown()==='more,s','narrow: More + Save: '+shown());
  find('d').classList.add('d-none');await tick();
  const li=[...doc.querySelectorAll('.awsum-page-more li')];
  assert(li.length===2&&!li[0].hidden&&li[1].hidden,'d-none from the screen hides the More item');
  find('a').classList.add('d-none');await tick();
  assert(shown()==='s','More hides when it has nothing to show: '+shown());
  find('a').classList.remove('d-none');find('d').classList.remove('d-none');await tick();
  frame.style.width='1000px';await tick();await tick();
  assert(shown()==='s,a,d'&&find('d').className==='btn btn-outline-danger','wide again: '+shown()+' '+find('d').className);
  pa.setMode('create');assert(shown()==='s','create: '+shown());
  find('s').click();assert(clicks===2,'still one listener after moving in and out of More');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
