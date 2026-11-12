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
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.platform_user import PlatformUser, MERCHANT_ADMIN_ROLE_NAME, STORE_ADMIN_ROLE_NAME
from backend.models_admin.store import Store
from backend.models_admin.merchant import Merchant
from backend.models_admin.business_type import BusinessType
from backend.models_admin.role import Role
from backend.models_admin.agency import Agency
from backend.models_admin.agency_type import AgencyType
from backend.config.templates import templates, get_brand_context
from backend.config.settings import APP_NAME
from backend.routers.auth import get_platform_admin_session, _log_platform_forbidden
from backend.services.merchant_features import business_option_defs

# Platform admin store ID (fixed)
ADMIN_STORE_ID = 1000001

router = APIRouter()


def _is_super_admin(db: Session, user: PlatformUser) -> bool:
    role = db.query(Role).filter(Role.i_role_id == user.i_role_id).first()
    return bool(role and role.c_name.strip().casefold() == "superadmin")


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
    from backend.services.web_sessions import authenticate
    return authenticate(request, db, allow_password_change=True)


def _set_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _build_login_redirect(request: Request, reason: str = "session_expired") -> RedirectResponse:
    next_path = quote(request.url.path or "/", safe="")
    url = f"/platform-admin?reason={quote(reason, safe='')}&next={next_path}"
    return RedirectResponse(url=url, status_code=303)


def _auth_user_or_home_redirect(request: Request, db: Session):
    try:
        user = get_current_user(request, db)
        if user.i_must_change_password == 1:
            return None, _set_no_cache_headers(RedirectResponse(url="/change-password", status_code=303))
        return user, None
    except HTTPException:
        resp = _build_login_redirect(request, reason="session_expired")
        resp.delete_cookie("access_token")
        return None, _set_no_cache_headers(resp)


def _resolve_store_brand_info(db: Session, login_store: Optional[int]) -> dict:
    if not login_store or login_store == ADMIN_STORE_ID:
        return {
            "brand_display_name": APP_NAME,
            "merchant_code": None,
            "store_code": None,
        }

    store = db.query(Store).filter(Store.i_store_id == login_store).first()
    if not store:
        return {
            "brand_display_name": APP_NAME,
            "merchant_code": None,
            "store_code": None,
        }

    merchant = db.query(Merchant).filter(Merchant.i_merchant_id == store.i_merchant_id).first()
    return {
        "brand_display_name": (store.c_store_name or "").strip() or APP_NAME,
        "merchant_code": merchant.c_merchant_code if merchant else None,
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

    if user.login_role != 1:
        raise HTTPException(status_code=403, detail="Forbidden")

    from backend.models_admin.store import Store
    from backend.models_admin.role import Role
    stores = db.query(Store).all()
    roles = db.query(Role).all()

    return _set_no_cache_headers(templates.TemplateResponse(request, "client/dashboard_admin.html", {
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
@router.get("/platform/admin")
def platform_admin_alias(request: Request, db: Session = Depends(get_db)):
    _, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp
    session_status, _ = get_platform_admin_session(request, db)
    if session_status != "authorized":
        raise HTTPException(status_code=403, detail="Forbidden")
    return _set_no_cache_headers(RedirectResponse(url="/platform/dashboard", status_code=303))


@router.get("/platform/dashboard", response_class=HTMLResponse)
def platform_dashboard(request: Request, db: Session = Depends(get_db)):
    user, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp

    session_status, _ = get_platform_admin_session(request, db)
    if session_status != "authorized":
        raise HTTPException(status_code=403, detail="Forbidden")

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

    return _set_no_cache_headers(templates.TemplateResponse(request, "platform/platform_dashboard.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Platform Admin",
        "is_super_admin": _is_super_admin(db, user),
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

    if user.login_role != 1:
        _log_platform_forbidden(request, db, "master_management", "login_role_not_1", user=user)
        raise HTTPException(status_code=403, detail="Forbidden")

    # Normalize aliases used by legacy/deep links.
    # Keep this centralized so template/JS can continue to use singular keys.
    if module == "invoices":
        module = "invoice"
    elif module == "contracts":
        module = "contract"

    if module == "user":
        return _set_no_cache_headers(templates.TemplateResponse(request, "platform/users.html", {
            "request": request,
            "APP_NAME": APP_NAME,
            "user": user,
            "role": "Platform Admin",
            "is_super_admin": _is_super_admin(db, user),
            "active_page": "master",
            "active_module": "user",
            **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
        }))

    if module == "session":
        return _set_no_cache_headers(templates.TemplateResponse(request, "platform/sessions.html", {
            "request": request, "APP_NAME": APP_NAME, "user": user, "role": "Platform Admin",
            "is_super_admin": _is_super_admin(db, user), "active_page": "master", "active_module": "session",
            **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
        }))

    allowed_modules = {"role", "business-type", "agency-type", "merchant", "merchant", "agency", "license", "subscription", "pricing-plan", "contract", "invoice", "payment-method", "user", "session", "store", "device-category", "device-type"}
    if module not in allowed_modules:
        module = "role"

    # Provide master data for dropdowns
    from backend.models_admin.store import Store
    from backend.models_admin.merchant import Merchant
    from backend.models_admin.pricing_plan import PricingPlan
    from backend.models_admin.device_category import DeviceCategory
    from backend.models_admin.device_type import DeviceType

    roles = db.query(Role).all()
    business_types = db.query(BusinessType).filter(BusinessType.c_status == "active").all()
    merchants = db.query(Merchant).filter(Merchant.c_status == "active").order_by(Merchant.i_merchant_id).all()
    agencies = db.query(Agency).order_by(Agency.i_agency_id).all()
    agency_types = db.query(AgencyType).filter(AgencyType.c_status == "active").order_by(AgencyType.i_agency_type_id).all()
    stores = db.query(Store).order_by(Store.i_store_id).all()
    pricing_plans = (
        db.query(PricingPlan)
        .filter(PricingPlan.c_status == "active")
        .order_by(PricingPlan.i_sort_order.asc(), PricingPlan.i_plan_id.asc())
        .all()
    )
    device_categories = (
        db.query(DeviceCategory)
        .filter(DeviceCategory.c_status == "active")
        .order_by(DeviceCategory.i_sort_order, DeviceCategory.i_device_category_id)
        .all()
    )
    device_types = (
        db.query(DeviceType)
        .filter(DeviceType.c_status == "active")
        .order_by(DeviceType.i_sort_order, DeviceType.i_device_type_id)
        .all()
    )
    users = db.query(PlatformUser).all()

    return _set_no_cache_headers(templates.TemplateResponse(request, "platform/master_management.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Platform Admin",
        "is_super_admin": _is_super_admin(db, user),
        "active_page": "master",
        "active_module": module,
        "roles": roles,
        "business_types": business_types,
        "merchants": merchants,
        "agencies": agencies,
        "agency_types": agency_types,
        "stores": stores,
        # Merchant > BUSINESS OPTIONS rows (labels / order / Coming soon from the Common Feature Registry).
        "business_option_defs": business_option_defs(),
        "pricing_plans": pricing_plans,
        "device_categories": device_categories,
        "device_types": device_types,
        "users": users,
        **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
    }))


@router.get("/platform/master/invoices", response_class=HTMLResponse)
def master_invoices_alias(request: Request):
    # Backward-compatible alias: always use singular module key in app internals.
    return _set_no_cache_headers(RedirectResponse(url="/platform/master/invoice", status_code=303))




@router.get("/platform/merchants-tree", response_class=HTMLResponse)
def platform_merchants_tree(request: Request, db: Session = Depends(get_db)):
    user, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp

    if user.login_role != 1:
        raise HTTPException(status_code=403, detail="Forbidden")

    return _set_no_cache_headers(templates.TemplateResponse(request, "platform/merchants_tree.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Platform Admin",
        "active_page": "merchants-tree",
        **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
    }))


# ==========================
# Store Dashboard
# ==========================
@router.get("/dashboard/store", response_class=HTMLResponse)
def dashboard_store(request: Request, db: Session = Depends(get_db)):
    return _merchant_dashboard_response(request, db, "STANDARD", active_menu="dashboard")


def _merchant_dashboard_response(request: Request, db: Session, dashboard_type: str, active_menu: str = "dashboard"):
    user, redirect_resp = _auth_user_or_home_redirect(request, db)
    if redirect_resp:
        return redirect_resp

    role = db.get(Role, user.i_role_id)
    if user.login_role not in [2, 3] and (not role or role.c_name not in {MERCHANT_ADMIN_ROLE_NAME, STORE_ADMIN_ROLE_NAME, "StoreAdmin", "CustomerAdmin"}):
        return _set_no_cache_headers(RedirectResponse(url="/", status_code=303))
    from backend.services.app_navigation import resolve_app_scope
    scope = resolve_app_scope(request, db, user)

    template_map = {
        "STANDARD": "merchant/dashboard_standard.html",
        "RESTAURANT": "merchant/dashboard_restaurant.html",
        "DELI": "merchant/dashboard_deli.html",
        "TUXEDO_RENTAL": "merchant/dashboard_tuxedo.html",
    }
    template_name = template_map.get((dashboard_type or "").upper(), "merchant/dashboard_standard.html")

    brand_info = _resolve_store_brand_info(db, user.login_store)
    context_type = "store"
    if user.i_merchant_id is not None:
        merchant = db.get(Merchant, user.i_merchant_id)
        brand_info = {
            "brand_display_name": merchant.c_merchant_name,
            "merchant_code": merchant.c_merchant_code, "store_code": None,
        }
        context_type = "merchant"

    return _set_no_cache_headers(templates.TemplateResponse(request, template_name, {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": context_type.title(),
        "login_store": user.login_store,
        "dashboard_type": (dashboard_type or "STANDARD").upper(),
        "active_menu": active_menu,
        "active_page": "dashboard",
        "app_navigation": scope.navigation(),
        **get_brand_context(
            context_type=context_type,
            brand_display_name=brand_info["brand_display_name"],
            merchant_code=brand_info["merchant_code"],
            store_code=brand_info["store_code"],
        ),
    }))


@router.get("/merchant/dashboard/standard", response_class=HTMLResponse)
def dashboard_standard(request: Request, db: Session = Depends(get_db)):
    return _merchant_dashboard_response(request, db, "STANDARD", active_menu="dashboard")


@router.get("/merchant/dashboard/restaurant", response_class=HTMLResponse)
def dashboard_restaurant(request: Request, db: Session = Depends(get_db)):
    return _merchant_dashboard_response(request, db, "RESTAURANT", active_menu="dashboard")


@router.get("/merchant/dashboard/deli", response_class=HTMLResponse)
def dashboard_deli(request: Request, db: Session = Depends(get_db)):
    return _merchant_dashboard_response(request, db, "DELI", active_menu="dashboard")


@router.get("/merchant/dashboard/tuxedo", response_class=HTMLResponse)
def dashboard_tuxedo(request: Request, db: Session = Depends(get_db)):
    return _merchant_dashboard_response(request, db, "TUXEDO_RENTAL", active_menu="dashboard")



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
            merchant_code=brand_info["merchant_code"],
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
            merchant_code=brand_info["merchant_code"],
            store_code=brand_info["store_code"],
        ),
    }))


@router.get("/dashboard/customer", response_class=HTMLResponse)
def dashboard_customer_legacy():
    return RedirectResponse(url="/dashboard/client", status_code=307)
