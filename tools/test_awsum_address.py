"""Common Address (static/js/awsum-address.js): one ZIP lookup / City-State autofill for every address form.

The Agency / Merchant / Store screen (master_management.html) and the Employee form (master_group.js) use this module
instead of their own copies; the browser test drives it with a mocked ZIP API.
"""
import unittest
from pathlib import Path

from tools import test_image_components as browser

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "awsum-address.js"


class CommonAddressSourceTests(unittest.TestCase):
    def test_every_address_form_uses_the_common_module(self):
        management = (ROOT / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        self.assertIn('<script src="/static/js/awsum-address.js?v=1"></script>', management)
        self.assertIn("window.AwsumAddress.bindZipAutoFill(", management)
        self.assertNotIn("zip-lookup-status", management)          # the lookup UI lives only in the module
        self.assertNotIn("/zip-lookup/", management)
        group = (ROOT / "templates" / "platform" / "master_group.html").read_text(encoding="utf-8")
        self.assertIn("{% if mg_entity == 'employee' %}<script src=\"/static/js/awsum-address.js?v=1\"></script>", group)
        js = (ROOT / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
        self.assertIn("AwsumAddress.bindZipAutoFill({ apiBase: '/merchant/api'", js)
        self.assertNotIn("zip-lookup", js)


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class CommonAddressBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_zip_autofill_messages_and_callbacks(self):
        script = MODULE.read_text(encoding="utf-8") + "\n" + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 document.body.insertAdjacentHTML('beforeend','<div><input id="z"></div><input id="c"><input id="s"><input id="n">');
 const calls=[];const filled=[];
 window.fetch=async(path)=>{calls.push(path);const zip=path.split('/').pop();
   const body=zip==='22031'?{found:true,city:'Fairfax',state:'va',timezone:'America/New_York'}:{found:false};
   return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});};
 AwsumAddress.bindZipAutoFill({apiBase:'/merchant/api',zip:'z',city:'c',state:'s',country:'n',
   onFilled:(d)=>filled.push(d.timezone),onAddressChange:()=>filled.push('changed')});
 AwsumAddress.bindZipAutoFill({apiBase:'/x',zip:'z',city:'c',state:'s',country:'n'}); // bound once
 const z=document.getElementById('z'),$=id=>document.getElementById(id);
 z.dispatchEvent(new Event('focus'));z.value='22031';z.dispatchEvent(new Event('blur'));await pause(50);
 assert(calls.length===1&&calls[0]==='/merchant/api/zip-lookup/22031','scope API base: '+calls);
 assert($('c').value==='Fairfax'&&$('s').value==='VA'&&$('n').value==='United States','City / State / Country filled');
 assert(filled[0]==='America/New_York','screen callback');
 z.dispatchEvent(new Event('focus'));z.value='abc';z.dispatchEvent(new Event('blur'));await pause(20);
 assert(/Enter a US ZIP code/.test(z.parentElement.querySelector('.zip-lookup-status').textContent),'invalid ZIP hint');
 z.dispatchEvent(new Event('focus'));z.value='99999';z.dispatchEvent(new Event('blur'));await pause(50);
 assert(/could not be found/.test(z.parentElement.querySelector('.zip-lookup-status').textContent),'not found hint');
 assert($('c').value==='Fairfax','a failed lookup keeps the typed address');
 $('s').dispatchEvent(new Event('change'));
 assert(filled.includes('changed'),'State change callback');
 assert(AwsumAddress.isUsCountry('usa')&&!AwsumAddress.isUsCountry('Canada'),'US country names');
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(e=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
