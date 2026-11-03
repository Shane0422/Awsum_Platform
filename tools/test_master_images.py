import json
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers

from backend.database.pg_platform import PlatformBase, platform_engine
from backend.database.md_image_migration import migrate_md_images
from backend.database.item_kind_migration import migrate_item_kinds
from backend.main import app
from backend.models_admin.master_group import MdMaster
from backend.routers import images as image_routes, master_group as routes
from backend.services import images, master_group as svc, image_service as master_images
from backend.services.paths import AwsumPathResolver
from tools.test_web_sessions import seed, login_token


def image_bytes(color="blue", format="PNG"):
    output = BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format)
    return output.getvalue()


class MasterImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awsum-md-image-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        PlatformBase.metadata.create_all(self.engine)
        migrate_item_kinds(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        seed(self.factory)

        def dependency():
            with self.factory() as db:
                yield db
        for context in (
            patch.object(images, "image_storage_root", return_value=self.root),
            patch.dict(app.dependency_overrides, {routes.get_db: dependency, image_routes.get_db: dependency}),
            patch("backend.main.PlatformSessionLocal", self.factory),
            patch("backend.services.web_session_http.PlatformSessionLocal", self.factory),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.db = self.factory()
        self.addCleanup(self.db.close)
        cat = svc.create_item(self.db, "category1", {"name": "Hardware"}, 1)
        self.payload = {
            "name": "Test item", "item_kind": "ITEM", "category1_id": cat["id"],
            "retail_price": "10", "unit": "EA", "billing_cycle": "ONE_TIME",
        }
        self.blue = image_bytes()
        self.red = image_bytes("red")
        self.client = TestClient(app, follow_redirects=False)
        self.addCleanup(self.client.close)
        self.tokens = {}
        self.sign_in()

    def sign_in(self, user_id=1):
        self.client.cookies.clear()
        token = self.tokens.get(user_id)
        if token is None:
            token = login_token(self.factory, user_id)
            self.tokens[user_id] = token
        self.client.cookies.set("access_token", token, domain="testserver.local", path="/")

    def post(self, payload=None, data=None, name="original.png", mime="image/png"):
        return self.client.post(
            "/platform/api/master-group/md/with-image",
            data={"payload": json.dumps(self.payload if payload is None else payload)},
            files={"image": (name, self.blue if data is None else data, mime)},
        )

    def create(self):
        return svc.create_item(self.db, "md", self.payload, 1, image_data=self.blue)

    def physical(self, item):
        return images.physical_image_path(images.ImagePath.parse(item["image_path"]))

    def assert_clean(self):
        self.assertEqual(list(self.root.rglob(".master-image-*")), [])

    def test_owner_resolver_uses_direct_master_with_merchant_parent(self):
        for owner, expected in (
            (AwsumPathResolver("PLATFORM"), "/platform-admin"),
            (AwsumPathResolver("AGENCY", agency_code="AGT00001"), "/agency/AGT00001"),
            (AwsumPathResolver("MERCHANT", merchant_code="CLT11001"), "/merchant/CLT11001"),
            (AwsumPathResolver("STORE", merchant_code="CLT11001", store_code="CLT00001"),
             "/merchant/CLT11001/store/CLT00001"),
        ):
            self.assertEqual(owner.get_master_image_path("MD00001"), expected + "/images/master/MD00001.webp")
        for code in ("../MD1", r"..\MD1", "/MD1", "MD%2f1"):
            with self.assertRaises(ValueError):
                AwsumPathResolver("PLATFORM").get_master_image_path(code)
        with self.assertRaises(ValueError):
            AwsumPathResolver("STORE", store_code="CLT00001")

    def test_add_returns_completed_record_and_private_versioned_url(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()["item"]
        self.assertEqual(item["code"], "MD00001")
        self.assertEqual(item["image_path"], "/platform-admin/images/master/MD00001.webp")
        self.assertIn("?v=", item["image_url"])
        self.assertEqual(self.db.get(MdMaster, item["id"]).c_image_path, item["image_path"])
        result = self.client.get(item["image_url"])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.headers["cache-control"], "private, no-store")
        with Image.open(BytesIO(result.content)) as saved:
            self.assertEqual((saved.format, saved.size), ("WEBP", (512, 512)))
            self.assertGreater(saved.getpixel((0, 0))[2], 250)
        self.assertNotIn("original", str(self.physical(item)))
        self.assert_clean()

    def test_all_supported_formats_normalize_to_code_webp(self):
        for extension, mime, fmt in (("jpg", "image/jpeg", "JPEG"), ("jpeg", "image/jpeg", "JPEG"),
                                     ("png", "image/png", "PNG"), ("webp", "image/webp", "WEBP")):
            with self.subTest(extension=extension):
                result = self.post(data=image_bytes(format=fmt), name="photo." + extension, mime=mime)
                self.assertEqual(result.status_code, 201, result.text)
                item = result.json()["item"]
                self.assertTrue(item["image_path"].endswith(item["code"] + ".webp"))
                with Image.open(self.physical(item)) as saved:
                    self.assertEqual((saved.format, saved.size), ("WEBP", (512, 512)))

    def test_invalid_images_and_invalid_fields_create_nothing(self):
        for content, name, mime, status in (
            (b"not an image", "bad.png", "image/png", 400),
            (self.blue, "../bad.png", "image/png", 400),
            (self.blue, "bad.svg", "image/svg+xml", 400),
            (self.blue, "bad.jpg", "image/jpeg", 400),
            (b"", "empty.png", "image/png", 400),
            (b"x" * (5 * 1024 * 1024 + 1), "large.png", "image/png", 400),
        ):
            with self.subTest(name=name):
                response = self.post(data=content, name=name, mime=mime)
                self.assertEqual(response.status_code, status, response.text)
        response = self.post(payload={**self.payload, "name": ""})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("name", response.json()["errors"])
        self.assertEqual(self.db.query(MdMaster).count(), 0)
        self.assertEqual(list(self.root.rglob("*.webp")), [])
        self.assert_clean()

    def test_exact_five_mib_upload_boundary_is_accepted(self):
        response = self.post(data=self.blue + b"\0" * (5 * 1024 * 1024 - len(self.blue)))
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()["item"]
        self.assertLess(self.physical(item).stat().st_size, 5 * 1024 * 1024)
        self.assert_clean()

    def test_replace_same_path_refreshes_cache_without_orphans(self):
        item = self.create()
        result = svc.update_item(self.db, "md", item["id"], {**self.payload, "name": "Updated"}, 1, image_data=self.red)
        self.assertEqual(result["code"], item["code"])
        self.assertEqual(result["image_path"], item["image_path"])
        self.assertNotEqual(result["image_url"], item["image_url"])
        self.assertEqual(self.physical(result).read_bytes(), images.process_image(self.red))
        self.assertEqual(result["retail_price"], "10.00")
        self.assertEqual(list(self.physical(item).parent.glob("*.webp")), [self.physical(item)])
        self.assert_clean()

    def test_replace_extension_removes_previous_only_with_successful_save(self):
        item = svc.create_item(self.db, "md", self.payload, 1)
        old_image = images.ImagePath("PLATFORM", None, "master", item["code"] + ".jpg")
        old = images.write_image(old_image, image_bytes(format="JPEG"))
        row = self.db.get(MdMaster, item["id"])
        row.c_image_path = old_image.logical_path
        self.db.commit()
        result = svc.update_item(self.db, "md", item["id"], self.payload, 1, image_data=self.red)
        self.assertFalse(old.exists())
        self.assertEqual(self.physical(result).read_bytes(), images.process_image(self.red))
        self.assert_clean()

    def test_remove_is_deferred_to_save_and_clears_reference(self):
        item = self.create()
        # A local select/remove/cancel has no API call and cannot mutate storage.
        self.assertTrue(self.physical(item).is_file())
        result = self.client.put(
            f'/platform/api/master-group/md/{item["id"]}/with-image',
            data={"payload": json.dumps(self.payload), "remove_image": "true"},
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNone(result.json()["item"]["image_path"])
        self.assertIsNone(result.json()["item"]["image_url"])
        self.assertFalse(self.physical(item).exists())
        self.db.expire_all()
        self.assertIsNone(self.db.get(MdMaster, item["id"]).c_image_path)
        self.assert_clean()

    def test_legacy_jpg_png_preserved_on_edit_and_removed_only_after_replacement_commit(self):
        for extension, fmt in (("jpg", "JPEG"), ("png", "PNG")):
            with self.subTest(extension=extension):
                item = svc.create_item(self.db, "md", self.payload, 1)
                reference = AwsumPathResolver("PLATFORM").get_master_image_path(item["code"], extension)
                legacy = images.write_image(images.ImagePath.parse(reference), image_bytes(format=fmt))
                original = legacy.read_bytes()
                self.db.get(MdMaster, item["id"]).c_image_path = reference
                self.db.commit()
                unchanged = svc.update_item(self.db, "md", item["id"], {**self.payload, "name": "Name only"}, 1)
                self.assertEqual(unchanged["image_path"], reference)
                self.assertEqual(legacy.read_bytes(), original)
                commit = self.db.commit
                def verify_commit():
                    self.assertTrue(legacy.is_file(), "Legacy image must exist until commit succeeds")
                    commit()
                with patch.object(self.db, "commit", side_effect=verify_commit):
                    saved = svc.update_item(self.db, "md", item["id"], self.payload, 1, image_data=self.red)
                self.assertEqual(saved["image_path"], reference.rsplit(".", 1)[0] + ".webp")
                self.assertFalse(legacy.exists())
                with Image.open(self.physical(saved)) as photo:
                    self.assertEqual((photo.format, photo.size), ("WEBP", (512, 512)))
                self.assert_clean()

    def test_db_commit_failure_restores_replaced_bytes_and_item_fields(self):
        item = self.create()
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")):
            with self.assertRaises(SQLAlchemyError):
                svc.update_item(self.db, "md", item["id"], {**self.payload, "name": "Not saved"},
                                1, image_data=self.red)
        self.assertEqual(self.physical(item).read_bytes(), images.process_image(self.blue))
        row = self.db.get(MdMaster, item["id"])
        self.assertEqual(row.c_item_name, self.payload["name"])
        self.assertEqual(row.c_image_path, item["image_path"])
        self.assert_clean()

    def test_db_commit_failure_restores_removed_file(self):
        item = self.create()
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")):
            with self.assertRaises(SQLAlchemyError):
                svc.update_item(self.db, "md", item["id"], self.payload, 1, remove_image=True)
        self.assertEqual(self.physical(item).read_bytes(), images.process_image(self.blue))
        self.assertEqual(self.db.get(MdMaster, item["id"]).c_image_path, item["image_path"])
        self.assert_clean()

    def test_commit_and_rollback_connection_failure_still_restore_file(self):
        item = self.create()
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")), \
                patch.object(self.db, "rollback", side_effect=SQLAlchemyError("Rollback failed")):
            with self.assertRaises(SQLAlchemyError):
                svc.update_item(self.db, "md", item["id"], self.payload, 1, image_data=self.red)
        self.db.rollback()
        self.assertEqual(self.physical(item).read_bytes(), images.process_image(self.blue))
        self.assert_clean()

    def test_failed_extension_change_restores_previous_and_removes_new(self):
        item = svc.create_item(self.db, "md", self.payload, 1)
        old_image = images.ImagePath("PLATFORM", None, "master", item["code"] + ".jpg")
        old_bytes = image_bytes(format="JPEG")
        old = images.write_image(old_image, old_bytes)
        self.db.get(MdMaster, item["id"]).c_image_path = old_image.logical_path
        self.db.commit()
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")):
            with self.assertRaises(SQLAlchemyError):
                svc.update_item(self.db, "md", item["id"], self.payload, 1, image_data=self.red)
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertFalse(old.with_suffix(".webp").exists())
        self.assertEqual(self.db.get(MdMaster, item["id"]).c_image_path, old_image.logical_path)
        self.assert_clean()

    def test_restore_failure_returns_explicit_error_and_retains_backup(self):
        item = self.create()
        replace = master_images.os.replace
        calls = 0
        def fail_restore(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Restore failed")
            return replace(source, target)
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")), \
                patch.object(master_images.os, "replace", side_effect=fail_restore), \
                self.assertLogs(master_images.logger, "CRITICAL"), self.assertRaises(HTTPException) as error:
            svc.update_item(self.db, "md", item["id"], self.payload, 1, image_data=self.red)
        self.assertEqual(error.exception.status_code, 500)
        self.assertIn("recovery failed", error.exception.detail)
        backups = list(self.root.rglob(".master-image-*.tmp"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), images.process_image(self.blue))

    def test_postcommit_cleanup_failure_is_reported_as_warning(self):
        item = self.create()
        unlink = Path.unlink
        def fail_cleanup(path, *args, **kwargs):
            if path.name.startswith(".master-image-"):
                raise OSError("Temporary cleanup failed")
            return unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_cleanup), self.assertLogs(master_images.logger, "ERROR"):
            result = svc.update_item(self.db, "md", item["id"], self.payload, 1, image_data=self.red)
        self.assertIn("cleanup", result["image_warning"])
        self.assertEqual(self.physical(result).read_bytes(), images.process_image(self.red))
        self.assertEqual(self.db.get(MdMaster, item["id"]).c_image_path, item["image_path"])

    def test_failed_add_commit_and_file_publish_leave_no_record_or_file(self):
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")):
            with self.assertRaises(SQLAlchemyError):
                self.create()
        self.assertEqual(self.db.query(MdMaster).count(), 0)
        self.assertEqual(list(self.root.rglob("MD*.webp")), [])
        with patch.object(master_images.os, "link", side_effect=OSError("Disk failure")), \
                self.assertLogs(master_images.logger, "ERROR"), self.assertRaises(HTTPException) as error:
            self.create()
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(self.db.query(MdMaster).count(), 0)
        self.assertEqual(list(self.root.rglob("MD*.webp")), [])
        self.assert_clean()

    def test_failed_replacement_preserves_old_image_and_database(self):
        item = self.create()
        with patch.object(master_images.os, "replace", side_effect=OSError("Disk failure")), \
                self.assertLogs(master_images.logger, "ERROR"), self.assertRaises(HTTPException):
            svc.update_item(self.db, "md", item["id"], {**self.payload, "name": "Not saved"}, 1, image_data=self.red)
        self.assertEqual(self.physical(item).read_bytes(), images.process_image(self.blue))
        self.assertEqual(self.db.get(MdMaster, item["id"]).c_item_name, self.payload["name"])
        self.assert_clean()

    def test_canonical_collision_does_not_overwrite_unreferenced_file(self):
        canonical = images.ImagePath("PLATFORM", None, "master", "MD00001.webp")
        occupied = images.write_image(canonical, self.red)
        with self.assertRaises(HTTPException) as error:
            self.create()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(occupied.read_bytes(), self.red)
        self.assertEqual(self.db.query(MdMaster).count(), 0)
        self.assert_clean()

    def test_missing_image_reference_can_be_removed_or_repaired(self):
        item = self.create()
        self.physical(item).unlink()
        with self.assertLogs(master_images.logger, "WARNING"):
            result = svc.update_item(self.db, "md", item["id"], self.payload, 1, image_data=self.red)
        self.assertEqual(self.physical(result).read_bytes(), images.process_image(self.red))
        self.physical(result).unlink()
        with self.assertLogs(master_images.logger, "WARNING"):
            result = svc.update_item(self.db, "md", item["id"], self.payload, 1, remove_image=True)
        self.assertIsNone(result["image_path"])
        self.assert_clean()

    def test_json_updates_and_arbitrary_merchant_paths_cannot_change_image(self):
        item = self.create()
        response = self.client.put(
            f'/platform/api/master-group/md/{item["id"]}',
            json={**self.payload, "active": False, "image_path": "/platform-admin/images/master/OTHER.png"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["item"]["image_path"], item["image_path"])
        self.assertFalse(response.json()["item"]["active"])
        self.assertTrue(self.physical(item).is_file())

    def test_generic_upload_delete_cannot_bypass_md_workflow(self):
        item = self.create()
        response = self.client.delete("/api/images", params={"image_path": item["image_path"]})
        self.assertEqual(response.status_code, 409, response.text)
        response = self.client.post("/api/images/platform-admin/platform-admin/master",
                                    params={"entity_key": item["code"]},
                                    files={"file": (item["code"] + ".jpg", image_bytes(format="JPEG"), "image/jpeg")})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.physical(item).read_bytes(), images.process_image(self.blue))
        svc.update_item(self.db, "md", item["id"], self.payload, 1, remove_image=True)
        response = self.client.post("/api/images/platform-admin/platform-admin/master",
                                    params={"entity_key": item["code"]},
                                    files={"file": (item["code"] + ".png", self.blue, "image/png")})
        self.assertEqual(response.status_code, 409, response.text)

    def test_unauthorized_wrong_role_and_cross_origin_blocked(self):
        item = self.create()
        self.client.cookies.clear()
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.client.get(item["image_path"]).status_code, 401)
        self.sign_in(3)
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.get(item["image_path"]).status_code, 403)
        self.sign_in(1)
        response = self.client.put(f'/platform/api/master-group/md/{item["id"]}/with-image',
                                   data={"payload": json.dumps(self.payload), "remove_image": "true"},
                                   headers={"origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertTrue(self.physical(item).is_file())

    def test_delete_uses_same_rollback_and_cleans_image_on_success(self):
        item = self.create()
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")):
            with self.assertRaises(SQLAlchemyError):
                svc.delete_item(self.db, "md", item["id"])
        self.assertTrue(self.physical(item).is_file())
        self.assertIsNotNone(self.db.get(MdMaster, item["id"]))
        response = self.client.delete(f'/platform/api/master-group/md/{item["id"]}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.physical(item).exists())
        self.assert_clean()

    def test_existing_exports_do_not_gain_image_columns_or_blobs(self):
        before_headers, _ = svc.export_table(self.db, "md")
        self.create()
        headers, rows = svc.export_table(self.db, "md")
        self.assertEqual(headers, before_headers)
        self.assertFalse(any("image" in column.lower() for column in headers))
        self.assertNotIn("/images/", str(rows))

    def test_minimal_migration_preserves_rows_and_is_repeatable(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_md_master (i_md_id INTEGER PRIMARY KEY, c_item_code VARCHAR(20))"))
            conn.execute(text("INSERT INTO tb_md_master VALUES (42, 'MD00042')"))
        migrate_md_images(engine)
        migrate_md_images(engine)
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT * FROM tb_md_master")).one(), (42, "MD00042", None))
        self.assertIn("c_image_path", {col["name"] for col in inspect(engine).get_columns("tb_md_master")})

    def test_browser_local_state_remove_cancel_and_validation(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "js" / "master_group.js").read_text(encoding="utf-8")
        functions = source[source.index("  function discardImage()"):source.index("  function openForm(")]
        script = r"""
const assert = require('node:assert/strict');
let destroyed=0;
let imageUploader={destroy(){destroyed++;}};
""" + functions + r"""
discardImage();
assert.equal(imageUploader,null);
assert.equal(destroyed,1);
discardImage();
assert.equal(destroyed,1);
"""
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AwsumImages.createUploader", source)
        self.assertIn("imageUploader.formData(body)", source)
        self.assertNotIn("imageObjectUrl", source)


@unittest.skipUnless(os.environ.get("AWSUM_TEST_POSTGRES_SESSIONS") == "1", "Opt-in isolated PostgreSQL test.")
class PostgresMasterImageTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_md_images_" + uuid4().hex
        with platform_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(platform_engine.url, connect_args={"options": f"-csearch_path={self.schema}"})
        self.factory = sessionmaker(bind=self.engine)
        self.addCleanup(self.cleanup_schema)
        PlatformBase.metadata.create_all(self.engine)
        migrate_item_kinds(self.engine)
        seed(self.factory)
        self.temp = tempfile.TemporaryDirectory(prefix="awsum-md-pg-test-")
        self.addCleanup(self.temp.cleanup)
        context = patch.object(images, "image_storage_root", return_value=Path(self.temp.name))
        context.start()
        self.addCleanup(context.stop)
        with self.factory() as db:
            category = svc.create_item(db, "category1", {"name": "Category"}, 1)
        self.payload = {"name": "Concurrent item", "item_kind": "ITEM", "category1_id": category["id"],
                        "unit": "EA", "billing_cycle": "ONE_TIME", "retail_price": "10"}

    def cleanup_schema(self):
        self.engine.dispose()
        with platform_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))

    def test_concurrent_create_and_replace_are_serialized(self):
        barrier = Barrier(2)
        def create(color):
            barrier.wait(timeout=10)
            with self.factory() as db:
                return svc.create_item(db, "md", self.payload, 1, image_data=image_bytes(color))
        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(create, ("blue", "red")))
        self.assertEqual({row["code"] for row in records}, {"MD00001", "MD00002"})
        barrier = Barrier(2)
        def replace(color):
            barrier.wait(timeout=10)
            with self.factory() as db:
                return svc.update_item(db, "md", records[0]["id"], {**self.payload, "name": color},
                                       1, image_data=image_bytes(color))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(replace, ("green", "yellow")))
        with self.factory() as db:
            row = db.get(MdMaster, records[0]["id"])
            file = images.physical_image_path(images.ImagePath.parse(row.c_image_path))
            self.assertEqual(file.read_bytes(), images.process_image(image_bytes(row.c_item_name)))
        self.assertEqual(list(Path(self.temp.name).rglob(".master-image-*")), [])

    def test_additive_migration_keeps_actual_postgres_ids_and_values(self):
        with self.factory() as db:
            item = svc.create_item(db, "md", self.payload, 1)
        with self.engine.begin() as conn:
            before = conn.execute(text("SELECT i_md_id, c_item_code, c_item_name, i_category1_id FROM tb_md_master")).all()
            conn.execute(text("ALTER TABLE tb_md_master DROP COLUMN c_image_path"))
        migrate_md_images(self.engine)
        migrate_md_images(self.engine)
        with self.engine.connect() as conn:
            after = conn.execute(text("SELECT i_md_id, c_item_code, c_item_name, i_category1_id FROM tb_md_master")).all()
            self.assertEqual(before, after)
            self.assertEqual(conn.execute(text("SELECT c_image_path FROM tb_md_master WHERE i_md_id=:id"),
                                          {"id": item["id"]}).scalar(), None)


if __name__ == "__main__":
    unittest.main()
