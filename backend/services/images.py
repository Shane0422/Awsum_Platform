"""Context-first logical image paths, independent of physical storage and presentation."""
from dataclasses import dataclass
from email.message import Message
from functools import lru_cache
import json
from hashlib import sha256
from io import BytesIO
import logging
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from backend.core.data_paths import PROJECT_ROOT, get_data_root
from backend.core.image_config import IMAGE_CONFIG
from backend.services.paths import (
    AwsumPathResolver, CONTEXTS, IMAGE_CATEGORIES, MASTER_SUBCATEGORIES,
    IMAGE_FILE as FILE, RESERVED, resource_storage_path, validate_owner,
    authorize_resource,
)
from backend.services.resource_storage import write_resource
from backend.services.builtin_assets import builtin_asset_file, builtin_asset_url


logger = logging.getLogger(__name__)
STATIC_ROOT = PROJECT_ROOT / "static"
CATEGORIES = IMAGE_CATEGORIES
PUBLIC_CATEGORIES = frozenset({"logo", "background", "common"})
EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
MAX_BYTES = IMAGE_CONFIG["maxInputBytes"]
MAX_PIXELS = IMAGE_CONFIG["maxInputPixels"]


def image_mutation_form(payload: str, remove_image: bool, image: UploadFile | None) -> tuple[dict, bytes | None]:
    try:
        values = json.loads(payload)
    except ValueError as exc:
        raise HTTPException(422, "A valid form object is required.") from exc
    if not isinstance(values, dict):
        raise HTTPException(422, "A valid form object is required.")
    if remove_image and image is not None:
        raise HTTPException(422, "Select a replacement image or remove the saved image, not both.")
    return values, read_image_upload(image) if image is not None else None


@dataclass(frozen=True)
class ImagePath:
    context_type: str
    owner_code: str | None
    image_category: str
    file_name: str
    sub_category: str | None = None
    merchant_code: str | None = None

    def __post_init__(self) -> None:
        self.owner.get_image_path(self.image_category, self.file_name, self.sub_category)
        if not FILE.fullmatch(self.file_name) or self.file_name.split(".", 1)[0].upper() in RESERVED:
            raise ValueError("Invalid image file name or type.")

    @property
    def owner(self) -> AwsumPathResolver:
        return AwsumPathResolver.for_owner(self.context_type, self.owner_code, merchant_code=self.merchant_code)

    @property
    def logical_path(self) -> str:
        return self.owner.get_image_path(self.image_category, self.file_name, self.sub_category)

    @classmethod
    def parse(cls, value: str) -> "ImagePath":
        owner, parts = AwsumPathResolver.parse_resource(value)
        if parts[0] != "images":
            raise ValueError("Expected an image resource.")
        return cls(owner.context_type, owner.owner_code, parts[1], parts[-1],
                   parts[2] if len(parts) == 4 else None,
                   owner.merchant_code if owner.context_type == "STORE" else None)


def image_storage_root() -> Path:
    return get_data_root()


def physical_image_path(image: ImagePath) -> Path:
    from backend.services.resource_storage import AwsumStorageService
    return AwsumStorageService.image_file(image.logical_path)


def legacy_image_path(url: str) -> ImagePath | None:
    """Compatibility mapping only; all new writes use ImagePath directly."""
    clean = url.split("?", 1)[0]
    if "\\" in clean or "%" in clean or ".." in clean.split("/") or "#" in clean:
        return None
    parts = clean.removeprefix("/static/images/").split("/")
    if not clean.startswith("/static/images/") or not parts:
        return None
    try:
        if parts[0] in {"platform", "platform_old"} and len(parts) == 2:
            filename = parts[1]
            category = "logo" if filename.startswith(("logo", "icon")) else "background" if filename.startswith("hero") else "common"
            if parts[0] == "platform_old":
                filename = "legacy-" + filename
            return ImagePath("PLATFORM", None, category, filename)
        if parts[:2] == ["brands", "platform"] and len(parts) == 3:
            return ImagePath("PLATFORM", None, "logo", parts[-1])
        if len(parts) in (4, 6) and parts[0] == "brands" and parts[1] in {"clients", "customers"}:
            if len(parts) == 6 and parts[3] == "stores":
                return ImagePath("STORE", parts[4], "logo", parts[5], merchant_code=parts[2])
            if len(parts) == 4:
                return ImagePath("MERCHANT", parts[2], "logo", parts[3])
        if len(parts) == 1:
            category = "background" if "banner" in parts[0] else "common"
            return ImagePath("PLATFORM", None, category, parts[0])
    except ValueError:
        return None
    return None


def legacy_physical_path(url: str) -> Path | None:
    clean = url.split("?", 1)[0]
    if not clean.startswith("/static/images/") or any(c in clean for c in ("\\", "%", "#")) or ".." in clean.split("/"):
        return None
    root = (STATIC_ROOT / "images").resolve()
    candidate = root.joinpath(*clean.removeprefix("/static/images/").split("/"))
    if not candidate.resolve().is_relative_to(root) or candidate.suffix.lower() not in EXTENSIONS:
        return None
    return candidate


def legacy_store_image_path(reference: str, merchant_code: str) -> ImagePath | None:
    parts = reference.split("/")
    if len(parts) not in (6, 7) or parts[:2] != ["", "store"] or parts[3] != "images":
        return None
    try:
        return ImagePath("STORE", parts[2], parts[4], parts[-1],
                         parts[5] if len(parts) == 7 else None, merchant_code)
    except ValueError:
        return None


def resolve_legacy_store_image(reference: str, db: Session) -> ImagePath | None:
    """Read-only compatibility. The old root can never be used for new resources."""
    parts = reference.split("/")
    if len(parts) < 3 or legacy_store_image_path(reference, "LEGACY") is None:
        return None
    from backend.models_admin.merchant import Merchant
    from backend.models_admin.store import Store
    row = db.query(Store, Merchant).join(Merchant, Store.i_merchant_id == Merchant.i_merchant_id).filter(
        Store.c_store_code == parts[2]).one_or_none()
    if row is None:
        return None
    store, merchant = row
    return legacy_store_image_path(reference, merchant.c_merchant_code)


def resolve_image_file(reference: str | None) -> Path | None:
    if not reference:
        return None
    clean = reference.split("?", 1)[0]
    if clean.startswith("/images/platform/"):
        candidate = builtin_asset_file(clean)
        return candidate if candidate.is_file() else None
    try:
        candidate = physical_image_path(ImagePath.parse(clean))
    except ValueError:
        candidate = legacy_physical_path(clean)
    if candidate is not None and candidate.is_file():
        return candidate
    return None


def image_url(reference: str | None) -> str | None:
    if not reference:
        return None
    clean = reference.split("?", 1)[0]
    if clean.startswith("/images/platform/"):
        file = resolve_image_file(clean)
        return f"{clean}?v={file.stat().st_mtime_ns}" if file else None
    mapped = legacy_image_path(clean)
    if mapped and physical_image_path(mapped).is_file():
        clean = mapped.logical_path
    file = resolve_image_file(clean)
    if file is None:
        return None
    return f"{clean}?v={file.stat().st_mtime_ns}"


def logo_url(reference: str | None) -> str | None:
    if not reference:
        return None
    clean = reference.split("?", 1)[0]
    try:
        image = ImagePath.parse(clean)
    except ValueError:
        if not clean.startswith(("/static/images/brands/", "/static/images/platform/", "/images/platform/logo/")):
            return None
    else:
        if image.image_category != "logo":
            return None
    return image_url(reference)


def platform_logo_url(configured_reference: str | None = None) -> str | None:
    if configured_reference:
        try:
            configured = ImagePath.parse(configured_reference.split("?", 1)[0])
        except ValueError:
            configured = legacy_image_path(configured_reference)
        if configured and configured.context_type != "PLATFORM":
            logger.warning("Ignoring non-Platform image configured as Platform logo: %s", configured_reference)
            configured_reference = None
    if configured_reference and (configured := logo_url(configured_reference)):
        return configured
    return builtin_asset_url("logo", "logo_full.png")


@lru_cache(maxsize=32)
def _has_transparency(reference: str) -> bool:
    file = resolve_image_file(reference)
    if file is None:
        return False
    try:
        with Image.open(file) as image:
            alpha = image.convert("RGBA").getchannel("A")
            return alpha.getextrema()[0] < 255
    except (OSError, UnidentifiedImageError):
        return False


def nav_logo_url(reference: str | None) -> str:
    """Logo for dark navigation bars: a transparent logo as-is, else the built-in transparent mark.

    The cache key includes the ?v= mtime token, so a replaced logo is re-checked.
    """
    if reference and _has_transparency(reference):
        return reference
    return builtin_asset_url("logo", "logo_nav.png")


def read_image_upload(upload: UploadFile) -> bytes:
    filename = upload.filename or ""
    disposition = Message()
    disposition["Content-Disposition"] = upload.headers.get("content-disposition", "")
    # Multipart parsers may strip Windows directories from filename; check the header too.
    for original in (filename, disposition.get_filename()):
        if original is None:
            continue
        if (not original or len(original) > 255 or original != original.strip()
                or original.startswith(".") or ".." in original
                or any(char in original for char in '/\\:%?#<>"|*')
                or any(ord(char) < 32 or ord(char) == 127 for char in original)
                or original.split(".", 1)[0].rstrip(" .").upper() in RESERVED):
            raise HTTPException(400, "Use a simple image filename without directories.")
    formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
    expected = formats.get((upload.content_type or "").lower())
    extension_formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
    if not expected or extension_formats.get(Path(filename).suffix.lower()) != expected:
        raise HTTPException(400, "Use a PNG, JPEG, or WebP image with a matching file extension.")
    data = upload.file.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "Image must be non-empty and no larger than 5 MB.")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != expected:
                raise HTTPException(400, "Image content does not match its file type.")
            if image.width * image.height > MAX_PIXELS:
                raise HTTPException(400, "Image dimensions must not exceed 20 megapixels.")
            image.verify()
        return data
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise HTTPException(400, "The selected file is not a valid PNG, JPEG, or WebP image.") from exc


def normalize_image(upload: UploadFile) -> bytes:
    return process_image(read_image_upload(upload))


def write_image(image: ImagePath, data: bytes) -> Path:
    """Create-only, immutable storage. Never overwrite another asset."""
    return write_resource(image.logical_path, data, root=image_storage_root())


def process_image(data: bytes) -> bytes:
    """The same square WEBP processor for every category, including internal callers."""
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "Image must be non-empty and no larger than 5 MB.")
    try:
        with Image.open(BytesIO(data)) as photo:
            if photo.format not in {"JPEG", "PNG", "WEBP"} or photo.width * photo.height > MAX_PIXELS:
                raise HTTPException(400, "Use a supported image no larger than 20 megapixels.")
            oriented = ImageOps.exif_transpose(photo)
            fitted = ImageOps.contain(oriented.convert("RGBA"), (IMAGE_CONFIG["width"], IMAGE_CONFIG["height"]),
                                     method=Image.Resampling.LANCZOS)
            square = Image.new("RGB", (IMAGE_CONFIG["width"], IMAGE_CONFIG["height"]), IMAGE_CONFIG["background"])
            square.paste(fitted, ((square.width - fitted.width) // 2, (square.height - fitted.height) // 2),
                         fitted.getchannel("A"))
            output = BytesIO()
            square.save(output, format=IMAGE_CONFIG["format"].upper(), quality=round(IMAGE_CONFIG["quality"] * 100), method=4)
            return output.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise HTTPException(400, "The selected file is not a valid image.") from exc


def logo_image_path(context_type: str, owner_code: str | None, filename: str | None = None,
                    *, merchant_code: str | None = None) -> ImagePath:
    return ImagePath(context_type, owner_code, "logo", filename or f"logo.{IMAGE_CONFIG['format']}",
                     merchant_code=merchant_code)


def image_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authorize_images(request: Request, db: Session, context_type: str, owner_code: str | None,
                     *, merchant_code: str | None = None):
    """Permission derives from server-side user/session membership, never supplied paths."""
    owner = AwsumPathResolver.for_owner(context_type, owner_code, merchant_code=merchant_code)
    return authorize_resource(request, db, owner)


def authorize_merchant_master_read(request: Request, db: Session, merchant_code: str):
    """Reading a Merchant's Master (MD item) image: everyone authorize_images allows, plus exactly the logins the
    Merchant / Store MD Master API admits for that Merchant (services.app_navigation TENANT_ROLES: its Merchant
    Admin, and its active Stores' admins, who view the Merchant's MD read-only). Membership comes from the server
    session, never from the path. Read only: image writes keep authorize_images, and MD images change only through
    MD Master Save / Delete."""
    try:
        return authorize_images(request, db, "MERCHANT", merchant_code)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        refused = exc
    from backend.models_admin.merchant import Merchant
    from backend.models_admin.session import SessionTbl
    from backend.services.app_navigation import resolve_app_scope
    from backend.services.web_sessions import authenticate
    user = authenticate(request, db)
    session = db.query(SessionTbl).filter_by(c_session_key=request.state.web_session_key).one()
    merchant = db.query(Merchant).filter(Merchant.c_merchant_code == merchant_code).first()
    if session.c_login_context not in ("MERCHANT", "STORE") or merchant is None:
        raise refused
    scope = resolve_app_scope(request, db, user)  # active Merchant (and Store), permitted role, from the session
    if scope.context != session.c_login_context or not scope.permitted or scope.merchant_id != merchant.i_merchant_id:
        raise refused
    return user
