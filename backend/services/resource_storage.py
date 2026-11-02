"""Central owner initialization and lazy, create-only storage for AWSUM resources."""
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import tempfile
import shutil

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.services.paths import AwsumPathResolver, owner_storage_path, resource_storage_path, validate_owner


logger = logging.getLogger(__name__)
MAX_DATA_BYTES = 100 * 1024 * 1024


def ensure_resource_directory(logical_path: str, *, root: Path | None = None) -> Path:
    """Validate before and after mkdir to reject paths through escaping links."""
    target = resource_storage_path(logical_path, root=root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.exception("RESOURCE_DIRECTORY_FAILED logical_path=%s", logical_path)
        raise HTTPException(500, "Storage directory could not be prepared. Please try again or contact an administrator.") from exc
    resource_storage_path(logical_path, root=root)
    return target.parent


class StorageInitializationError(RuntimeError):
    pass


class AwsumStorageService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def image_file(logical_path: str) -> Path:
        from backend.services.images import image_storage_root
        _, tail = AwsumPathResolver.parse_resource(logical_path)
        if tail[0] != "images":
            raise ValueError("An image logical path is required.")
        return resource_storage_path(logical_path, root=image_storage_root())

    def resolve_owner(self, context_type: str, entity_id: int | None = None) -> AwsumPathResolver:
        from backend.database.pg_platform import ADMIN_STORE_ID
        from backend.models_admin.agency import Agency
        from backend.models_admin.merchant import Merchant
        from backend.models_admin.store import Store
        if context_type == "PLATFORM":
            if entity_id is not None:
                raise ValueError("Platform storage has no organization ID.")
            return AwsumPathResolver("PLATFORM")
        models = {"AGENCY": Agency, "MERCHANT": Merchant, "STORE": Store}
        if context_type not in models or not isinstance(entity_id, int) or isinstance(entity_id, bool):
            raise ValueError("A valid organization context and database ID are required.")
        if context_type == "STORE" and entity_id == ADMIN_STORE_ID:
            raise ValueError("The technical HQ Store uses Platform storage, not a business Store root.")
        row = self.db.get(models[context_type], entity_id, populate_existing=True)
        if row is None:
            raise HTTPException(404, "Storage owner not found.")
        if context_type == "AGENCY":
            return AwsumPathResolver("AGENCY", agency_code=row.c_agency_code)
        if context_type == "MERCHANT":
            return AwsumPathResolver("MERCHANT", merchant_code=row.c_merchant_code)
        merchant = self.db.get(Merchant, row.i_merchant_id, populate_existing=True) if row.i_merchant_id is not None else None
        if merchant is None:
            raise HTTPException(409, "Store storage requires its saved Merchant relationship.")
        return AwsumPathResolver("STORE", merchant_code=merchant.c_merchant_code, store_code=row.c_store_code)

    def _initialize(self, context_type: str, entity_id: int | None = None) -> dict:
        owner = None
        try:
            from backend.services.images import image_storage_root
            owner = self.resolve_owner(context_type, entity_id)
            for kind in ("images", "data"):
                root = image_storage_root() if kind == "images" else None
                directory = owner_storage_path(owner, kind, root=root)
                directory.mkdir(parents=True, exist_ok=True)
                owner_storage_path(owner, kind, root=root)
            if owner.context_type == "MERCHANT":
                store_root = owner_storage_path(owner, "data").parent / "store"
                store_root.mkdir(parents=True, exist_ok=True)
                if not store_root.resolve().is_relative_to(owner_storage_path(owner, "data").parent.resolve()):
                    raise ValueError("Merchant Store namespace escapes its owner.")
        except (OSError, ValueError, HTTPException, SQLAlchemyError) as exc:
            logger.exception(
                "%s_STORAGE_INITIALIZATION_FAILED entity_id=%s code=%s logical_root=%s timestamp=%s success=false",
                context_type, entity_id, owner.owner_code if owner else None,
                owner.get_context_root() if owner else None, datetime.now(timezone.utc).isoformat(),
            )
            raise StorageInitializationError("Storage initialization failed; the saved business record is unchanged.") from exc
        result = {
            "context_type": context_type, "entity_id": entity_id, "code": owner.owner_code,
            "logical_root": owner.get_context_root(), "image_root": owner.get_image_root(),
            "data_root": owner.get_data_root(), "storage_initialized": True,
        }
        logger.info(
            "%s_STORAGE_INITIALIZED entity_id=%s code=%s logical_root=%s timestamp=%s success=true",
            context_type, entity_id, owner.owner_code, owner.get_context_root(), datetime.now(timezone.utc).isoformat(),
        )
        return result

    def initialize_platform_storage(self) -> dict:
        return self._initialize("PLATFORM")

    def initialize_agency_storage(self, agency_id: int) -> dict:
        return self._initialize("AGENCY", agency_id)

    def initialize_merchant_storage(self, merchant_id: int) -> dict:
        return self._initialize("MERCHANT", merchant_id)

    def initialize_store_storage(self, store_id: int) -> dict:
        return self._initialize("STORE", store_id)

    def initialize_after_registration(self, context_type: str, entity_id: int) -> dict:
        """Only call after commit: an unavailable disk must not roll back registration."""
        try:
            self._initialize(context_type, entity_id)
        except StorageInitializationError:
            return {
                "storage_initialized": False,
                "storage_warning": (
                    "Registration saved, but storage initialization is pending. Do not register this organization again. "
                    "Storage will be retried on its next use or server startup; contact an administrator if it persists."
                ),
            }
        return {"storage_initialized": True}

    def initialize_missing_storage(self, *, dry_run: bool = False) -> dict:
        from backend.database.pg_platform import ADMIN_STORE_ID
        from backend.models_admin.agency import Agency
        from backend.models_admin.merchant import Merchant
        from backend.models_admin.store import Store
        identities = [("PLATFORM", None)]
        for context_type, model, key in (
            ("AGENCY", Agency, Agency.i_agency_id),
            ("MERCHANT", Merchant, Merchant.i_merchant_id),
            ("STORE", Store, Store.i_store_id),
        ):
            query = self.db.query(key)
            if model is Store:
                query = query.filter(Store.i_store_id != ADMIN_STORE_ID)
            identities.extend((context_type, identity) for (identity,) in query.order_by(key))
        results, failures = [], []
        for context_type, entity_id in identities:
            try:
                if dry_run:
                    owner = self.resolve_owner(context_type, entity_id)
                    result = {"context_type": context_type, "entity_id": entity_id, "logical_root": owner.get_context_root(),
                              "image_root": owner.get_image_root(), "data_root": owner.get_data_root()}
                else:
                    result = self._initialize(context_type, entity_id)
                results.append(result)
            except (StorageInitializationError, HTTPException, ValueError) as exc:
                if dry_run:
                    logger.error("STORAGE_PREFLIGHT_FAILED context_type=%s entity_id=%s error=%s", context_type, entity_id, exc)
                failures.append({"context_type": context_type, "entity_id": entity_id,
                                 "error": "Storage initialization pending; check server logs and the saved owner relationship."})
        log = logger.warning if failures else logger.info
        log("STORAGE_INITIALIZATION_SUMMARY dry_run=%s owners=%s failures=%s timestamp=%s",
            dry_run, len(results), len(failures), datetime.now(timezone.utc).isoformat())
        return {"dry_run": dry_run, "initialized": results, "failures": failures}

    def resolve_image_path(self, context_type: str, entity_id: int | None, category: str,
                           filename: str, subcategory: str | None = None) -> str:
        return self.resolve_owner(context_type, entity_id).get_image_path(category, filename, subcategory)

    def resolve_data_path(self, context_type: str, entity_id: int | None, category: str,
                          filename: str | None = None) -> str:
        return self.resolve_owner(context_type, entity_id).get_data_path(category, filename)

    def ensure_image_category(self, context_type: str, entity_id: int | None, category: str,
                              subcategory: str | None = None) -> Path:
        from backend.services.images import image_storage_root
        logical = self.resolve_image_path(context_type, entity_id, category, "storage.png", subcategory)
        return ensure_resource_directory(logical, root=image_storage_root())

    def ensure_data_category(self, context_type: str, entity_id: int | None, category: str) -> Path:
        logical = self.resolve_data_path(context_type, entity_id, category, "storage.json")
        return ensure_resource_directory(logical)


def write_resource(logical_path: str, data: bytes | Path, *, root: Path | None = None) -> Path:
    ensure_resource_directory(logical_path, root=root)
    target = resource_storage_path(logical_path, root=root)
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".resource-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            if isinstance(data, Path):
                with data.open("rb") as source:
                    shutil.copyfileobj(source, stream, length=1024 * 1024)
            else:
                stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    except FileExistsError as exc:
        raise HTTPException(409, "Resource already exists; no file was overwritten.") from exc
    except OSError as exc:
        logger.exception("Could not save resource %s", logical_path)
        raise HTTPException(500, "Resource could not be saved.") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def authorized_data_path(request, db, logical_path: str) -> Path:
    from backend.services.paths import authorize_resource
    owner, parts = AwsumPathResolver.parse_resource(logical_path)
    if parts[0] != "data" or not parts[-1]:
        raise ValueError("A data file path is required.")
    validate_owner(db, owner)
    authorize_resource(request, db, owner)
    return resource_storage_path(logical_path)


def write_data(request, db, logical_path: str, data: bytes) -> Path:
    """Shared entry for future data writers; paths are not an authorization credential."""
    authorized_data_path(request, db, logical_path)
    if len(data) > MAX_DATA_BYTES:
        raise HTTPException(413, "Data file exceeds the 100 MiB limit.")
    return write_resource(logical_path, data)
