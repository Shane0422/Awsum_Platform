"""Real DOM checks of common money and the current application form code."""
import json
import re
import unittest

from jinja2 import Environment, FileSystemLoader

from tools import test_image_components as browser


ROOT = browser.ROOT


def source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")



@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class MoneyBrowserTests(unittest.TestCase):
    # Reuse the existing isolated Chromium runner; no second process/cleanup engine.
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_md_form_common_money_discount_and_comma_free_multipart(self):
        template = Environment(loader=FileSystemLoader(ROOT / "templates")).get_template("platform/master_group.html")
        context = template.new_context({"mg_entity": "md", "mg_title": "MD Master"})
        markup = "".join(template.blocks["content"](context))
        markup = re.sub(r"<script\b[^>]*>.*?</script>", "", markup, flags=re.DOTALL)
        setup = (
            f"document.getElementById('uploader').innerHTML={json.dumps(markup)};\n"
            f"(0,eval)({json.dumps(source('static/js/money.js'))});\n"
        )
        script = r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
const find=name=>document.getElementById('mgf_'+name);
let writes=[];
window.bootstrap={Modal:class {show(){} hide(){}}};
const row={id:1,code:'MDTEST',name:'Money test',retail_price:'2500.50',cost:'1000.00',
  p1_mode:'DISCOUNT',p1_discount_pct:'12.50',p1_price:'2187.94',p2_mode:'FIXED',
  p2_price:'1000000.00',active:true};
window.fetch=async(url,options={})=>{
  if(['PUT','POST'].includes(options.method)){
    const payload=JSON.parse(options.body.get('payload'));writes.push(payload);
    return new Response(JSON.stringify({item:{...row,...payload}}),{status:200});
  }
  return new Response(JSON.stringify(String(url).endsWith('/options')
    ?{can_view_cost:true,category1:[],category2:[],category3:[],taxes:[],
      item_kinds:['HARDWARE'],billing_cycles:['ONE_TIME'],license_types:[]}
    :{items:[row],total:1,page:1,pages:1,sort:'name',direction:'asc'}),{status:200});
};
let ready;
const add=document.addEventListener;
document.addEventListener=function(type,listener,...args){
  if(type==='DOMContentLoaded')ready=listener;else add.call(this,type,listener,...args);
};
"""
        script += f"\n(0,eval)({json.dumps(source('static/js/master_group.js'))});\n"
        script += r"""
document.addEventListener=add;
(async()=>{
  await ready();
  document.querySelector('[data-act="edit"]').click();await tick();
  assert(document.querySelectorAll('input[data-awsum-money]').length===5,'five MD money fields');
  assert(find('retail_price').value==='2,500.50'&&find('cost').value==='1,000.00','initial presentation');
  assert(find('p1_price').value==='2,187.94'&&find('p2_price').value==='1,000,000.00','level presentation');
  assert(!find('p1_discount_pct').hasAttribute('data-awsum-money'),'percentage is not money');
  find('retail_price').dispatchEvent(new Event('focus'));
  assert(find('retail_price').value==='2500.50','focus ungrouping');
  find('retail_price').value='1000000';
  find('retail_price').dispatchEvent(new Event('input',{bubbles:true}));
  find('retail_price').dispatchEvent(new Event('blur'));
  assert(find('retail_price').value==='1,000,000.00','blur grouping');
  assert(find('p1_price').value==='875,000.00','exact live discount calculation');
  find('cost').value='1,23';
  find('cost').dispatchEvent(new Event('input',{bubbles:true}));
  document.getElementById('mgForm').dispatchEvent(new Event('submit',{cancelable:true}));
  await tick();
  assert(writes.length===0,'invalid grouping blocks request');
  assert(document.getElementById('mgFormError').textContent.includes('monetary'),'visible form error');
  find('cost').value='1,000.50';
  find('cost').dispatchEvent(new Event('input',{bubbles:true}));
  document.getElementById('mgForm').dispatchEvent(new Event('submit',{cancelable:true}));
  await tick();
  assert(writes.length===1,'one mock parent Save');
  assert(writes[0].retail_price==='1000000.00'&&writes[0].cost==='1000.50','comma-free decimal payload');
  assert(writes[0].p1_price==='875000.00'&&writes[0].p2_price==='1000000.00','all levels normalized');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(setup + script)

    def test_current_metadata_controls_serialization_dirty_state_and_sorting(self):
        template = source("templates/platform/master_management.html")
        rendered_template = Environment(loader=FileSystemLoader(ROOT / "templates")).get_template("platform/master_management.html")
        context = rendered_template.new_context({"active_module": "pricing-plan", "brand_icon_path": ""})
        rendered = "".join(rendered_template.blocks["content"](context))
        scripts = [body for attributes, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", rendered, re.DOTALL)
                   if "application/json" not in attributes]
        functions = []
        for name in ("createField", "getFormData", "snapshotFormState", "toComparableValue"):
            match = re.search(rf"  function {name}\([^\n]*\) \{{.*?\n  \}}", template, re.DOTALL)
            self.assertIsNotNone(match, name)
            functions.append(match[0])
        money_fields = re.findall(r"^\s*(\{ name: '[^']+', label: '[^']+', type: 'money'[^\n]*\}),", template, re.MULTILINE)
        self.assertEqual(len(money_fields), 22)
        script = f"(0,eval)({json.dumps(source('static/js/money.js'))});\n"
        script += f"const renderedScripts={json.dumps(scripts)};\n"
        script += (
            "const FIELD_ICONS={};let activeModule='pricing-plan';\n"
            "const selectedMasterId={value:'1'};\n"
            "let config={fields:[" + ",".join(money_fields) + "]};\n"
            + "\n".join(functions) + "\n"
        )
        script += r"""
try {
  const assert=(ok,message)=>{if(!ok)throw Error(message);};
  renderedScripts.forEach(code=>new Function(code));
  const host=document.getElementById('uploader');
  // Metadata has duplicate names across modules, so render each module control independently.
  for(const field of config.fields){
    host.replaceChildren(createField(field));
    const input=document.getElementById('field_'+field.name);
    assert(input.hasAttribute('data-awsum-money')&&input.type==='text',field.name+' uses shared input');
    assert(input.readOnly===!!field.readonly,field.name+' preserves read-only state');
    AwsumMoneyInput.setValue(input,'2500.5');
    assert(input.value==='2,500.50',field.name+' presentation');
    assert(getFormData()[field.name]==='2500.50',field.name+' normalized payload');
    const before=JSON.stringify(snapshotFormState());
    input.dispatchEvent(new Event('focus'));
    assert(input.value==='2500.50','editable value');
    assert(JSON.stringify(snapshotFormState())===before,'focus does not dirty form');
    input.dispatchEvent(new Event('blur'));
    if(field.min==='0'){
      input.value='-0.01';input.dispatchEvent(new Event('input'));
      assert(!input.checkValidity(),'domain minimum preserved');
    }
  }
  config={fields:[{name:'monthly_fee',label:'Snapshot',type:'money',readonly:true}]};
  activeModule='subscription';
  host.replaceChildren(createField(config.fields[0]));
  AwsumMoneyInput.setValue(document.getElementById('field_monthly_fee'),2500.5);
  assert(getFormData().monthly_fee===2500.5,'existing numeric JSON contract preserved');
  assert(toComparableValue('1,000.00').value===1000,'numeric sorting ignores grouping');
  assert(toComparableValue('$2,500.50').value===2500.5,'currency list sorting');
  const optional=document.createElement('input');AwsumMoneyInput.bind(optional);
  assert(AwsumMoneyInput.getValue(optional)==='','unset preserved');
  AwsumMoneyInput.setValue(optional,0);assert(optional.value==='0.00','zero differs from unset');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
} catch(error) {
  document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});
}
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
