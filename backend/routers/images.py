"""Common image delivery and authenticated owner-scoped image writes."""
import re
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.database.pg_platform import PlatformSessionLocal
from backend.services import images


router = APIRouter()


def get_db():
    with PlatformSessionLocal() as db:
        yield db


def _image(request: Request) -> images.ImagePath:
    try:
        return images.ImagePath.parse(request.url.path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/platform-admin/images/{asset_path:path}")
def platform_image(request: Request, asset_path: str, db: Session = Depends(get_db)):
    return _deliver(request, db, _image(request))


@router.get("/{context_type}/{owner_code}/images/{asset_path:path}")
def organization_image(request: Request, context_type: str, owner_code: str,
                       asset_path: str, db: Session = Depends(get_db)):
    if context_type == "store":
        image = images.resolve_legacy_store_image(request.url.path, db)
        if image is None:
            raise HTTPException(404, "Legacy Store image owner not found.")
        images.validate_owner(db, image.owner)
        if image.image_category not in images.PUBLIC_CATEGORIES:
            images.authorize_images(request, db, "STORE", image.owner_code, merchant_code=image.merchant_code)
        return RedirectResponse(image.logical_path, status_code=307, headers={"Cache-Control": "no-store"})
    return _deliver(request, db, _image(request))


@router.get("/merchant/{merchant_code}/store/{store_code}/images/{asset_path:path}")
def store_image(request: Request, merchant_code: str, store_code: str,
                asset_path: str, db: Session = Depends(get_db)):
    return _deliver(request, db, _image(request))


def _deliver(request: Request, db: Session, image: images.ImagePath):
    images.validate_owner(db, image.owner)
    public = image.image_category in images.PUBLIC_CATEGORIES
    agency_user_photo = image.image_category == "users" and image.context_type == "AGENCY"
    if agency_user_photo:
        from backend.services import web_sessions
        from backend.services.agency_permissions import AgencyPortalPermissions
        from backend.models_admin.agency import Agency
        from backend.models_admin.platform_user import PlatformUser
        user = web_sessions.authenticate(request, db)
        if user.i_agency_id is not None:
            query = AgencyPortalPermissions(user).query(db, "users")
        else:
            images.authorize_images(request, db, image.context_type, image.owner_code)
            query = db.query(PlatformUser)
        if not query.join(Agency, Agency.i_agency_id == PlatformUser.i_agency_id).filter(
            Agency.c_agency_code == image.owner_code, PlatformUser.c_image_path == image.logical_path,
        ).first():
            raise HTTPException(404, "User photo not found.")
    elif image.context_type == "MERCHANT" and image.image_category == "master":
        images.authorize_merchant_master_read(request, db, image.owner_code)  # + its Stores' logins (view only)
    elif not public:
        images.authorize_images(request, db, image.context_type, image.owner_code, merchant_code=image.merchant_code)
    if image.image_category == "users" and not agency_user_photo:
        from backend.database.pg_platform import ADMIN_STORE_ID
        from backend.models_admin.platform_user import PlatformUser
        if image.context_type != "PLATFORM" or not db.query(PlatformUser.i_platform_user_id).filter(
            PlatformUser.i_store_id == ADMIN_STORE_ID, PlatformUser.c_image_path == image.logical_path
        ).first():
            raise HTTPException(404, "User photo not found.")
    file = images.physical_image_path(image)
    if not file.is_file():
        raise HTTPException(404, "Image not found.")
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=0, must-revalidate" if public else "private, no-store",
    }
    return FileResponse(file, headers=headers)


@router.post("/api/images/{context_type}/{owner_code}/{image_category}")
def upload_image(
    request: Request, context_type: str, owner_code: str, image_category: str,
    sub_category: str | None = None, merchant_code: str | None = None,
    file: UploadFile = File(...), db: Session = Depends(get_db),
    entity_key: str | None = None,
):
    from backend.routers.system_settings import _validate_origin
    context = {value: key for key, value in images.CONTEXTS.items()}.get(context_type)
    if context is None:
        raise HTTPException(400, "Invalid image owner context.")
    owner = None if context == "PLATFORM" and owner_code == "platform-admin" else owner_code
    try:
        resolver = images.AwsumPathResolver.for_owner(context, owner, merchant_code=merchant_code)
        if entity_key is None:
            raise ValueError("A stable entity_key is required; the upload filename is not a storage identifier.")
        key = int(entity_key) if image_category == "users" and entity_key.isdecimal() else entity_key
        reference = resolver.get_entity_image_path(image_category, key)
        image = images.ImagePath.parse(reference)
        if sub_category:
            image = images.ImagePath(context, owner, image_category, image.file_name, sub_category, merchant_code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    images.authorize_images(request, db, image.context_type, image.owner_code, merchant_code=image.merchant_code)
    _validate_origin(request)
    _protect_md_image(db, image)
    data = images.normalize_image(file)
    images.write_image(image, data)
    return {"image_path": image.logical_path, "image_url": images.image_url(image.logical_path)}


@router.post("/api/images/merchant/{merchant_code}/store/{store_code}/{image_category}")
def upload_store_image(
    request: Request, merchant_code: str, store_code: str, image_category: str,
    sub_category: str | None = None, file: UploadFile = File(...), db: Session = Depends(get_db),
    entity_key: str | None = None,
):
    return upload_image(request, "store", store_code, image_category, sub_category, merchant_code, file, db, entity_key)


@router.get("/api/images/branding")
def management_branding(
    request: Request, context_type: str, access_code: str | None = None, owner_id: int | None = None,
    db: Session = Depends(get_db),
):
    from backend.services.branding import resolve_access_code, resolve_branding
    images.authorize_images(request, db, "PLATFORM", None)
    # Admin editors pass the saved organization's DB ID so previews never depend on code lookups.
    if owner_id is not None:
        context = context_type.upper()
        key = {"AGENCY": "agency_id", "MERCHANT": "merchant_id", "STORE": "store_id"}.get(context)
        if key is None:
            raise HTTPException(422, "Invalid logo context.")
        return resolve_branding(db, context, include_inactive=True, **{key: owner_id}).public()
    if not access_code:
        raise HTTPException(422, "access_code or owner_id is required.")
    return resolve_access_code(db, access_code, context_type, include_inactive=True).public()


@router.delete("/api/images")
def delete_image(request: Request, image_path: str, db: Session = Depends(get_db)):
    from backend.routers.system_settings import _validate_origin
    from backend.models_admin.merchant import Merchant
    from backend.models_admin.agency import Agency
    from backend.models_admin.store import Store
    from backend.models_admin.system_settings import SystemSettings
    try:
        image = images.ImagePath.parse(image_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    images.authorize_images(request, db, image.context_type, image.owner_code, merchant_code=image.merchant_code)
    _validate_origin(request)
    _protect_md_image(db, image)
    for model in (Merchant, Agency, Store, SystemSettings):
        if db.query(model).filter(model.c_logo_url == image.logical_path).first():
            raise HTTPException(409, "Image is in use. Reset its logo reference first.")
    path = images.physical_image_path(image)
    if not path.is_file():
        raise HTTPException(404, "Image not found.")
    path.unlink()
    return {"deleted": True, "image_path": image.logical_path}


def _protect_md_image(db: Session, image: images.ImagePath) -> None:
    """MD item images (Platform and Merchant master images) change only through MD Master Save / Delete."""
    if image.image_category == "users":
        raise HTTPException(409, "Manage profile photos through the authorized User Save workflow.")
    if image.context_type not in ("PLATFORM", "MERCHANT") or image.image_category != "master":
        return
    from backend.services.master_group import _lock_md_images
    from backend.models_admin.master_group import MdMaster
    _lock_md_images(db)
    code = image.file_name.rsplit(".", 1)[0]
    if re.fullmatch(r"MD\d+", code, re.IGNORECASE) or db.query(MdMaster).filter(
        (MdMaster.c_item_code == code) | (MdMaster.c_image_path == image.logical_path)
    ).first():
        raise HTTPException(409, "Manage this item image through MD Master Save Changes or Delete.")
