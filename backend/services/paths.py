"""AWSUM PLATFORM / TUXEDO POS owner-first logical resource paths."""
from dataclasses import dataclass
from pathlib import Path
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.data_paths import get_data_root
from backend.core.image_config import IMAGE_CONFIG


CONTEXTS = {"PLATFORM": "platform-admin", "AGENCY": "agency", "MERCHANT": "merchant", "STORE": "store"}
IMAGE_CATEGORIES = frozenset({"logo", "master", "users", "employee", "background", "common"})
MASTER_SUBCATEGORIES = frozenset({"item", "category", "modifier", "department", "group", "other"})
DATA_CATEGORIES = {
    "PLATFORM": frozenset({"master", "security", "audit", "common"}),
    "AGENCY": frozenset({"master", "common"}),
    "MERCHANT": frozenset({"master", "common"}),
    "STORE": frozenset({"master", "sales", "transaction", "employee", "inventory", "report", "common"}),
}
CODE = re.compile(r"[A-Z0-9][A-Z0-9_-]{0,31}")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
IMAGE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.(?:png|jpg|jpeg|webp|gif)")
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
DATA_EXTENSIONS = frozenset({".db", ".sqlite", ".sqlite3", ".bak", ".json", ".csv", ".txt", ".pdf", ".zip"})


def validate_code(value: str | None) -> None:
    if not value or not CODE.fullmatch(value) or value in RESERVED:
        raise ValueError("Invalid organization code.")


def validate_filename(value: str) -> None:
    if (not NAME.fullmatch(value) or ".." in value or value.endswith(".")
            or value.split(".", 1)[0].upper() in RESERVED):
        raise ValueError("Invalid resource filename.")


@dataclass(frozen=True)
class AwsumPathResolver:
    context_type: str
    agency_code: str | None = None
    merchant_code: str | None = None
    store_code: str | None = None

    def __post_init__(self) -> None:
        if self.context_type not in CONTEXTS:
            raise ValueError("Invalid resource context.")
        expected = {
            "PLATFORM": (), "AGENCY": ("agency_code",),
            "MERCHANT": ("merchant_code",), "STORE": ("merchant_code", "store_code"),
        }[self.context_type]
        for field in ("agency_code", "merchant_code", "store_code"):
            value = getattr(self, field)
            if field in expected:
                validate_code(value)
            elif value is not None:
                raise ValueError(f"{field} is not part of this owner context.")

    @classmethod
    def for_owner(cls, context_type: str, owner_code: str | None = None, *, merchant_code: str | None = None):
        if context_type == "STORE":
            return cls(context_type, merchant_code=merchant_code, store_code=owner_code)
        if merchant_code is not None:
            raise ValueError("Merchant parent is only accepted for Store context.")
        if context_type == "PLATFORM":
            if owner_code is not None:
                raise ValueError("Platform has no organization code.")
            return cls(context_type)
        if context_type == "AGENCY":
            return cls(context_type, agency_code=owner_code)
        if context_type == "MERCHANT":
            return cls(context_type, merchant_code=owner_code)
        raise ValueError("Invalid resource context.")

    @property
    def owner_code(self) -> str | None:
        return {"PLATFORM": None, "AGENCY": self.agency_code,
                "MERCHANT": self.merchant_code, "STORE": self.store_code}[self.context_type]

    def get_context_root(self) -> str:
        if self.context_type == "PLATFORM":
            return "/platform-admin"
        if self.context_type == "AGENCY":
            return f"/agency/{self.agency_code}"
        root = f"/merchant/{self.merchant_code}"
        return root + f"/store/{self.store_code}" if self.context_type == "STORE" else root

    def get_image_root(self) -> str:
        return self.get_context_root() + "/images/"

    def get_image_path(self, category: str, filename: str, subcategory: str | None = None) -> str:
        if category not in IMAGE_CATEGORIES:
            raise ValueError("Invalid image category.")
        if subcategory is not None and (category != "master" or subcategory not in MASTER_SUBCATEGORIES):
            raise ValueError("Invalid image subcategory.")
        validate_filename(filename)
        if not IMAGE_FILE.fullmatch(filename):
            raise ValueError("Invalid image extension.")
        return self.get_image_root() + category + "/" + (subcategory + "/" if subcategory else "") + filename

    def get_entity_image_path(self, category: str, entity_key: str | int,
                              extension: str = IMAGE_CONFIG["format"]) -> str:
        if category == "users":
            if not isinstance(entity_key, int) or isinstance(entity_key, bool) or entity_key <= 0:
                raise ValueError("A positive permanent User ID is required.")
        elif category in {"master", "employee"}:
            validate_code(entity_key if isinstance(entity_key, str) else None)
        elif category not in {"logo", "background", "common"}:
            raise ValueError("Invalid image category.")
        elif not isinstance(entity_key, str) or not entity_key:
            raise ValueError("A permanent entity key is required.")
        if extension not in {"jpg", "jpeg", "png", "webp"}:
            raise ValueError("Unsupported image extension.")
        return self.get_image_path(category, f"{entity_key}.{extension}")

    def get_master_image_path(self, master_code: str, extension: str = IMAGE_CONFIG["format"]) -> str:
        validate_code(master_code)
        if extension not in {"jpg", "jpeg", "png", "webp"}:
            raise ValueError("Unsupported master image extension.")
        return self.get_entity_image_path("master", master_code, extension)

    def get_user_image_path(self, user_id: int, extension: str = IMAGE_CONFIG["format"]) -> str:
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            raise ValueError("A positive permanent User ID is required.")
        if extension not in {"jpg", "jpeg", "png", "webp"}:
            raise ValueError("Unsupported user image extension.")
        return self.get_entity_image_path("users", user_id, extension)

    def get_data_root(self) -> str:
        return self.get_context_root() + "/data/"

    def get_data_path(self, category: str, filename: str | None = None) -> str:
        if category not in DATA_CATEGORIES[self.context_type]:
            raise ValueError("Invalid data category for this context.")
        root = self.get_data_root() + category + "/"
        if filename is None:
            return root
        validate_filename(filename)
        if Path(filename).suffix.lower() not in DATA_EXTENSIONS:
            raise ValueError("Unsupported data file extension.")
        return root + filename

    @classmethod
    def parse_resource(cls, value: str):
        if not isinstance(value, str) or any(c in value for c in ("\\", "%", "?", "#", ":")):
            raise ValueError("Invalid logical resource path.")
        parts = value.split("/")
        if not parts or parts.pop(0) != "":
            raise ValueError("Resource path must be absolute and logical.")
        if parts and parts[0] == "platform-admin":
            owner, offset = cls("PLATFORM"), 1
        elif len(parts) >= 2 and parts[0] == "agency":
            owner, offset = cls("AGENCY", agency_code=parts[1]), 2
        elif len(parts) >= 4 and parts[0] == "merchant" and parts[2] == "store":
            owner, offset = cls("STORE", merchant_code=parts[1], store_code=parts[3]), 4
        elif len(parts) >= 2 and parts[0] == "merchant":
            owner, offset = cls("MERCHANT", merchant_code=parts[1]), 2
        else:
            raise ValueError("Resource has no valid owner hierarchy; Store requires its Merchant.")
        tail = parts[offset:]
        if not tail or tail[0] not in {"images", "data"}:
            raise ValueError("Invalid resource type.")
        if tail[0] == "images" and len(tail) in (3, 4):
            rebuilt = owner.get_image_path(tail[1], tail[-1], tail[2] if len(tail) == 4 else None)
        elif tail[0] == "data" and len(tail) == 3:
            rebuilt = owner.get_data_path(tail[1], tail[2] or None)
        else:
            raise ValueError("Invalid resource hierarchy.")
        if rebuilt != value:
            raise ValueError("Resource path is not canonical.")
        return owner, tail


def resource_storage_path(logical_path: str, *, root: Path | None = None) -> Path:
    """Local adapter. Database records never contain this physical path."""
    AwsumPathResolver.parse_resource(logical_path)
    return _contained_path(logical_path, root)


def owner_storage_path(owner: AwsumPathResolver, resource_type: str, *, root: Path | None = None) -> Path:
    if resource_type not in {"images", "data"}:
        raise ValueError("Invalid resource type.")
    logical = owner.get_image_root() if resource_type == "images" else owner.get_data_root()
    return _contained_path(logical, root)


def _contained_path(logical_path: str, root: Path | None) -> Path:
    base = (get_data_root() if root is None else root).resolve()
    candidate = base.joinpath(*logical_path.lstrip("/").split("/"))
    if not candidate.resolve().is_relative_to(base):
        raise ValueError("Resource escapes configured storage.")
    return candidate


def validate_owner(db: Session, owner: AwsumPathResolver):
    """Validate actual DB relationships even for public images and Platform operators."""
    from backend.models_admin.agency import Agency
    from backend.models_admin.merchant import Merchant
    from backend.models_admin.store import Store
    if owner.context_type == "PLATFORM":
        return None
    if owner.context_type == "AGENCY":
        row = db.query(Agency).filter(Agency.c_agency_code == owner.agency_code).one_or_none()
    else:
        merchant = db.query(Merchant).filter(Merchant.c_merchant_code == owner.merchant_code).one_or_none()
        if merchant is None:
            raise HTTPException(404, "Resource Merchant not found.")
        if owner.context_type == "MERCHANT":
            return merchant
        row = db.query(Store).filter(Store.c_store_code == owner.store_code,
                                     Store.i_merchant_id == merchant.i_merchant_id).one_or_none()
    if row is None:
        raise HTTPException(404, "Resource owner or Merchant/Store relationship not found.")
    return row


def authorize_resource(request, db: Session, owner: AwsumPathResolver):
    from backend.routers.auth import get_platform_admin_session
    from backend.services.web_sessions import authenticate
    from backend.models_admin.session import SessionTbl
    from backend.models_admin.role import Role
    user = authenticate(request, db)
    organization = validate_owner(db, owner)
    row = db.query(SessionTbl).filter_by(c_session_key=request.state.web_session_key).one()
    if row.c_login_context == "PLATFORM":
        status, _ = get_platform_admin_session(request, db)
        if status == "authorized":
            return user
        raise HTTPException(403, "Platform resource management permission required.")
    if row.c_login_context == owner.context_type and organization is not None:
        identity = {"AGENCY": row.i_agency_id, "MERCHANT": row.i_merchant_id, "STORE": row.i_store_id}
        key = {"AGENCY": "i_agency_id", "MERCHANT": "i_merchant_id", "STORE": "i_store_id"}[owner.context_type]
        role = db.get(Role, user.i_role_id)
        if (getattr(organization, key) == identity[owner.context_type]
                and role and role.c_name.casefold() in {"superadmin", "admin", "customeradmin", "storeadmin"}):
            return user
    raise HTTPException(403, "You cannot manage resources owned by this organization.")
