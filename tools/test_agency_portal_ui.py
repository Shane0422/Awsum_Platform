"""Real Chromium checks of shared Agency navigation, list layout, and form errors."""
import json
import re
from types import SimpleNamespace
import unittest

from backend.routers.agency_portal import fields
from backend.services.agency_permissions import AgencyPortalPermissions
from tools import test_image_components as browser
from tools.test_platform_navigation import render_navigation


def portal_fixture(section, kind=None, **branding):
    base, context, _ = render_navigation()
    context.update(
        active_page="agency-portal", section=section, title="Agency Dashboard" if section == "dashboard" else "", kind=kind or section,
        permissions=AgencyPortalPermissions(SimpleNamespace(i_agency_id=1, i_platform_user_id=1)),
        agency=SimpleNamespace(c_agency_name="Test Agency", c_agency_code="AGT00001"),
        counts={"Merchants": 1, "Stores": 1, "Devices": 1, "Users": 1},
        profile={"name": "Test Admin", "email": "login@example.test", "phone": "555-1111"},
        fields=fields(section), rows=[{"id": 1, "code": "TEST00001", "name": "Test Record",
                                      "email": "login@example.test", "phone": "555-1111", "status": "active"}],
        q="", status="", page=1, size=20, total=1, pages=1, list_path=f"/agency/{section}",
    )
    if kind == "devices":
        context.update(title="Test Store - Devices", fields=[], list_path="/agency/stores/1/devices")
    if section == "users":
        context["rows"].append({**context["rows"][0], "id": 2, "name": "Other Admin"})
    context.update(branding)
    return base.environment.get_template("agency/portal.html").render(context)


class AgencyPortalTemplateTests(unittest.TestCase):
    def test_agency_templates_have_no_duplicate_styles_or_common_layout_markup(self):
        for name in ("base.html", "portal.html"):
            source = (browser.ROOT / "templates" / "agency" / name).read_text(encoding="utf-8")
            self.assertNotIn("<style", source)
            self.assertNotIn('style=', source)
            self.assertNotIn("<table", source)
            self.assertNotIn('class="modal', source)
        self.assertIn('--platform-brand-scale: 1.5', portal_fixture("dashboard"))
        self.assertIn('--platform-brand-scale: 1.5', render_navigation()[2])
        source = (browser.ROOT / "templates" / "agency" / "base.html").read_text(encoding="utf-8")
        self.assertEqual(source.strip(), '{% extends "platform/platform_base.html" %}')

    def test_every_page_reuses_common_shell_and_does_not_show_platform_actions(self):
        for section in ("dashboard", "merchants", "stores", "users", "reports", "settings"):
            with self.subTest(section=section):
                html = portal_fixture(section)
                self.assertIn('class="platform-view awsum-list awsum-admin"', html)
                self.assertIn("awsum-page-header", html)
                self.assertEqual(html.count('href="/static/css/awsum-list.css'), 1)
                navigation = html.split("<nav")[1].split("</nav>")[0]
                for key in ("dashboard", "merchants", "stores", "users", "reports", "settings"):
                    self.assertIn(f'href="/agency/{key}"', navigation)
                self.assertNotIn('href="/platform/', navigation)
                self.assertNotIn('href="/platform/', html)
                self.assertNotIn("Device &amp; License", navigation)
                if section in ("merchants", "stores", "users"):
                    self.assertIn("awsum-filter-card", html)
                    self.assertIn("awsum-table-card", html)
                if section in ("merchants", "stores"):
                    self.assertIn("Read Only.", html)
                    self.assertNotIn('data-bs-target="#edit-', html)
                    self.assertNotIn('data-awsum-form', html)
                if section == "users":
                    self.assertIn('data-bs-target="#delete-2"', html)
                    self.assertNotIn('data-bs-target="#delete-1"', html)
                    self.assertIn('data-method="DELETE"', html)


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class AgencyPortalBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_platform_and_agency_share_header_dimensions_and_styles(self):
        css = "\n".join((browser.ROOT / name).read_text(encoding="utf-8") for name in (
            r"static\bootstrap\css\bootstrap.min.css", r"static\css\brand.css",
        ))
        pages = [render_navigation()[2], portal_fixture("dashboard")]
        pages = [
            re.sub(r"<link\b[^>]*>", "", re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL))
            .replace("</head>", f"<style>{css}</style></head>")
            for html in pages
        ]
        serialized = json.dumps(pages).replace("</", "<\\/")
        script = f"const pages={serialized};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
(async()=>{
  const frames=[];
  for(const html of pages){
    const frame=document.createElement('iframe');frame.style.border='0';frame.srcdoc=html;
    const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
    document.body.appendChild(frame);await loaded;frames.push(frame);
  }
  for(const width of [1440,1200,1024,768,375,320]){
    for(const frame of frames){frame.style.width=width+'px';frame.style.height='900px';}
    await new Promise(resolve=>setTimeout(resolve,40));
    const measurements=frames.map(frame=>{
      const doc=frame.contentDocument,win=frame.contentWindow;
      const style=selector=>{
        const css=win.getComputedStyle(doc.querySelector(selector));
        return [css.height,css.fontSize,css.padding,css.backgroundColor,css.color,css.borderRadius].join('|');
      };
      assert(doc.documentElement.scrollWidth<=width+1,'shared header fits '+width);
      return {
        height:doc.querySelector('.platform-header').getBoundingClientRect().height,
        brand:style('.brand-text'),profile:style('#userDropdown'),menu:style('.platform-nav-link.active')
      };
    });
    assert(JSON.stringify(measurements[0])===JSON.stringify(measurements[1]),'identical header dimensions/styles '+width+' '+JSON.stringify(measurements));
  }
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)

    def test_responsive_navigation_forms_and_read_only_devices(self):
        root = browser.ROOT
        css = "\n".join((root / name).read_text(encoding="utf-8") for name in (
            r"static\bootstrap\css\bootstrap.min.css", r"static\css\brand.css",
            r"static\css\platform-modal.css", r"static\css\awsum-list.css",
        ))
        pages = []
        for section, kind in (
            ("dashboard", None), ("merchants", None), ("stores", None),
            ("users", None), ("reports", None), ("settings", None), ("stores", "devices"),
        ):
            html = portal_fixture(section, kind)
            html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            html = re.sub(r"<link\b[^>]*>", "", html)
            html = html.replace("</body>", f"<style>{css}</style></body>")
            pages.append({"section": section, "kind": kind, "html": html})
        image_html = portal_fixture("dashboard", company_logo_path="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='26'%3E%3Crect width='160' height='26' fill='navy'/%3E%3C/svg%3E")
        image_html = re.sub(r"<script\b[^>]*>.*?</script>", "", image_html, flags=re.DOTALL)
        image_html = re.sub(r"<link\b[^>]*>", "", image_html)
        pages.append({"section": "dashboard", "kind": None, "html": image_html.replace("</body>", f"<style>{css}</style></body>")})
        forms = (root / "static" / "js" / "management-form.js").read_text(encoding="utf-8")
        serialized = json.dumps(pages).replace("</", "<\\/")
        script = f"const pages={serialized};const forms={json.dumps(forms)};\n" + r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
(async()=>{
  for(const item of pages){
    const frame=document.createElement('iframe');frame.style.border='0';frame.srcdoc=item.html;
    const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
    document.body.appendChild(frame);await loaded;
    const doc=frame.contentDocument,win=frame.contentWindow;
    assert(doc.querySelector('.platform-nav-link.active').getAttribute('href')==='/agency/'+item.section,'selected Agency menu');
    for(const width of [1440,768,375,320]){
      frame.style.width=width+'px';frame.style.height='800px';await tick();
      assert(doc.documentElement.scrollWidth<=width+1,'page fits: '+item.section+' '+width);
      const header=doc.querySelector('.platform-header'),brand=doc.querySelector('.company-logo,.brand-text');
      header.style.setProperty('--platform-brand-scale','1');
      const beforeHeight=header.getBoundingClientRect().height;
      const beforeBrand=brand.getBoundingClientRect();
      const beforeFont=parseFloat(win.getComputedStyle(brand).fontSize);
      header.style.setProperty('--platform-brand-scale','1.5');
      const afterBrand=brand.getBoundingClientRect();
      assert(Math.abs(header.getBoundingClientRect().height-beforeHeight)<1,'header height unchanged: '+width);
      if(brand.matches('.brand-text')){
        assert(Math.abs(parseFloat(win.getComputedStyle(brand).fontSize)/beforeFont-1.5)<0.01,'text logo enlarged 1.5x');
      }else if(width>=768){
        assert(Math.abs(afterBrand.height/beforeBrand.height-1.5)<0.01,'image height enlarged 1.5x');
        assert(Math.abs(afterBrand.width/beforeBrand.width-1.5)<0.01,'image width enlarged 1.5x');
      }
      assert(afterBrand.top>=header.getBoundingClientRect().top&&afterBrand.bottom<=header.getBoundingClientRect().bottom,'logo stays inside header');
      assert(afterBrand.right<=doc.querySelector('.topbar-meta').getBoundingClientRect().left,'logo does not overlap account menu');
    }
    if(item.kind==='devices'||['merchants','stores'].includes(item.section)){
      assert(!doc.querySelector('form[data-awsum-form]'),'read-only page has no editor');
      assert(!doc.querySelector('[data-bs-target]'),'no read-only mutation modal');
    }
    if(item.section==='users'){
      const form=doc.querySelector('#add-user form');
      const modal=doc.getElementById('add-user');modal.style.display='block';modal.removeAttribute('aria-hidden');
      let calls=[],release;
      win.fetch=(path,options)=>{calls.push({path,payload:JSON.parse(options.body)});return new Promise(resolve=>release=()=>resolve(new win.Response(JSON.stringify({detail:'Duplicate Agency Email'}),{status:409})));};
      win.eval(forms);
      for(const [name,value] of Object.entries({name:'New Admin',email:'alternate@example.test',phone:'123',password:'Secret!42',confirm_password:'Secret!42'}))form.elements[name].value=value;
      form.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));
      form.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));
      assert(calls.length===1&&form.querySelector('[type=submit]').disabled,'duplicate submits blocked');
      assert(Object.keys(calls[0].payload).sort().join(',')==='confirm_password,email,name,password,phone,status','no scope or role submitted');
      release();await tick();
      assert(form.querySelector('[data-form-error]').textContent==='Duplicate Agency Email','server error surfaced');
      assert(!form.querySelector('[type=submit]').disabled,'failure permits explicit retry');
      assert(doc.querySelector('#edit-1 form').getAttribute('data-method')==='PUT','existing-user edit does not recreate');
      const deleteForm=doc.querySelector('#delete-2 form');
      assert(deleteForm.getAttribute('data-method')==='DELETE','delete reuses common form');
      assert(!doc.querySelector('#delete-1'),'cannot delete own account');
      const deleteModal=doc.getElementById('delete-2');deleteModal.style.display='block';deleteModal.removeAttribute('aria-hidden');
      deleteForm.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));
      assert(calls.length===2&&Object.keys(calls[1].payload).length===0,'delete submits no identity fields');
      release();await tick();
      assert(deleteForm.querySelector('[data-form-error]').textContent==='Duplicate Agency Email','delete error surfaced');
    }
    if(item.section==='settings'){
      assert(doc.querySelector('[name=email]').readOnly,'settings Login Email cannot be edited');
      assert(doc.querySelector('a[href="/auth/change-password"]'),'reuses Change Password');
    }
    frame.remove();
  }
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0,pages:8,viewports:4});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
