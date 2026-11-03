"""Shared admin skin coverage using rendered templates, without API/DB changes."""
import json
import re
import unittest

from tools import test_image_components as browser
from tools.test_platform_navigation import render_navigation


MODULES = (
    "agency-type", "business-type", "agency", "merchant", "store",
    "device-category", "device-type", "license", "pricing-plan", "subscription",
    "invoice", "contract", "payment-method", "role",
)
OTHER_PAGES = (
    ("users.html", "master", "user"), ("sessions.html", "master", "session"),
    ("system_settings.html", "system-settings", ""),
    ("platform_stores.html", "stores", ""), ("merchants_tree.html", "merchants-tree", ""),
)


def render_admin(filename, page, module):
    base, context, _ = render_navigation()
    context.update(
        active_page=page, active_module=module, brand_logo_path="",
        roles=[], business_types=[], merchants=[], agencies=[], agency_types=[],
        stores=[], pricing_plans=[], device_categories=[], device_types=[],
        filter={"status": "", "business_type": "", "channel_type": ""},
    )
    return base.environment.get_template(f"platform/{filename}").render(context)


class PlatformAdminTemplateTests(unittest.TestCase):
    def test_every_management_module_reuses_single_shared_stylesheet(self):
        for filename, page, module in [
            *(("master_management.html", "master", module) for module in MODULES),
            *OTHER_PAGES,
        ]:
            with self.subTest(page=filename, module=module):
                html = render_admin(filename, page, module)
                self.assertEqual(html.count('href="/static/css/awsum-list.css?v=3"'), 1)
                self.assertIn('class="platform-view awsum-list awsum-admin"', html)
                self.assertGreater(html.rfind('href="/static/css/awsum-list.css'), html.rfind("</style>"))
                if filename == "master_management.html":
                    for control in ("masterSearch", "masterListBody", "btnListAdd",
                                    "btnListEdit", "btnListDelete", "btnListExport",
                                    "masterForm", "masterEditModal"):
                        self.assertIn(f'id="{control}"', html)
                if filename == "system_settings.html":
                    self.assertEqual(html.count('role="tab"'), 4)
                if filename != "merchants_tree.html":
                    self.assertIn('awsum-page-header', html)
                    self.assertIn('awsum-filter-card', html)
                    self.assertIn('awsum-settings-card' if filename == "system_settings.html" else 'awsum-table-card', html)

    def test_dashboard_does_not_use_admin_skin(self):
        _, _, html = render_navigation()
        self.assertNotIn('class="platform-view awsum-list', html)
        self.assertNotIn('href="/static/css/awsum-list.css', html)


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class PlatformAdminBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_shared_visuals_and_preserved_layouts(self):
        root = browser.ROOT
        css = "\n".join((root / name).read_text(encoding="utf-8") for name in (
            r"static\bootstrap\css\bootstrap.min.css", r"static\css\brand.css",
            r"static\css\platform-modal.css",
        ))
        shared = (root / "static" / "css" / "awsum-list.css").read_text(encoding="utf-8")
        source = (root / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        layout = re.search(r"  function applyMasterLayoutMode\(\) \{.*?\n  \}", source, re.DOTALL).group()
        pages = []
        for filename, page, module in [
            *(("master_management.html", "master", module) for module in MODULES), *OTHER_PAGES,
        ]:
            html = render_admin(filename, page, module)
            html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            html = re.sub(r"<link\b[^>]*>", "", html)
            html = html.replace("</head>", f"<style>{css}</style></head>")
            html = html.replace("</body>", f"<style>{shared}</style></body>")
            pages.append({"html": html, "module": module, "filename": filename})
        serialized = json.dumps(pages).replace("</", "<\\/")
        script = f"const pages={serialized};const layout={json.dumps(layout)};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
(async()=>{
  for(const fixture of pages){
    const frame=document.createElement('iframe');frame.style.border='0';frame.style.width='1440px';frame.style.height='900px';
    frame.srcdoc=fixture.html;
    const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
    document.body.appendChild(frame);await loaded;
    const doc=frame.contentDocument,win=frame.contentWindow;
    const root=doc.querySelector('.awsum-admin');
    assert(root,'shared skin applied');
    if(fixture.filename!=='merchants_tree.html'){
      const header=root.querySelector('.awsum-page-header');
      const filters=root.querySelector('.awsum-filter-card');
      const content=root.querySelector('.awsum-table-card,.awsum-settings-card');
      assert(header&&filters&&content,'shared header/filter/content structure');
      assert(!content.contains(header)&&!content.contains(filters),'header and filters are outside table card');
      assert(!!(header.compareDocumentPosition(filters)&4)&&!!(filters.compareDocumentPosition(content)&4),'header then filters then content');
    }
    if(fixture.filename==='master_management.html'){
      win.eval("const activeModule="+JSON.stringify(fixture.module)+";const isListCentricModule=true;"+
        "const masterListCol=document.getElementById('masterListCol'),masterEditorCol=document.getElementById('masterEditorCol'),masterListToolbar=document.getElementById('masterListToolbar');"+
        layout+";applyMasterLayoutMode();");
      assert(doc.getElementById('masterEditorCol').classList.contains('d-none'),'existing list-centric editor visibility');
    }
    const table=root.querySelector('.table');
    if(table){
      table.querySelector('thead').innerHTML='<tr><th class="sortable-col" tabindex="0">Code</th><th>Name</th><th>Status</th><th>Actions</th></tr>';
      table.querySelector('tbody').innerHTML='<tr><td>TEST00001</td><td>Sample</td><td><span class="badge text-bg-success">Active</span></td><td><button class="btn btn-outline-danger btn-sm" disabled>Delete</button></td></tr>';
      const head=win.getComputedStyle(table.querySelector('th'));
      assert(head.color==='rgb(16, 46, 104)'&&head.backgroundColor==='rgb(248, 251, 255)','same table palette: '+fixture.module);
      assert(win.getComputedStyle(table.querySelector('.badge')).backgroundColor==='rgb(220, 248, 232)','same status badge');
      assert(table.querySelector('button').disabled,'disabled controls stay disabled');
    }
    const card=root.querySelector('.awsum-table-card,.card,.card-modern,.platform-users-table-wrap,.tree-card');
    assert(win.getComputedStyle(card).borderRadius==='12px','same card rounding');
    const primary=root.querySelector('.btn-primary,.btn-dark');
    if(primary)assert(win.getComputedStyle(primary).backgroundColor==='rgb(18, 99, 235)','same primary action');
    const input=root.querySelector('.form-control');
    if(input){
      assert(win.getComputedStyle(input).borderRadius==='7px','same inputs');
      input.classList.add('is-invalid');
      assert(win.getComputedStyle(input).borderColor!=='rgb(213, 226, 244)','validation border preserved');
      input.classList.remove('is-invalid');
      input.disabled=true;
      assert(win.getComputedStyle(input).backgroundColor==='rgb(243, 246, 251)','disabled field visibly distinct');
      input.disabled=false;
    }
    for(const width of [1440,768,375]){
      frame.style.width=width+'px';await tick();
      assert(doc.documentElement.scrollWidth<=width+1,'no page overflow: '+fixture.filename+'/'+fixture.module+' '+width);
      if(fixture.filename!=='merchants_tree.html'){
        const header=root.querySelector('.awsum-page-header').getBoundingClientRect();
        const filters=root.querySelector('.awsum-filter-card').getBoundingClientRect();
        const content=root.querySelector('.awsum-table-card,.awsum-settings-card').getBoundingClientRect();
        assert(header.bottom<=filters.top+1&&filters.bottom<=content.top+1,'visible header/filter/table vertical order: '+fixture.module+' '+width);
      }
      const wrap=root.querySelector('.table-responsive,.platform-users-table-wrap');
      if(wrap)assert(win.getComputedStyle(wrap).overflowX==='auto','existing table scrolling');
    }
    if(fixture.module==='store'){
      assert(doc.getElementById('masterRoleView').classList.contains('store-split'),'store split retained');
      assert(!doc.getElementById('storeSplitter').classList.contains('d-none'),'store resize control retained');
      assert(doc.getElementById('storeToolsArea').contains(doc.getElementById('masterSearchWrap')),'filters in shared card');
      assert(!doc.getElementById('storeToolsArea').contains(doc.getElementById('masterListHead')),'store page header stays above filter card');
    }
    if(fixture.filename==='system_settings.html'){
      assert(root.querySelectorAll('[role="tab"]').length===4,'all settings tabs preserved');
      const activeTab=win.getComputedStyle(root.querySelector('.nav-link.active'));
      assert(activeTab.backgroundColor==='rgb(255, 255, 255)'&&activeTab.color==='rgb(16, 46, 104)'&&activeTab.boxShadow.includes('rgb(18, 99, 235)'),'shared active tab');
    }
    frame.remove();
  }
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0,pages:pages.length,viewports:3});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
