"""Common Collapsible Section (static/js/collapsible-section.js, window.AwsumCollapsible) on its own, without Bootstrap
(the hidden-attribute path; the Bootstrap Collapse path is covered by the Store License tab in
tools.test_store_master_detail): markup, escaping, accessibility, initial state, toggling, Expand All / Collapse All,
actions outside the heading button and the state kept across a re-render.
"""
import json
import unittest

from tools import test_image_components as browser

COMPONENT = (browser.ROOT / "static" / "js" / "collapsible-section.js").read_text(encoding="utf-8")


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class CollapsibleSectionTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_sections_toggle_expand_collapse_all_and_keep_state(self):
        script = f"delete window.bootstrap;(0,eval)({json.dumps(COMPONENT)});\n" + r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
const host=document.getElementById('uploader');
const render=()=>{host.innerHTML=AwsumCollapsible.controlsHtml()+
  AwsumCollapsible.html({id:'a',title:'Contract <Quantities>',summary:'POS 1/3 & more',expanded:true,actionsHtml:'<button type="button" id="act">Edit</button>',bodyHtml:'<p id="bodyA">A</p>'})+
  AwsumCollapsible.html({id:'b',title:'History',summary:'2 issues',expanded:false,bodyHtml:'<p id="bodyB">B</p>'});
  AwsumCollapsible.bind(host);};
render();
const sec=id=>host.querySelector('[data-awsum-collapsible="'+id+'"]'),tog=id=>sec(id).querySelector('.awsum-collapsible-toggle');
const body=id=>sec(id).querySelector('.awsum-collapsible-body'),open=id=>tog(id).getAttribute('aria-expanded')==='true'&&!body(id).hidden;
// Markup: escaped text, a real button with aria-controls, chevron, summary; actions beside (not inside) the button.
assert(tog('a').tagName==='BUTTON'&&tog('a').type==='button','heading is a button (keyboard: Enter / Space)');
assert(tog('a').getAttribute('aria-controls')===body('a').id&&body('a').id==='awsumCollapsible-a','aria-controls');
assert(sec('a').querySelector('.awsum-collapsible-title').textContent==='Contract <Quantities>'&&!host.innerHTML.includes('<Quantities>'),'title escaped');
assert(sec('a').querySelector('.awsum-collapsible-summary').textContent==='POS 1/3 & more','summary');
assert(!!sec('a').querySelector('.awsum-collapsible-chevron'),'chevron');
assert(!tog('a').contains(document.getElementById('act'))&&sec('a').querySelector('.awsum-collapsible-actions').contains(document.getElementById('act')),'actions outside the toggle');
// Initial state.
assert(open('a')&&!open('b')&&body('b').hidden,'initial: a open, b closed');
// Toggle; actions stay usable while collapsed.
tog('a').click();assert(!open('a')&&body('a').hidden&&sec('a').classList.contains('is-open')===false,'a closed');
let acted=0;document.getElementById('act').addEventListener('click',()=>acted++);document.getElementById('act').click();
assert(acted===1&&open('a')===false,'action click does not toggle the section');
tog('b').click();assert(open('b'),'b opened');
// Expand All / Collapse All.
host.querySelector('[data-awsum-collapsible-all="collapse"]').click();assert(!open('a')&&!open('b'),'Collapse All');
host.querySelector('[data-awsum-collapsible-all="expand"]').click();assert(open('a')&&open('b'),'Expand All');
// The chosen state survives a re-render with the same ids (initial values are only the first state).
tog('a').click();render();
assert(!open('a')&&open('b'),'state kept after re-render');
assert(AwsumCollapsible.isExpanded('a')===false&&AwsumCollapsible.isExpanded('zzz',true)===true,'isExpanded');
document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
"""
        self.run_browser("(async()=>{try{" + script + "}catch(e){document.getElementById('result').textContent="
                         "JSON.stringify({ok:false,error:e.stack});}})();")


if __name__ == "__main__":
    unittest.main()
