"""AWSUM_AUTO_MIGRATE and approved migrations: startup never changes the DB when OFF, safe_schema_migrate is
non-destructive, backups are fully verified, tools.migrate_platform separates --check / --init / --apply and
test runs can never change a real PostgreSQL database. Uses SQLite and mocks only (no real DB change)."""
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, text
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import StaticPool

from backend.database import migration_policy as policy
from backend.database import platform_backup as backup
from backend.utils import safe_schema_migrate as ssm

REAL_URL = "postgresql+psycopg://user:secret@db.local:5432/awsum_platform"
TEST_URL = "postgresql+psycopg://user:secret@db.local:5432/awsum_platform_test"


def sqlite():
    return create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


class PolicyTests(unittest.TestCase):
    def test_auto_migrate_defaults_off(self):
        for value, expected in (("", False), ("0", False), ("off", False), ("maybe", False),
                                ("1", True), ("true", True), (" ON ", True), ("yes", True)):
            with self.subTest(value=value), patch.dict(os.environ, {"AWSUM_AUTO_MIGRATE": value}), redirect_stdout(io.StringIO()):
                self.assertIs(policy.auto_migrate_enabled(), expected)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AWSUM_AUTO_MIGRATE", None)
            self.assertFalse(policy.auto_migrate_enabled())

    def test_test_runs_cannot_change_real_postgresql(self):
        self.assertTrue(policy.is_test_process())
        with self.assertRaises(policy.MigrationBlocked):
            policy.assert_schema_change_allowed(REAL_URL)
        policy.assert_schema_change_allowed(TEST_URL)
        policy.assert_schema_change_allowed(sqlite())
        # Every real schema entry point refuses before connecting (the real Platform engine points at awsum_platform).
        from backend.database import db_init_platform, pg_platform
        if not str(pg_platform.platform_engine.url.database).endswith("_test"):
            with self.assertRaises(policy.MigrationBlocked):
                db_init_platform.init_platform_db()
            with self.assertRaises(policy.MigrationBlocked):
                pg_platform.ensure_platform_database_exists()
            with self.assertRaises(policy.MigrationBlocked):
                ssm.safe_schema_migrate(declarative_base(), pg_platform.platform_engine)


class SafeSchemaMigrateTests(unittest.TestCase):
    def setUp(self):
        self.engine = sqlite()
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_demo (id INTEGER PRIMARY KEY, legacy_col TEXT)"))
            conn.execute(text("INSERT INTO tb_demo (id, legacy_col) VALUES (1, 'kept')"))
        self.Base = declarative_base()

        class Demo(self.Base):
            __tablename__ = "tb_demo"
            id = Column(Integer, primary_key=True)
            name = Column(String(20))

        class Fresh(self.Base):
            __tablename__ = "tb_fresh"
            id = Column(Integer, primary_key=True)

    def test_plan_is_read_only(self):
        plan = ssm.plan_schema_changes(self.Base, self.engine)
        self.assertEqual(plan, {"missing_tables": ["tb_fresh"], "missing_columns": {"tb_demo": ["name"]},
                                "extra_columns": {"tb_demo": ["legacy_col"]}})
        self.assertEqual(inspect(self.engine).get_table_names(), ["tb_demo"])

    def test_adds_only_and_keeps_existing_data_and_columns(self):
        with redirect_stdout(io.StringIO()):
            ssm.safe_schema_migrate(self.Base, self.engine)
        columns = {c["name"] for c in inspect(self.engine).get_columns("tb_demo")}
        self.assertEqual(columns, {"id", "legacy_col", "name"})
        self.assertIn("tb_fresh", inspect(self.engine).get_table_names())
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT id, legacy_col, name FROM tb_demo")).one(), (1, "kept", None))

    def test_remaining_mismatch_raises_and_never_recreates(self):
        stuck = {"missing_tables": [], "missing_columns": {"tb_demo": ["name"]}, "extra_columns": {}}
        with patch.object(ssm, "plan_schema_changes", side_effect=[{"missing_tables": [], "missing_columns": {}, "extra_columns": {}}, stuck]), \
                patch.object(ssm, "recreate_table", side_effect=AssertionError("recreate must never run")), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(ssm.SchemaMismatchError) as ctx:
                ssm.safe_schema_migrate(self.Base, self.engine)
        self.assertIn("never dropped or recreated", str(ctx.exception))
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT legacy_col FROM tb_demo")).scalar(), "kept")


class PlatformCheckTests(unittest.TestCase):
    def test_check_platform_schema_reports_without_creating_anything(self):
        from backend.database.db_init_platform import check_platform_schema, describe_schema_check
        engine = sqlite()
        plan = check_platform_schema(engine)
        self.assertTrue(plan["pending"])
        self.assertIn("tb_item_kind", plan["missing_tables"])
        self.assertEqual(plan["destructive"], [])
        self.assertTrue(describe_schema_check(plan)[0].startswith("missing tables:"))
        self.assertEqual(inspect(engine).get_table_names(), [])


class StartupTests(unittest.TestCase):
    def run_startup(self, auto, plan):
        from backend import main
        out = io.StringIO()
        with patch.dict(os.environ, {"AWSUM_AUTO_MIGRATE": auto}), \
                patch.object(main, "ensure_platform_database_exists") as ensure, \
                patch.object(main, "init_platform_db") as init, \
                patch.object(main, "check_platform_schema", return_value=plan) as check, \
                patch.object(main, "PlatformSessionLocal"), \
                patch("backend.services.web_sessions.idle_timeout"), \
                patch("backend.services.storage_layout.validate_runtime_references") as validate, \
                patch("backend.services.storage_layout.final_layout_enabled", return_value=True), \
                redirect_stdout(out):
            main.on_startup()
        return ensure, init, check, validate, out.getvalue()

    def test_off_never_migrates_and_warns_about_pending_changes(self):
        plan = {"pending": True, "missing_tables": ["tb_common_group"], "missing_columns": {"tb_md_detail": ["x"]},
                "extra_columns": {}, "destructive": []}
        ensure, init, check, validate, log = self.run_startup("", plan)
        ensure.assert_not_called()
        init.assert_not_called()
        check.assert_called_once()
        validate.assert_called_once()  # read-only reference check still runs
        self.assertIn("AWSUM_AUTO_MIGRATE=OFF", log)
        self.assertIn("[MIGRATION][WARN]   missing tables: tb_common_group", log)
        self.assertIn("tools.migrate_platform", log)

    def test_on_keeps_the_existing_startup_migration(self):
        ensure, init, check, _validate, log = self.run_startup("1", {})
        ensure.assert_called_once()
        init.assert_called_once_with()  # never allow_destructive at startup
        check.assert_not_called()
        self.assertIn("AWSUM_AUTO_MIGRATE=ON", log)

    def test_on_logs_blocked_or_mismatch_errors_and_still_starts(self):
        from backend import main
        for error in (ssm.SchemaMismatchError("still differs"), policy.MigrationBlocked("blocked")):
            with self.subTest(error=type(error).__name__), patch.object(main, "init_platform_db", side_effect=error):
                out = io.StringIO()
                with patch.dict(os.environ, {"AWSUM_AUTO_MIGRATE": "on"}),                         patch.object(main, "ensure_platform_database_exists"),                         patch.object(main, "PlatformSessionLocal") as session,                         patch("backend.services.web_sessions.idle_timeout"), redirect_stdout(out):
                    main.on_startup()
                session.assert_called_once()  # startup finished
                self.assertIn(f"[MIGRATION][ERROR] {type(error).__name__}", out.getvalue())
                self.assertIn("[MIGRATION][WARN] Startup continues", out.getvalue())


class DestructiveCleanupTests(unittest.TestCase):
    """init_platform_db skips legacy DROP cleanups (warning) unless a separately approved run allows them."""

    def test_destructive_cleanup_is_skipped_with_a_warning(self):
        import inspect as pyinspect
        from backend.database import db_init_platform as dbi
        out = io.StringIO()
        stop = RuntimeError("stop after the destructive check")
        with patch("backend.database.migration_policy.assert_schema_change_allowed"),                 patch.object(dbi, "legacy_destructive_targets", return_value=["table tb_user"]),                 patch.object(dbi, "platform_engine"),                 patch.object(dbi, "load_platform_models", side_effect=stop) as models,                 redirect_stdout(out), self.assertRaises(RuntimeError) as ctx:
            dbi.init_platform_db()
        self.assertIs(ctx.exception, stop)  # not aborted by the destructive check: the migration continues
        models.assert_called_once()
        self.assertIn("[MIGRATION][WARN] Destructive legacy cleanup skipped (table tb_user)", out.getvalue())
        source = pyinspect.getsource(dbi.init_platform_db)
        guarded = source.split("if allow_destructive:", 1)[1].split("_ensure_merchant_code_schema", 1)[0]
        for drop in ("_drop_legacy_tb_user_dependencies", "_drop_legacy_tb_user_table",
                     "_drop_legacy_session_user_column", "_drop_legacy_store_type_dependencies"):
            self.assertIn(drop, guarded)  # DROP steps run only with allow_destructive



class BackupVerifyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="awsum-backup-")
        self.addCleanup(folder.cleanup)
        self.dump = Path(folder.name) / "awsum_platform-before-migration-20261108-120000.dump"
        self.dump.write_bytes(b"PGDMP fake dump")
        self.now = datetime.now(timezone.utc)
        self.write_manifest()
        self.listing = ";\n;     dbname: awsum_platform\n;\n1; 0 0 TABLE DATA public tb_item_kind postgres\n"

    def write_manifest(self, **changes):
        manifest = {"manifest_version": 1, "created_at_utc": self.now.isoformat(), "label": "before-migration",
                    "target": {"host": "db.local", "port": 5432, "database": "awsum_platform"},
                    "dump_file": self.dump.name, "format": "custom", "size_bytes": self.dump.stat().st_size,
                    "sha256": hashlib.sha256(self.dump.read_bytes()).hexdigest()}
        manifest.update(changes)
        backup.manifest_path(self.dump).write_text(json.dumps(manifest), encoding="utf-8")

    def verify(self, returncode=0, listing=None, **kw):
        result = subprocess.CompletedProcess([], returncode, listing if listing is not None else self.listing, "boom")
        with patch.object(backup, "_restore_listing", return_value=result):
            return backup.verify_backup(self.dump, REAL_URL, now=kw.pop("now", self.now), **kw)

    def problems(self, **kw):
        with self.assertRaises(backup.BackupInvalid) as ctx:
            self.verify(**kw)
        return " | ".join(ctx.exception.problems)

    def test_valid_backup_passes(self):
        self.assertEqual(self.verify()["target"]["database"], "awsum_platform")

    def test_each_check_rejects(self):
        self.assertIn("older than", self.problems(now=self.now + timedelta(hours=25)))
        self.assertIn("pg_restore --list failed", self.problems(returncode=1))
        self.assertIn("another database", self.problems(listing=";     dbname: other_db\n TABLE DATA x"))
        self.assertIn("no table data", self.problems(listing=";     dbname: awsum_platform\n"))
        self.write_manifest(target={"host": "db.local", "port": 5432, "database": "other_db"})
        self.assertIn("is not the target", self.problems())
        self.write_manifest(sha256="0" * 64)
        self.assertIn("SHA-256", self.problems())
        self.write_manifest(created_at_utc=(self.now + timedelta(hours=1)).isoformat())
        self.assertIn("in the future", self.problems())
        self.write_manifest(dump_file="other.dump")
        self.assertIn("another file", self.problems())
        backup.manifest_path(self.dump).unlink()
        self.assertIn("manifest missing", self.problems())


class CliTests(unittest.TestCase):
    def setUp(self):
        from tools import migrate_platform as cli
        self.cli = cli
        folder = tempfile.TemporaryDirectory(prefix="awsum-migrate-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        records = patch.object(backup, "backup_directory", return_value=self.folder)
        records.start()
        self.addCleanup(records.stop)
        self.engine = sqlite()
        self.calls = []

    def run_cli(self, argv, url=TEST_URL, exists=True, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.cli.run(argv, engine=self.engine, url=url, database_exists=lambda: exists,
                                create_database=lambda: self.calls.append("create"),
                                migrate=lambda **flags: self.calls.append(("migrate", flags)), **kw)
        return code, out.getvalue()

    def test_check_is_read_only(self):
        code, log = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertIn("missing tables:", log)
        self.assertNotIn("secret", log)  # masked URL
        self.assertEqual((inspect(self.engine).get_table_names(), self.calls), ([], []))
        code, log = self.run_cli(["--check"], exists=False)
        self.assertIn("run --init", log)

    def test_apply_and_init_require_the_target_name(self):
        for argv in (["--apply", "--backup", "auto"], ["--apply", "--backup", "auto", "--confirm-db", "awsum_platform"],
                     ["--init"]):
            with self.subTest(argv=argv), self.assertRaises(self.cli.MigrationAborted):
                self.run_cli(argv)
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
            self.cli.parse_args(["--check", "--allow-destructive"])
        self.assertEqual(self.calls, [])

    def test_test_process_cannot_target_real_database(self):
        with self.assertRaises(policy.MigrationBlocked):
            self.run_cli(["--apply", "--backup", "auto", "--confirm-db", "awsum_platform"], url=REAL_URL)
        self.assertEqual(self.calls, [])

    def test_init_is_only_for_a_new_database(self):
        code, _ = self.run_cli(["--init", "--confirm-db", "awsum_platform_test"], exists=False)
        self.assertEqual((code, self.calls), (0, ["create", ("migrate", {})]))
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_existing (id INTEGER)"))
        with self.assertRaises(self.cli.MigrationAborted):
            self.run_cli(["--init", "--confirm-db", "awsum_platform_test"])

    def test_apply_needs_existing_db_and_verified_backup(self):
        apply = ["--apply", "--confirm-db", "awsum_platform_test"]
        with self.assertRaises(self.cli.MigrationAborted):
            self.run_cli(apply + ["--backup", "x.dump"])  # empty database: use --init
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_existing (id INTEGER)"))
        with self.assertRaises(self.cli.MigrationAborted):
            self.run_cli(apply)  # no backup
        with patch.object(backup, "verify_backup", side_effect=backup.BackupInvalid(["SHA-256 differs"])):
            with self.assertRaises(backup.BackupInvalid):
                self.run_cli(apply + ["--backup", "x.dump"])
        self.assertEqual(self.calls, [])  # nothing migrated without a verified backup
        manifest = {"size_bytes": 10, "created_at_utc": "2026-11-08T00:00:00+00:00"}
        with patch.object(backup, "verify_backup", return_value=manifest):
            code, log = self.run_cli(apply + ["--backup", "x.dump"])
        self.assertEqual((code, self.calls), (0, [("migrate", {"allow_destructive": False})]))
        self.assertIn("[BACKUP] verified x.dump", log)
        records = list(self.folder.glob("*-migration-apply-*.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text(encoding="utf-8"))["backup"], "x.dump")

    def test_apply_refuses_pending_destructive_cleanup_without_approval(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE tb_existing (id INTEGER)"))
        plan = {"pending": True, "missing_tables": [], "missing_columns": {}, "extra_columns": {},
                "destructive": ["table tb_user"]}
        with patch.object(self.cli, "check_platform_schema", return_value=plan):
            with self.assertRaises(self.cli.MigrationAborted):
                self.run_cli(["--apply", "--confirm-db", "awsum_platform_test", "--backup", "x.dump"])
        self.assertEqual(self.calls, [])

    def test_lock_refuses_a_second_run(self):
        class Conn:
            dialect = type("D", (), {"name": "postgresql"})()
            def execute(self, *a, **k):
                return type("R", (), {"scalar": lambda self: False})()
            def commit(self): pass
            def close(self): self.closed = True
        engine = type("E", (), {"connect": lambda self: Conn()})()
        with self.assertRaises(policy.MigrationBlocked):
            with policy.migration_lock(engine):
                pass


if __name__ == "__main__":
    unittest.main()
