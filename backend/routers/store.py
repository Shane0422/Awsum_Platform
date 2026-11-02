# backend/routers/store.py
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from backend.database.pg_platform import PlatformSessionLocal
from backend.database.store_db import StoreBase, get_store_engine, get_store_backup_dir
from backend.models_admin.store import Store as StoreModel
from backend.models_admin.merchant import Merchant
from backend.models_admin.platform_user import PlatformUser
from backend.utils.safe_schema_migrate import safe_schema_migrate
from backend.config.templates import templates, get_brand_context

router = APIRouter()


def get_db():
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session):
    from backend.services.web_sessions import authenticate
    return authenticate(request, db)


def require_store_access(user, store_id: int):
    """Allow access if user is platform admin or the store matches their login_store."""
    if user.login_role == 1:
        return
    if user.login_store != store_id:
        raise HTTPException(status_code=403, detail="Not authorized")


@router.post("/migrate/{store_code}")
def migrate_store(store_code: str, request: Request, merchant_code: str, db: Session = Depends(get_db)):
    """특정 매장의 DB를 자동 마이그레이션"""
    from backend.services.paths import AwsumPathResolver, authorize_resource
    from backend.routers.system_settings import _validate_origin
    try:
        owner = AwsumPathResolver("STORE", merchant_code=merchant_code, store_code=store_code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    authorize_resource(request, db, owner)
    _validate_origin(request)
    engine = get_store_engine(merchant_code, store_code, db=db)
    try:
        safe_schema_migrate(StoreBase, engine, get_store_backup_dir(merchant_code, store_code, db=db))
    finally:
        engine.dispose()
    return {"message": f"✅ Store {store_code} DB migrated successfully."}


@router.get("/{store_id}/workspace", response_class=HTMLResponse)
@router.get("/{store_id}/workspace/{module}", response_class=HTMLResponse)
def store_workspace(
    request: Request,
    store_id: int,
    module: str = "dashboard",
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    require_store_access(user, store_id)

    store = db.query(StoreModel).filter(StoreModel.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == store.i_merchant_id).first()

    # Normalize module name (fallback to dashboard)
    module = (module or "dashboard").lower()
    allowed_modules = {
        "dashboard",
        "master-code",
        "employee",
        "marketing",
        "membership",
        "sales",
        "inventory",
        "ledger",
        "reports",
        "settings",
    }
    if module not in allowed_modules:
        module = "dashboard"

    return templates.TemplateResponse(
        "store/workspace_dashboard.html",
        {
            "request": request,
            "APP_NAME": "Store Workspace",
            "store_id": store.i_store_id,
            "store_name": store.c_store_name,
            "store_phone": store.c_phone,
            "store_email": store.c_email,
            "store_website": store.c_website,
            "receipt_store_name": store.c_receipt_store_name,
            "receipt_phone": store.c_receipt_phone,
            "receipt_email": store.c_receipt_email,
            "receipt_website_url": store.c_receipt_website_url,
            "receipt_message": store.c_receipt_message,
            "user": user,
            "role": ("Platform Admin" if user.login_role == 1 else user.login_role),
            "active_module": module,
            **get_brand_context(
                context_type="store",
                brand_display_name=store.c_store_name,
                merchant_code=merchant.c_merchant_code if merchant else None,
                store_code=store.c_store_code,
            ),
        },
    )
