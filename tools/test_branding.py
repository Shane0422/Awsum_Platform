import json
import re
import shutil
import subprocess
import tempfile
from io import BytesIO
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import templates as template_config
from backend.database.pg_platform import PlatformBase
from backend.main import app
from backend.models_admin.merchant import Merchant
from backend.models_admin.agency import Agency
from backend.models_admin.role import Role
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.session import SessionTbl
from backend.models_admin.store import Store
from backend.models_admin.system_settings import SystemSettings
from backend.routers import auth, dashboard, platform_store, store
from backend.services import branding, images, web_sessions
from tools.test_web_sessions import PASSWORD, request, seed


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        PlatformBase.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        seed(self.factory)
        self.temp = tempfile.TemporaryDirectory(prefix="awsum-branding-")
        self.addCleanup(self.temp.cleanup)
        self.static = Path(self.temp.name)
        self.patch_root = patch.object(images, "STATIC_ROOT", self.static)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        storage_patch = patch.object(images, "image_storage_root", return_value=self.static / "media")
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        import shutil
        from backend.services.builtin_assets import BUILTIN_ROOT
        shutil.copytree(BUILTIN_ROOT, self.static / "builtin")
        builtin_patch = patch("backend.services.builtin_assets.BUILTIN_ROOT", self.static / "builtin")
        builtin_patch.start()
        self.addCleanup(builtin_patch.stop)
        for key in ("platform", "agency", "merchant", "store"):
            path = self.static / "images" / "brands" / key / "logo.png"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"fixture-logo")
        fallback = self.static / "builtin" / "logo" / "logo_full.png"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_bytes(b"default-platform-logo")
        with self.factory() as db:
            db.get(SystemSettings, 1).c_logo_url = "/static/images/brands/platform/logo.png"
            db.get(Agency, 1).c_logo_url = "/static/images/brands/agency/logo.png"
            db.get(Merchant, 1).c_logo_url = "/static/images/brands/merchant/logo.png"
            db.get(Merchant, 1).i_agency_id = 1
            db.get(Store, 2).c_logo_url = "/static/images/brands/store/logo.png"
            db.commit()
        def dependency():
            with self.factory() as db:
                yield db
        overrides = {module.get_db: dependency for module in (auth, platform_store, dashboard, store)}
        for p in (
            patch.dict(app.dependency_overrides, overrides),
            patch("backend.main.PlatformSessionLocal", self.factory),
            patch("backend.services.web_session_http.PlatformSessionLocal", self.factory),
            patch("backend.services.reporting.PlatformSessionLocal", self.factory),
            patch.object(template_config, "PlatformSessionLocal", self.factory),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(app, follow_redirects=False)
        self.addCleanup(self.client.close)

    def brand_data(self, response):
        self.assertEqual(response.status_code, 200, response.text[:200])
        content = re.search(r'<script id="loginBrandingData" type="application/json">(.*?)</script>',
                            response.text, re.S).group(1)
        return json.loads(content)

    def test_explicit_resolver_and_full_logo_inheritance(self):
        with self.factory() as db:
            self.assertIn("/platform/", branding.resolve_branding(db).logo_url)
            agency = branding.resolve_branding(db, "AGENCY", agency_id=1)
            self.assertEqual(agency.organization_name, "Test Agency")
            self.assertIn("/agency/", agency.logo_url)
            merchant = branding.resolve_branding(db, "MERCHANT", merchant_id=1)
            self.assertEqual(merchant.organization_name, "Customer Merchant")
            self.assertIn("/merchant/", merchant.logo_url)
            brand = branding.resolve_branding(db, "STORE", store_id=2)
            self.assertEqual(brand.organization_name, "Test Store")
            self.assertIn("/store/", brand.logo_url)
            db.get(Store, 2).c_logo_url = None
            db.flush()
            self.assertIn("/merchant/", branding.resolve_branding(db, "STORE", store_id=2).logo_url)
            db.get(Merchant, 1).c_logo_url = None
            db.flush()
            self.assertIn("/agency/", branding.resolve_branding(db, "STORE", store_id=2).logo_url)
            self.assertIn("/agency/", branding.resolve_branding(db, "MERCHANT", merchant_id=1).logo_url)
            db.get(Agency, 1).c_logo_url = None
            db.flush()
            for context, ids in (("STORE", {"store_id": 2}), ("MERCHANT", {"merchant_id": 1}),
                                 ("AGENCY", {"agency_id": 1})):
                self.assertIn("/platform/", branding.resolve_branding(db, context, **ids).logo_url)

    def test_platform_default_and_text_fallback_cache_busting_and_safe_assets(self):
        with self.factory() as db:
            settings = db.get(SystemSettings, 1)
            settings.c_logo_url = None
            db.flush()
            (self.static / "images" / "brands" / "platform" / "logo.png").unlink()
            logo = branding.resolve_branding(db).logo_url
            self.assertIn("logo_full.png?v=", logo)
            file = self.static / "builtin" / "logo" / "logo_full.png"
            file.write_bytes(b"updated platform logo")
            import os
            os.utime(file, ns=(file.stat().st_atime_ns, file.stat().st_mtime_ns + 1000000))
            self.assertNotEqual(logo, branding.resolve_branding(db).logo_url)
            file.unlink()
            with self.assertRaises(FileNotFoundError):
                branding.resolve_branding(db)
        for url in ("https://attacker.test/logo.png", "/static/../secret.png",
                    "/static/images/brands/../../secret.png", "/static/images/brands/%2e%2e/a.png",
                    "/static/images/brands/a.svg", "javascript:alert(1)"):
            self.assertIsNone(branding.versioned_logo(url))

    def test_public_entries_ignore_previous_store_session_and_keep_platform_expiry_brand(self):
        with self.factory() as db:
            token = web_sessions.create_session(db, db.get(PlatformUser, 3), request(), {"scope_type": "store"})
        self.client.cookies.set("access_token", token, domain="testserver.local", path="/")
        for path in ("/platform-admin", "/platform-admin?reason=session_expired"):
            data = self.brand_data(self.client.get(path))
            self.assertEqual(data["context_type"], "PLATFORM")
            self.assertIn("/platform/", data["logo_url"])
            self.assertNotIn("/store/", data["logo_url"])
            self.assertEqual(data["description"], "Platform Administration")
        for context, code, name, logo in (
            ("agency", "AGT00001", "Test Agency", "/agency/"),
            ("merchant", "CLT11001", "Customer Merchant", "/merchant/"),
            ("store", "STR00002", "Test Store", "/store/"),
        ):
            response = self.client.get(f"/login/{context}/{code}?reason=session_expired")
            data = self.brand_data(response)
            self.assertEqual(data["context_type"], context.upper())
            self.assertEqual(data["organization_name"], name)
            self.assertIn(logo, data["logo_url"])
            self.assertIn("no-store", response.headers["cache-control"])
        # Revoked cookies must not block a public login entry.
        with self.factory() as db:
            web_sessions.terminate_session(db, db.query(SessionTbl).one().i_session_id, 1)
        self.assertEqual(self.brand_data(self.client.get("/login/store/STR00002"))["context_type"], "STORE")

    def test_access_code_api_public_fields_ambiguity_and_inactive_organizations(self):
        response = self.client.get("/auth/branding?access_code=clt11001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {
            "context_type", "organization_name", "logo_url", "brand_name",
            "login_entry", "access_code", "description",
        })
        self.assertEqual(response.json()["context_type"], "MERCHANT")
        self.assertEqual(self.client.get("/auth/branding?access_code=bad").status_code, 404)
        with self.factory() as db:
            db.get(Store, 2).c_store_code = "CLT11001"
            db.commit()
        self.assertEqual(self.client.get("/auth/branding?access_code=CLT11001").status_code, 409)
        self.assertEqual(self.client.get("/auth/branding?access_code=CLT11001&context_type=MERCHANT").status_code, 200)
        with self.factory() as db:
            db.get(Merchant, 1).c_status = "inactive"
            db.commit()
        self.assertEqual(self.client.get("/login/merchant/CLT11001").status_code, 404)
        self.assertEqual(self.client.get("/login/platform/ADMIN").status_code, 404)

    def test_platform_login_logout_and_nonplatform_endpoints_remain_restricted(self):
        data = {"access_code": "ADMIN", "login_mode": "platform_admin",
                "email": "user1@example.test", "password": PASSWORD}
        self.assertEqual(self.client.post("/platform-admin/login", data=data).status_code, 303)
        response = self.client.get("/auth/logout")
        self.assertEqual(response.headers["location"], "/platform-admin")
        self.assertEqual(self.brand_data(self.client.get(response.headers["location"]))["context_type"], "PLATFORM")
        self.assertEqual(self.client.post("/platform-admin/login", data=data).status_code, 303)
        with self.factory() as db:
            from datetime import timedelta
            row = db.query(SessionTbl).filter_by(c_status="ACTIVE").one()
            row.dt_expires = web_sessions.utcnow() - timedelta(seconds=1)
            db.commit()
        expired = self.client.get("/platform/sessions")
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(expired.headers["x-awsum-login"], "/platform-admin?reason=session_expired")
        brand = self.brand_data(self.client.get(expired.headers["x-awsum-login"]))
        self.assertEqual(brand["context_type"], "PLATFORM")
        self.assertIn("/platform/", brand["logo_url"])
        # Store Portal login (organization login, auth.REGISTRATION_OWNERS): the Store's admin (StoreAdmin role) signs in
        # with the Store code and gets one STORE session for that Store.
        result = self.client.post("/auth/login", data={
            "store_code": "STR00002", "email": "store@example.test", "password": PASSWORD,
        })
        self.assertEqual(result.status_code, 303, result.text)
        with self.factory() as db:
            row = db.query(SessionTbl).filter_by(i_platform_user_id=3, c_status="ACTIVE").one()
            self.assertEqual((row.c_login_context, row.i_store_id, row.i_agency_id), ("STORE", 2, None))
        self.client.get("/auth/logout")
        # Cross-organization access is refused: the Store admin with another organization's code, and a Platform user
        # with the Store code (not an admin of that Store).
        for code, email in (("CLT11001", "store@example.test"), ("AGT00001", "store@example.test"),
                            ("STR00002", "user1@example.test")):
            with self.subTest(code=code, email=email):
                denied = self.client.post("/auth/login", data={"store_code": code, "email": email, "password": PASSWORD})
                self.assertEqual(denied.status_code, 401, denied.text)
                self.assertEqual(denied.json()["message"], "Invalid credentials.")
                self.assertNotIn("access_token", denied.cookies)
        with self.factory() as db:
            self.assertEqual(db.query(SessionTbl).filter_by(i_platform_user_id=3, c_status="ACTIVE").count(), 0)

    def organization_admins(self):
        """Agency Admin of Agency 1 and Merchant Admin of Merchant 1, as session_context.login_identity requires:
        the organization's admin role, its organization id and the HQ Store; plus Agency 2 / Merchant 2 for denial."""
        from backend.database.pg_platform import ADMIN_STORE_ID
        with self.factory() as db:
            db.add(Role(i_role_id=99, c_name="Agency Admin", c_status="active"))
            db.add(Role(i_role_id=98, c_name="Merchant Admin", c_status="active"))
            db.add(Agency(i_agency_id=2, c_agency_code="AGT00002", c_agency_name="Other Agency", c_status="active"))
            db.add(Merchant(i_merchant_id=2, c_merchant_code="CLT11002", c_merchant_name="Other Merchant", c_status="active"))
            db.get(Agency, 1).c_status = "active"
            db.get(Merchant, 1).c_status = "active"
            hashed = db.get(PlatformUser, 3).c_password
            for user_id, role_id, agency_id, merchant_id in ((41, 99, 1, None), (42, 98, None, 1)):
                db.add(PlatformUser(i_platform_user_id=user_id, i_store_id=ADMIN_STORE_ID, i_role_id=role_id,
                                    i_agency_id=agency_id, i_merchant_id=merchant_id, c_email=f"org{user_id}@example.test",
                                    c_password=hashed, c_first_name="Org", c_last_name="Admin", c_status="active",
                                    i_must_change_password=0))
            db.commit()

    def test_every_context_preserves_its_entry_on_logout_and_termination(self):
        from fastapi import HTTPException
        self.organization_admins()
        # Cross-organization access is refused: an organization admin cannot open a session for another organization.
        with self.factory() as db:
            for user_id, claims in ((41, {"scope_type": "agency", "agency_id": 2}),
                                    (42, {"scope_type": "merchant", "merchant_id": 2}),
                                    (41, {"scope_type": "merchant", "merchant_id": 1}),
                                    (3, {"scope_type": "agency", "agency_id": 1})):
                with self.subTest(denied=user_id, claims=claims), self.assertRaises(HTTPException) as refused:
                    web_sessions.create_session(db, db.get(PlatformUser, user_id), request(), claims)
                self.assertEqual(refused.exception.status_code, 401)
            self.assertEqual(db.query(SessionTbl).count(), 0)
        for context, claims, expected, user_id in (
            ("agency", {"agency_id": 1}, "/login/agency/AGT00001", 41),
            ("merchant", {"merchant_id": 1}, "/login/merchant/CLT11001", 42),
            ("store", {}, "/login/store/STR00002", 3),
        ):
            with self.subTest(context=context):
                with self.factory() as db:
                    token = web_sessions.create_session(db, db.get(PlatformUser, user_id), request(),
                                                        {"scope_type": context, **claims})
                    row = db.query(SessionTbl).filter_by(c_status="ACTIVE").one()
                    self.assertEqual(row.c_login_path, expected)
                    web_sessions.terminate_session(db, row.i_session_id, 1)
                self.client.cookies.set("access_token", token, domain="testserver.local", path="/")
                rejected = self.client.get("/platform/sessions")
                self.assertEqual(rejected.status_code, 401)
                self.assertEqual(rejected.headers["x-awsum-login"], expected + "?reason=session_expired")
                self.client.cookies.set("access_token", token, domain="testserver.local", path="/")
                self.assertEqual(self.client.get("/auth/logout").headers["location"], expected)
                self.assertEqual(self.brand_data(self.client.get(expected))["context_type"], context.upper())
                with self.factory() as db:
                    token = web_sessions.create_session(db, db.get(PlatformUser, user_id), request(),
                                                        {"scope_type": context, **claims})
                self.client.cookies.set("access_token", token, domain="testserver.local", path="/")
                self.assertEqual(self.client.get("/auth/logout").headers["location"], expected)
                with self.factory() as db:
                    row = db.query(SessionTbl).order_by(SessionTbl.i_session_id.desc()).first()
                    self.assertEqual(row.c_status, "LOGGED_OUT")

    def test_logo_uploads_persist_references_and_reset_activates_inheritance(self):
        from PIL import Image
        from backend.services.branding import resolve_branding
        data = {"access_code": "ADMIN", "login_mode": "platform_admin",
                "email": "user1@example.test", "password": PASSWORD}
        self.assertEqual(self.client.post("/platform-admin/login", data=data).status_code, 303)
        image = BytesIO()
        Image.new("RGB", (120, 40), "blue").save(image, format="PNG")
        with patch.object(images, "image_storage_root", return_value=self.static / "media"):
            for route, model, key in (("merchant/1", Merchant, 1), ("store/2", Store, 2), ("agency/1", Agency, 1)):
                response = self.client.post(f"/platform/{route}/logo",
                    files={"file": ("logo.png", image.getvalue(), "image/png")},
                    headers={"Origin": "http://testserver"})
                self.assertEqual(response.status_code, 200, response.text)
                with self.factory() as db:
                    self.assertEqual(db.get(model, key).c_logo_url, response.json()["image_path"])
                    self.assertTrue(branding.versioned_logo(db.get(model, key).c_logo_url))
            for route, model, key in (("store/2", Store, 2), ("merchant/1", Merchant, 1), ("agency/1", Agency, 1)):
                response = self.client.delete(f"/platform/{route}/logo", headers={"Origin": "http://testserver"})
                self.assertEqual(response.status_code, 200, response.text)
                with self.factory() as db:
                    self.assertIsNone(db.get(model, key).c_logo_url)
            with self.factory() as db:
                self.assertIn("/platform/", resolve_branding(db, "STORE", store_id=2).logo_url)

    def test_existing_home_triggered_logout_keeps_organization_entry(self):
        with self.factory() as db:
            token = web_sessions.create_session(db, db.get(PlatformUser, 3), request(), {"scope_type": "store"})
        self.client.cookies.set("access_token", token, domain="testserver.local", path="/")
        response = self.client.get("/", headers={"Referer": "http://testserver/dashboard/store"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login/store/STR00002")
        with self.factory() as db:
            self.assertEqual(db.query(SessionTbl).one().c_status, "LOGGED_OUT")


@unittest.skipUnless(shutil.which("node"), "Node.js is required for login branding UI behavior tests.")
class BrandingViewTests(unittest.TestCase):
    def test_fixed_entry_cannot_be_rebranded_and_general_resolution_ignores_stale_response(self):
        script = r"""
const vm=require('vm'),fs=require('fs'),assert=require('assert');
const source=fs.readFileSync(process.argv[2],'utf8');
const initial={
  context_type:'PLATFORM',organization_name:'Platform Name',logo_url:'/static/images/platform/logo_full.png?v=1',
  description:'Platform Administration',login_entry:'/platform-admin',access_code:'ADMIN',
};
function setup(readOnly, brand=initial) {
  const elements=new Map(),requests=[],timers=[];
  function get(id) {
    if(!elements.has(id))elements.set(id,{
      value:'',readOnly:false,events:{},classes:new Set(),textContent:'',
      classList:{
        toggle(name,value){const e=get(id);if(value)e.classes.add(name);else e.classes.delete(name);},
        add(name){get(id).classes.add(name);},remove(name){get(id).classes.delete(name);},
      },
      addEventListener(name,fn){this.events[name]=fn;},removeAttribute(name){delete this[name];},
    });
    return elements.get(id);
  }
  get('loginBrandingData').textContent=JSON.stringify(brand);
  get('loginAccessCode').value='PREVIOUS_STORE_CODE';
  get('loginAccessCode').readOnly=readOnly;
  const context={
    document:{getElementById:get},URLSearchParams,
    fetch:url=>new Promise(resolve=>requests.push({url,resolve})),
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},
  };
  vm.runInNewContext(source,context);
  return {get,requests,timers};
}
(async()=>{
  for(const brand of [initial,{...initial,context_type:'STORE',organization_name:'Store Name',
      logo_url:'/static/images/brands/store/logo.png',description:'Store Sign In'}]){
    const s=setup(true,brand);
    assert.equal(s.requests.length,0);
    assert.equal(s.get('loginBrandLogo').src,brand.logo_url);
    assert.equal(s.get('loginOrganization').textContent,brand.organization_name);
    assert.equal(s.get('loginAccessCode').events.input,undefined);
    s.get('loginBrandLogo').events.error();
    assert(s.get('loginBrandLogo').classes.has('d-none'));
    assert(!s.get('loginBrandName').classes.has('d-none'));
  }
  const s=setup(false);
  assert.equal(s.requests.length,1); // remembered code resolves only on the generic entry
  s.get('loginAccessCode').value='AGT00001';
  const change=s.get('loginAccessCode').events.change();
  assert.equal(s.requests.length,2);
  const agency={...initial,organization_name:'Agency Name',logo_url:'/static/images/brands/agency/logo.png',description:'Agency Sign In'};
  s.requests[1].resolve({ok:true,json:async()=>agency});
  await change;
  assert.equal(s.get('loginOrganization').textContent,'Agency Name');
  s.requests[0].resolve({ok:true,json:async()=>({...initial,organization_name:'STALE STORE'})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(s.get('loginOrganization').textContent,'Agency Name');
  s.get('loginAccessCode').value='INVALID';
  const failed=s.get('loginAccessCode').events.change();
  s.requests[2].resolve({ok:false,json:async()=>({detail:'Login organization not found.'})});
  await failed;
  assert.equal(s.get('loginBrandLogo').src,initial.logo_url);
  assert.equal(s.get('loginBrandStatus').textContent,'Login organization not found.');
  console.log('Fixed Platform/Store entries, stale AJAX response, image fallback, and error state verified.');
})().catch(error=>{console.error(error);process.exit(1);});
"""
        path = Path(__file__).resolve().parents[1] / "static" / "js" / "login-branding.js"
        result = subprocess.run(["node", "-", str(path)], input=script.encode("utf-8"),
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
