import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.models_admin.merchant import Merchant
from backend.models_admin.agency import Agency
from backend.models_admin.agency_type import AgencyType
from backend.models_admin.business_type import BusinessType
from backend.models_admin.store import Store
from backend.routers import platform_store as routes
from backend.services import images, paths, resource_storage
from backend.services.resource_storage import AwsumStorageService, StorageInitializationError
from tools import test_images, test_web_sessions


class StorageInitializationTests(unittest.TestCase):
    setUp_images = test_images.ImageStorageTests.setUp
    sign_in = test_images.ImageStorageTests.sign_in

    def setUp(self):
        self.setUp_images()
        context = patch.object(paths, "get_data_root", return_value=self.root / "data")
        context.start()
        self.addCleanup(context.stop)
        with self.factory() as db:
            db.add(AgencyType(i_agency_type_id=1, c_agency_type_code="TYPE1", c_agency_type_name="Agency type", c_status="active"))
            db.add(BusinessType(i_business_type_id=1, c_business_code="RETAIL", c_name="Retail"))
            db.commit()
        self.db = self.factory()
        self.addCleanup(self.db.close)
        self.service = AwsumStorageService(self.db)
        self.user = SimpleNamespace(login_role=1, i_platform_user_id=1)
        auth = patch.object(routes, "get_current_user", return_value=self.user)
        auth.start()
        self.addCleanup(auth.stop)
        self.request = SimpleNamespace()
        self.agency_payload = {"contact_name": "Contact", "company_name": "Agency", "agency_type_id": 1}
        self.merchant_payload = {"merchant_name": "New Merchant", "primary_agency_id": 1, "business_type": "Retail"}
        self.store_payload = {"store_name": "New Store", "merchant_id": 1, "installed_by_agency_id": 1}

    def roots(self, owner):
        return (
            paths.owner_storage_path(owner, "images", root=self.media),
            paths.owner_storage_path(owner, "data"),
        )

    def assert_basic(self, owner):
        for directory in self.roots(owner):
            self.assertTrue(directory.is_dir(), str(directory))
            self.assertEqual(list(directory.iterdir()), [], str(directory))

    def test_platform_and_existing_owners_basic_roots_are_idempotent_and_lazy(self):
        calls = (
            (self.service.initialize_platform_storage, (), paths.AwsumPathResolver("PLATFORM")),
            (self.service.initialize_agency_storage, (1,), paths.AwsumPathResolver("AGENCY", agency_code="AGT00001")),
            (self.service.initialize_merchant_storage, (1,), paths.AwsumPathResolver("MERCHANT", merchant_code="CLT11001")),
            (self.service.initialize_store_storage, (2,), paths.AwsumPathResolver("STORE", merchant_code="CLT11001", store_code="STR00002")),
        )
        for function, args, owner in calls:
            with self.subTest(context=owner.context_type), self.assertLogs(resource_storage.logger, "INFO") as logs:
                first = function(*args)
                second = function(*args)
                self.assertEqual(first, second)
                self.assertEqual(first["logical_root"], owner.get_context_root())
                self.assertTrue(first["storage_initialized"])
                self.assert_basic(owner)
                self.assertIn(owner.context_type + "_STORAGE_INITIALIZED", logs.output[0])
                self.assertIn("timestamp=", logs.output[0])
        self.assertFalse((self.media / "store").exists())
        self.assertFalse((self.root / "data" / "store").exists())

    def test_store_resolves_current_merchant_from_database_and_not_frontend_codes(self):
        self.db.add(Merchant(i_merchant_id=2, c_merchant_code="MER00001", c_merchant_name="Another Merchant"))
        self.db.get(Store, 2).i_merchant_id = 2
        self.db.commit()
        result = self.service.initialize_store_storage(2)
        self.assertEqual(result["logical_root"], "/merchant/MER00001/store/STR00002")
        self.assertFalse((self.media / "merchant" / "CLT11001").exists())
        with self.assertRaises(TypeError):
            self.service.initialize_store_storage(2, merchant_code="CLT11001")
        with self.assertRaises(ValueError):
            self.service.resolve_owner("STORE", ADMIN_STORE_ID)

    def test_invalid_missing_and_orphan_owners_never_create_guessed_paths(self):
        for context, identity in (("STORE", 999), ("AGENCY", "../AG1"), ("MERCHANT", True), ("SERVER", 1)):
            with self.subTest(context=context), self.assertLogs(resource_storage.logger, "ERROR"), \
                    self.assertRaises(StorageInitializationError):
                self.service._initialize(context, identity)
        self.db.get(Store, 2).i_merchant_id = None
        self.db.commit()
        with self.assertLogs(resource_storage.logger, "ERROR"), self.assertRaises(StorageInitializationError):
            self.service.initialize_store_storage(2)
        self.assertFalse(self.media.exists())
        self.assertFalse((self.root / "data" / "store").exists())

    def test_invalid_owner_codes_categories_and_traversal_are_rejected(self):
        self.db.get(Merchant, 1).c_merchant_code = "../merchant"
        self.db.commit()
        with self.assertLogs(resource_storage.logger, "ERROR"), self.assertRaises(StorageInitializationError):
            self.service.initialize_store_storage(2)
        for category in ("../master", r"..\master", "employees", "/master"):
            with self.assertRaises(ValueError):
                self.service.ensure_image_category("PLATFORM", None, category)
        with self.assertRaises(ValueError):
            self.service.resolve_data_path("PLATFORM", None, "sales", "sale.json")
        with self.assertRaises(ValueError):
            self.service.resolve_image_path("PLATFORM", None, "master", "../MD1.png")
        self.assertFalse(self.media.exists())

    def test_category_helpers_are_lazy_and_repair_missing_directories(self):
        self.service.initialize_store_storage(2)
        self.assert_basic(self.service.resolve_owner("STORE", 2))
        category = self.service.ensure_image_category("STORE", 2, "master")
        self.assertEqual(category, self.media / "merchant" / "CLT11001" / "store" / "STR00002" / "images" / "master")
        category.rmdir()
        self.assertEqual(self.service.ensure_image_category("STORE", 2, "master"), category)
        data = self.service.ensure_data_category("STORE", 2, "sales")
        self.assertTrue(data.is_dir())
        self.assertFalse(data.with_name("transaction").exists())
        self.assertFalse(category.with_name("employee").exists())

    def test_generic_image_and_data_writes_self_heal_before_first_use(self):
        image = images.ImagePath("STORE", "STR00002", "employee", "EMP1.png", merchant_code="CLT11001")
        path = images.write_image(image, self.png)
        self.assertEqual(path.read_bytes(), self.png)
        self.assertFalse(path.parent.with_name("master").exists())
        self.sign_in()
        logical = self.service.resolve_data_path("STORE", 2, "sales", "SALE1.json")
        # Standard authorization remains required even though directories are auto-created.
        with patch.object(resource_storage, "authorized_data_path", return_value=paths.resource_storage_path(logical)) as auth:
            saved = resource_storage.write_data(self.request, self.db, logical, b'{"total":1}')
        auth.assert_called_once()
        self.assertEqual(saved.read_bytes(), b'{"total":1}')
        self.assertFalse(saved.parent.with_name("transaction").exists())
        path.unlink()
        path.parent.rmdir()
        images.write_image(image, self.png)
        self.assertTrue(path.is_file())

    def test_storage_errors_are_explicit_and_retry_preserves_existing_files(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("Access denied")), \
                self.assertLogs(resource_storage.logger, "ERROR"):
            pending = self.service.initialize_after_registration("STORE", 2)
        self.assertFalse(pending["storage_initialized"])
        self.assertIn("Do not register", pending["storage_warning"])
        self.assertIsNotNone(self.db.get(Store, 2))
        self.assertTrue(self.service.initialize_store_storage(2)["storage_initialized"])
        logo = images.ImagePath("STORE", "STR00002", "logo", "logo.png", merchant_code="CLT11001")
        path = images.write_image(logo, self.png)
        before = path.stat()
        self.service.initialize_store_storage(2)
        self.assertEqual(path.read_bytes(), self.png)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_file_directory_conflict_is_never_overwritten_or_deleted(self):
        owner = self.service.resolve_owner("STORE", 2)
        image_root, _ = self.roots(owner)
        image_root.parent.mkdir(parents=True)
        image_root.write_bytes(b"occupied")
        with self.assertLogs(resource_storage.logger, "ERROR"), self.assertRaises(StorageInitializationError):
            self.service.initialize_store_storage(2)
        self.assertEqual(image_root.read_bytes(), b"occupied")

    def test_lazy_directory_failure_is_logged_and_returns_storage_error(self):
        logical = paths.AwsumPathResolver("PLATFORM").get_image_path("master", "MD1.png")
        with patch.object(Path, "mkdir", side_effect=PermissionError("Access denied")), \
                self.assertLogs(resource_storage.logger, "ERROR"), self.assertRaises(HTTPException) as error:
            resource_storage.write_resource(logical, self.png, root=self.media)
        self.assertEqual(error.exception.status_code, 500)
        self.assertIn("directory", error.exception.detail)
        self.assertFalse(self.media.exists())

    def test_existing_repair_dry_run_and_repeat_preserve_business_rows(self):
        before = [(s.i_store_id, s.c_store_code, s.i_merchant_id) for s in self.db.query(Store).order_by(Store.i_store_id)]
        plan = self.service.initialize_missing_storage(dry_run=True)
        self.assertFalse(plan["failures"])
        self.assertEqual(len(plan["initialized"]), 4)
        self.assertFalse(self.media.exists())
        first = self.service.initialize_missing_storage()
        second = self.service.initialize_missing_storage()
        self.assertEqual(first, second)
        self.assertFalse(first["failures"])
        self.assertNotIn(ADMIN_STORE_ID, [r["entity_id"] for r in first["initialized"] if r["context_type"] == "STORE"])
        self.assertEqual(before, [(s.i_store_id, s.c_store_code, s.i_merchant_id) for s in self.db.query(Store).order_by(Store.i_store_id)])

    def test_repair_reports_orphan_without_blocking_other_owners(self):
        self.db.add(Store(i_store_id=4, c_store_code="STR00004", c_store_name="Orphan"))
        self.db.commit()
        with self.assertLogs(resource_storage.logger, "ERROR"):
            result = self.service.initialize_missing_storage()
        self.assertEqual(result["failures"][0]["entity_id"], 4)
        self.assertEqual(len(result["initialized"]), 4)
        self.assertFalse((self.media / "store" / "STR00004").exists())

    def create_with_generators(self, function, payload, *leading):
        with patch.object(routes, "_generate_next_merchant_code", return_value="MER00001"), \
                patch.object(routes, "_generate_merchant_store_code", return_value=("STR20001", 20001)):
            if function is routes.create_store or function is routes.create_merchant_store:
                return json.loads(function(*leading, self.request, payload, self.db).body)
            return json.loads(function(payload, self.request, self.db).body)

    def test_all_registration_entry_points_initialize_only_after_commit(self):
        committed = []
        def after_commit(db):
            committed.append(True)
        event.listen(self.db, "after_commit", after_commit)
        self.addCleanup(event.remove, self.db, "after_commit", after_commit)
        original = AwsumStorageService.initialize_after_registration
        seen = []
        def check_commit(service, context, identity):
            self.assertTrue(committed, "Initialization must follow commit")
            seen.append(context)
            return original(service, context, identity)
        with patch.object(AwsumStorageService, "initialize_after_registration", check_commit):
            agency = self.create_with_generators(routes.create_agent, self.agency_payload)
            merchant = self.create_with_generators(routes.create_merchant, self.merchant_payload)
            store = self.create_with_generators(routes.create_store, self.store_payload)
            with patch.object(routes, "_generate_merchant_store_code", return_value=("STR20002", 20002)):
                linked = json.loads(routes.create_merchant_store(1, self.request, {"store_name": "Linked"}, self.db).body)
        self.assertEqual(seen, ["AGENCY", "MERCHANT", "STORE", "STORE"])
        for context, response, identity_key in (
            ("AGENCY", agency, "id"), ("MERCHANT", merchant, "id"),
            ("STORE", store, "store_id"), ("STORE", linked, "store_id"),
        ):
            self.assertTrue(response["storage_initialized"])
            owner = self.service.resolve_owner(context, response[identity_key])
            self.assert_basic(owner)
        self.assertTrue(agency["agency_code"].startswith("AGT"))
        self.assertFalse(any("TMP_" in str(file) for file in self.root.rglob("*")))

    def test_failed_registration_does_not_initialize_storage(self):
        with patch.object(self.db, "commit", side_effect=IntegrityError("db failed", {}, Exception("failure"))), \
                patch.object(AwsumStorageService, "initialize_after_registration") as initialize:
            with self.assertRaises(HTTPException):
                self.create_with_generators(routes.create_agent, self.agency_payload)
        initialize.assert_not_called()
        self.assertFalse(self.media.exists())
        self.assertEqual(self.db.query(Agency).count(), 1)

    def test_registration_disk_failure_keeps_record_and_returns_warning(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("Disk unavailable")), \
                self.assertLogs(resource_storage.logger, "ERROR"):
            response = self.create_with_generators(routes.create_merchant, self.merchant_payload)
        self.assertFalse(response["storage_initialized"])
        self.assertIn("Registration saved", response["storage_warning"])
        merchant = self.db.get(Merchant, response["id"])
        self.assertEqual(merchant.c_merchant_code, "MER00001")
        self.assertTrue(self.service.initialize_merchant_storage(response["id"])["storage_initialized"])

    def test_storage_failure_is_reported_by_agency_merchant_and_both_store_forms(self):
        cases = (
            (routes.create_agent, self.agency_payload, (), "AGENCY", "id"),
            (routes.create_merchant, self.merchant_payload, (), "MERCHANT", "id"),
            (routes.create_store, self.store_payload, (), "STORE", "store_id"),
            (routes.create_merchant_store, {"store_name": "Linked pending"}, (1,), "STORE", "store_id"),
        )
        for index, (function, payload, leading, context, key) in enumerate(cases):
            with self.subTest(context=context), patch.object(Path, "mkdir", side_effect=PermissionError("Disk unavailable")), \
                    patch.object(routes, "_generate_merchant_store_code", return_value=(f"STR{20001+index:05d}", 20001+index)), \
                    self.assertLogs(resource_storage.logger, "ERROR"):
                if function is routes.create_merchant_store:
                    response = json.loads(function(*leading, self.request, payload, self.db).body)
                else:
                    response = self.create_with_generators(function, payload, *leading)
            self.assertFalse(response["storage_initialized"])
            self.assertIn("storage_warning", response)
            self.assertTrue(self.service._initialize(context, response[key])["storage_initialized"])

    def test_non_admin_registration_never_initializes_storage(self):
        self.user.login_role = 2
        with patch.object(AwsumStorageService, "initialize_after_registration") as initialize:
            for function, payload in ((routes.create_agent, self.agency_payload),
                                      (routes.create_merchant, self.merchant_payload), (routes.create_store, self.store_payload)):
                with self.subTest(function=function.__name__), self.assertRaises(HTTPException) as error:
                    self.create_with_generators(function, payload)
                self.assertEqual(error.exception.status_code, 403)
        initialize.assert_not_called()
        self.assertFalse(self.media.exists())

    def test_configured_root_is_reused_without_new_environment_setting(self):
        configured = self.root / "configured"
        with patch.dict(os.environ, {"AWSUM_STORAGE_ROOT": str(configured), "AWSUM_DATA_ROOT": ""}), \
                patch.object(paths, "get_data_root", images.get_data_root), \
                patch.object(images, "image_storage_root", lambda: images.get_data_root()):
            result = self.service.initialize_store_storage(2)
            self.assertEqual(result["logical_root"], "/merchant/CLT11001/store/STR00002")
            for directory in (
                configured / "merchant" / "CLT11001" / "store" / "STR00002" / "images",
                configured / "merchant" / "CLT11001" / "store" / "STR00002" / "data",
            ):
                self.assertTrue(directory.is_dir())
        self.assertFalse((self.root / "data").exists())

    def test_display_name_changes_and_deactivation_never_move_or_delete_storage(self):
        self.service.initialize_store_storage(2)
        path = images.write_image(images.ImagePath("STORE", "STR00002", "logo", "logo.png", merchant_code="CLT11001"), self.png)
        self.db.get(Store, 2).c_store_name = "Renamed"
        self.db.commit()
        self.service.initialize_store_storage(2)
        routes.delete_store(2, self.request, self.db)
        routes.delete_agent(1, self.request, self.db)
        execute = self.db.execute
        refs = SimpleNamespace(all=lambda: [("tb_store", "i_merchant_id")])
        with patch.object(self.db, "execute", side_effect=lambda statement, *args, **kwargs:
                          refs if "pg_constraint" in str(statement) else execute(statement, *args, **kwargs)):
            routes.delete_merchant(1, self.request, self.db)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), self.png)
        self.assertEqual(self.db.get(Store, 2).c_store_code, "STR00002")
        self.assertEqual(self.db.get(Merchant, 1).c_merchant_code, "CLT11001")

    def test_warning_is_presented_in_both_registration_ui_flows(self):
        source = (Path(__file__).resolve().parents[1] / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        start = source.index("  function notifyStorageWarning(")
        function = source[start:source.index("  async function saveItem(", start)]
        script = "const assert=require('node:assert/strict');let messages=[];const alert=v=>messages.push(v);\n" + function + """
notifyStorageWarning({storage_initialized:true});
assert.equal(messages.length,0);
notifyStorageWarning({storage_initialized:false,storage_warning:'Registration saved; storage pending.'});
assert.deepEqual(messages,['Registration saved; storage pending.']);
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("notifyStorageWarning(savedStore)", source)
        self.assertIn("notifyStorageWarning(json)", source)


@unittest.skipUnless(os.environ.get("AWSUM_TEST_POSTGRES_SESSIONS") == "1", "Opt-in isolated PostgreSQL test.")
class PostgresStorageInitializationTests(unittest.TestCase):
    setup_postgres = test_web_sessions.PostgresSessionTests.setUp
    cleanup_schema = test_web_sessions.PostgresSessionTests.cleanup_schema

    def setUp(self):
        import tempfile
        self.setup_postgres()
        self.temp = tempfile.TemporaryDirectory(prefix="awsum-storage-pg-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for context in (
            patch.object(paths, "get_data_root", return_value=self.root),
            patch.object(images, "image_storage_root", return_value=self.root / "media"),
            patch.object(routes, "get_current_user", return_value=SimpleNamespace(login_role=1, i_platform_user_id=1)),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.db = self.factory()
        self.addCleanup(self.db.close)
        self.db.add(AgencyType(i_agency_type_id=1, c_agency_type_code="TYPE1", c_agency_type_name="Agency type", c_status="active"))
        self.db.add(BusinessType(i_business_type_id=1, c_business_code="RETAIL", c_name="Retail"))
        self.db.commit()
        # Seed fixtures use explicit IDs; align only this isolated schema's Agency sequence.
        from sqlalchemy import text
        self.db.execute(text("SELECT setval(pg_get_serial_sequence('tb_agency','i_agency_id'), (SELECT MAX(i_agency_id) FROM tb_agency))"))
        self.db.execute(text("SELECT setval(pg_get_serial_sequence('tb_merchant','i_merchant_id'), (SELECT MAX(i_merchant_id) FROM tb_merchant))"))
        self.db.commit()

    def test_real_generators_committed_rows_and_storage_failures(self):
        original = AwsumStorageService.initialize_after_registration
        def check_committed(service, context, identity):
            with self.factory() as independent:
                owner = AwsumStorageService(independent).resolve_owner(context, identity)
                self.assertTrue(owner.get_context_root())
            return original(service, context, identity)
        request = SimpleNamespace()
        with patch.object(AwsumStorageService, "initialize_after_registration", check_committed):
            agency = json.loads(routes.create_agent(
                {"contact_name": "PG contact", "company_name": "PG agency", "agency_type_id": 1}, request, self.db).body)
            merchant = json.loads(routes.create_merchant(
                {"merchant_name": "PG merchant", "primary_agency_id": agency["id"], "business_type": "Retail"}, request, self.db).body)
            store = json.loads(routes.create_store(
                request, {"store_name": "PG store", "merchant_id": merchant["id"], "installed_by_agency_id": agency["id"]}, self.db).body)
            legacy = json.loads(routes.create_merchant({"merchant_name": "PG legacy"}, request, self.db).body)
            linked = json.loads(routes.create_merchant_store(legacy["id"], request, {"store_name": "PG linked"}, self.db).body)
        for code, prefix in (
            (agency["agency_code"], "AGT"), (merchant["merchant_code"], "MER"),
            (store["store_code"], "STR"), (legacy["c_merchant_code"], "MER"),
            (linked["store_code"], "STR"),
        ):
            self.assertRegex(code, rf"^{prefix}[0-9]{{2}}[A-HJ-NP-Z2-9]{{4}}$")
        for response in (agency, merchant, store, legacy, linked):
            self.assertTrue(response["storage_initialized"])
        service = AwsumStorageService(self.db)
        self.assertEqual(service.resolve_owner("STORE", store["store_id"]).get_context_root(),
                         f"/merchant/{merchant['merchant_code']}/store/{store['store_code']}")
        self.assertEqual(service.resolve_owner("STORE", linked["store_id"]).get_context_root(),
                         f"/merchant/{legacy['c_merchant_code']}/store/{linked['store_code']}")
        with patch.object(Path, "mkdir", side_effect=OSError("Disk failure")), self.assertLogs(resource_storage.logger, "ERROR"):
            pending = json.loads(routes.create_merchant({"merchant_name": "Pending storage"}, request, self.db).body)
        with self.factory() as independent:
            self.assertIsNotNone(independent.get(Merchant, pending["id"]))
        self.assertFalse(pending["storage_initialized"])
        self.assertTrue(service.initialize_merchant_storage(pending["id"])["storage_initialized"])
        self.assertFalse(service.initialize_missing_storage()["failures"])


if __name__ == "__main__":
    unittest.main()
