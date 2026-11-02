import json
from io import StringIO
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.database.pg_platform import PlatformBase
from backend.services import builtin_assets, images, system_settings, storage_layout
from backend.services.paths import AwsumPathResolver, resource_storage_path
from tools import migrate_final_storage as migration
from tools.test_web_sessions import seed


class FinalStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awsum-final-storage-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "Awsum_Platform"
        self.storage = self.root / "Awsum_Data"
        self.project.mkdir()
        self.engine = create_engine("sqlite://")
        PlatformBase.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        seed(sessionmaker(bind=self.engine))
        self.file("static/images/platform/logo_full.png", b"built-in logo")
        self.file("data/media/platform-admin/images/logo/logo_full.png", b"built-in logo")
        self.file("data/media/merchant/CLT11001/images/logo/logo.png", b"merchant custom")
        self.file("data/media/merchant/CLT11001/store/STR00002/images/logo/logo.png", b"store custom")
        self.file("data/media/merchant/CLT99999/images/logo/logo.png", b"unknown owner")
        self.file("data/media/store/STR00002/images/logo/logo.png", b"old standalone copy")
        self.file("data/image-migrations/old.json", b'{"status":"COMMITTED"}')
        sqlite = self.project / "data" / "platform-admin" / "data" / "master" / "admin.db"
        sqlite.parent.mkdir(parents=True)
        with closing(sqlite3.connect(sqlite)) as db:
            db.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO preserved VALUES (7,'preserved')")
            db.commit()

    def file(self, relative, data):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def plan(self):
        return migration.build_plan(self.engine, self.storage, project=self.project)

    def test_inventory_uses_db_codes_separates_builtin_unknown_and_actual_store_owner(self):
        plan = self.plan()
        mapped = {item["relative"]: item for item in plan["files"]}
        self.assertEqual(mapped["static/images/platform/logo_full.png"]["kind"], "builtin")
        self.assertEqual(mapped["data/media/platform-admin/images/logo/logo_full.png"]["kind"], "retained_archive")
        store = mapped["data/media/merchant/CLT11001/store/STR00002/images/logo/logo.png"]
        self.assertEqual(store["owner"]["id"], 2)
        self.assertEqual(store["owner"]["merchant_id"], 1)
        self.assertTrue(store["destination"].endswith(str(Path("merchant/CLT11001/store/STR00002/images/logo/logo.png"))))
        self.assertEqual(mapped["data/media/merchant/CLT99999/images/logo/logo.png"]["kind"], "retained_archive")
        self.assertEqual(mapped["data/media/store/STR00002/images/logo/logo.png"]["kind"], "retained_archive")
        self.assertFalse(self.storage.exists())

    def test_backup_copy_verify_cleanup_preserves_db_and_postgres_cluster(self):
        postgres = self.storage / "PostgreSQL" / "Data" / "sentinel"
        postgres.parent.mkdir(parents=True)
        postgres.write_bytes(b"never touch cluster files")
        plan = self.plan()
        manifest = migration.apply_plan(plan, self.engine)
        self.assertTrue(manifest.is_file())
        self.assertTrue((self.project / "data").exists())
        self.assertEqual(migration.verify_plan(plan, self.engine)["database_unchanged"], True)
        target = self.storage / "platform-admin" / "data" / "master" / "admin.db"
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as db:
            self.assertEqual(db.execute("SELECT * FROM preserved").fetchone(), (7, "preserved"))
        with self.assertRaises(ValueError):
            migration.cleanup_plan(plan, self.engine, app_verified=False)
        migration.cleanup_plan(plan, self.engine, app_verified=True)
        migration.cleanup_plan(plan, self.engine, app_verified=True)
        self.assertFalse((self.project / "data").exists())
        self.assertEqual(postgres.read_bytes(), b"never touch cluster files")
        self.assertEqual(json.loads(manifest.read_text())["status"], "COMPLETED")
        self.assertTrue((self.storage / "merchant" / "CLT11001" / "store").is_dir())
        self.assertFalse((self.storage / "store").exists())
        self.assertTrue(all(Path(item["backup"]).is_file() for item in plan["files"]))

    def test_destination_collision_prevents_backup_or_copy_and_preserves_bytes(self):
        target = self.storage / "merchant" / "CLT11001" / "images" / "logo" / "logo.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"existing destination")
        plan = self.plan()
        self.assertTrue(plan["conflicts"])
        with self.assertRaises(RuntimeError):
            migration.apply_plan(plan, self.engine)
        self.assertEqual(target.read_bytes(), b"existing destination")
        self.assertFalse((self.storage / "platform-admin").exists())

    def test_cleanup_refuses_source_or_destination_changes_new_files_and_db_changes(self):
        plan = self.plan()
        migration.apply_plan(plan, self.engine)
        original = self.project / "data/media/merchant/CLT11001/images/logo/logo.png"
        original.write_bytes(b"new source")
        with self.assertRaises(RuntimeError):
            migration.cleanup_plan(plan, self.engine, app_verified=True)
        original.write_bytes(b"merchant custom")
        new = self.file("data/new-runtime.bin", b"new data")
        with self.assertRaises(RuntimeError):
            migration.cleanup_plan(plan, self.engine, app_verified=True)
        new.unlink()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE tb_merchant SET c_merchant_name='changed' WHERE i_merchant_id=1"))
        with self.assertRaisesRegex(RuntimeError, "DB changed"):
            migration.cleanup_plan(plan, self.engine, app_verified=True)
        self.assertTrue(original.exists())

    def test_invalid_sqlite_active_journal_and_overlapping_root_are_rejected(self):
        sqlite = self.project / "data/platform-admin/data/master/admin.db"
        journal = Path(str(sqlite) + "-wal")
        journal.write_bytes(b"live journal")
        with self.assertRaisesRegex(RuntimeError, "Quiesce"):
            self.plan()
        journal.unlink()
        with self.assertRaises(ValueError):
            migration.build_plan(self.engine, self.project / "data", project=self.project)

    def test_one_configured_root_direct_images_and_rejected_legacy_environment(self):
        with patch.dict(os.environ, {"AWSUM_STORAGE_ROOT": str(self.storage), "AWSUM_DATA_ROOT": ""}):
            self.assertEqual(system_settings.get_storage_root()[0], self.storage.resolve())
            self.assertEqual(images.image_storage_root(), self.storage.resolve())
            self.assertEqual(images.physical_image_path(images.ImagePath("PLATFORM", None, "master", "MD00001.png")),
                             self.storage / "platform-admin/images/master/MD00001.png")
        with patch.dict(os.environ, {"AWSUM_DATA_ROOT": str(self.storage)}), self.assertRaisesRegex(RuntimeError, "retired"):
            system_settings.get_storage_root()
        with patch.dict(os.environ, {"AWSUM_STORAGE_ROOT": str(system_settings.PROJECT_ROOT / "data"), "AWSUM_DATA_ROOT": ""}), \
                self.assertRaisesRegex(ValueError, "outside"):
            system_settings.get_storage_root()

    def test_bundled_asset_paths_and_configured_custom_priority_without_tenant_copies(self):
        builtins = self.project / "static/images/platform"
        with patch.object(builtin_assets, "BUILTIN_ROOT", builtins), patch.object(images, "image_storage_root", return_value=self.storage):
            migration.apply_plan(self.plan(), self.engine)
            fallback = images.platform_logo_url()
            self.assertTrue(fallback.startswith("/images/platform/logo/"))
            custom = self.storage / "platform-admin/images/logo/custom.png"
            custom.parent.mkdir(parents=True, exist_ok=True)
            custom.write_bytes(b"custom logo")
            self.assertTrue(images.platform_logo_url("/platform-admin/images/logo/custom.png").startswith("/platform-admin/"))
            self.assertEqual(images.resolve_image_file(fallback).read_bytes(), b"built-in logo")
            for url in ("/images/platform/logo/../secret", "/images/platform/logo/%2e.png", "/images/merchant/logo.png"):
                with self.assertRaises(ValueError):
                    builtin_assets.builtin_asset_file(url)
            self.assertFalse((builtins.parent / "merchant").exists())

    def test_cutover_marker_and_missing_reference_validation(self):
        with patch.dict(os.environ, {"AWSUM_STORAGE_ROOT": str(self.storage), "AWSUM_DATA_ROOT": ""}):
            migration.apply_plan(self.plan(), self.engine)
            self.assertTrue(storage_layout.final_layout_enabled())
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE tb_system_settings SET c_logo_url=:reference WHERE i_settings_id=1"),
                             {"reference": "/images/platform/logo/logo_full.png"})
            self.assertEqual(storage_layout.validate_runtime_references(self.engine), 1)
            reference = "/merchant/CLT11001/images/logo/logo.png"
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE tb_merchant SET c_logo_url=:reference WHERE i_merchant_id=1"), {"reference": reference})
            self.assertEqual(storage_layout.validate_runtime_references(self.engine), 2)
            resource_storage_path(reference).unlink()
            with self.assertRaisesRegex(RuntimeError, "missing"):
                storage_layout.validate_runtime_references(self.engine)

    def test_bundled_login_assets_remain_public_with_expired_or_invalid_session_cookie(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app, follow_redirects=False)
        self.addCleanup(client.close)
        client.cookies.set("access_token", "expired.invalid.cookie")
        for asset in ("/images/platform/logo/logo_full.png", "/images/platform/background/hero_platform.jpg",
                      "/images/platform/defaults/default-user.svg", "/images/platform/defaults/no-image.svg"):
            result = client.get(asset)
            self.assertEqual(result.status_code, 200, result.text[:100])
        self.assertEqual(client.get("/images/platform/logo/missing.png").status_code, 404)

    def test_database_snapshot_and_code_migration_maps_use_private_runtime_audit(self):
        from tools import backup_platform_db_snapshot as snapshot, migrate_business_codes as codes
        output = StringIO()
        with patch.dict(os.environ, {"AWSUM_STORAGE_ROOT": str(self.storage), "AWSUM_DATA_ROOT": ""}), \
                patch.object(snapshot.psycopg, "connect") as connect, patch("sys.stdout", output):
            connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
            self.assertEqual(snapshot.main(), 0)
        backup = Path(json.loads(output.getvalue())["backup_dir"])
        self.assertTrue(backup.is_relative_to(self.storage / "platform-admin/data/audit/database-snapshots"))
        self.assertTrue((backup / "metadata.json").is_file())
        self.assertTrue(codes._mapping_file(self.storage, None).is_relative_to(
            self.storage / "platform-admin/data/audit/code-migrations"))

    def test_standalone_commands_read_the_same_local_storage_configuration(self):
        env_file = self.root / ".env"
        env_file.write_text(f"AWSUM_STORAGE_ROOT={self.storage}\n", encoding="utf-8")
        command = (
            "from pathlib import Path; from backend.core import local_env; "
            f"local_env.LOCAL_ENV_PATH=Path({str(env_file)!r}); "
            "from backend.services.system_settings import get_storage_root; "
            f"assert get_storage_root()==(Path({str(self.storage)!r}), 'environment')"
        )
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"AWSUM_STORAGE_ROOT", "AWSUM_DATA_ROOT"}}
        result = subprocess.run([sys.executable, "-c", command], env=environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
