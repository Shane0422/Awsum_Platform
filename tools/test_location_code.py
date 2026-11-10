"""Common Location Code rule: backend normalization (Category III / MD Master saves) and the common
browser input (AwsumCodeInput) loaded by the common layout."""
import json
import unittest

from backend.services import location_code
from tools import test_image_components as browser
from tools import test_store_locations as tsl  # module import: its tests are not collected twice

ROOT = browser.ROOT


class LocationCodeRuleTests(unittest.TestCase):
    def test_trim_upper_case_and_keep_existing_characters(self):
        self.assertEqual(location_code.normalize(" k01-s01 "), "K01-S01")
        self.assertEqual(location_code.normalize("w01-r02-l03"), "W01-R02-L03")
        self.assertEqual(location_code.normalize("a.b/c_1 2"), "A.B/C_1 2")  # no new character restriction
        self.assertEqual(location_code.normalize("K01-G01"), "K01-G01")      # existing values stay as they are
        for blank in (None, "", "   "):
            self.assertIsNone(location_code.normalize(blank))
        self.assertIsNone(location_code.length_error("X" * 50))
        self.assertEqual(location_code.length_error("X" * 51), "Must be 50 characters or fewer.")

    def test_master_group_uses_the_common_rule(self):
        from backend.services import master_group as svc
        self.assertIs(svc.normalize_recommended_location, location_code.normalize)
        self.assertEqual(svc.RECOMMENDED_LOCATION_MAX, location_code.LOCATION_CODE_MAX)


class LocationCodeSaveTests(tsl.RecommendedLocationTests):
    """Category III and MD Master saves: lower case / spaces from any client are stored trimmed upper case."""

    def setUp(self):
        super().setUp()
        # New values must be active Location Codes of their Area in some Store (Common Location Selector).
        self.db.add_all([tsl.StoreLocation(i_store_id=1, c_location_code=code, c_location_name=code, c_location_area=area)
                         for code, area in (("K01-S01", "SALES_FLOOR"), ("K01-G01", "SALES_FLOOR"),
                                            ("W03", "WAREHOUSE"), ("W03-A01", "WAREHOUSE"))])
        self.db.commit()

    def test_category3_and_md_saves_store_upper_case(self):
        from backend.services import master_group as svc
        c3 = svc.update_item(self.db, "category3", self.c3["id"],
                             {**self.leaf, "sales_location": "  k01-s01 ", "warehouse_location": "w03"}, 1)
        self.assertEqual((c3["sales_location"], c3["warehouse_location"]), ("K01-S01", "W03"))
        md = self.md(sales_location=" k01-g01", warehouse_location="w03-a01 ")
        row = self.db.get(svc.MdMaster, md["id"])
        self.assertEqual((row.c_sales_location, row.c_warehouse_location), ("K01-G01", "W03-A01"))
        # Other MD fields keep their own rules (Item Name is not upper-cased).
        self.assertEqual(md["name"], "Cola")
        # Saving without the keys keeps the stored values.
        kept = svc.update_item(self.db, "md", md["id"], {**self.md_body, "second_name": "Alt"}, 1)
        self.assertEqual((kept["sales_location"], kept["warehouse_location"]), ("K01-G01", "W03-A01"))

    # The inherited RecommendedLocationTests run in their own module; do not repeat them here.
    test_category3_default_is_common_trimmed_uppercase_and_kept_when_absent = None
    test_md_inherits_overrides_and_reset_returns_to_category_default = None
    test_recommended_location_is_not_stock_and_not_a_store_location = None
    test_md_keeps_a_saved_code_that_no_store_has_and_inactive_codes_cannot_be_new = None


class CodeInputAdoptionTests(unittest.TestCase):
    def test_common_layout_loads_it_and_master_group_uses_it(self):
        base = (ROOT / "templates" / "platform" / "platform_base.html").read_text(encoding="utf-8")
        self.assertEqual(base.count('src="/static/js/code-input.js?v=2"'), 1)
        client = (ROOT / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
        # Category III / MD Location fields are read-only codes from the Common Location Selector (no typing);
        # Code Input stays for the codes that are still typed (Location Master Zone, Common Maintenance codes).
        self.assertIn("input(key, 'text', 'maxlength=\"50\" readonly')", client)
        self.assertNotIn("input(key, 'text', 'maxlength=\"50\" data-awsum-code')", client)
        self.assertIn("input('zone', 'text', 'maxlength=\"10\" data-awsum-code')", client)
        self.assertIn("AwsumCodeInput.bindAll($('mgFields'))", client)
        self.assertNotIn("text-transform:uppercase", client)  # no screen-only CSS capitals


class CodeInputBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_typing_paste_drop_composition_and_rebinding(self):
        page = ("<!doctype html><html><head><meta charset='utf-8'><script>"
                + (ROOT / "static" / "js" / "code-input.js").read_text(encoding="utf-8")
                + "</script></head><body><input id='a' data-awsum-code value='pre-filled'><input id='b'></body></html>")
        script = f"const html={json.dumps(page).replace('</', '<' + chr(92) + '/')};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
(async()=>{
  const frame=document.createElement('iframe');frame.srcdoc=html;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow,a=doc.getElementById('a'),b=doc.getElementById('b');
  const C=win.AwsumCodeInput;
  C.bindAll(doc);C.bindAll(doc); // binding twice adds nothing
  assert(a.value==='PRE-FILLED'&&a.autocapitalize==='characters'&&!a.spellcheck,'bound; a value filled before binding is converted');
  assert(b.value===''&&!b.hasAttribute('data-awsum-code'),'unmarked inputs are untouched');
  // Screen listeners see the upper-case value (capture phase) and run once per input.
  let seen=[];a.addEventListener('input',()=>seen.push(a.value));
  a.focus();a.select();doc.execCommand('insertText',false,'k01-s01');
  assert(a.value==='K01-S01'&&seen.join()==='K01-S01','typing: '+a.value+' / '+seen.join());
  a.value='w01-r02';a.dispatchEvent(new win.InputEvent('input',{bubbles:true,inputType:'insertFromPaste'}));
  assert(a.value==='W01-R02','paste');
  a.value='x9-y';a.dispatchEvent(new win.InputEvent('input',{bubbles:true,inputType:'insertFromDrop'}));
  assert(a.value==='X9-Y','drop');
  a.value='12-34';a.dispatchEvent(new win.InputEvent('input',{bubbles:true}));
  assert(a.value==='12-34','digits and hyphens kept');
  a.value='  k1 ';a.dispatchEvent(new win.Event('change',{bubbles:true}));
  assert(a.value==='K1','change trims (and upper-cases a value set by a script): "'+a.value+'"');
  C.destroy(a);a.value='low';a.dispatchEvent(new win.InputEvent('input',{bubbles:true}));
  assert(a.value==='low','destroy removes the behavior');
  assert(C.normalize('k01-s01')==='K01-S01','normalize helper');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
