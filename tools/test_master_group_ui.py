"""Focused rendering and list-interaction checks without live API or DB access."""
import json
import re
import unittest

from tools import test_image_components as browser
from tools.test_platform_navigation import render_navigation


PAGES = [
    ("tax", "Tax"), ("category1", "Category I"), ("category2", "Category II"),
    ("category3", "Category III"), ("md", "MD Master"),
]


def render_list(entity, title):
    base, context, _ = render_navigation()
    context.update(mg_entity=entity, mg_title=title, active_page="master-group")
    return base.environment.get_template("platform/master_group.html").render(context)


class MasterGroupListTemplateTests(unittest.TestCase):
    def test_all_lists_share_styles_and_keep_existing_controls(self):
        for entity, title in PAGES:
            with self.subTest(entity=entity):
                html = render_list(entity, title)
                self.assertEqual(html.count('href="/static/css/awsum-list.css?v=3"'), 1)
                self.assertIn("mg-page awsum-list", html)
                self.assertIn(f'data-entity="{entity}"', html)
                self.assertIn(f"Add {title}</button>", html)
                self.assertIn('src="/static/js/master_group.js?v=10"', html)
                for control in ("mgSearch", "mgActiveFilter", "mgPageSize", "mgExportBtn",
                                "mgAddBtn", "mgHead", "mgBody", "mgPrev", "mgNext",
                                "mgForm", "mgSave"):
                    self.assertEqual(html.count(f'id="{control}"'), 1)
                for control in ("mgSearch", "mgActiveFilter", "mgPageSize"):
                    self.assertIn(f'for="{control}"', html)
                self.assertNotIn(".mg-page .mg-table", html)


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class MasterGroupListBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_all_list_styles_responsive_and_existing_interactions(self):
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8") + "\n" + "\n".join(
            (browser.ROOT / "static" / "css" / name).read_text(encoding="utf-8")
            for name in ("brand.css", "awsum-list.css"))
        client = (browser.ROOT / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
        money = (browser.ROOT / "static" / "js" / "money.js").read_text(encoding="utf-8")
        mock = r"""
window.requests=[];
window.bootstrap={Modal:class{show(){}hide(){}}};
window.fetch=async(path,options)=>{
  const url=new URL(path,'http://127.0.0.1');requests.push({path,method:options.method});
  if(url.pathname.endsWith('/options'))return new Response(JSON.stringify({can_view_cost:true}));
  const q=url.searchParams;
  const item={id:1,code:'TEST00001',name:'Sample Record',rate:'5.00',display_order:1,
    active:true,category1_name:'Food',category2_name:'Beverage',category3_name:'Soft Drink',
    retail_price:'2.50',cost:'1.20',item_kind:'HARDWARE',billing_cycle:'ONE_TIME'};
  return new Response(JSON.stringify({items:q.get('q')==='empty'?[]:[item],
    total:40,page:Number(q.get('page')||1),pages:2,sort:q.get('sort')||'code',direction:q.get('direction')||'asc'}));
};
"""
        pages = []
        for entity, title in PAGES:
            html = render_list(entity, title)
            html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
            html = re.sub(r"<link\b[^>]*>", "", html)
            html = html.replace("</head>", f"<style>{css}</style></head>")
            html = html.replace("</body>", f"<script>{mock}\n{money}\n{client}</script></body>")
            pages.append(html)
        serialized = json.dumps(pages).replace("</", "<\\/")
        script = f"const pages={serialized};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
const wait=async(check)=>{for(let i=0;i<100;i++){if(check())return;await tick();}throw Error('List timed out');};
(async()=>{
  for(const html of pages){
    const frame=document.createElement('iframe');frame.style.border='0';frame.srcdoc=html;
    const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
    document.body.appendChild(frame);await loaded;
    const doc=frame.contentDocument,win=frame.contentWindow,find=id=>doc.getElementById(id);
    await wait(()=>find('mgBody').textContent.includes('Sample Record'));
    assert(find('mgSubtitle').textContent.length>0,'existing subtitle populated');
    assert(find('mgBody').querySelectorAll('[data-act]').length===3,'Edit/Activate/Delete controls preserved');
    assert(win.getComputedStyle(doc.querySelector('.awsum-table-card')).borderRadius==='12px','common card styling');
    assert(win.getComputedStyle(doc.querySelector('.text-bg-success')).backgroundColor==='rgb(220, 248, 232)','common status badge');
    for(const width of [1440,768,375,320]){
      frame.style.width=width+'px';frame.style.height='800px';await tick();
      assert(doc.documentElement.scrollWidth<=width+1,'no whole-page horizontal overflow: '+width);
      const wrap=doc.querySelector('.awsum-list-table-wrap');
      assert(wrap.getBoundingClientRect().right<=width,'wide table remains inside responsive wrapper');
      for(const id of ['mgSearch','mgActiveFilter','mgPageSize','mgAddBtn']){
        const r=find(id).getBoundingClientRect();assert(r.left>=0&&r.right<=width+1,'control fits: '+id);
      }
    }
    find('mgSearch').value='sample';find('mgSearch').dispatchEvent(new win.Event('input'));
    await wait(()=>win.requests.some(r=>r.path.includes('q=sample')));
    find('mgActiveFilter').value='active';find('mgActiveFilter').dispatchEvent(new win.Event('change'));
    await wait(()=>win.requests.some(r=>r.path.includes('active=active')));
    find('mgPageSize').value='10';find('mgPageSize').dispatchEvent(new win.Event('change'));
    await wait(()=>win.requests.some(r=>r.path.includes('size=10')));
    find('mgHead').querySelector('[data-sort="name"]').click();
    await wait(()=>find('mgHead').querySelector('[data-sort="name"]').classList.contains('sorted'));
    find('mgHead').querySelector('[data-sort="name"]').dispatchEvent(new win.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
    await wait(()=>find('mgHead').querySelector('[data-sort="name"]').classList.contains('desc'));
    find('mgNext').click();await wait(()=>find('mgPageLabel').textContent==='2 / 2');
    find('mgPrev').click();await wait(()=>find('mgPageLabel').textContent==='1 / 2');
    find('mgSearch').value='empty';find('mgSearch').dispatchEvent(new win.Event('input'));
    await wait(()=>find('mgBody').textContent.includes('No records found.'));
    assert(win.requests.every(r=>r.method==='GET'&&r.path.startsWith('/platform/api/master-group/')),'existing read-only endpoints');
    frame.remove();
  }
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0,pages:5,viewports:4});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
