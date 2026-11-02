"""Copy-only image migration with preflight conflicts and a persistent recovery map."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text

from backend.services import images
from backend.services.paths import AwsumPathResolver, resource_storage_path


logger = logging.getLogger(__name__)


def migrate_image_storage(engine, *, dry_run=False) -> dict:
    root = images.image_storage_root()
    audit = resource_storage_path(AwsumPathResolver("PLATFORM").get_data_path("audit"), root=root)
    manifest_dir = audit / "image-migrations"
    with engine.begin() as conn:
        # Serialize all app workers during the short copy/reference transaction.
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(781962145)"))
        assets = {}
        references = []
        skipped = []
        completed_legacy_assets = []
        completed_copies = set()
        for manifest in manifest_dir.glob("*.json"):
            record = json.loads(manifest.read_text(encoding="utf-8"))
            if record.get("status") == "COMMITTED":
                completed_copies.update(
                    (asset["source"], asset["logical_path"], asset["physical_path"])
                    for asset in record["assets"]
                )
        store_merchants = dict(conn.execute(text("""
            SELECT s.c_store_code,c.c_merchant_code FROM tb_store s
            JOIN tb_merchant c ON c.i_merchant_id=s.i_merchant_id
        """)).all()) if inspect(conn).has_table("tb_store") and inspect(conn).has_table("tb_merchant") else {}

        def source_file(source_url):
            source = images.legacy_physical_path(source_url)
            if source is not None:
                return source
            if source_url.startswith("/store/"):
                parts = source_url.split("/")
                if len(parts) < 3 or images.legacy_store_image_path(source_url, store_merchants.get(parts[2])) is None:
                    return None
                candidate = root.joinpath(*source_url.lstrip("/").split("/"))
                if candidate.resolve().is_relative_to(root.resolve()):
                    return candidate
            return None

        def add(source_url, target_image, *, historical=False):
            source = source_file(source_url)
            if source is None or not source.is_file():
                raise RuntimeError(f"Image reference has no readable source: {source_url}")
            destination = target_image.logical_path
            physical = images.physical_image_path(target_image)
            # After committed cutover, canonical images can be replaced/deleted.
            # Only a live legacy DB reference should request another migration.
            if historical and (str(source), destination, str(physical)) in completed_copies:
                completed_legacy_assets.append(source_url)
                return
            digest = images.image_digest(source)
            if destination in assets and assets[destination]["sha256"] != digest:
                raise RuntimeError(f"Conflicting source images target {destination}; nothing was overwritten.")
            if destination not in assets:
                assets[destination] = {
                    "logical_path": destination, "source": str(source), "legacy_urls": [],
                    "physical_path": str(physical), "sha256": digest,
                }
            assets[destination]["legacy_urls"].append(source_url)

        static_images = images.STATIC_ROOT / "images"
        if static_images.is_dir():
            for source in sorted(static_images.rglob("*")):
                if not source.is_file() or source.suffix.lower() not in images.EXTENSIONS:
                    continue
                old_url = "/static/" + source.relative_to(images.STATIC_ROOT).as_posix()
                target = images.legacy_image_path(old_url)
                if target and target.context_type == "STORE" and store_merchants.get(target.owner_code) != target.merchant_code:
                    skipped.append(old_url)
                    continue
                if target:
                    add(old_url, target, historical=True)
                else:
                    skipped.append(old_url)
        # Preserve unlinked historical Store assets in place; never guess their parent.
        old_store_root = root / "store"
        if old_store_root.is_dir():
            for source in sorted(old_store_root.rglob("*")):
                if not source.is_file():
                    continue
                old_url = "/" + source.relative_to(root).as_posix()
                parts = old_url.split("/")
                merchant = store_merchants.get(parts[2]) if len(parts) >= 3 else None
                try:
                    target = images.ImagePath("STORE", parts[2], parts[4], parts[-1],
                                              parts[5] if len(parts) == 7 else None, merchant)
                    if len(parts) not in (6, 7) or parts[3] != "images":
                        raise ValueError("Invalid old Store image hierarchy.")
                except (ValueError, IndexError):
                    skipped.append(old_url)
                    continue
                add(old_url, target, historical=True)

        tables = (
            ("tb_system_settings", "i_settings_id", None, "PLATFORM"),
            ("tb_agency", "i_agency_id", "c_agency_code", "AGENCY"),
            ("tb_merchant", "i_merchant_id", "c_merchant_code", "MERCHANT"),
            ("tb_store", "i_store_id", "c_store_code", "STORE"),
        )
        for table, key, code_column, context in tables:
            if not inspect(conn).has_table(table):
                continue
            code_select = code_column or "NULL"
            for identifier, code, reference in conn.execute(
                text(f"SELECT {key},{code_select},c_logo_url FROM {table} WHERE c_logo_url IS NOT NULL")
            ).all():
                if not reference:
                    continue
                clean = reference.split("?", 1)[0]
                try:
                    existing = images.ImagePath.parse(clean)
                except ValueError:
                    existing = None
                if existing:
                    if existing.context_type != context or existing.owner_code != code or existing.image_category != "logo":
                        raise RuntimeError(f"Image owner mismatch in {table} row {identifier}: {reference}")
                    if context == "STORE" and existing.merchant_code != store_merchants.get(code):
                        raise RuntimeError(f"Store image Merchant mismatch in {table} row {identifier}: {reference}")
                    if not images.physical_image_path(existing).is_file():
                        raise RuntimeError(f"Logical image is missing in {table} row {identifier}: {reference}")
                    continue
                legacy = source_file(clean)
                if legacy is None or not legacy.is_file():
                    raise RuntimeError(f"Missing/invalid image in {table} row {identifier}: {reference}")
                legacy_owner = images.legacy_image_path(clean)
                if legacy_owner and (
                    legacy_owner.context_type != context or legacy_owner.owner_code != code
                    or (context == "STORE" and legacy_owner.merchant_code != store_merchants.get(code))
                ):
                    raise RuntimeError(f"Legacy image owner mismatch in {table} row {identifier}: {reference}")
                target = images.logo_image_path(context, code, legacy.name,
                                               merchant_code=store_merchants.get(code) if context == "STORE" else None)
                add(clean, target)
                references.append({"table": table, "key": key, "id": identifier,
                                   "before": reference, "after": target.logical_path})

        for asset in assets.values():
            target = Path(asset["physical_path"])
            if target.exists() and (not target.is_file() or images.image_digest(target) != asset["sha256"]):
                raise RuntimeError(f"Image destination conflict: {asset['logical_path']}; nothing was overwritten.")
        mapped_urls = {url for asset in assets.values() for url in asset["legacy_urls"]}
        skipped = [url for url in skipped if url not in mapped_urls]
        plan = {"assets": list(assets.values()), "references": references, "unmapped_legacy_assets": skipped,
                "completed_legacy_assets": completed_legacy_assets}
        if completed_legacy_assets:
            logger.info("Preserved canonical ownership for %d previously migrated legacy image assets.",
                        len(completed_legacy_assets))
        if dry_run:
            return plan
        if not references and all(Path(asset["physical_path"]).is_file() for asset in assets.values()):
            if skipped:
                logger.warning("Legacy images retained without reassignment: %s", skipped)
            return {**plan, "status": "UNCHANGED", "manifest": None}
        # Map is outside public media storage and written before any copies/DB updates.
        manifest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        manifest = manifest_dir / f"{stamp}-{uuid4().hex}.json"
        plan.update(status="PLANNED", created_at=stamp, manifest=str(manifest))

        def record():
            temporary = manifest.with_suffix(".tmp")
            temporary.write_text(json.dumps(plan, ensure_ascii=True, indent=2), encoding="utf-8")
            os.replace(temporary, manifest)

        record()
        try:
            for asset in assets.values():
                target = Path(asset["physical_path"])
                if not target.exists():
                    data = Path(asset["source"]).read_bytes()
                    if sha256(data).hexdigest() != asset["sha256"]:
                        raise RuntimeError(f"Source changed during migration: {asset['source']}")
                    images.write_image(images.ImagePath.parse(asset["logical_path"]), data)
                if images.image_digest(target) != asset["sha256"]:
                    raise RuntimeError(f"Image copy verification failed: {asset['logical_path']}")
            plan["status"] = "COPIED_PENDING_DB_COMMIT"
            record()
            for reference in references:
                result = conn.execute(text(f"""
                    UPDATE {reference['table']} SET c_logo_url=:after
                    WHERE {reference['key']}=:id AND c_logo_url=:before
                """), reference)
                if result.rowcount != 1:
                    raise RuntimeError("Image reference changed concurrently; rerun after resolving the change.")
        except Exception:
            plan["status"] = "FAILED_DB_ROLLBACK_FILES_RETAINED"
            record()
            raise
    plan["status"] = "COMMITTED"
    record()
    if skipped:
        logger.warning("Image migration left %d unclassified legacy assets unchanged; inspect recovery map.", len(skipped))
    return plan
