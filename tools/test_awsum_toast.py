"""Common Toast Notification (static/js/awsum-toast.js): behavior in a real browser + system-wide adoption."""
import json
import re
import unittest

from tools import test_image_components as browser

TOAST_JS = browser.ROOT / "static" / "js" / "awsum-toast.js"
TOAST_CSS = browser.ROOT / "static" / "css" / "awsum-toast.css"
LAYOUTS = ("templates/platform/platform_base.html", "templates/shared/dashboard_base.html", "templates/store/workspace_base.html")


def toast_page(body=""):
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{TOAST_CSS.read_text(encoding='utf-8')}</style><script>{TOAST_JS.read_text(encoding='utf-8')}</script>"
            "</head><body style='margin:0'>"
            "<div id='layout' style='height:2400px;padding:40px'><input id='field'><ul><li><button id='tab' class='active'>Tab</button></li></ul></div>"
            f"{body}</body></html>")


class ToastBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_toast_rules_overlay_auto_close_persist_dedupe_keyboard_and_flash(self):
        script = f"const html={json.dumps(toast_page()).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const open=async()=>{
  const frame=document.createElement('iframe');frame.style.cssText='border:0;width:1200px;height:700px';frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;return frame;
};
(async()=>{
  let frame=await open();
  let win=frame.contentWindow,doc=frame.contentDocument,T=win.AwsumToast;
  const items=()=>[...doc.querySelectorAll('.awsum-toast')];
  const key=k=>doc.dispatchEvent(new win.KeyboardEvent('keydown',{key:k,bubbles:true}));
  // Standard messages.
  assert(T.message('created','Category III')==='Category III created.'&&T.message('update','Tax')==='Tax updated.'
    &&T.message('deleted','MD00002')==='MD00002 deleted.'&&T.message('activated','Color')==='Color activated.'
    &&T.message('deactivate','User')==='User deactivated.'&&T.message('status','Store')==='Store status changed.','standard messages');
  // Layout, scroll, focus and selected tab never change.
  doc.getElementById('field').focus();win.scrollTo(0,300);
  const before=[doc.getElementById('layout').getBoundingClientRect().top,win.scrollY,doc.activeElement.id,doc.querySelector('.active').id].join('|');
  T.success('MD00002 updated.');
  const toast=items()[0],region=doc.getElementById('awsumToastRegion'),box=toast.getBoundingClientRect();
  assert([doc.getElementById('layout').getBoundingClientRect().top,win.scrollY,doc.activeElement.id,doc.querySelector('.active').id].join('|')===before,'layout / scroll / focus / tab unchanged');
  assert(win.getComputedStyle(region).position==='fixed'&&box.top<40&&doc.documentElement.clientWidth-box.right<40&&box.width<=360,
    'small overlay at the top right '+JSON.stringify([box.top,box.right,box.width,doc.documentElement.clientWidth]));
  assert(toast.getAttribute('role')==='status'&&region.getAttribute('aria-live')==='polite','success announced politely');
  // Success closes after 1 second.
  await sleep(700);assert(items().length===1,'still visible before 1 s');
  await sleep(600);assert(items().length===0,'success closed after 1 s');
  // Continuous successes never pile up; the same text is not duplicated.
  T.success('A updated.');T.success('B updated.');T.success('B updated.');
  assert(items().length===1&&items()[0].textContent.includes('B updated.'),'one success at a time');
  T.clear();
  // Errors / validation warnings stay; announced assertively; identical errors are not stacked.
  T.error('Unable to save: Name is required.');T.error('Unable to save: Name is required.');T.warning('Item is in use.');
  assert(items().length===2&&items()[0].getAttribute('role')==='alert','errors are not duplicated');
  await sleep(1500);assert(items().length===2,'error / warning do not close by themselves');
  // A success shown with errors does not remove them.
  T.success('C updated.');assert(items().length===3,'success next to the errors');await sleep(1300);
  assert(items().length===2&&items().every(t=>t.dataset.kind!=='success'),'only the success closed');
  // Keyboard: Escape closes the newest toast, but not while a dialog is open (the dialog keeps Escape).
  const modal=doc.createElement('div');modal.className='modal show';doc.body.appendChild(modal);
  key('Escape');assert(items().length===2,'open dialog keeps Escape');
  modal.remove();key('Escape');assert(items().length===1&&items()[0].dataset.kind==='danger','Escape closes the newest');
  // Close button is a keyboard-focusable, labelled button.
  const close=items()[0].querySelector('button.awsum-toast-close');
  assert(close.getAttribute('aria-label')==='Close notification','close button label');
  close.focus();key('Escape');assert(items().length===0,'Escape on a focused toast closes it');
  // Keyboard focus on a success toast pauses auto close until focus leaves.
  T.success('Focus paused.');items()[0].querySelector('button').focus();await sleep(1300);
  assert(items().length===1,'focused success stays');doc.getElementById('field').focus();await sleep(1200);
  assert(items().length===0,'closes after focus leaves');
  // At most 3 visible; text is never HTML.
  ['E1','E2','E3','E4'].forEach(m=>T.error(m));
  assert(items().length===3&&!items().some(t=>t.textContent.includes('E1')),'max 3 visible');
  T.clear();T.error('<b>bold</b>');assert(!doc.querySelector('.awsum-toast b')&&items()[0].textContent.includes('<b>bold</b>'),'text only');
  T.clear();assert(T.show('')===null&&items().length===0,'empty message shows nothing');
  // flash(): shown once after the next page load (reload flows such as the Agency Portal forms).
  T.flash('Agency settings updated.');frame.remove();
  frame=await open();doc=frame.contentDocument;
  assert(doc.querySelector('.awsum-toast-success').textContent.includes('Agency settings updated.'),'flash shown after reload');
  frame.remove();frame=await open();
  assert(!frame.contentDocument.querySelector('.awsum-toast'),'flash shown only once');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


class ToastAdoptionTests(unittest.TestCase):
    def read(self, path):
        return (browser.ROOT / path).read_text(encoding="utf-8")

    def test_every_layout_loads_the_common_toast_once(self):
        for layout in LAYOUTS:
            html = self.read(layout)
            self.assertEqual(html.count('src="/static/js/awsum-toast.js?v=1"'), 1, layout)
            self.assertEqual(html.count('href="/static/css/awsum-toast.css?v=1"'), 1, layout)

    def test_old_page_banners_are_replaced(self):
        for path, element in (("templates/platform/master_group.html", "mgMessage"),
                              ("templates/platform/system_settings.html", "settingsGlobalMessage"),
                              ("templates/platform/sessions.html", "sessionMessage"),
                              ("templates/platform/components/user_management.html", "platformUsersAlert")):
            self.assertNotIn(f'id="{element}"', self.read(path), path)

    def test_portal_screens_use_the_toast_instead_of_native_alert(self):
        # Screens rendered with the common layouts (Platform / Agency / Merchant / Store portals).
        for path in ("templates/platform/master_management.html", "templates/platform/platform_stores.html",
                     "templates/platform/components/user_management.html", "static/js/master_group.js",
                     "static/js/system_settings.js", "static/js/sessions.js", "static/js/store_devices_panel.js",
                     "static/js/management-form.js"):
            source = self.read(path)
            self.assertIsNone(re.search(r"(?<![.\w])alert\(", source), path)
        self.assertIn("AwsumToast.flash(", self.read("static/js/management-form.js"))

    def test_master_group_results_use_standard_messages(self):
        source = self.read("static/js/master_group.js")
        for snippet in ("AwsumToast.message(editing ? 'updated' : 'created', data.item.code)",
                        "AwsumToast.message(row.active ? 'deactivated' : 'activated', row.code)",
                        "AwsumToast.message('deleted', row.code)"):
            self.assertIn(snippet, source)


if __name__ == "__main__":
    unittest.main()
