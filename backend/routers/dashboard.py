# ==========================
# 대시보드
# backend/templates/
# ├── client/dashboard_admin.html   # SuperAdmin 전용
# ├── client/dashboard_store.html   # Store 관리자/Manager 전용
# ├── client/dashboard_staff.html   # 직원 전용
# └── client/dashboard_client.html  # Client 사용자 전용
# 📌 정리
# SuperAdmin → client/dashboard_admin.html (Store/Role 관리)
# Store(Admin/Manager) → client/dashboard_store.html (예약/재고/매출 KPI)
# Staff → client/dashboard_staff.html (간단 업무 안내)
# Client User → client/dashboard_client.html (내 예약/내 정보)
# ==========================

# backend/routers/dashboard.py
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.store import Store
from backend.models_admin.business_type import BusinessType
from backend.models_admin.role import Role
from backend.config.templates import templates, get_brand_context
from backend.config.settings import APP_NAME
from backend.utils.jwt_handler import SECRET_KEY, ALGORITHM

# Platform admin store ID (fixed)
ADMIN_STORE_ID = 1000001

router = APIRouter()

# ==========================
# DB 연결 헬퍼
# ==========================
def get_db():
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================
# JWT 검증
# ==========================
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

    # Ensure store is a consistent int for permission checks
    try:
        store = int(store)
    except (TypeError, ValueError):
        pass

    user.login_store = store
    user.login_role = role
    return user


def _set_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _auth_user_or_home_redirect(request: Request, db: Session):
    try:
        user = get_current_user(request, db)
        if user.i_must_change_password == 1:
            return None, _set_no_cache_headers(RedirectResponse(url="/change-password", status_code=303))
        return user, None
    except HTTPException:
        resp = RedirectResponse(url="/", status_code=303)
        resp.delete_cookie("access_token")
        return None, _set_no_cache_headers(resp)


def _client_brand_code(client_id: Optional[int]) -> Optional[str]:
    if not client_id:
        return None
    return f"CLT_{int(client_id):05d}"


def _resolve_store_brand_info(db: Session, login_store: Optional[int]) -> dict:
    if not login_store or login_store == ADMIN_STORE_ID:
        return {
            "brand_display_name": APP_NAME,
            "client_code": None,
            "store_code": None,
        }

    store = db.query(Store).filter(Store.i_store_id == login_store).first()
    if not store:
        return {
            "brand_display_name": APP_NAME,
            "client_code": None,
            "store_code": None,
        }

    return {
        "brand_display_name": (store.c_store_name or "").strip() or APP_NAME,
        "client_code": _client_brand_code(store.i_account_id),
        "store_code": (store.c_store_code or "").strip() or None,
    }

# ==========================
# SuperAdmin Dashboard
# ==========================
@router.get("/dashboard/admin", response_class=HTMLResponse)
def dashboard_admin(request: Request, db: Session = Depends(get_db)):
    user, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp

    if not (user.login_role == 1 or user.login_store == ADMIN_STORE_ID):
        return _set_no_cache_headers(RedirectResponse(url="/", status_code=303))

    from backend.models_admin.store import Store
    from backend.models_admin.role import Role
    stores = db.query(Store).all()
    roles = db.query(Role).all()

    return _set_no_cache_headers(templates.TemplateResponse("client/dashboard_admin.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "SuperAdmin",
        "stores": stores,
        "roles": roles,
        "login_store": user.login_store,
        **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
    }))

# ==========================
# Platform Dashboard (Top-level Admin)
# ==========================
@router.get("/platform/dashboard", response_class=HTMLResponse)
def platform_dashboard(request: Request, db: Session = Depends(get_db)):
    user, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp

    # Only platform-level admins should access this page
    if not (user.login_role == 1 or user.login_store == ADMIN_STORE_ID):
        return _set_no_cache_headers(RedirectResponse(url="/", status_code=303))

    from backend.models_admin.store import Store

    total_companies = db.query(Store).count()
    active_stores = db.query(Store).filter(Store.c_status == "active").count()
    inactive_stores = db.query(Store).filter(Store.c_status != "active").count()

    recent_companies = (
        db.query(Store)
        .order_by(Store.i_store_id.desc())
        .limit(6)
        .all()
    )

    total_users = db.query(PlatformUser).count()

    # Provide data for the top bar context panel
    from backend.models_admin.role import Role

    roles = db.query(Role).all()
    business_types = db.query(BusinessType).filter(BusinessType.c_status == "active").all()
    stores = db.query(Store).order_by(Store.i_store_id).all()

    users = db.query(PlatformUser).all()

    return _set_no_cache_headers(templates.TemplateResponse("platform/platform_dashboard.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Platform Admin",
        "active_page": "dashboard",
        "login_store": user.login_store,
        "roles": roles,
        "business_types": business_types,
        "stores": stores,
        "users": users,
        "total_companies": total_companies,
        "active_stores": active_stores,
        "inactive_stores": inactive_stores,
        "total_users": total_users,
        "recent_companies": recent_companies,
        **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
    }))


# ==========================
# Master Management
# ==========================
@router.get("/platform/master/{module}", response_class=HTMLResponse)
def master_management(request: Request, module: str, db: Session = Depends(get_db)):
    user, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp

    if not (user.login_role == 1 or user.login_store == ADMIN_STORE_ID):
        return _set_no_cache_headers(RedirectResponse(url="/", status_code=303))

    allowed_modules = {"role", "business-type", "client", "store", "user", "session"}
    if module not in allowed_modules:
        module = "role"

    # Provide master data for dropdowns
    from backend.models_admin.store import Store
    from backend.models_admin.account import Client

    roles = db.query(Role).all()
    business_types = db.query(BusinessType).filter(BusinessType.c_status == "active").all()
    clients = db.query(Client).filter(Client.c_status == "active").order_by(Client.i_account_id).all()
    stores = db.query(Store).order_by(Store.i_store_id).all()
    users = db.query(PlatformUser).all()

    return _set_no_cache_headers(templates.TemplateResponse("platform/master_management.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Platform Admin",
        "active_page": "master",
        "active_module": module,
        "roles": roles,
        "business_types": business_types,
        "clients": clients,
        "stores": stores,
        "users": users,
        **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
    }))


# ==========================
# Store Dashboard
# ==========================
@router.get("/dashboard/store", response_class=HTMLResponse)
def dashboard_store(request: Request, db: Session = Depends(get_db)):
    return _client_dashboard_response(request, db, "STANDARD", active_menu="dashboard")


def _client_dashboard_response(request: Request, db: Session, dashboard_type: str, active_menu: str = "dashboard"):
    user, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp

    if user.login_role not in [2, 3]:
        return _set_no_cache_headers(RedirectResponse(url="/", status_code=303))

    template_map = {
        "STANDARD": "client/dashboard_standard.html",
        "RESTAURANT": "client/dashboard_restaurant.html",
        "DELI": "client/dashboard_deli.html",
        "TUXEDO_RENTAL": "client/dashboard_tuxedo.html",
    }
    template_name = template_map.get((dashboard_type or "").upper(), "client/dashboard_standard.html")

    brand_info = _resolve_store_brand_info(db, user.login_store)

    return _set_no_cache_headers(templates.TemplateResponse(template_name, {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Store",
        "login_store": user.login_store,
        "dashboard_type": (dashboard_type or "STANDARD").upper(),
        "active_menu": active_menu,
        **get_brand_context(
            context_type="store",
            brand_display_name=brand_info["brand_display_name"],
            client_code=brand_info["client_code"],
            store_code=brand_info["store_code"],
        ),
    }))


@router.get("/client/dashboard/standard", response_class=HTMLResponse)
def dashboard_standard(request: Request, db: Session = Depends(get_db)):
    return _client_dashboard_response(request, db, "STANDARD", active_menu="dashboard")


@router.get("/client/dashboard/restaurant", response_class=HTMLResponse)
def dashboard_restaurant(request: Request, db: Session = Depends(get_db)):
    return _client_dashboard_response(request, db, "RESTAURANT", active_menu="dashboard")


@router.get("/client/dashboard/deli", response_class=HTMLResponse)
def dashboard_deli(request: Request, db: Session = Depends(get_db)):
    return _client_dashboard_response(request, db, "DELI", active_menu="dashboard")


@router.get("/client/dashboard/tuxedo", response_class=HTMLResponse)
def dashboard_tuxedo(request: Request, db: Session = Depends(get_db)):
    return _client_dashboard_response(request, db, "TUXEDO_RENTAL", active_menu="dashboard")


# Legacy customer URL aliases kept for compatibility.
@router.get("/customer/dashboard/standard", response_class=HTMLResponse)
def dashboard_standard_customer_alias():
    return RedirectResponse(url="/client/dashboard/standard", status_code=307)


@router.get("/customer/dashboard/restaurant", response_class=HTMLResponse)
def dashboard_restaurant_customer_alias():
    return RedirectResponse(url="/client/dashboard/restaurant", status_code=307)


@router.get("/customer/dashboard/deli", response_class=HTMLResponse)
def dashboard_deli_customer_alias():
    return RedirectResponse(url="/client/dashboard/deli", status_code=307)


@router.get("/customer/dashboard/tuxedo", response_class=HTMLResponse)
def dashboard_tuxedo_customer_alias():
    return RedirectResponse(url="/client/dashboard/tuxedo", status_code=307)


# Legacy aliases kept for compatibility; they redirect to the new customer routes.
@router.get("/standard/dashboard", response_class=HTMLResponse)
def dashboard_standard_legacy():
    return RedirectResponse(url="/client/dashboard/standard", status_code=307)


@router.get("/restaurant/dashboard", response_class=HTMLResponse)
def dashboard_restaurant_legacy():
    return RedirectResponse(url="/client/dashboard/restaurant", status_code=307)


@router.get("/deli/dashboard", response_class=HTMLResponse)
def dashboard_deli_legacy():
    return RedirectResponse(url="/client/dashboard/deli", status_code=307)


@router.get("/tuxedo/dashboard", response_class=HTMLResponse)
def dashboard_tuxedo_legacy():
    return RedirectResponse(url="/client/dashboard/tuxedo", status_code=307)

# ==========================
# Staff Dashboard
# ==========================
@router.get("/dashboard/staff", response_class=HTMLResponse)
def dashboard_staff(request: Request, db: Session = Depends(get_db)):
    user, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp

    if user.login_role != 4:
        return _set_no_cache_headers(RedirectResponse(url="/", status_code=303))

    brand_info = _resolve_store_brand_info(db, user.login_store)

    return _set_no_cache_headers(templates.TemplateResponse("client/dashboard_staff.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Staff",
        "login_store": user.login_store,
        **get_brand_context(
            context_type="store",
            brand_display_name=brand_info["brand_display_name"],
            client_code=brand_info["client_code"],
            store_code=brand_info["store_code"],
        ),
    }))

# ==========================
# Client Dashboard
# ==========================
@router.get("/dashboard/client", response_class=HTMLResponse)
def dashboard_client(request: Request, db: Session = Depends(get_db)):
    user, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp

    if user.login_role != 5:
        return _set_no_cache_headers(RedirectResponse(url="/", status_code=303))

    brand_info = _resolve_store_brand_info(db, user.login_store)

    return _set_no_cache_headers(templates.TemplateResponse("client/dashboard_client.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Client",
        "login_store": user.login_store,
        **get_brand_context(
            context_type="store",
            brand_display_name=brand_info["brand_display_name"],
            client_code=brand_info["client_code"],
            store_code=brand_info["store_code"],
        ),
    }))


@router.get("/dashboard/customer", response_class=HTMLResponse)
def dashboard_customer_legacy():
    return RedirectResponse(url="/dashboard/client", status_code=307)
