import json
import re
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers

from backend.database.pg_platform import PlatformBase
from backend.database.image_storage_migration import migrate_image_storage
from backend.main import app
from backend.models_admin.merchant import Merchant
from backend.models_admin.agency import Agency
from backend.models_admin.session import SessionTbl
from backend.models_admin.role import Role
from backend.models_admin.store import Store
from backend.services import branding, images, image_service, license_email, resource_storage
from backend.routers import images as image_routes
from tools.test_web_sessions import seed, login_token


class CommonImageBackgroundTests(unittest.TestCase):
    def test_contain_preserves_aspect_ratio_full_image_and_opaque_background(self):
        for width, height, kind in [(100, 400, "PNG"), (400, 100, "JPEG"), (200, 200, "WEBP")]:
            with self.subTest(kind=kind, size=(width, height)):
                source = Image.new("RGB", (width, height), "#222222")
                output = BytesIO()
                source.save(output, kind)
                with Image.open(BytesIO(images.process_image(output.getvalue()))) as saved:
                    self.assertEqual((saved.size, saved.format, saved.mode), ((512, 512), "WEBP", "RGB"))
                    mask = saved.convert("L").point(lambda value: 255 if value < 100 else 0)
                    left, top, right, bottom = mask.getbbox()
                    self.assertAlmostEqual((right - left) / (bottom - top), width / height, delta=.01)
                    self.assertLessEqual(abs((left + right) / 2 - 256), 1)
                    self.assertLessEqual(abs((top + bottom) / 2 - 256), 1)
                    if width != height:
                        self.assertTrue(all(value >= 250 for value in saved.getpixel((0, 0))))

    def test_transparency_and_padding_use_same_configurable_background(self):
        source = Image.new("RGBA", (100, 200), (0, 0, 0, 0))
        source.paste((255, 0, 0, 255), (25, 50, 75, 150))
        output = BytesIO()
        source.save(output, "PNG")
        for background, expected in [("#ffffff", (255, 255, 255)), ("#00ff00", (0, 255, 0))]:
            with self.subTest(background=background), patch.dict(images.IMAGE_CONFIG, background=background):
                with Image.open(BytesIO(images.process_image(output.getvalue()))) as saved:
                    for point in [(0, 0), (256, 20)]:
                        self.assertLessEqual(max(abs(a - b) for a, b in zip(saved.getpixel(point), expected)), 4)


class ImageStorageTests(unittest.TestCase):
    def test_every_category_and_owner_uses_identical_processor_service_and_configuration(self):
        owner = images.AwsumPathResolver("STORE", merchant_code="CLT11001", store_code="STR00002")
        expected = images.process_image(self.png)
        for category, key in (("users", 42), ("master", "MD00001"), ("employee", "EMP00001"),
                              ("logo", "logo"), ("background", "background"), ("common", "BANNER")):
            with self.subTest(category=category):
                db = MagicMock()
                row = SimpleNamespace(c_image_path=None)
                with image_service.image_operation_lock(owner, category), \
                        patch.object(images, "process_image", wraps=images.process_image) as processor:
                    image_service.commit_entity_image(db, row, owner, key, category=category, image_data=self.png)
                processor.assert_called_once_with(self.png)
                self.assertEqual(row.c_image_path, owner.get_entity_image_path(category, key))
                file = resource_storage.AwsumStorageService.image_file(row.c_image_path)
                self.assertEqual(file.read_bytes(), expected)
                self.assertTrue(file.is_relative_to(self.media))
                db.commit.assert_called_once()
        config = json.loads((Path(__file__).resolve().parents[1] / "config" / "image.json").read_text())
        self.assertEqual(images.IMAGE_CONFIG, config)
        with patch.dict(images.IMAGE_CONFIG, width=128, height=128):
            with Image.open(BytesIO(images.process_image(self.png))) as processed:
                self.assertEqual(processed.size, (128, 128))

    def test_generic_upload_requires_stable_key_and_never_uses_original_name(self):
        self.sign_in()
        route = "/api/images/merchant/CLT11001/store/STR00002/employee"
        result = self.client.post(route, files={"file": ("My Camera Photo.JPG", self.png, "image/png")})
        self.assertEqual(result.status_code, 400)
        for key in ("../EMP1", r"D:\EMP1", "/EMP1"):
            result = self.client.post(route, params={"entity_key": key},
                files={"file": ("photo.png", self.png, "image/png")})
            self.assertEqual(result.status_code, 400)
        result = self.client.post(route, params={"entity_key": "EMP42"},
            files={"file": ("My Camera Photo.PNG", self.png, "image/png")})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["image_path"].endswith("/employee/EMP42.webp"))
        with Image.open(images.resolve_image_file(result.json()["image_path"])) as photo:
            self.assertEqual((photo.format, photo.size), ("WEBP", (512, 512)))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awsum-image-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.static = self.root / "static"
        self.media = self.root / "data" / "media"
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        PlatformBase.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        seed(self.factory)
        with self.factory() as db:
            db.get(Role, 2).c_name = "Admin"
            db.commit()
        self.addCleanup(self.engine.dispose)
        def dependency():
            with self.factory() as db:
                yield db
        for context in (
            patch.object(images, "STATIC_ROOT", self.static),
            patch.object(images, "image_storage_root", return_value=self.media),
            patch.dict(app.dependency_overrides, {image_routes.get_db: dependency}),
            patch("backend.main.PlatformSessionLocal", self.factory),
            patch("backend.services.web_session_http.PlatformSessionLocal", self.factory),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.client = TestClient(app, follow_redirects=False)
        self.addCleanup(self.client.close)
        output = BytesIO()
        Image.new("RGB", (4, 4), "blue").save(output, "PNG")
        self.png = output.getvalue()

    def legacy(self, relative, content=None):
        file = self.static / "images" / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(self.png if content is None else content)
        return file

    def sign_in(self, user_id=1):
        token = login_token(self.factory, user_id)
        self.client.cookies.set("access_token", token, domain="testserver.local", path="/")

    def test_exact_context_first_paths_and_optional_master_subcategory(self):
        for context, code, prefix in (
            ("PLATFORM", None, "/platform-admin"),
            ("AGENCY", "AGT00001", "/agency/AGT00001"),
            ("MERCHANT", "CLT11001", "/merchant/CLT11001"),
            ("STORE", "STR00002", "/merchant/CLT11001/store/STR00002"),
        ):
            for category in images.CATEGORIES:
                image = images.ImagePath(context, code, category, "EMP000007.jpg",
                                         merchant_code="CLT11001" if context == "STORE" else None)
                self.assertEqual(image.logical_path, prefix + "/images/" + category + "/EMP000007.jpg")
                self.assertEqual(images.ImagePath.parse(image.logical_path), image)
            image = images.ImagePath(context, code, "master", "IT000025.jpg", "item",
                                     "CLT11001" if context == "STORE" else None)
            self.assertEqual(image.logical_path, prefix + "/images/master/item/IT000025.jpg")
            self.assertTrue(images.physical_image_path(image).is_relative_to(self.media))

    def test_rejects_traversal_encoded_separators_reserved_names_and_unknown_categories(self):
        for path in (
            "/store/../images/logo/logo.png", "/store/ST1/images/../secret.png",
            "/store/ST1/images/logo/..\\secret.png", "/store/ST1/images/logo/%2e%2e.png",
            "/store/ST1/images/logo/CON.png", "/store/ST1/images/employees/EMP1.png",
            "/Store/ST1/images/logo/logo.png", "/store/ST1/images/Logo/logo.png",
            "/store/ST1/images/employee/extra/EMP1.png", "/platform-admin/STR1/images/logo/logo.png",
            "/store/ST1/images/logo/logo.svg", "/store/ST1/images/logo/a.png:secret",
            "/store/ST1/images/logo/a.png?x=1",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                images.ImagePath.parse(path)
        for path in ("/static/images/../secrets.png", "/static/images/%2e/a.png", "https://example.test/a.png"):
            self.assertIsNone(images.legacy_physical_path(path))

    def test_validation_checks_content_extension_size_and_pixels(self):
        def upload(content, name="logo.png", content_type="image/png"):
            return UploadFile(BytesIO(content), filename=name, headers=Headers({"content-type": content_type}))
        normalized = images.normalize_image(upload(self.png))
        with Image.open(BytesIO(normalized)) as image:
            self.assertEqual((image.format, image.size), ("WEBP", (512, 512)))
        for file in (
            upload(b"not an image"), upload(self.png, "../logo.png"), upload(self.png, "logo.jpg", "image/jpeg"),
            upload(self.png, "logo.svg", "image/svg+xml"), upload(b"x" * (images.MAX_BYTES + 1)),
        ):
            with self.assertRaises(HTTPException) as error:
                images.normalize_image(file)
            self.assertEqual(error.exception.status_code, 400)
        with patch.object(images, "MAX_PIXELS", 8), self.assertRaises(HTTPException):
            images.normalize_image(upload(self.png))

    def test_create_only_atomic_write_and_failure_cleanup(self):
        image = images.ImagePath("STORE", "STR00002", "employee", "EMP1.png", merchant_code="CLT11001")
        path = images.write_image(image, self.png)
        with self.assertRaises(HTTPException) as error:
            images.write_image(image, b"replacement")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(path.read_bytes(), self.png)
        with patch.object(resource_storage.os, "link", side_effect=OSError("disk unavailable")), self.assertLogs(resource_storage.logger, level="ERROR"):
            with self.assertRaises(HTTPException) as error:
                images.write_image(images.ImagePath("STORE", "STR00002", "employee", "EMP2.png", merchant_code="CLT11001"), self.png)
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_public_logo_ignores_stale_cookie_private_images_require_session(self):
        public = images.ImagePath("PLATFORM", None, "logo", "logo.png")
        private = images.ImagePath("STORE", "STR00002", "employee", "EMP1.png", merchant_code="CLT11001")
        images.write_image(public, self.png)
        images.write_image(private, self.png)
        self.client.cookies.set("access_token", "expired-invalid-token", domain="testserver.local", path="/")
        response = self.client.get(public.logical_path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, self.png)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.client.get(private.logical_path).status_code, 401)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(private.logical_path).status_code, 401)
        self.sign_in(3)
        response = self.client.get(private.logical_path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_owner_permission_upload_delete_and_cross_owner_denial(self):
        self.sign_in(3)
        def post(context, owner, category="employee", name="EMP1.png"):
            return self.client.post(f"/api/images/{context}/{owner}/{category}",
                params={"entity_key": "EMP1", **({"merchant_code": "CLT11001"} if context == "store" else {})},
                files={"file": (name, self.png, "image/png")}, headers={"Origin": "http://testserver"})
        response = post("store", "STR00002")
        self.assertEqual(response.status_code, 200, response.text)
        reference = response.json()["image_path"]
        self.assertEqual(reference, "/merchant/CLT11001/store/STR00002/images/employee/EMP1.webp")
        for context, code in (("store", "HQ"), ("merchant", "CLT11001"), ("platform-admin", "platform-admin")):
            response = post(context, code)
            self.assertEqual(response.status_code, 403, response.text)
        other = images.ImagePath("STORE", "HQ", "employee", "EMP1.png", merchant_code="CLT11001")
        images.write_image(other, self.png)
        self.assertEqual(self.client.get(other.logical_path).status_code, 403)
        self.assertEqual(self.client.delete("/api/images", params={"image_path": other.logical_path}).status_code, 403)
        self.assertEqual(self.client.delete("/api/images", params={"image_path": reference},
            headers={"Origin": "https://untrusted.example"}).status_code, 403)
        response = self.client.delete("/api/images", params={"image_path": reference},
            headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(images.physical_image_path(images.ImagePath.parse(reference)).exists())

    def test_platform_admin_all_contexts_and_referenced_logo_not_deletable(self):
        self.sign_in()
        for context, code in (("platform-admin", "platform-admin"), ("agency", "AGT00001"),
                              ("merchant", "CLT11001"), ("store", "STR00002")):
            response = self.client.post(f"/api/images/{context}/{code}/logo",
                params={"entity_key": "logo", **({"merchant_code": "CLT11001"} if context == "store" else {})},
                files={"file": ("logo.png", self.png, "image/png")}, headers={"Origin": "http://testserver"})
            self.assertEqual(response.status_code, 200, response.text)
        with self.factory() as db:
            db.get(Store, 2).c_logo_url = "/merchant/CLT11001/store/STR00002/images/logo/logo.webp"
            db.commit()
        response = self.client.delete("/api/images", params={"image_path": "/merchant/CLT11001/store/STR00002/images/logo/logo.webp"})
        self.assertEqual(response.status_code, 409, response.text)
        response = self.client.post("/api/images/merchant/CLT11001/store/STR00002/master", params={"sub_category": "item", "entity_key": "IT000025"},
            files={"file": ("IT000025.png", self.png, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["image_path"], "/merchant/CLT11001/store/STR00002/images/master/item/IT000025.webp")

    def migration_fixture(self):
        self.legacy("platform/logo_full.png")
        old = "/static/images/brands/clients/CLT11001/logo.png"
        source = self.legacy("brands/clients/CLT11001/logo.png")
        with self.factory() as db:
            db.get(Merchant, 1).c_logo_url = old
            db.commit()
        return old, source

    def test_migration_copy_manifest_references_idempotence_and_email_loader(self):
        old, source = self.migration_fixture()
        before = source.read_bytes()
        dry = migrate_image_storage(self.engine, dry_run=True)
        self.assertEqual(len(dry["references"]), 1)
        self.assertFalse(self.media.exists())
        plan = migrate_image_storage(self.engine)
        self.assertEqual(plan["status"], "COMMITTED")
        manifest = json.loads(Path(plan["manifest"]).read_text())
        self.assertEqual(manifest["references"][0]["before"], old)
        with self.factory() as db:
            merchant = db.get(Merchant, 1)
            self.assertEqual(merchant.c_logo_url, "/merchant/CLT11001/images/logo/logo.png")
            self.assertEqual(merchant.i_merchant_id, 1)
            self.assertEqual(db.get(Store, 2).i_merchant_id, 1)
            self.assertEqual(images.resolve_image_file(merchant.c_logo_url).read_bytes(), before)
            self.assertTrue(branding.resolve_branding(db).logo_url.startswith("/images/platform/logo/"))
            self.assertIsNotNone(license_email.load_company_branding(db)["logo_bytes"])
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(images.image_url(old).startswith("/merchant/CLT11001/images/logo/"))
        rerun = migrate_image_storage(self.engine)
        self.assertEqual(rerun["status"], "UNCHANGED")
        self.assertEqual(rerun["references"], [])
        self.assertEqual(len(list((self.media / "platform-admin/data/audit/image-migrations").glob("*.json"))), 1)

    def test_migration_preflight_conflict_preserves_all_files_and_database(self):
        old, source = self.migration_fixture()
        target = images.ImagePath("MERCHANT", "CLT11001", "logo", "logo.png")
        images.write_image(target, b"different owner image")
        with self.assertRaisesRegex(RuntimeError, "destination conflict"):
            migrate_image_storage(self.engine)
        with self.factory() as db:
            self.assertEqual(db.get(Merchant, 1).c_logo_url, old)
        self.assertEqual(source.read_bytes(), self.png)
        self.assertEqual(images.physical_image_path(target).read_bytes(), b"different owner image")
        self.assertFalse((self.media / "platform-admin/data/audit/image-migrations").exists())

    def test_migration_database_failure_records_recovery_and_retry(self):
        old, source = self.migration_fixture()
        def fail_update(conn, cursor, statement, params, context, many):
            if statement.lstrip().startswith("UPDATE tb_merchant"):
                raise RuntimeError("injected DB update failure")
        event.listen(self.engine, "before_cursor_execute", fail_update)
        try:
            with self.assertRaisesRegex(RuntimeError, "injected"):
                migrate_image_storage(self.engine)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_update)
        with self.factory() as db:
            self.assertEqual(db.get(Merchant, 1).c_logo_url, old)
        manifest = next((self.media / "platform-admin/data/audit/image-migrations").glob("*.json"))
        self.assertEqual(json.loads(manifest.read_text())["status"], "FAILED_DB_ROLLBACK_FILES_RETAINED")
        self.assertTrue(source.exists())
        self.assertEqual(migrate_image_storage(self.engine)["status"], "COMMITTED")

    def test_committed_cutover_does_not_compare_or_overwrite_newer_images(self):
        old, source = self.migration_fixture()
        migrate_image_storage(self.engine)
        canonical = images.physical_image_path(images.ImagePath("MERCHANT", "CLT11001", "logo", "logo.png"))
        with Image.open(BytesIO(self.png)) as photo:
            photo.putpixel((0, 0), (255, 0, 0, 255))
            output = BytesIO()
            photo.save(output, "PNG")
        newer = output.getvalue()
        canonical.write_bytes(newer)
        source.write_bytes(b"Changed legacy archive")
        result = migrate_image_storage(self.engine)
        self.assertEqual(result["status"], "UNCHANGED")
        self.assertIn(old, result["completed_legacy_assets"])
        self.assertEqual(canonical.read_bytes(), newer)
        self.assertEqual(source.read_bytes(), b"Changed legacy archive")

    def test_committed_cutover_does_not_resurrect_deleted_unreferenced_images(self):
        self.migration_fixture()
        migrate_image_storage(self.engine)
        canonical = images.physical_image_path(images.ImagePath("PLATFORM", None, "logo", "logo_full.png"))
        canonical.unlink()
        self.assertEqual(migrate_image_storage(self.engine)["status"], "UNCHANGED")
        self.assertFalse(canonical.exists())

    def test_missing_referenced_image_after_cutover_is_still_reported_without_restoring_archive(self):
        self.migration_fixture()
        migrate_image_storage(self.engine)
        canonical = images.physical_image_path(images.ImagePath("MERCHANT", "CLT11001", "logo", "logo.png"))
        canonical.unlink()
        with self.assertRaisesRegex(RuntimeError, "Logical image is missing"):
            migrate_image_storage(self.engine)
        self.assertFalse(canonical.exists())

    def test_logical_branding_inheritance_and_cache_version(self):
        from backend.models_admin.system_settings import SystemSettings
        owners = (("PLATFORM", None, SystemSettings, 1),
                  ("AGENCY", "AGT00001", Agency, 1),
                  ("MERCHANT", "CLT11001", Merchant, 1),
                  ("STORE", "STR00002", Store, 2))
        with self.factory() as db:
            db.get(Merchant, 1).i_agency_id = 1
            for context, code, model, key in owners:
                image = images.logo_image_path(context, code, "logo.png", merchant_code="CLT11001" if context == "STORE" else None)
                images.write_image(image, self.png)
                db.get(model, key).c_logo_url = image.logical_path
            db.commit()
            for context, code, model, key in reversed(owners):
                result = branding.resolve_branding(db, "STORE", store_id=2)
                expected = images.logo_image_path(context, code, "logo.png", merchant_code="CLT11001" if context == "STORE" else None).logical_path
                self.assertEqual(result.logo_url.split("?")[0], expected)
                self.assertIn("?v=", result.logo_url)
                db.get(model, key).c_logo_url = None
                db.flush()
            self.assertTrue(branding.resolve_branding(db).logo_url.startswith("/images/platform/logo/"))

    def test_authenticated_management_preview_preserves_inactive_organization_images(self):
        with self.factory() as db:
            merchant = db.get(Merchant, 1)
            merchant.c_status = "inactive"
            image = images.logo_image_path("MERCHANT", "CLT11001", "logo.png")
            images.write_image(image, self.png)
            merchant.c_logo_url = image.logical_path
            db.commit()
            with self.assertRaises(HTTPException) as error:
                branding.resolve_access_code(db, "CLT11001")
            self.assertEqual(error.exception.status_code, 404)
        url = "/api/images/branding?access_code=CLT11001&context_type=MERCHANT"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.sign_in(3)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.cookies.clear()
        self.sign_in(1)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["logo_url"].startswith("/merchant/CLT11001/images/logo/"))

    @unittest.skipUnless(shutil.which("node"), "Node required for browser preview behavior")
    def test_browser_preview_uses_api_urls_without_path_guessing_or_stale_response(self):
        source = (Path(__file__).resolve().parents[1] / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        start = source.index("  async function loadBrandLogo(")
        end = source.index("  function createBrandLogoUploadPanel", start)
        functions = re.sub(r"\{\{.*?\}\}", '"/platform-admin/images/logo/logo.png?v=1"', source[start:end])
        script = r"""
const assert = require('node:assert/strict');
const requests = [];
const timers = [];
const window = {setTimeout(fn) {timers.push(fn);}};
const image = {dataset:{},src:'',classList:{add(){},remove(){}},
  removeAttribute(name) {delete this[name];}};
const hint = {};
const fetch = (url) => new Promise(resolve => requests.push({url,resolve}));
""" + functions + r"""
(async () => {
  const first = loadBrandLogo(image,'CLT11001',hint);
  const second = loadBrandLogo(image,'CLT00001',hint,'STORE');
  assert.equal(requests[0].url,'/api/images/branding?access_code=CLT11001&context_type=MERCHANT');
  assert.equal(requests[1].url,'/api/images/branding?access_code=CLT00001&context_type=STORE');
  requests[1].resolve({ok:true,json:async()=>({logo_url:'/merchant/CLT11001/store/CLT00001/images/logo/logo-new.png?v=42'})});
  await second; timers.splice(0).forEach(fn=>fn());
  requests[0].resolve({ok:true,json:async()=>({logo_url:'/merchant/CLT11001/images/logo/old.png?v=1'})});
  await first; timers.splice(0).forEach(fn=>fn());
  assert.equal(image.src,'/merchant/CLT11001/store/CLT00001/images/logo/logo-new.png?v=42');
  assert.equal(hint.textContent,'Resolved image: /merchant/CLT11001/store/CLT00001/images/logo/logo-new.png');
  const third = loadBrandLogo(image,'CLT11001',hint);
  requests[2].resolve({ok:true,json:async()=>({logo_url:'/merchant/CLT11001/images/logo/stale.png?v=2'})});
  await third;
  await loadBrandLogo(image,'',hint);
  timers.splice(0).forEach(fn=>fn());
  assert.equal(image.src,'/platform-admin/images/logo/logo.png?v=1');
})().catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
