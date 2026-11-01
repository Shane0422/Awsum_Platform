"""Copy-only migration of legacy application data; never touches PostgreSQL data files."""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text

from backend.core.data_paths import PROJECT_ROOT, get_data_root
from backend.services.images import image_digest
from backend.services.paths import AwsumPathResolver, resource_storage_path
from backend.services.resource_storage import write_resource


logger = logging.getLogger(__name__)


def migrate_resource_paths(engine, *, dry_run=False) -> dict:
    root = get_data_root()
    manifest_dir = resource_storage_path(AwsumPathResolver("PLATFORM").get_data_path("audit")) / "path-migrations"
    assets = {}
    references = []
    skipped = []
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(781962146)"))
        stores = conn.execute(text("""
            SELECT s.i_store_id,s.c_store_code,c.c_client_code,s.c_db_path
            FROM tb_store s JOIN tb_client c ON c.i_client_id=s.i_account_id
        """)).all() if inspect(conn).has_table("tb_store") else []
        owners = {}

        def add(source: Path, logical: str):
            if not source.is_file():
                raise RuntimeError(f"Referenced data file is missing: {source}")
            allowed = (root / "company", PROJECT_ROOT / "data" / "platform",
                       PROJECT_ROOT / "data" / "contracts", PROJECT_ROOT / "db_backups" / "company")
            if not any(source.resolve().is_relative_to(base.resolve()) for base in allowed):
                raise RuntimeError(f"Legacy source escapes its recognized storage root: {source}")
            if source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                # Automatic cutover of live SQLite is unsafe. Require quiesced/checkpointed writers.
                for suffix in ("-wal", "-journal"):
                    journal = Path(str(source) + suffix)
                    if journal.exists() and journal.stat().st_size:
                        raise RuntimeError(f"Quiesce/checkpoint SQLite before migrating {source}; active journal found.")
            digest = image_digest(source)
            target = resource_storage_path(logical)
            if logical in assets and assets[logical]["sha256"] != digest:
                raise RuntimeError(f"Conflicting data sources: {logical}")
            if target.exists() and (not target.is_file() or image_digest(target) != digest):
                raise RuntimeError(f"Data destination conflict: {logical}; nothing overwritten.")
            assets[logical] = {"source": str(source), "logical_path": logical,
                               "physical_path": str(target), "sha256": digest}

        platform_legacy = PROJECT_ROOT / "data" / "platform" / "admin.db"
        if platform_legacy.is_file():
            logical = AwsumPathResolver("PLATFORM").get_data_path("master", "admin.db")
            committed = False
            for record in manifest_dir.glob("*.json"):
                previous = json.loads(record.read_text(encoding="utf-8"))
                if previous.get("status") == "COMMITTED" and any(
                    asset["source"] == str(platform_legacy) and asset["logical_path"] == logical
                    for asset in previous["assets"]
                ):
                    committed = True
                    break
            if committed:
                if not resource_storage_path(logical).is_file():
                    raise RuntimeError("Previously migrated Platform SQLite is missing; restore it rather than an old archive.")
            else:
                add(platform_legacy, logical)

        for identifier, code, merchant, reference in stores:
            try:
                owner = AwsumPathResolver("STORE", merchant_code=merchant, store_code=code)
            except ValueError:
                # Technical HQ Store is not a tenant data owner.
                if reference:
                    raise RuntimeError(f"Store {identifier} has data but no valid tenant owner.")
                continue
            owners[identifier] = owner
            if reference and reference.startswith("/"):
                try:
                    parsed, tail = AwsumPathResolver.parse_resource(reference)
                except ValueError:
                    parsed = None
                if parsed:
                    if parsed != owner or tail[0] != "data" or not resource_storage_path(reference).is_file():
                        raise RuntimeError(f"Invalid Store data reference: {identifier}")
                    # The canonical SQLite file may now contain newer transactions.
                    # Never compare/copy its frozen legacy source after cutover.
                    continue
            legacy = root / "company" / code
            if legacy.is_dir():
                for source in sorted(legacy.rglob("*")):
                    if not source.is_file():
                        continue
                    if source.parent != legacy:
                        skipped.append(str(source))
                        continue
                    category = "master" if source.name == "main.db" else "common"
                    logical = owner.get_data_path(category, source.name)
                    add(source, logical)
                    if source.name == "main.db" and not reference:
                        references.append({"table": "tb_store", "key": "i_store_id", "id": identifier,
                                           "column": "c_db_path", "before": reference, "after": logical})
            backup = PROJECT_ROOT / "db_backups" / "company" / code
            if backup.is_dir():
                for source in sorted(backup.rglob("*")):
                    if source.is_file():
                        # Flattened names still fail preflight if different sources collide.
                        logical = owner.get_data_path("common", "backup-" + source.name)
                        add(source, logical)
            if reference:
                try:
                    parsed, tail = AwsumPathResolver.parse_resource(reference)
                except ValueError:
                    parsed = None
                if parsed:
                    if parsed != owner or tail[0] != "data" or not resource_storage_path(reference).is_file():
                        raise RuntimeError(f"Invalid Store data reference: {identifier}")
                    continue
                # Operational PostgreSQL DB names/URLs are identifiers, not filesystem paths.
                if "://" in reference or ("\\" not in reference and "/" not in reference):
                    continue
                source = Path(reference)
                source = source if source.is_absolute() else PROJECT_ROOT / source
                if not source.resolve().is_relative_to(legacy.resolve()):
                    raise RuntimeError(f"Unrecognized Store data file path: Store {identifier}; manual review required.")
                logical = owner.get_data_path("master", source.name)
                add(source, logical)
                references.append({"table": "tb_store", "key": "i_store_id", "id": identifier,
                                   "column": "c_db_path", "before": reference, "after": logical})

        if inspect(conn).has_table("tb_contract"):
            for identifier, store_id, merchant_id, reference in conn.execute(text("""
                SELECT i_contract_id,i_store_id,i_account_id,c_contract_pdf_path
                FROM tb_contract WHERE c_contract_pdf_path IS NOT NULL AND c_contract_pdf_path<>''
            """)).all():
                owner = owners.get(store_id)
                actual = conn.execute(text("SELECT i_account_id FROM tb_store WHERE i_store_id=:id"),
                                      {"id": store_id}).scalar()
                if owner is None or actual != merchant_id:
                    raise RuntimeError(f"Contract {identifier} has mismatched Merchant/Store ownership.")
                try:
                    existing_owner, parts = AwsumPathResolver.parse_resource(reference)
                except ValueError:
                    existing_owner = None
                if existing_owner:
                    if existing_owner != owner or parts[0] != "data" or not resource_storage_path(reference).is_file():
                        raise RuntimeError(f"Contract {identifier} has an invalid logical file reference.")
                    continue
                clean = reference.replace("\\", "/")
                if not clean.startswith("data/contracts/") or len(clean.split("/")) != 3:
                    raise RuntimeError(f"Unrecognized contract path: Contract {identifier}.")
                source = PROJECT_ROOT / Path(reference)
                logical = owner.get_data_path("common", source.name)
                add(source, logical)
                references.append({"table": "tb_contract", "key": "i_contract_id", "id": identifier,
                                   "column": "c_contract_pdf_path", "before": reference, "after": logical})

        plan = {"assets": list(assets.values()), "references": references, "unmapped_legacy_assets": skipped}
        if dry_run:
            return plan
        if not references and all(Path(asset["physical_path"]).is_file() for asset in assets.values()):
            if skipped:
                logger.warning("Legacy data paths require manual review: %s", skipped)
            return {**plan, "status": "UNCHANGED", "manifest": None}
        manifest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        manifest = manifest_dir / f"{stamp}-{uuid4().hex}.json"
        plan.update(status="PLANNED", manifest=str(manifest))

        def record():
            temporary = manifest.with_suffix(".tmp")
            temporary.write_text(json.dumps(plan, ensure_ascii=True, indent=2), encoding="utf-8")
            os.replace(temporary, manifest)

        record()
        try:
            for asset in assets.values():
                source, target = Path(asset["source"]), Path(asset["physical_path"])
                if not target.exists():
                    if image_digest(source) != asset["sha256"]:
                        raise RuntimeError("Data source changed while migrating; stop writers and retry.")
                    write_resource(asset["logical_path"], source)
                if image_digest(source) != asset["sha256"] or image_digest(target) != asset["sha256"]:
                    raise RuntimeError("Data source or destination changed while migrating; stop writers and retry.")
            plan["status"] = "COPIED_PENDING_DB_COMMIT"
            record()
            for ref in references:
                result = conn.execute(text(f"""
                    UPDATE {ref['table']} SET {ref['column']}=:after
                    WHERE {ref['key']}=:id AND
                      ({ref['column']}=:before OR ({ref['column']} IS NULL AND :before IS NULL))
                """), ref)
                if result.rowcount != 1:
                    raise RuntimeError("Data reference changed concurrently; no reference was committed.")
        except Exception:
            plan["status"] = "FAILED_DB_ROLLBACK_FILES_RETAINED"
            record()
            raise
    plan["status"] = "COMMITTED"
    record()
    return plan
