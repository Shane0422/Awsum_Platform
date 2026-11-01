import json
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.database.image_storage_migration import migrate_image_storage
from backend.database.resource_path_migration import migrate_resource_paths
from backend.database.store_db import get_store_engine, get_store_backup_dir
from backend.models_admin.account import Account
from backend.models_admin.store import Store
from backend.models_admin.contract import Contract
from backend.services import images, paths, resource_storage
from backend.routers import platform_store
from tools import test_images


class GlobalPathTests(unittest.TestCase):
    setUp = test_images.ImageStorageTests.setUp
    sign_in = test_images.ImageStorageTests.sign_in
    legacy = test_images.ImageStorageTests.legacy

    def test_exact_owner_roots_data_categories_and_store_requires_merchant(self):
        examples = (
            (paths.AwsumPathResolver("PLATFORM"), "/platform-admin"),
            (paths.AwsumPathResolver("AGENCY", agency_code="AG000001"), "/agency/AG000001"),
            (paths.AwsumPathResolver("MERCHANT", merchant_code="MC000001"), "/merchant/MC000001"),
            (paths.AwsumPathResolver("STORE", merchant_code="MC000001", store_code="ST000001"),
             "/merchant/MC000001/store/ST000001"),
        )
        for owner, prefix in examples:
            self.assertEqual(owner.get_context_root(), prefix)
            self.assertEqual(owner.get_image_root(), prefix + "/images/")
            self.assertEqual(owner.get_data_root(), prefix + "/data/")
            for category in paths.DATA_CATEGORIES[owner.context_type]:
                logical = owner.get_data_path(category)
                self.assertEqual(logical, prefix + "/data/" + category + "/")
                self.assertEqual(paths.AwsumPathResolver.parse_resource(logical)[0], owner)
            self.assertEqual(owner.get_image_path("master", "IT000025.jpg", "item"),
                             prefix + "/images/master/item/IT000025.jpg")
        for create in (
            lambda: paths.AwsumPathResolver("STORE", store_code="ST000001"),
            lambda: images.ImagePath("STORE", "ST000001", "logo", "logo.png"),
            lambda: paths.AwsumPathResolver("STORE", merchant_code="MC000001", store_code="ST1", agency_code="AG1"),
            lambda: paths.AwsumPathResolver.parse_resource("/store/ST000001/data/master/main.db"),
            lambda: paths.AwsumPathResolver.parse_resource("/data/store/ST000001/master/main.db"),
            lambda: paths.AwsumPathResolver.parse_resource("/agency/AG1/merchant/MC1/store/ST1/data/master/main.db"),
        ):
            with self.assertRaises(ValueError):
                create()

    def test_canonical_traversal_and_data_validation(self):
        root = "/merchant/CLT11001/store/STR00002/"
        for suffix in (
            "images/logo/../secret.png", "images/logo/%2e%2e.png", "images/logo/..\\secret.png",
            "images/employees/EMP1.png", "images/Logo/logo.png", "images/logo/CON.png",
            "data/transaction/../secret.json", "data/sales/evil.exe", "data/sales/a.json:secret",
            "data/employee/a.json?x=1", "data/employee/CON.txt", "data/employee/a..txt",
            "data/security/a.json", "uploads/common/a.json",
        ):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                paths.AwsumPathResolver.parse_resource(root + suffix)

    def add_second_merchant(self):
        with self.factory() as db:
            db.add(Account(i_client_id=2, c_client_code="CLT11002", c_client_name="Other Merchant"))
            db.add(Store(i_store_id=4, c_store_code="STR00004", i_account_id=2, c_store_name="Other Store"))
            db.commit()

    def test_wrong_merchant_denied_even_for_public_read_and_platform_admin(self):
        self.add_second_merchant()
        # A physical file under an incorrect parent must not make it an accessible resource.
        wrong = images.ImagePath("STORE", "STR00002", "logo", "logo.png", merchant_code="CLT11002")
        images.write_image(wrong, self.png)
        self.assertEqual(self.client.get(wrong.logical_path).status_code, 404)
        self.sign_in()
        response = self.client.post("/api/images/merchant/CLT11002/store/STR00002/logo",
                                   params={"entity_key": "logo"},
                                   files={"file": ("logo.png", self.png, "image/png")})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.client.delete("/api/images", params={"image_path": wrong.logical_path}).status_code, 404)
        self.assertEqual(self.client.post("/api/images/store/STR00002/logo",
            files={"file": ("logo.png", self.png, "image/png")}).status_code, 400)

    def test_multiple_stores_isolated_and_agency_is_not_in_path(self):
        with self.factory() as db:
            db.add(Store(i_store_id=4, c_store_code="STR00004", i_account_id=1,
                         i_installed_by_agency_id=1, c_store_name="Second Store"))
            db.commit()
        first = images.ImagePath("STORE", "STR00002", "employee", "EMP1.png", merchant_code="CLT11001")
        second = images.ImagePath("STORE", "STR00004", "employee", "EMP1.png", merchant_code="CLT11001")
        images.write_image(first, self.png)
        images.write_image(second, self.png)
        self.assertNotEqual(images.physical_image_path(first), images.physical_image_path(second))
        self.assertNotIn("agency", second.logical_path)
        self.sign_in(3)
        self.assertEqual(self.client.get(first.logical_path).status_code, 200)
        self.assertEqual(self.client.get(second.logical_path).status_code, 403)

    def test_standalone_migration_all_categories_and_safe_read_only_alias(self):
        legacy = self.media / "store" / "STR00002" / "images"
        for category, tail in (("logo", "logo.png"), ("master", "item/IT1.png"), ("employee", "EMP1.png")):
            source = legacy / category / Path(tail)
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(self.png)
        old = "/store/STR00002/images/logo/logo.png"
        with self.factory() as db:
            db.get(Store, 2).c_logo_url = old
            db.commit()
        plan = migrate_image_storage(self.engine)
        self.assertEqual(len(plan["assets"]), 3)
        new = "/merchant/CLT11001/store/STR00002/images/logo/logo.png"
        with self.factory() as db:
            self.assertEqual(db.get(Store, 2).c_logo_url, new)
        self.assertTrue((legacy / "logo" / "logo.png").exists())
        self.assertEqual(migrate_image_storage(self.engine)["status"], "UNCHANGED")
        self.client.cookies.set("access_token", "stale", domain="testserver.local", path="/")
        response = self.client.get(old)
        self.assertEqual(response.status_code, 307, response.text)
        self.assertEqual(response.headers["location"], new)
        self.assertEqual(self.client.get(new).status_code, 200)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/store/STR00002/images/employee/EMP1.png").status_code, 401)
        self.assertEqual(self.client.delete("/api/images", params={"image_path": old}).status_code, 400)
        self.assertEqual(self.client.get("/store/UNKNOWN/images/logo/logo.png").status_code, 404)

    def test_nested_image_migration_conflict_does_not_change_reference(self):
        old_source = self.media / "store" / "STR00002" / "images" / "logo" / "logo.png"
        old_source.parent.mkdir(parents=True)
        old_source.write_bytes(self.png)
        target = images.ImagePath("STORE", "STR00002", "logo", "logo.png", merchant_code="CLT11001")
        images.write_image(target, b"other")
        with self.factory() as db:
            db.get(Store, 2).c_logo_url = "/store/STR00002/images/logo/logo.png"
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "conflict"):
            migrate_image_storage(self.engine)
        self.assertEqual(old_source.read_bytes(), self.png)
        with self.factory() as db:
            self.assertEqual(db.get(Store, 2).c_logo_url, "/store/STR00002/images/logo/logo.png")

    def test_orphan_legacy_store_images_retained_without_guessed_parent(self):
        source = self.legacy("brands/clients/CLT11001/stores/STR99999/logo.png")
        standalone = self.media / "store" / "STR99999" / "images" / "logo" / "logo.png"
        standalone.parent.mkdir(parents=True)
        standalone.write_bytes(self.png)
        with self.assertLogs("backend.database.image_storage_migration", level="WARNING"):
            result = migrate_image_storage(self.engine)
        self.assertEqual(len(result["unmapped_legacy_assets"]), 2)
        self.assertEqual(result["assets"], [])
        self.assertTrue(source.is_file())
        self.assertTrue(standalone.is_file())
        self.assertFalse((self.media / "merchant").exists())

    def test_data_operations_validate_membership_and_never_serve_publicly(self):
        self.add_second_merchant()
        owner = paths.AwsumPathResolver("STORE", merchant_code="CLT11001", store_code="STR00002")
        wrong = paths.AwsumPathResolver("STORE", merchant_code="CLT11002", store_code="STR00002")
        from tools.test_web_sessions import request, login_token
        token = login_token(self.factory, 3)
        with patch.object(paths, "get_data_root", return_value=self.root / "data"):
            with self.factory() as db:
                file = resource_storage.write_data(request(token), db, owner.get_data_path("sales", "sale.json"), b'{"ok":true}')
                self.assertEqual(file.read_bytes(), b'{"ok":true}')
                with self.assertRaises(HTTPException) as error:
                    resource_storage.write_data(request(token), db, wrong.get_data_path("sales", "sale.json"), b"x")
                self.assertEqual(error.exception.status_code, 404)
                with self.assertRaises(HTTPException) as error:
                    resource_storage.write_data(request(), db, owner.get_data_path("sales", "other.json"), b"x")
                self.assertEqual(error.exception.status_code, 401)
            self.assertEqual(self.client.get(owner.get_data_path("sales", "sale.json")).status_code, 404)

    def test_sqlite_helper_requires_verified_merchant_and_refuses_unmigrated_legacy(self):
        root = self.root / "data"
        with patch.object(paths, "get_data_root", return_value=root), patch("backend.core.data_paths.get_data_root", return_value=root):
            with self.factory() as db:
                with self.assertRaises(HTTPException):
                    get_store_engine("UNKNOWN", "STR00002", db=db)
                legacy = root / "company" / "STR00002" / "main.db"
                legacy.parent.mkdir(parents=True)
                with closing(sqlite3.connect(legacy)) as conn:
                    with conn:
                        conn.execute("CREATE TABLE evidence(value TEXT)")
                        conn.execute("INSERT INTO evidence VALUES ('preserved')")
                with self.assertRaisesRegex(RuntimeError, "migrate_global_paths"):
                    get_store_engine("CLT11001", "STR00002", db=db)
            with patch("backend.database.resource_path_migration.get_data_root", return_value=root), patch(
                "backend.database.resource_path_migration.PROJECT_ROOT", self.root
            ):
                dry = migrate_resource_paths(self.engine, dry_run=True)
                self.assertEqual(len(dry["references"]), 1)
                self.assertTrue(legacy.is_file())
                plan = migrate_resource_paths(self.engine)
                self.assertEqual(plan["status"], "COMMITTED")
                logical = "/merchant/CLT11001/store/STR00002/data/master/main.db"
                with self.factory() as db:
                    self.assertEqual(db.get(Store, 2).c_db_path, logical)
                    engine = get_store_engine("CLT11001", "STR00002", db=db)
                    self.assertEqual(engine.url.database, str(paths.resource_storage_path(logical)))
                    engine.dispose()
                    self.assertIn("merchant", get_store_backup_dir("CLT11001", "STR00002", db=db))
                with closing(sqlite3.connect(paths.resource_storage_path(logical))) as conn:
                    with conn:
                        conn.execute("INSERT INTO evidence VALUES ('new transaction')")
                # Restart cannot overwrite or reject a live canonical DB due to old-copy hash differences.
                self.assertEqual(migrate_resource_paths(self.engine)["status"], "UNCHANGED")
                with closing(sqlite3.connect(legacy)) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)
                mapping = json.loads(Path(plan["manifest"]).read_text())
                self.assertIsNone(mapping["references"][0]["before"])

    def test_data_migration_conflict_and_active_sqlite_journal_fail_before_changes(self):
        root = self.root / "data"
        legacy = root / "company" / "STR00002" / "main.db"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"fixture database")
        owner = paths.AwsumPathResolver("STORE", merchant_code="CLT11001", store_code="STR00002")
        with patch.object(paths, "get_data_root", return_value=root), patch(
            "backend.database.resource_path_migration.get_data_root", return_value=root
        ), patch("backend.database.resource_path_migration.PROJECT_ROOT", self.root):
            journal = Path(str(legacy) + "-wal")
            journal.write_bytes(b"active transaction")
            with self.assertRaisesRegex(RuntimeError, "Quiesce"):
                migrate_resource_paths(self.engine)
            journal.unlink()
            resource_storage.write_resource(owner.get_data_path("master", "main.db"), b"different")
            with self.assertRaisesRegex(RuntimeError, "conflict"):
                migrate_resource_paths(self.engine)
            with self.factory() as db:
                self.assertIsNone(db.get(Store, 2).c_db_path)
            self.assertFalse((root / "platform-admin/data/audit/path-migrations").exists())

    def contract(self, db, reference=None, merchant_id=1):
        from datetime import date
        contract = Contract(i_contract_id=1, i_subscription_id=1, i_account_id=merchant_id,
                            i_store_id=2, dt_contract_start_date=date(2026, 1, 1),
                            c_contract_pdf_path=reference)
        db.add(contract)
        db.commit()
        return contract

    def test_contract_files_and_references_migrate_without_changing_relations(self):
        root = self.root / "data"
        source = root / "contracts" / "contract_1.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"%PDF-1.4 fixture")
        with self.factory() as db:
            self.contract(db, "data/contracts/contract_1.pdf")
        with patch.object(paths, "get_data_root", return_value=root), patch(
            "backend.database.resource_path_migration.get_data_root", return_value=root
        ), patch("backend.database.resource_path_migration.PROJECT_ROOT", self.root):
            plan = migrate_resource_paths(self.engine)
            self.assertEqual(len(plan["references"]), 1)
            logical = "/merchant/CLT11001/store/STR00002/data/common/contract_1.pdf"
            with self.factory() as db:
                row = db.get(Contract, 1)
                self.assertEqual(row.c_contract_pdf_path, logical)
                self.assertEqual((row.i_account_id, row.i_store_id), (1, 2))
            self.assertEqual(paths.resource_storage_path(logical).read_bytes(), source.read_bytes())
            self.assertEqual(migrate_resource_paths(self.engine)["status"], "UNCHANGED")

    def test_contract_download_writes_private_logical_path_and_checks_parent(self):
        from tools.test_web_sessions import request, login_token
        token = login_token(self.factory)
        root = self.root / "data"
        with patch.object(paths, "get_data_root", return_value=root), patch.object(
            platform_store, "_build_contract_pdf_bytes", return_value=b"%PDF-1.4 fixture"
        ):
            with self.factory() as db:
                self.contract(db)
                response = platform_store.download_contract_pdf(1, request(token), db)
                self.assertEqual(response.media_type, "application/pdf")
                reference = db.get(Contract, 1).c_contract_pdf_path
                self.assertTrue(reference.startswith("/merchant/CLT11001/store/STR00002/data/common/contract-1-"))
                self.assertEqual(paths.resource_storage_path(reference).read_bytes(), b"%PDF-1.4 fixture")
                self.assertTrue(reference.endswith(".pdf"))
                db.get(Contract, 1).i_account_id = 999
                db.commit()
                with self.assertRaises(HTTPException) as error:
                    platform_store.download_contract_pdf(1, request(token), db)
                self.assertEqual(error.exception.status_code, 409)

    def test_resource_migration_db_failure_keeps_sources_and_records_retry_mapping(self):
        from sqlalchemy import event
        root = self.root / "data"
        source = root / "company" / "STR00002" / "main.db"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"fixture database")
        def fail(conn, cursor, statement, params, context, many):
            if statement.lstrip().startswith("UPDATE tb_store"):
                raise RuntimeError("injected data reference failure")
        with patch.object(paths, "get_data_root", return_value=root), patch(
            "backend.database.resource_path_migration.get_data_root", return_value=root
        ), patch("backend.database.resource_path_migration.PROJECT_ROOT", self.root):
            event.listen(self.engine, "before_cursor_execute", fail)
            try:
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    migrate_resource_paths(self.engine)
            finally:
                event.remove(self.engine, "before_cursor_execute", fail)
            with self.factory() as db:
                self.assertIsNone(db.get(Store, 2).c_db_path)
            manifest = next((root / "platform-admin/data/audit/path-migrations").glob("*.json"))
            self.assertEqual(json.loads(manifest.read_text())["status"], "FAILED_DB_ROLLBACK_FILES_RETAINED")
            self.assertTrue(source.is_file())
            self.assertEqual(migrate_resource_paths(self.engine)["status"], "COMMITTED")

    def test_reassigning_store_with_resources_is_explicitly_blocked(self):
        self.add_second_merchant()
        from tools.test_web_sessions import request, login_token
        token = login_token(self.factory)
        with self.factory() as db:
            db.get(Store, 2).c_logo_url = "/merchant/CLT11001/store/STR00002/images/logo/logo.png"
            db.commit()
            with self.assertRaises(HTTPException) as error:
                platform_store.update_store(2, {"client_id": 2}, request(token), db)
            self.assertEqual(error.exception.status_code, 409)
            db.rollback()
            self.assertEqual(db.get(Store, 2).i_account_id, 1)

    def test_platform_sqlite_archive_migration_preserves_new_canonical_transactions(self):
        root = self.root / "data"
        source = root / "platform" / "admin.db"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"legacy archive")
        with patch.object(paths, "get_data_root", return_value=root), patch(
            "backend.database.resource_path_migration.get_data_root", return_value=root
        ), patch("backend.database.resource_path_migration.PROJECT_ROOT", self.root):
            plan = migrate_resource_paths(self.engine)
            self.assertEqual(len(plan["assets"]), 1)
            self.assertEqual(plan["assets"][0]["logical_path"], "/platform-admin/data/master/admin.db")
            target = paths.resource_storage_path("/platform-admin/data/master/admin.db")
            self.assertEqual(target.read_bytes(), source.read_bytes())
            target.write_bytes(b"new canonical transactions")
            self.assertEqual(migrate_resource_paths(self.engine)["status"], "UNCHANGED")
            self.assertEqual(target.read_bytes(), b"new canonical transactions")
            self.assertEqual(source.read_bytes(), b"legacy archive")

    def test_branding_never_selects_another_merchants_store_namespace(self):
        from backend.services.branding import resolve_branding
        self.add_second_merchant()
        wrong = images.ImagePath("STORE", "STR00002", "logo", "logo.png", merchant_code="CLT11002")
        platform = images.ImagePath("PLATFORM", None, "logo", "logo.png")
        images.write_image(wrong, self.png)
        images.write_image(platform, self.png)
        with self.factory() as db:
            db.get(Store, 2).c_logo_url = wrong.logical_path
            db.commit()
            with self.assertLogs("backend.services.branding", level="WARNING"):
                result = resolve_branding(db, "STORE", store_id=2)
            self.assertTrue(result.logo_url.startswith("/images/platform/logo/"))

    def test_bootstrap_storage_root_does_not_depend_on_new_settings_columns(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
        from backend.services import system_settings
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE tb_system_settings (i_settings_id INTEGER,c_data_root_path TEXT)"))
                conn.execute(text("INSERT INTO tb_system_settings VALUES (1,:path)"), {"path": str(self.root / "configured")})
            factory = sessionmaker(bind=engine)
            with patch.object(system_settings, "PlatformSessionLocal", factory), patch.dict(
                system_settings.os.environ, {"AWSUM_STORAGE_ROOT": "", "AWSUM_DATA_ROOT": ""}
            ):
                root, source = system_settings.get_storage_root()
                self.assertEqual(root, (self.root / "configured").resolve())
                self.assertEqual(source, "system_settings")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
