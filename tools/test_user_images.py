import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from threading import Barrier
import unittest
from unittest.mock import patch

import bcrypt
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.database.user_image_migration import migrate_user_images
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.session import SessionTbl
from backend.routers import platform_store as routes
from backend.services import images, image_service as master_images
from backend.services.paths import AwsumPathResolver
from tools import test_master_images, test_web_sessions


class UserImageTests(unittest.TestCase):
    def setUp(self):
        test_master_images.MasterImageTests.setUp(self)
        with self.factory() as db:
            db.get(test_web_sessions.Role, 2).c_name = "Admin"
            db.commit()
        self.payload = {
            "name": "Photo User", "email": "photo@example.test", "role_id": 2, "status": "active",
            "password": "TempUser!42", "confirm_password": "TempUser!42", "must_change_password": True,
        }

    sign_in = test_master_images.MasterImageTests.sign_in
    physical = test_master_images.MasterImageTests.physical
    assert_clean = test_master_images.MasterImageTests.assert_clean

    def create(self):
        response = self.client.post("/platform/user/with-image", data={"payload": json.dumps(self.payload)},
                                    files={"image": ("personal-photo.png", self.blue, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def update(self, identity, *, data=None, remove=False, changes=None, headers=None):
        return self.client.put(
            f"/platform/user/{identity}/with-image",
            data={"payload": json.dumps({**self.payload, **(changes or {})}), "remove_image": str(remove).lower()},
            files={"image": ("replacement.png", data, "image/png")} if data is not None else None,
            headers=headers,
        )

    def test_numeric_id_paths_all_owner_contexts_and_no_employee_alias(self):
        for owner in (AwsumPathResolver("PLATFORM"), AwsumPathResolver("AGENCY", agency_code="AGT00001"),
                      AwsumPathResolver("MERCHANT", merchant_code="CLT11001"),
                      AwsumPathResolver("STORE", merchant_code="CLT11001", store_code="CLT00001")):
            self.assertEqual(owner.get_user_image_path(100001, "jpg"),
                             owner.get_context_root() + "/images/users/100001.jpg")
        for identity in ("me@example.test", "Shane", "../123", "001", 0, -1, True):
            with self.assertRaises(ValueError):
                AwsumPathResolver("PLATFORM").get_user_image_path(identity)

    def test_user_and_md_submit_to_the_same_processor_and_transaction_service(self):
        from backend.services import master_group
        category = self.db.query(master_group.Category1).first()
        payload = {"name": "Common framework", "item_kind": "HARDWARE", "category1_id": category.i_category1_id,
                   "retail_price": "10", "unit": "EA", "billing_cycle": "ONE_TIME"}
        with patch.object(images, "process_image", wraps=images.process_image) as processor:
            user = self.create()
            master = master_group.create_item(self.db, "md", payload, 1, image_data=self.blue)
        self.assertEqual(processor.call_count, 2)
        self.assertTrue(all(call.args == (self.blue,) for call in processor.call_args_list))
        self.assertEqual(self.physical(user).read_bytes(), self.physical(master).read_bytes())
        self.assertTrue(user["image_path"].endswith(f'/users/{user["id"]}.webp'))
        self.assertTrue(master["image_path"].endswith(f'/master/{master["code"]}.webp'))
        self.assertIn("users", images.CATEGORIES)
        self.assertIn("employee", images.CATEGORIES)
        self.assertNotIn("users", images.PUBLIC_CATEGORIES)

    def test_create_returns_finished_user_and_photo_with_original_security_values(self):
        item = self.create()
        self.assertEqual(item["image_path"], f'/platform-admin/images/users/{item["id"]}.webp')
        self.assertIn("?v=", item["image_url"])
        self.db.expire_all()
        row = self.db.get(PlatformUser, item["id"])
        self.assertTrue(bcrypt.checkpw(self.payload["password"].encode(), row.c_password.encode()))
        self.assertEqual((row.i_role_id, row.i_store_id, row.i_must_change_password), (2, ADMIN_STORE_ID, 1))
        photo = self.client.get(item["image_url"])
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo.headers["cache-control"], "private, no-store")
        with Image.open(self.physical(item)) as saved:
            self.assertEqual((saved.format, saved.size), ("WEBP", (512, 512)))
        users = self.client.get("/platform/users").json()
        serialized = next(user for user in users if user["id"] == item["id"])
        self.assertEqual(serialized["image_url"], item["image_url"])
        self.assertNotIn("password", serialized)
        self.assert_clean()

    def test_shared_reset_preserves_platform_photo_identity_and_validation(self):
        item = self.create()
        payload = {"password": "Replacement!42", "confirm_password": "Replacement!42"}
        invalid = self.client.post(f'/platform/user/{item["id"]}/reset-password',
                                   json={**payload, "confirm_password": "mismatch"})
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.db.expire_all()
        row = self.db.get(PlatformUser, item["id"])
        self.assertTrue(bcrypt.checkpw(self.payload["password"].encode(), row.c_password.encode()))
        reset = self.client.post(f'/platform/user/{item["id"]}/reset-password', json=payload)
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(reset.json(), {"success": True, "must_change_password": True})
        self.db.expire_all()
        row = self.db.get(PlatformUser, item["id"])
        self.assertTrue(bcrypt.checkpw(payload["password"].encode(), row.c_password.encode()))
        self.assertEqual((row.i_agency_id, row.i_role_id, row.i_must_change_password,
                          row.c_email, row.c_image_path),
                         (None, 2, 1, self.payload["email"], item["image_path"]))
        self.assertEqual(self.client.get(item["image_url"]).status_code, 200)

    def test_replace_remove_cancel_and_immutable_identifier(self):
        item = self.create()
        old_hash = self.db.get(PlatformUser, item["id"]).c_password
        response = self.update(item["id"], data=self.red, changes={"name": "Renamed", "email": "changed@example.test"})
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["image_path"], item["image_path"])
        self.assertNotEqual(updated["image_url"], item["image_url"])
        with Image.open(self.physical(item)) as photo:
            self.assertLessEqual(max(abs(a - b) for a, b in zip(photo.getpixel((0, 0))[:3], (255, 0, 0))), 3)
        # A local remove/cancel does not call Save and cannot affect the saved reference.
        self.assertEqual(self.client.get(updated["image_url"]).status_code, 200)
        self.db.expire_all()
        self.assertEqual(self.db.get(PlatformUser, item["id"]).c_password, old_hash)
        response = self.update(item["id"], remove=True, changes={"email": "changed@example.test"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["image_path"])
        self.assertFalse(self.physical(item).exists())
        self.assertEqual(list(self.root.rglob("*.svg")), [])
        self.assertEqual(self.client.get(item["image_url"]).status_code, 404)
        self.assert_clean()

    def test_invalid_upload_creates_no_user_or_photo(self):
        before = self.db.query(PlatformUser).count()
        for name, data, mime in (("../123.png", self.blue, "image/png"), ("bad.jpg", self.blue, "image/jpeg"),
                                 ("bad.svg", b"<svg/>", "image/svg+xml"), ("bad.png", b"broken", "image/png"),
                                 ("large.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")):
            response = self.client.post("/platform/user/with-image", data={"payload": json.dumps(self.payload)},
                                        files={"image": (name, data, mime)})
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.db.query(PlatformUser).count(), before)
        self.assertEqual(list(self.root.rglob("*.png")), [])

    def test_existing_photo_is_not_uploaded_or_revalidated_on_account_only_edits(self):
        item = self.create()
        original = self.physical(item).read_bytes()
        for changes in ({"name": "Name only"}, {"role_id": 1, "status": "inactive"},
                        {"email": "renamed@example.test", "must_change_password": False}):
            with self.subTest(changes=changes), patch.object(images, "normalize_image") as normalize:
                response = self.client.put(f"/platform/user/{item['id']}/with-image",
                                           data={"payload": json.dumps(changes), "remove_image": "false"})
                self.assertEqual(response.status_code, 200, response.text)
                normalize.assert_not_called()
                self.assertEqual(response.json()["image_path"], item["image_path"])
                self.assertEqual(self.physical(item).read_bytes(), original)
        self.db.expire_all()
        self.assertEqual(self.db.get(PlatformUser, item["id"]).c_image_path, item["image_path"])

    def test_original_upload_names_are_not_canonical_storage_filenames(self):
        item = self.create()
        for name, fmt, mime in (("myphoto.JPG", "JPEG", "image/jpeg"),
                                ("profile photo.jpeg", "JPEG", "image/jpeg"),
                                ("\ud504\ub85c\ud544 \uc0ac\uc9c4.PNG", "PNG", "image/png"),
                                ("my photo.WEBP", "WEBP", "image/webp")):
            with self.subTest(name=name):
                response = self.client.put(
                    f"/platform/user/{item['id']}/with-image", data={"payload": "{}"},
                    files={"image": (name, test_master_images.image_bytes(format=fmt), mime)},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["image_path"], f"/platform-admin/images/users/{item['id']}.webp")
                with Image.open(self.physical(item)) as saved:
                    self.assertEqual((saved.format, saved.size), ("WEBP", (512, 512)))
                self.assertEqual(self.client.get(response.json()["image_url"]).status_code, 200)
                self.assertNotIn("media", self.physical(item).relative_to(self.root).parts)
        self.db.expire_all()
        self.assertNotIn("\\", self.db.get(PlatformUser, item["id"]).c_image_path)

    def test_new_upload_path_names_are_rejected_without_changing_saved_photo(self):
        item = self.create()
        original = self.physical(item).read_bytes()
        for name in ("../photo.png", r"..\photo.png", "/platform-admin/images/users/4.png",
                     r"D:\Awsum_Data\photo.png", "D:photo.png", "https://example.test/photo.png",
                     "photo..png", "photo%2fother.png", "photo.png?x=1", "CON.png"):
            with self.subTest(name=name):
                response = self.client.put(
                    f"/platform/user/{item['id']}/with-image", data={"payload": "{}"},
                    files={"image": (name, self.red, "image/png")},
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.physical(item).read_bytes(), original)
        self.assert_clean()

    def test_legacy_photo_reference_survives_account_edit_then_migrates_after_commit(self):
        item = self.create()
        for extension, fmt in (("jpg", "JPEG"), ("png", "PNG")):
            with self.subTest(extension=extension):
                current = self.physical(item)
                legacy = current.with_suffix("." + extension)
                legacy.write_bytes(test_master_images.image_bytes("blue", format=fmt))
                current.unlink()
                reference = f"/platform-admin/images/users/{item['id']}.{extension}"
                with self.factory() as db:
                    db.get(PlatformUser, item["id"]).c_image_path = reference
                    db.commit()
                response = self.client.put(f"/platform/user/{item['id']}/with-image",
                                           data={"payload": '{"name":"Keep old photo"}'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["image_path"], reference)
                commit = self.db.commit
                def verify_commit():
                    self.assertTrue(legacy.is_file(), "Old extension must survive until SQL commit")
                    self.assertTrue(current.is_file(), "Replacement must exist before SQL commit")
                    commit()
                with patch.object(routes, "get_current_user", return_value=SimpleNamespace(login_role=1, i_platform_user_id=1)), \
                        patch.object(self.db, "commit", side_effect=verify_commit):
                    routes._save_user(item["id"], {"name": "New WEBP"}, SimpleNamespace(), self.db, image_data=self.red)
                self.assertFalse(legacy.exists())
                self.assertTrue(current.is_file())
                self.assert_clean()

    def test_failed_legacy_extension_replace_keeps_old_photo_and_reference(self):
        item = self.create()
        target = self.physical(item)
        old = target.with_suffix(".jpg")
        original = test_master_images.image_bytes("blue", format="JPEG")
        old.write_bytes(original)
        target.unlink()
        reference = f"/platform-admin/images/users/{item['id']}.jpg"
        with self.factory() as db:
            db.get(PlatformUser, item["id"]).c_image_path = reference
            db.commit()
        with patch.object(routes, "get_current_user", return_value=SimpleNamespace(login_role=1, i_platform_user_id=1)), \
                patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")), self.assertRaises(SQLAlchemyError):
            routes._save_user(item["id"], {}, SimpleNamespace(), self.db, image_data=self.red)
        self.assertEqual(old.read_bytes(), original)
        self.assertFalse(target.exists())
        self.assertEqual(self.db.get(PlatformUser, item["id"]).c_image_path, reference)
        self.assert_clean()

    def test_profile_upload_preserves_exif_orientation_and_limits_original_not_intermediate_png(self):
        item = self.create()
        photo = Image.new("RGB", (100, 200), "blue")
        photo.paste("red", (0, 0, 100, 100))
        exif = Image.Exif()
        exif[274] = 6
        output = BytesIO()
        photo.save(output, "JPEG", quality=95, exif=exif)
        result = self.client.put(f"/platform/user/{item['id']}/with-image",
            data={"payload": "{}"}, files={"image": ("phone.jpg", output.getvalue(), "image/jpeg")})
        self.assertEqual(result.status_code, 200, result.text)
        with Image.open(self.physical(item)) as saved:
            self.assertGreater(saved.getpixel((32, 256))[2], 240)
            self.assertGreater(saved.getpixel((480, 256))[0], 240)
        photo = Image.frombytes("RGB", (1500, 1500), random.Random(42).randbytes(1500 * 1500 * 3))
        output = BytesIO()
        photo.save(output, "PNG")
        self.assertGreater(len(output.getvalue()), 5 * 1024 * 1024)
        output = BytesIO()
        photo.save(output, "JPEG", quality=90)
        self.assertLess(len(output.getvalue()), 5 * 1024 * 1024)
        result = self.client.put(f"/platform/user/{item['id']}/with-image",
            data={"payload": "{}"}, files={"image": ("camera.jpg", output.getvalue(), "image/jpeg")})
        self.assertEqual(result.status_code, 200, result.text)
        with Image.open(self.physical(item)) as saved:
            self.assertEqual((saved.format, saved.size), ("WEBP", (512, 512)))
        self.assert_clean()

    def test_legacy_replacement_does_not_overwrite_unreferenced_canonical_file(self):
        item = self.create()
        canonical = self.physical(item)
        conflict = canonical.read_bytes()
        legacy = canonical.with_suffix(".png")
        legacy.write_bytes(self.blue)
        reference = f"/platform-admin/images/users/{item['id']}.png"
        with self.factory() as db:
            db.get(PlatformUser, item["id"]).c_image_path = reference
            db.commit()
        result = self.update(item["id"], data=self.red)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(canonical.read_bytes(), conflict)
        self.assertEqual(legacy.read_bytes(), self.blue)
        self.assertEqual(self.db.get(PlatformUser, item["id"]).c_image_path, reference)
        self.assert_clean()

    def test_user_guards_still_block_invalid_roles_duplicate_email_and_self_deactivation(self):
        item = self.create()
        old_bytes = self.physical(item).read_bytes()
        duplicate = self.client.post("/platform/user/with-image", data={"payload": json.dumps(self.payload)},
                                     files={"image": ("photo.png", self.red, "image/png")})
        self.assertEqual(duplicate.status_code, 409)
        invalid = self.update(item["id"], data=self.red, changes={"role_id": 999})
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertEqual(self.physical(item).read_bytes(), old_bytes)
        self_deactivate = self.update(1, remove=True, changes={"email": "user1@example.test", "status": "inactive"})
        self.assertEqual(self_deactivate.status_code, 400, self_deactivate.text)
        with self.factory() as db:
            db.get(PlatformUser, 2).c_status = "inactive"
            db.commit()
        demote = self.update(1, changes={"email": "user1@example.test", "role_id": 2})
        self.assertEqual(demote.status_code, 400, demote.text)
        self.assertIn("last active SuperAdmin", demote.json()["detail"])

    def test_authorization_and_origin_enforced_before_image_validation(self):
        item = self.create()
        self.client.cookies.clear()
        self.assertEqual(self.update(item["id"], data=b"bad").status_code, 401)
        self.assertEqual(self.client.get(item["image_url"]).status_code, 401)
        self.sign_in(3)
        self.assertEqual(self.update(item["id"], data=self.red).status_code, 403)
        self.assertEqual(self.client.get(item["image_url"]).status_code, 403)
        self.sign_in(1)
        response = self.update(item["id"], remove=True, headers={"origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.physical(item).exists())

    def test_generic_upload_delete_cannot_manage_user_photos(self):
        item = self.create()
        for path in (item["image_path"], "/platform-admin/images/users/999999.png"):
            result = self.client.delete("/api/images", params={"image_path": path})
            self.assertEqual(result.status_code, 409, result.text)
        result = self.client.post("/api/images/platform-admin/platform-admin/users",
                                  params={"entity_key": "999999"},
                                  files={"file": ("999999.png", self.blue, "image/png")})
        self.assertEqual(result.status_code, 409)
        self.assertTrue(self.physical(item).is_file())

    def test_json_updates_ignore_arbitrary_photo_reference_and_preserve_file(self):
        item = self.create()
        response = self.client.put(f'/platform/user/{item["id"]}', json={
            "name": "JSON update", "image_path": "/platform-admin/images/users/1.png",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["image_path"], item["image_path"])
        self.assertTrue(self.physical(item).is_file())

    def test_commit_failure_restores_old_photo_and_all_user_values(self):
        item = self.create()
        old_bytes = self.physical(item).read_bytes()
        row = self.db.get(PlatformUser, item["id"])
        old_name, old_hash = row.c_first_name, row.c_password
        with patch.object(routes, "get_current_user", return_value=SimpleNamespace(login_role=1, i_platform_user_id=1)), \
                patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")):
            with self.assertRaises(SQLAlchemyError):
                routes._save_user(item["id"], {"name": "Failed update"}, SimpleNamespace(), self.db, image_data=self.red)
        self.assertEqual(self.physical(item).read_bytes(), old_bytes)
        self.assertEqual(self.db.get(PlatformUser, item["id"]).c_first_name, old_name)
        self.assertEqual(self.db.get(PlatformUser, item["id"]).c_password, old_hash)
        self.assert_clean()

    def test_remove_commit_failure_restores_old_photo(self):
        item = self.create()
        old_bytes = self.physical(item).read_bytes()
        with patch.object(routes, "get_current_user", return_value=SimpleNamespace(login_role=1, i_platform_user_id=1)), \
                patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")):
            with self.assertRaises(SQLAlchemyError):
                routes._save_user(item["id"], {}, SimpleNamespace(), self.db, remove_image=True)
        self.assertEqual(self.physical(item).read_bytes(), old_bytes)
        self.assertEqual(self.db.get(PlatformUser, item["id"]).c_image_path, item["image_path"])
        self.assert_clean()

    def test_file_failure_and_failed_create_commit_leave_no_user_record(self):
        before = self.db.query(PlatformUser).count()
        with patch.object(routes, "get_current_user", return_value=SimpleNamespace(login_role=1, i_platform_user_id=1)), \
                patch.object(master_images.os, "link", side_effect=OSError("Disk failed")), \
                self.assertLogs(master_images.logger, "ERROR"), self.assertRaises(HTTPException):
            routes._save_user(None, self.payload, SimpleNamespace(), self.db, image_data=self.blue)
        self.assertEqual(self.db.query(PlatformUser).count(), before)
        with patch.object(routes, "get_current_user", return_value=SimpleNamespace(login_role=1, i_platform_user_id=1)), \
                patch.object(self.db, "commit", side_effect=SQLAlchemyError("Commit failed")), self.assertRaises(SQLAlchemyError):
            routes._save_user(None, self.payload, SimpleNamespace(), self.db, image_data=self.blue)
        self.assertEqual(self.db.query(PlatformUser).count(), before)
        self.assertEqual(list(self.root.rglob("*.webp")), [])
        self.assert_clean()

    def test_profile_update_does_not_change_active_sessions_or_password(self):
        item = self.create()
        before = [(row.i_session_id, row.c_status, row.c_session_key) for row in self.db.query(SessionTbl).all()]
        response = self.update(1, data=self.blue, changes={"name": "Same session", "email": "user1@example.test",
                                                        "role_id": 1, "must_change_password": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        after = [(row.i_session_id, row.c_status, row.c_session_key) for row in self.db.query(SessionTbl).all()]
        self.assertEqual(before, after)
        self.assertEqual(self.db.query(SessionTbl).filter_by(c_status="ACTIVE").count(), 1)
        self.assertEqual(self.client.get("/platform/users").status_code, 200)

    def test_additive_schema_migration_preserves_existing_security_fields(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_platform_user (i_platform_user_id INTEGER PRIMARY KEY, c_password VARCHAR, i_role_id INTEGER)"))
            conn.execute(text("INSERT INTO tb_platform_user VALUES (100001,'unchanged-hash',1)"))
        migrate_user_images(engine)
        migrate_user_images(engine)
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT * FROM tb_platform_user")).one(), (100001, "unchanged-hash", 1, None))
        self.assertTrue(next(c for c in inspect(engine).get_columns("tb_platform_user") if c["name"] == "c_image_path")["nullable"])


@unittest.skipUnless(os.environ.get("AWSUM_TEST_POSTGRES_SESSIONS") == "1", "Opt-in isolated PostgreSQL test.")
class PostgresUserImageTests(unittest.TestCase):
    cleanup_schema = test_master_images.PostgresMasterImageTests.cleanup_schema

    def setUp(self):
        test_master_images.PostgresMasterImageTests.setUp(self)
        with self.engine.begin() as conn:
            conn.execute(text("SELECT setval(pg_get_serial_sequence('tb_platform_user','i_platform_user_id'),3,true)"))
        context = patch.object(routes, "get_current_user", return_value=SimpleNamespace(
            login_role=1, i_platform_user_id=1))
        context.start()
        self.addCleanup(context.stop)

    def test_concurrent_user_create_and_replace_preserve_ids_and_matching_files(self):
        barrier = Barrier(2)
        def create(color):
            barrier.wait(timeout=10)
            with self.factory() as db:
                response = routes._save_user(None, {
                    "name": color, "email": color + "@example.test", "role_id": 1,
                    "password": "Temporary!42", "confirm_password": "Temporary!42",
                }, SimpleNamespace(), db, image_data=test_master_images.image_bytes(color))
                return json.loads(response.body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(create, ("blue", "red")))
        self.assertEqual(len({row["id"] for row in rows}), 2)
        barrier = Barrier(2)
        def replace(color):
            barrier.wait(timeout=10)
            with self.factory() as db:
                routes._save_user(rows[0]["id"], {"name": color}, SimpleNamespace(), db,
                                  image_data=test_master_images.image_bytes(color))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(replace, ("green", "yellow")))
        with self.factory() as db:
            row = db.get(PlatformUser, rows[0]["id"])
            file = images.physical_image_path(images.ImagePath.parse(row.c_image_path))
            self.assertTrue(row.c_image_path.endswith(f'/{rows[0]["id"]}.webp'))
            with Image.open(file) as photo, Image.open(BytesIO(test_master_images.image_bytes(row.c_first_name))) as expected:
                self.assertEqual((photo.format, photo.size), ("WEBP", (512, 512)))
                self.assertLessEqual(max(abs(a - b) for a, b in zip(photo.getpixel((0, 0))[:3], expected.getpixel((0, 0))[:3])), 3)
        self.assertEqual(list(Path(self.temp.name).rglob(".master-image-*")), [])

    def test_postgres_migration_twice_preserves_security_fields_and_active_sessions(self):
        test_web_sessions.login_token(self.factory)
        with self.engine.begin() as conn:
            query = "SELECT i_platform_user_id,i_store_id,i_role_id,c_email,c_password,i_must_change_password,c_status FROM tb_platform_user ORDER BY i_platform_user_id"
            before = conn.execute(text(query)).all()
            sessions = conn.execute(text("SELECT i_session_id,c_session_key,c_status FROM tb_session")).all()
            conn.execute(text("ALTER TABLE tb_platform_user DROP COLUMN c_image_path"))
        migrate_user_images(self.engine)
        migrate_user_images(self.engine)
        with self.engine.connect() as conn:
            self.assertEqual(before, conn.execute(text(query)).all())
            self.assertEqual(sessions, conn.execute(text("SELECT i_session_id,c_session_key,c_status FROM tb_session")).all())
            self.assertEqual(conn.execute(text("SELECT count(*) FROM tb_platform_user WHERE c_image_path IS NOT NULL")).scalar(), 0)


if __name__ == "__main__":
    unittest.main()
