# backend/routers/store.py
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from backend.database.pg_platform import PlatformSessionLocal
from backend.database.store_db import StoreBase, get_store_engine, get_store_backup_dir
from backend.models_admin.store import Store as StoreModel
from backend.models_admin.platform_user import PlatformUser
from backend.utils.safe_schema_migrate import safe_schema_migrate
from backend.config.templates import templates, get_brand_context
from backend.utils.jwt_handler import SECRET_KEY, ALGORITHM
from jose import jwt, JWTError

router = APIRouter()


def get_db():
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _client_brand_code(client_id: int | None) -> str | None:
    if not client_id:
        return None
    return f"CLT_{int(client_id):05d}"


def get_current_user(request: Request, db: Session):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid: int = payload.get("uid")
        store = payload.get("store")
        role: int = payload.get("role")
        principal_type = payload.get("principal_type", "platform_user")
        if uid is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if principal_type != "platform_user":
        raise HTTPException(status_code=401, detail="Invalid token principal for platform DB")

    user = db.query(PlatformUser).filter(PlatformUser.i_platform_user_id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    try:
        store = int(store)
    except (TypeError, ValueError):
        pass

    user.login_store = store
    user.login_role = role
    if user.i_must_change_password == 1:
        raise HTTPException(status_code=403, detail="Password change required")
    return user


def require_store_access(user, store_id: int):
    """Allow access if user is platform admin or the store matches their login_store."""
    if user.login_role == 1:
        return
    if user.login_store != store_id:
        raise HTTPException(status_code=403, detail="Not authorized")


@router.post("/migrate/{store_code}")
def migrate_store(store_code: str):
    """특정 매장의 DB를 자동 마이그레이션"""
    engine = get_store_engine(store_code)
    backup_dir = get_store_backup_dir(store_code)
    safe_schema_migrate(StoreBase, engine, backup_dir)
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
                client_code=_client_brand_code(store.i_account_id),
                store_code=store.c_store_code,
            ),
        },
    )
