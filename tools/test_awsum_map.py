"""Common Map (static/js/awsum-map.js + backend.services.maps): one Show Map for every address form.

The Employee ADDRESS tab uses it now (its screen test is in tools.test_employee_master); Merchant / Store / Agency forms
reuse the same module. The browser test drives the module with a mocked map-config API and an iframe whose src is
recorded instead of loaded, so no request ever reaches Google. Keys here are obvious fakes.
"""
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.routers import merchant_store
from backend.services import maps
from tools import test_common_app_navigation
from tools import test_image_components as browser

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "awsum-map.js"
FAKE_KEY = "FAKE-embed-key/for+tests"


class CommonMapSourceTests(unittest.TestCase):
    def test_employee_form_uses_the_common_module_without_a_key_in_code(self):
        group = (ROOT / "templates" / "platform" / "master_group.html").read_text(encoding="utf-8")
        self.assertIn('<script src="/static/js/awsum-map.js?v=2"></script>{% endif %}', group)  # Employee page only
        js = (ROOT / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
        self.assertIn("AwsumMap.create($('mgEmpMap'), { configUrl: '/merchant/api/map-config'", js)
        self.assertNotIn("google.com/maps", js)                     # the map URLs live only in the module
        self.assertNotIn("mgf_address_line2'", js.split("AwsumMap.create(")[1].split("});")[0])  # Line 2 never mapped
        # No Google API key (AIza...) anywhere in server code, scripts or templates.
        for folder in ("backend", "static/js", "templates"):
            for file in (ROOT / folder).rglob("*"):
                if file.suffix in (".py", ".js", ".html"):
                    self.assertIsNone(re.search(r"AIza[0-9A-Za-z_\-]{20,}", file.read_text(encoding="utf-8", errors="ignore")), file)

    def test_config_comes_from_the_environment_only(self):
        with patch.dict(os.environ, {maps.EMBED_KEY_ENV: ""}):
            self.assertEqual(maps.map_client_config(), {"enabled": False, "embed_key": ""})
        with patch.dict(os.environ, {maps.EMBED_KEY_ENV: f"  {FAKE_KEY} "}):
            self.assertEqual(maps.map_client_config(), {"enabled": True, "embed_key": FAKE_KEY})


class CommonMapApiTests(unittest.TestCase):
    """GET /merchant/api/map-config: any signed-in Portal user (Merchant / Store login); enabled + Embed key only."""

    def setUp(self):
        test_common_app_navigation.CommonAppNavigationTests.setUp(self)
        self.app.include_router(merchant_store.router)

    def login(self, scope):
        test_common_app_navigation.CommonAppNavigationTests.login(self, scope)

    def test_merchant_login_reads_only_enabled_and_the_embed_key(self):
        self.assertIn(self.client.get("/merchant/api/map-config").status_code, (401, 403))   # not signed in
        self.login("MERCHANT")
        with patch.dict(os.environ, {maps.EMBED_KEY_ENV: ""}):
            res = self.client.get("/merchant/api/map-config")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"enabled": False, "embed_key": ""})
        self.assertIn("no-store", res.headers["cache-control"])
        with patch.dict(os.environ, {maps.EMBED_KEY_ENV: FAKE_KEY, "AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY": "SECRET-X"}):
            res = self.client.get("/merchant/api/map-config")
        self.assertEqual(res.json(), {"enabled": True, "embed_key": FAKE_KEY})
        self.assertNotIn("SECRET-X", res.text)

    def test_store_login_reads_it_without_store_management_and_employee_data_stays_refused(self):
        self.login("STORE")  # a Store login has no Store Management permission (its ZIP lookup is refused)
        self.assertEqual(self.client.get("/merchant/api/zip-lookup/22031").status_code, 403)
        with patch.dict(os.environ, {maps.EMBED_KEY_ENV: FAKE_KEY}):
            res = self.client.get("/merchant/api/map-config")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"enabled": True, "embed_key": FAKE_KEY})

    def test_signed_out_is_refused(self):
        self.login("MERCHANT")
        self.client.cookies.clear()
        with patch.dict(os.environ, {maps.EMBED_KEY_ENV: FAKE_KEY}):
            res = self.client.get("/merchant/api/map-config")
        self.assertIn(res.status_code, (401, 403))
        self.assertNotIn(FAKE_KEY, res.text)


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class CommonMapBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_show_hide_reset_fallback_and_embed(self):
        script = r"""
// Record iframe URLs instead of loading them: nothing reaches Google from this test.
Object.defineProperty(HTMLIFrameElement.prototype,'src',{configurable:true,set(v){this.dataset.testSrc=v;},get(){return this.dataset.testSrc||'';}});
""" + MODULE.read_text(encoding="utf-8") + "\n" + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const KEY=__KEY__;
 document.body.insertAdjacentHTML('beforeend','<input id="l1"><input id="l2"><input id="c"><input id="s"><input id="z"><input id="n"><div id="m1"></div><div id="m2"></div>');
 const $=id=>document.getElementById(id);
 const calls=[],opened=[];let release;
 window.open=(url,target,features)=>{opened.push({url,target,features});return null;};const gate=new Promise(r=>{release=r;});
 window.fetch=async(path)=>{calls.push(path);if(path==='/slow/map-config')await gate;
   const body=path==='/key/map-config'||path==='/slow/map-config'?{enabled:true,embed_key:KEY}:{enabled:false,embed_key:''};
   return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});};
 const fields={line1:'l1',city:'c',state:'s',zip:'z',country:'n'};
 // formatAddress / isComplete: Address Line 2 is not a part; Line 1 + ZIP or Line 1 + City + State.
 assert(AwsumMap.formatAddress({line1:' 1 Main  St ',city:'Fairfax',state:'VA',zip:'22031',country:'United States'})==='1 Main St, Fairfax, VA 22031, United States','formatted');
 assert(AwsumMap.isComplete({line1:'1 Main',zip:'22031'})&&AwsumMap.isComplete({line1:'1 Main',city:'Fairfax',state:'VA'}),'complete');
 assert(!AwsumMap.isComplete({line1:'1 Main',city:'Fairfax'})&&!AwsumMap.isComplete({zip:'22031',city:'Fairfax',state:'VA'})&&!AwsumMap.isComplete(null),'incomplete');
 // No key: hidden by default, nothing requested until Show Map.
 const map=AwsumMap.create('m1',{configUrl:'/none/map-config',fields});
 const btn=$('m1').querySelector('[data-map-toggle]'),panel=$('m1').querySelector('[data-map-panel]'),msg=$('m1').querySelector('[data-map-message]'),ext=$('m1').querySelector('[data-map-external]');
 assert(btn.textContent==='Show Map'&&panel.classList.contains('d-none')&&!map.isVisible()&&calls.length===0,'hidden by default, no request');
 assert(ext&&ext.tagName==='BUTTON'&&ext.textContent==='Open in Google Maps'&&!panel.contains(ext)&&!$('m1').querySelector('a'),'separate Open in Google Maps button, no duplicate link');
 $('l1').value='1 Main St';btn.click();await pause(20);
 assert(!map.isVisible()&&/Enter Address Line 1 and a ZIP Code/.test(msg.textContent)&&calls.length===0,'incomplete address: hint, no request');
 msg.textContent='';ext.click();
 assert(opened.length===0&&/Enter Address Line 1 and a ZIP Code/.test(msg.textContent),'incomplete address: no new tab, hint');
 $('z').value='22031';$('z').dispatchEvent(new Event('input',{bubbles:true}));
 assert(msg.textContent==='','editing clears the hint');
 $('l1').value='12 A&B St #5 / Rear?';$('l2').value='Apt 9 SECRET-UNIT';$('c').value='Fairfax';$('s').value='VA';$('n').value='United States';
 btn.click();await pause(30);
 assert(map.isVisible()&&btn.textContent==='Hide Map'&&!panel.classList.contains('d-none'),'shown');
 assert(calls.join()==='/none/map-config','config read on Show Map only');
 assert(!$('m1').querySelector('iframe')&&/not set up/.test(msg.textContent),'no key: no map frame, hint');
 ext.click();
 const tab=opened[0]||{},href=tab.url||'',u=new URL(href);
 assert(opened.length===1&&href.startsWith('https://www.google.com/maps/search/?api=1&query=')&&tab.target==='_blank'&&tab.features==='noopener,noreferrer','new tab, official URL, noopener,noreferrer: '+JSON.stringify(tab));
 assert(u.searchParams.get('query')==='12 A&B St #5 / Rear?, Fairfax, VA 22031, United States'&&!/[&#?]B|#5/.test(href.split('query=')[1]),'query safely encoded: '+href);
 assert(!href.includes('SECRET-UNIT')&&!href.includes('Apt'),'Address Line 2 excluded');
 assert(map.isVisible()&&calls.length===1,'the external tab leaves the inside map as it is and reads no config');
 btn.click();
 assert(!map.isVisible()&&btn.textContent==='Show Map'&&panel.classList.contains('d-none'),'Hide');
 // Without any Show Map and without a key: the external button still works with the current values.
 const plain=AwsumMap.create('m2',{fields});
 $('m2').querySelector('[data-map-external]').click();
 assert(opened.length===2&&new URL(opened[1].url).searchParams.get('query')==='12 A&B St #5 / Rear?, Fairfax, VA 22031, United States'&&!plain.isVisible(),'external button needs no key and no config');
 btn.click();await pause(20);
 assert(calls.length===1,'config read once per page');
 // Address edits reset (typing, and the ZIP autofill's input events on City / State / Country).
 $('c').value='Vienna';$('c').dispatchEvent(new Event('input',{bubbles:true}));
 assert(!map.isVisible()&&btn.textContent==='Show Map'&&panel.classList.contains('d-none'),'City edit resets');
 btn.click();await pause(20);$('n').dispatchEvent(new Event('change',{bubbles:true}));
 assert(!map.isVisible(),'Country change resets');
 // Embed key: the map frame with the encoded key and address; Hide / Reset remove it.
 const keyed=AwsumMap.create($('m2'),{configUrl:'/key/map-config',fields});
 $('m2').querySelector('[data-map-toggle]').click();await pause(30);
 const iframe=$('m2').querySelector('iframe');
 assert(iframe&&iframe.closest('.ratio')&&iframe.title==='Map','map frame in the common Bootstrap ratio box');
 const src=new URL(iframe.src);
 assert(iframe.src.startsWith('https://www.google.com/maps/embed/v1/place?key=')&&src.searchParams.get('key')===KEY&&src.searchParams.get('q')==='12 A&B St #5 / Rear?, Vienna, VA 22031, United States','embed URL: '+iframe.src);
 assert(!iframe.src.includes(KEY)&&!iframe.src.includes('SECRET-UNIT'),'key encoded, Line 2 excluded');
 keyed.hide();
 assert(!$('m2').querySelector('iframe')&&!keyed.isVisible(),'Hide removes the map frame');
 keyed.setAddress({line1:'9 Oak Rd',city:'Reston',state:'VA'});
 assert(!keyed.isVisible(),'setAddress while hidden does not show');
 await keyed.show();
 assert(new URL($('m2').querySelector('iframe').src).searchParams.get('q')==='9 Oak Rd, Reston, VA','setAddress address shown');
 keyed.setAddress({line1:'1 Elm St',zip:'20190'});await pause(20);
 assert(new URL($('m2').querySelector('iframe').src).searchParams.get('q')==='1 Elm St, 20190','setAddress while shown redraws');
 keyed.reset();
 assert(!$('m2').querySelector('iframe')&&!keyed.isVisible()&&$('m2').querySelector('[data-map-toggle]').textContent==='Show Map','reset');
 await keyed.show();
 assert(new URL($('m2').querySelector('iframe').src).searchParams.get('q')==='12 A&B St #5 / Rear?, Vienna, VA 22031, United States','after reset: the current field values again');
 keyed.reset();
 // A Hide during the config read cancels that Show (no late map frame).
 const slow=AwsumMap.create('m2',{configUrl:'/slow/map-config',fields});
 const pending=slow.show();slow.hide();release();await pending;await pause(20);
 assert(!$('m2').querySelector('iframe')&&!slow.isVisible(),'cancelled Show adds no frame');
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack});});
""".replace("__KEY__", repr(FAKE_KEY))
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
