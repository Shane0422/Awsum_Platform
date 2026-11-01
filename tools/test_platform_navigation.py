"""Focused template and Chromium checks for the shared Platform navigation."""
from collections import Counter
from html.parser import HTMLParser
import json
import re
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from tools import test_image_components as browser


MENU_LINKS = [
    ("Dashboard", "/platform/dashboard"),
    ("Tax", "/platform/master-group/tax"),
    ("Category I", "/platform/master-group/category-1"),
    ("Category II", "/platform/master-group/category-2"),
    ("Category III", "/platform/master-group/category-3"),
    ("MD Master", "/platform/master-group/md-master"),
    ("Agency Types", "/platform/master/agency-type?v=20260322"),
    ("Business Types", "/platform/master/business-type"),
    ("Agencies", "/platform/master/agency"),
    ("Merchants", "/platform/master/merchant"),
    ("Stores", "/platform/master/store"),
    ("Device Categories", "/platform/master/device-category"),
    ("Device Types", "/platform/master/device-type"),
    ("Devices", "/platform/master/store"),
    ("Licenses", "/platform/master/license"),
    ("Pricing Plans", "/platform/master/pricing-plan"),
    ("Subscriptions", "/platform/master/subscription"),
    ("Invoices", "/platform/master/invoice"),
    ("Payments", "/platform/master/payment-method"),
    ("Roles & Permissions", "/platform/master/role"),
    ("Users", "/platform/master/user"),
    ("Sessions", "/platform/master/session"),
    ("System Settings", "/platform/system-settings"),
    ("Logout", "/auth/logout"),
]


def render_navigation(**overrides):
    environment = Environment(
        loader=FileSystemLoader(browser.ROOT / "templates"), undefined=StrictUndefined,
        autoescape=True,
    )
    context = {
        "APP_NAME": "AWSUM Platform", "company_name": "AWSUM Platform",
        "company_logo_path": "", "brand_icon_path": "", "role": "Platform Admin",
        "user": {"i_platform_user_id": 1, "c_first_name": "Test", "c_last_name": "Admin", "c_image_path": ""},
        "image_url": lambda path: path, "active_page": "dashboard", "active_module": "",
        "is_super_admin": True, "total_companies": 0, "active_stores": 0,
        "inactive_stores": 0, "total_users": 0, "recent_companies": [],
    }
    context.update(overrides)
    template = environment.get_template("platform/platform_dashboard.html")
    return template, context, template.render(context)


class MenuParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "a":
            self.current = [attrs, ""]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append((self.current[0], self.current[1].strip()))
            self.current = None


class PlatformNavigationTemplateTests(unittest.TestCase):
    def test_all_existing_menu_routes_and_disabled_items_preserved(self):
        _, _, html = render_navigation()
        parser = MenuParser()
        parser.feed(html)
        actual = [(label, attrs["href"]) for attrs, label in parser.links
                  if "platform-nav-link" in attrs.get("class", "") or
                  "platform-menu-link" in attrs.get("class", "")]
        self.assertEqual(Counter(actual), Counter(MENU_LINKS))
        self.assertNotIn("platform-sidebar", html)
        self.assertNotIn("sidebar-width", html)
        self.assertEqual(html.count('aria-disabled="true"'), 4)
        for label in ("Business Tree", "Services", "Commissions", "Audit Log"):
            self.assertRegex(html, rf'aria-disabled="true"[^>]*>{label}<')
        self.assertIn('href="/settings"', html)
        self.assertIn('href="/auth/logout"', html)

    def test_system_settings_permission_is_unchanged(self):
        _, _, html = render_navigation(is_super_admin=False)
        self.assertNotIn('href="/platform/system-settings"', html)
        self.assertIn('href="/platform/master/session"', html)

    def test_active_page_for_each_existing_route(self):
        for label, route in MENU_LINKS:
            if label in ("Devices", "Logout"):
                continue
            path = route.split("?")[0]
            if label == "Dashboard":
                page, module = "dashboard", ""
            elif label == "System Settings":
                page, module = "system-settings", ""
            else:
                page, module = path.split("/")[-2:]
            with self.subTest(label=label):
                _, _, html = render_navigation(active_page=page, active_module=module)
                parser = MenuParser()
                parser.feed(html)
                self.assertEqual(
                    [name for attrs, name in parser.links if attrs.get("aria-current") == "page"],
                    [label],
                )

    def test_dashboard_body_is_rendered_without_changes(self):
        template, context, html = render_navigation()
        expected = "".join(template.blocks["content"](template.new_context(context)))
        self.assertIn(expected, html)
        self.assertIn('<main class="platform-main">', html)


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class PlatformNavigationBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_responsive_mega_menus_and_keyboard_interactions(self):
        _, _, html = render_navigation(active_page="master", active_module="agency")
        html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', "", html, flags=re.DOTALL)
        html = re.sub(r"<link\b[^>]*>", "", html)
        css = (browser.ROOT / "static" / "bootstrap" / "css" / "bootstrap.min.css").read_text(encoding="utf-8")
        html = html.replace("</head>", f"<style>{css}</style></head>")
        serialized = json.dumps(html).replace("</", "<\\/")
        script = f"const renderedPage={serialized};\n"
        script += r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
(async()=>{
  const frame=document.createElement('iframe');
  frame.style.border='0';frame.style.maxWidth='none';frame.srcdoc=renderedPage;
  const loaded=new Promise(resolve=>frame.addEventListener('load',resolve,{once:true}));
  document.body.appendChild(frame);await loaded;
  const doc=frame.contentDocument,win=frame.contentWindow;
  const toggles=Array.from(doc.querySelectorAll('.platform-nav-toggle'));
  assert(!doc.querySelector('aside,.platform-sidebar'),'left Sidebar is removed, not hidden');
  const order=Array.from(doc.querySelector('.platform-nav-row').children).map(node=>node.textContent.trim().replace(/\s*[\u25be\u25bc]$/,''));
  assert(JSON.stringify(order)===JSON.stringify(['Dashboard','Master Group','Business','Device & License','Reports']),'required top menu order');
  assert(doc.getElementById('nav-business').classList.contains('has-active'),'current group highlighted');
  for(const [width,height] of [[1440,900],[1024,768],[768,700],[375,667],[320,568],[1280,240]]){
    frame.style.width=width+'px';frame.style.height=height+'px';await tick();
    for(const toggle of toggles){
      if(toggle.getAttribute('aria-expanded')==='true')toggle.click();
      toggle.focus();toggle.click();
      const panel=doc.getElementById(toggle.getAttribute('aria-controls'));
      assert(!panel.hidden&&toggle.getAttribute('aria-expanded')==='true','click opens menu');
      assert(doc.querySelectorAll('.platform-mega-menu:not([hidden])').length===1,'one open menu');
      const rect=panel.getBoundingClientRect();
      assert(rect.left>=0&&rect.right<=width+1,'menu width fits '+width);
      assert(rect.bottom<=height+1,'menu height fits '+width+'x'+height);
      assert(panel.scrollWidth<=panel.clientWidth+1,'no menu horizontal clipping');
      assert(doc.documentElement.scrollWidth<=width+1,'no page horizontal overflow');
      assert(doc.querySelector('.platform-main').getBoundingClientRect().left===0,'no Sidebar left offset');
      const content=doc.querySelector('.platform-main').getBoundingClientRect();
      doc.dispatchEvent(new win.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
      assert(panel.hidden&&doc.activeElement===toggle,'Escape closes and restores focus');
      assert(doc.querySelector('.platform-main').getBoundingClientRect().top===content.top,'menu does not move page body');
    }
  }
  frame.style.width='1440px';frame.style.height='900px';await tick();
  const business=doc.getElementById('nav-business');
  business.focus();business.dispatchEvent(new win.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
  const agency=doc.querySelector('a[href="/platform/master/agency"]');
  assert(doc.activeElement===doc.querySelector('#mega-business a'),'ArrowDown opens and focuses first link');
  assert(win.getComputedStyle(agency).backgroundColor==='rgb(231, 240, 254)','selected item is light blue');
  assert(agency.getAttribute('aria-current')==='page','current page exposed accessibly');
  doc.getElementById('nav-master-group').click();
  assert(doc.getElementById('mega-business').hidden,'opening next group closes previous');
  doc.querySelector('.platform-main').click();
  assert(doc.querySelectorAll('.platform-mega-menu:not([hidden])').length===0,'outside click closes');
  business.focus();business.click();
  doc.getElementById('userDropdown').focus();await tick();
  assert(doc.getElementById('mega-business').hidden,'tabbing out closes menu');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0,viewports:6});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_browser(script)


if __name__ == "__main__":
    unittest.main()
