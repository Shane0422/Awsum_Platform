import os
from urllib.parse import urlparse

from backend.core.local_env import load_local_env

load_local_env()

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.database.pg_platform import (
    ensure_platform_database_exists,
    PlatformSessionLocal,
    platform_engine,
    get_platform_db_url,
    get_platform_db_url_source,
    mask_db_url,
)
from backend.database.db_init_platform import check_platform_schema, describe_schema_check, init_platform_db
from backend.database.migration_policy import MigrationBlocked, auto_migrate_enabled
from backend.utils.safe_schema_migrate import SchemaMismatchError
from backend.config.messages import popup_multi_choice
from backend.config.settings import APP_NAME
from backend.config.templates import templates, get_brand_context
from backend.models_admin.merchant import Merchant
from backend.routers import auth, store, dashboard, common, subscription
from backend.routers import platform_store
from backend.routers import platform_store_license
from backend.routers import device_master
from backend.routers import system_settings
from backend.routers import master_group
from backend.routers import agency_portal
from backend.services.reporting import GlobalReportMiddleware
from backend.services.web_session_http import enforce_web_session, session_error_response
from backend.routers import images as image_routes
from backend.services.images import ImagePath
from backend.services.builtin_assets import BUILTIN_ROOT, BUILTIN_URL_PREFIX, FAVICON_ICO, builtin_asset_file

app = FastAPI(title=f"{APP_NAME} API")
app.add_middleware(GlobalReportMiddleware)
app.include_router(image_routes.router)

PLATFORM_PUBLIC_PATHS = {
    "/platform/device/activate",
    "/platform/device/validate",
    "/platform/license/activate",
}


@app.middleware("http")
async def protect_platform_admin_routes(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()
    if method == "GET":
        try:
            ImagePath.parse(path)
        except ValueError:
            pass
        else:
            # The image endpoint itself protects private categories.
            return await call_next(request)
    if path == "/platform-admin" and method == "GET":
        return await call_next(request)
    if path == "/platform-admin/login" and method == "POST":
        return await call_next(request)

    is_platform_route = path == "/platform" or path.startswith("/platform/") or path == "/dashboard/admin"
    is_platform_admin_route = path.startswith("/platform-admin/")
    if not (is_platform_route or is_platform_admin_route) or path in PLATFORM_PUBLIC_PATHS:
        return await call_next(request)

    db = PlatformSessionLocal()
    try:
        session_status, user = auth.get_platform_admin_session(request, db)
    finally:
        db.close()

    accepts_html = "text/html" in (request.headers.get("accept") or "")
    if session_status == "anonymous":
        return session_error_response(request, "Your session has ended or expired. Please sign in again.")

    if session_status != "authorized" or not user:
        if accepts_html:
            return HTMLResponse("Forbidden", status_code=403)
        return JSONResponse({"detail": "Forbidden"}, status_code=403)

    if user.i_must_change_password == 1:
        if request.method == "GET" and accepts_html:
            return RedirectResponse(url="/change-password", status_code=303)
        return JSONResponse({"detail": "Password change required"}, status_code=403)

    request.state.platform_admin_user = user
    response = await call_next(request)
    # Prevent back/forward cache from re-showing admin pages after logout.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _set_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# Runs outside module-specific authorization so future web modules inherit
# server-side revocation checks too. Device activation is explicitly excluded.
app.middleware("http")(enforce_web_session)


def _came_from_protected_page(request: Request) -> bool:
    referer = request.headers.get("referer") or ""
    if not referer:
        return False
    try:
        path = urlparse(referer).path or ""
    except Exception:
        return False
    return path.startswith("/platform") or path.startswith("/dashboard")

# ------------------------------------
# Paths (ensure this works regardless of working directory)
# ------------------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# ✅ 정적 파일 & 템플릿 경로
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount(BUILTIN_URL_PREFIX, StaticFiles(directory=str(BUILTIN_ROOT)), name="builtin_platform_images")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(builtin_asset_file(FAVICON_ICO), media_type="image/x-icon")


def report_startup_schema(engine) -> None:
    """AWSUM_AUTO_MIGRATE=OFF: read-only checks only. Pending changes are logged; the schema / data never change."""
    print("[MIGRATION] AWSUM_AUTO_MIGRATE=OFF: startup does not change the database schema or data.")
    plan = check_platform_schema(engine)
    if plan["pending"]:
        print(f"[MIGRATION][WARN] Pending Platform schema changes ({len(describe_schema_check(plan))}):")
        for line in describe_schema_check(plan):
            print(f"[MIGRATION][WARN]   {line}")
        print("[MIGRATION][WARN] Apply an approved migration: python -m tools.migrate_platform --check, "
              "then --apply --backup auto --confirm-db <database>.")
    else:
        print("[MIGRATION] No missing tables / columns detected (index or data steps are applied only by "
              "tools.migrate_platform --apply).")
    from backend.services.storage_layout import final_layout_enabled, validate_runtime_references
    if final_layout_enabled():
        validate_runtime_references(engine)  # read-only reference check, as before


# ✅ Startup 이벤트 (PostgreSQL awsum_platform 초기화 + Seed 데이터 등록)
@app.on_event("startup")
def on_startup():
    from backend.services.web_sessions import idle_timeout, max_minutes
    max_minutes()
    from backend.services.session_metadata import trusted_proxy_networks
    trusted_proxy_networks()
    source = get_platform_db_url_source()
    masked_url = mask_db_url(get_platform_db_url())
    if source == "default":
        print("[WARN] AWSUM_PLATFORM_DATABASE_URL is not set. Falling back to default PostgreSQL URL.")
    else:
        print(f"[INFO] Platform DB URL source: {source}")
    print(f"[INFO] Platform DB URL (masked): {masked_url}")

    if auto_migrate_enabled():
        print("[MIGRATION] AWSUM_AUTO_MIGRATE=ON: running the non-destructive Platform migration at startup.")
        try:
            ensure_platform_database_exists()
            init_platform_db()  # never DROP / recreate / delete data automatically
        except (MigrationBlocked, SchemaMismatchError) as exc:
            print(f"[MIGRATION][ERROR] {type(exc).__name__}: {exc}")
            print("[MIGRATION][WARN] Startup continues; nothing destructive was run. Fix it with an approved "
                  "migration (python -m tools.migrate_platform).")
    else:
        report_startup_schema(platform_engine)
    with PlatformSessionLocal() as db:
        idle_timeout(db)


# ✅ 플랫폼 홈
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Preserve the originating login entry for the existing home-triggered logout.
    if request.cookies.get("access_token") and _came_from_protected_page(request):
        from backend.services.web_sessions import logout_session
        from backend.services.web_session_http import session_login_path
        with PlatformSessionLocal() as db:
            target = session_login_path(request, db)
            logout_session(request, db)
        response = RedirectResponse(target, status_code=303)
        response.delete_cookie("access_token")
        return _set_no_cache_headers(response)
    resp = templates.TemplateResponse(
        request,
        "platform/platform_home.html",
        {"request": request, "login_mode": "general"},
    )
    return _set_no_cache_headers(resp)


def _normalize_merchant_code(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


@app.get("/merchant/{merchant_code}", response_class=HTMLResponse)
async def merchant_home(request: Request, merchant_code: str):
    db: Session = PlatformSessionLocal()
    try:
        merchants = db.query(Merchant).all()

        matched_merchant = next(
            (
                merchant
                for merchant in merchants
                if _normalize_merchant_code(merchant.c_merchant_code or "") == _normalize_merchant_code(merchant_code)
                or _normalize_merchant_code(merchant.c_merchant_name or "") == _normalize_merchant_code(merchant_code)
            ),
            None,
        )

        if not matched_merchant:
            return templates.TemplateResponse(
                "shared/merchant_not_found.html",
                {"request": request, "merchant_code": merchant_code},
                status_code=404,
            )

        merchant_name = matched_merchant.c_merchant_name or APP_NAME
        return templates.TemplateResponse(
            "shared/merchant_home.html",
            {
                "request": request,
                "merchant_name": merchant_name,
                "hero_title": f"Elegance. Style. {merchant_name}",
                "hero_subtitle": "Premium formalwear rentals for your most memorable moments.",
                "contact_email": matched_merchant.c_email or "support@awsumsolution.com",
                "contact_phone": matched_merchant.c_phone or "+1 (000) 000-0000",
                **get_brand_context(
                    context_type="merchant",
                    brand_display_name=merchant_name,
                    merchant_code=matched_merchant.c_merchant_code,
                ),
            },
        )
    finally:
        db.close()


@app.get("/change-password", response_class=HTMLResponse)
async def change_password_alias():
    return RedirectResponse(url="/auth/change-password", status_code=303)

# ✅ 404 처리 → 팝업 출력
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        return session_error_response(request, str(exc.detail))
    accepts_html = "text/html" in (request.headers.get("accept") or "")
    is_api_request = request.method != "GET" or request.url.path.startswith("/subscriptions")

    if is_api_request and not accepts_html:
        return JSONResponse(content={"detail": exc.detail}, status_code=exc.status_code)

    if exc.status_code == 404:
        html = popup_multi_choice(
            title="Invalid Page",
            message=f"The page {request.url.path} does not exist.",
            choices={1: "Go Home"},
            redirect_path="/confirm-result"
        )
        return HTMLResponse(content=html, status_code=404)
    return HTMLResponse(content=str(exc.detail), status_code=exc.status_code)


# ✅ 라우터 등록
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(auth.platform_admin_router, tags=["Platform Admin"])
app.include_router(store.router, prefix="/store", tags=["Store"])
app.include_router(platform_store.router, prefix="", tags=["PlatformStore"])
app.include_router(platform_store_license.router, prefix="", tags=["PlatformStoreLicense"])
app.include_router(system_settings.router, prefix="", tags=["SystemSettings"])
app.include_router(master_group.router, prefix="", tags=["MasterGroup"])
app.include_router(agency_portal.router)
app.include_router(device_master.router, prefix="", tags=["DeviceMaster"])
app.include_router(subscription.router, prefix="", tags=["Subscription"])
app.include_router(dashboard.router, prefix="", tags=["Dashboard"])
app.include_router(common.router, prefix="", tags=["Common"])
