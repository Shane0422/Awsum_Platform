"""Common Matrix (static/js/awsum-matrix.js, window.AwsumMatrix) on its own page in headless Chrome, with the real
AwsumMoneyInput / Money Keypad and the Bootstrap + platform styles: Inherited / Override display, cents-based entry,
$0.00, revert (null), only changed cells collected, dirty rules, read-only columns, search / status / overrides-only
filters, fixed columns with horizontal scrolling, server conflicts, no name attributes, escaping.
No server or database is involved (the component never calls an API).
"""
import json
import unittest

from tools import test_image_components as browser

STATIC = browser.ROOT / "static"
SCRIPTS = [(STATIC / "js" / name).read_text(encoding="utf-8")
           for name in ("money.js", "money-keypad.js", "awsum-matrix.js")]
STYLES = "\n".join((STATIC / path).read_text(encoding="utf-8") for path in (
    "bootstrap/css/bootstrap.min.css", "css/brand.css", "css/awsum-money-keypad.css", "css/awsum-matrix.css"))

SETUP = r"""
const assert=(ok,m)=>{if(!ok)throw Error(m);};
const style=document.createElement('style');style.textContent=STYLES;document.head.appendChild(style);
SCRIPTS.forEach(code=>(0,eval)(code));
const host=document.getElementById('uploader');host.style.width='520px';
const rows=[{id:11,label:'#1 Default',merchant:'5.00'},{id:12,label:'#2 <Large>',merchant:'1234.5'}];
const columns=[
  {id:100,label:'A Store',sub:'STR-A',active:true,header:{key:'pos_visible',label:'POS Visible',value:true,version:'0'}},
  {id:101,label:'B <Store>',sub:'STR-B',active:true,header:{key:'pos_visible',label:'POS Visible',value:false,version:'7.1'}},
  {id:102,label:'C Store',sub:'STR-C',active:true,header:{key:'pos_visible',label:'POS Visible',value:true,version:'0'}},
  {id:103,label:'D Store',sub:'STR-D',active:false,readOnly:true,header:{key:'pos_visible',label:'POS Visible',value:true,version:'0'}}];
const overrides={'100|11':['0.00','3.1'],'101|12':['99.00','4.2']};
let changesSeen=[];
const matrix=AwsumMatrix.create(host,{id:'t',rows,columns,
  fixedColumns:[{key:'label',label:'Detail',render:r=>'<span class="fx">'+r.label.replace(/</g,'&lt;')+'</span>'},
                {key:'merchant',label:'Merchant Price',render:r=>'$'+AwsumMoneyInput.format(r.merchant)}],
  cell:(r,c)=>{const o=overrides[c.id+'|'+r.id];return {inherited:r.merchant,override:o?o[0]:null,version:o?o[1]:'0'};},
  money:{min:'0',max:'9999999999.99',keypad:true},
  onChange:s=>changesSeen.push(s)});
const td=(r,c)=>host.querySelector('[data-cell="'+r+'|'+c+'"]');
const inp=(r,c)=>td(r,c).querySelector('input');
const src=(r,c)=>td(r,c).querySelector('[data-matrix-source]').textContent;
const keyIn=(input,data,inputType='insertText')=>input.dispatchEvent(new InputEvent('beforeinput',{inputType,data,cancelable:true,bubbles:true}));
const selectAll=input=>{input.focus();input.setSelectionRange(0,input.value.length);};
const typeNew=(input,digits)=>{selectAll(input);for(const d of digits)keyIn(input,d);input.blur();};
const tick=()=>new Promise(r=>setTimeout(r,20));
"""


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class AwsumMatrixTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def run_matrix(self, body):
        setup = SETUP.replace("STYLES", json.dumps(STYLES)).replace("SCRIPTS", json.dumps(SCRIPTS))
        self.run_browser("(async()=>{try{" + setup + body +
                         "document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});"
                         "}catch(e){document.getElementById('result').textContent=JSON.stringify({ok:false,error:e.stack||String(e)});}})();")

    def test_display_entry_revert_dirty_and_changes(self):
        self.run_matrix(r"""
// Display: inherited cells are empty with the inherited value as placeholder; overrides show their value.
assert(inp(11,102).value===''&&inp(11,102).placeholder==='5.00'&&src(11,102)==='Inherited','inherited cell');
assert(inp(12,102).placeholder==='1,234.50','inherited placeholder formatted');
assert(inp(11,100).value==='0.00'&&src(11,100)==='Override'&&td(11,100).classList.contains('is-override'),'$0.00 override shown');
assert(td(11,102).querySelector('[data-matrix-revert]').hidden&&!td(11,100).querySelector('[data-matrix-revert]').hidden,'revert only on overrides');
assert(!host.querySelector('input[name]'),'no name attribute on any input');
assert(host.querySelector('th[data-col="101"] .awsum-matrix-col-title').textContent==='B <Store>'&&!host.innerHTML.includes('B <Store>'),'labels escaped');
assert(!matrix.isDirty()&&JSON.stringify(matrix.changes())==='{"cells":[],"headers":[]}','clean after load');
// Cents-based entry (AwsumMoneyInput): 4,5,0 -> 4.50 on an inherited cell = new override.
typeNew(inp(11,102),'450');await tick();
assert(inp(11,102).value==='4.50'&&src(11,102)==='Override · Changed'&&td(11,102).classList.contains('is-dirty'),'cents entry 4.50 is an override');
// The same amount as the inherited value is still an explicit override.
typeNew(inp(11,101),'500');await tick();
assert(inp(11,101).value==='5.00'&&td(11,101).classList.contains('is-dirty'),'equal to inherited = override');
// $0.00 typed into an inherited cell: entering 0 keeps 0.00 (an override).
typeNew(inp(12,100),'0');await tick();
assert(inp(12,100).value==='0.00'&&src(12,100)==='Override · Changed','$0.00 override from inherited');
// Revert of a saved override -> null (inherited), dirty.
td(11,100).querySelector('[data-matrix-revert]').click();
assert(inp(11,100).value===''&&src(11,100)==='Inherited · Changed'&&td(11,100).classList.contains('is-dirty'),'revert -> inherited');
// Editing a saved override and returning to its loaded value is clean again.
typeNew(inp(12,101),'12300');await tick();assert(td(12,101).classList.contains('is-dirty'),'override edited');
typeNew(inp(12,101),'9900');await tick();assert(!td(12,101).classList.contains('is-dirty')&&src(12,101)==='Override','back to loaded value: clean');
// A new override typed then cleared (delete all) returns to inherited: clean.
typeNew(inp(12,102),'77');await tick();selectAll(inp(12,102));keyIn(inp(12,102),null,'deleteContentBackward');inp(12,102).blur();await tick();
assert(inp(12,102).value===''&&!td(12,102).classList.contains('is-dirty')&&src(12,102)==='Inherited','cleared new override: clean');
// Header switch (POS Visible): only real changes count.
const sw=c=>host.querySelector('[data-matrix-head="'+c+'"]');
sw(102).click();sw(101).click();sw(101).click();
const ch=matrix.changes();
const cells=ch.cells.map(c=>c.row+'|'+c.column+'='+c.value+'@'+c.version).sort().join(',');
assert(cells==='11|100=null@3.1,11|101=5.00@0,11|102=4.50@0,12|100=0.00@0','only changed cells: '+cells);
assert(JSON.stringify(ch.headers)==='[{"column":102,"key":"pos_visible","value":false,"version":"0"}]','only changed header: '+JSON.stringify(ch.headers));
assert(matrix.isDirty()&&matrix.dirtyCount()===5&&host.classList.contains('is-dirty'),'dirty count');
assert(changesSeen.length&&changesSeen[changesSeen.length-1].count===5,'onChange reports the count');
// Invalid input (over the max) blocks changes() with the money message.
inp(11,102).value='99999999999';inp(11,102).dispatchEvent(new Event('input',{bubbles:true}));
let threw='';try{matrix.changes();}catch(e){threw=e.message;}
assert(threw.includes('at most')&&!matrix.isValid(),'invalid amount refused: '+threw);
// Read-only column: disabled inputs, switch and revert.
assert(inp(11,103).disabled&&host.querySelector('[data-matrix-head="103"]').disabled&&td(11,103).querySelector('[data-matrix-revert]').disabled,'read-only column');
assert(host.querySelector('th[data-col="103"] .is-read-only').textContent==='View only','read-only badge');
// Keypad button is the Common Money Keypad trigger for that cell.
assert(td(11,102).querySelector('[data-money-keypad]').dataset.moneyKeypad===inp(11,102).id,'keypad trigger');
""")

    def test_filters_fixed_columns_conflicts_and_reload(self):
        self.run_matrix(r"""
const visible=c=>!host.querySelector('th[data-col="'+c+'"]').hidden&&!td(11,c).hidden;
const search=host.querySelector('[data-matrix-filter="q"]'),status=host.querySelector('[data-matrix-filter="status"]'),only=host.querySelector('[data-matrix-filter="overridesOnly"]');
search.value='str-c';search.dispatchEvent(new Event('input',{bubbles:true}));
assert(!visible(100)&&!visible(101)&&visible(102)&&!visible(103),'search by code');
search.value='';search.dispatchEvent(new Event('input',{bubbles:true}));
status.value='inactive';status.dispatchEvent(new Event('change',{bubbles:true}));
assert(!visible(100)&&visible(103),'status: inactive');
status.value='active';status.dispatchEvent(new Event('change',{bubbles:true}));
assert(visible(100)&&visible(101)&&visible(102)&&!visible(103),'status: active');
only.checked=true;only.dispatchEvent(new Event('change',{bubbles:true}));
assert(visible(100)&&visible(101)&&!visible(102),'overrides only (price or hidden)');
search.value='zzz';search.dispatchEvent(new Event('input',{bubbles:true}));
assert(!host.querySelector('[data-matrix-empty]').classList.contains('d-none'),'empty message');
search.value='';search.dispatchEvent(new Event('input',{bubbles:true}));only.checked=false;only.dispatchEvent(new Event('change',{bubbles:true}));
status.value='all';status.dispatchEvent(new Event('change',{bubbles:true}));
// Fixed columns: sticky with offsets; the value columns scroll horizontally under them.
const scroll=host.querySelector('.awsum-matrix-scroll'),fixed0=host.querySelector('thead [data-fixed="0"]'),fixed1=host.querySelector('thead [data-fixed="1"]');
assert(getComputedStyle(fixed0).position==='sticky'&&getComputedStyle(fixed1).position==='sticky','fixed columns are sticky');
assert(fixed0.style.left==='0px'&&Math.abs(parseFloat(fixed1.style.left)-fixed0.getBoundingClientRect().width)<1,'fixed offsets');
assert(scroll.scrollWidth>scroll.clientWidth,'value columns overflow horizontally');
const before=fixed1.getBoundingClientRect().left,colBefore=host.querySelector('th[data-col="102"]').getBoundingClientRect().left;
scroll.scrollLeft=200;await tick();
assert(Math.abs(fixed1.getBoundingClientRect().left-before)<1&&host.querySelector('th[data-col="102"]').getBoundingClientRect().left<colBefore-150,'fixed columns stay while Store columns scroll');
assert(document.documentElement.scrollWidth<=document.documentElement.clientWidth,'no page-level horizontal scroll');
// Phone width: the component itself never overflows (only its table scrolls inside the frame).
host.style.width='340px';matrix.layout();await tick();
const toolbar=host.querySelector('.awsum-matrix-toolbar');
assert(host.scrollWidth<=host.clientWidth+1&&toolbar.scrollWidth<=toolbar.clientWidth+1,'340px: no overflow ('+host.scrollWidth+'/'+host.clientWidth+')');
assert([...toolbar.children].every(el=>el.getBoundingClientRect().right<=host.getBoundingClientRect().right+1),'340px: toolbar wraps inside');
host.style.width='520px';matrix.layout();await tick();
// Conflicts: a 409 result replaces only the listed cells with the server state and highlights them.
typeNew(inp(11,102),'450');typeNew(inp(12,102),'800');await tick();
matrix.markConflicts([{row:11,column:102,override:'6.00',version:'9.1'},{column:101,header:true,value:true,version:'7.2'}]);
assert(inp(11,102).value==='6.00'&&td(11,102).classList.contains('is-conflict')&&!td(11,102).classList.contains('is-dirty'),'conflict cell shows the latest value, not dirty');
assert(td(11,102).title.includes('another user'),'conflict message');
assert(host.querySelector('[data-matrix-head="101"]').checked&&host.querySelector('th[data-col="101"]').classList.contains('is-conflict'),'header conflict');
const ch=matrix.changes();
assert(JSON.stringify(ch.cells)==='[{"row":12,"column":102,"value":"8.00","version":"0"}]','other unsaved edits kept: '+JSON.stringify(ch.cells));
// Editing the conflicted cell again uses the new version token.
typeNew(inp(11,102),'700');await tick();
assert(matrix.changes().cells.find(c=>c.row===11).version==='9.1'&&!td(11,102).classList.contains('is-conflict'),'new token after conflict');
// confirmDiscard uses the Common Confirm when present.
let asked=null;window.AwsumConfirm={show:o=>{asked=o;return Promise.resolve({confirmed:false});}};
assert(await matrix.confirmDiscard()===false&&asked.title==='Discard changes?','confirmDiscard via AwsumConfirm');
// Reload (setData) after a save: clean state with the server values.
matrix.setData({rows,columns,cell:(r,c)=>({inherited:r.merchant,override:c.id===102&&r.id===11?'7.00':null,version:'10.1'})});
assert(!matrix.isDirty()&&inp(11,102).value==='7.00'&&inp(11,100).value===''&&src(11,100)==='Inherited','reloaded clean');
assert(await matrix.confirmDiscard()===true,'nothing to discard');
matrix.destroy();assert(host.innerHTML===''&&!host.classList.contains('awsum-matrix'),'destroy');
""")


if __name__ == "__main__":
    unittest.main()
