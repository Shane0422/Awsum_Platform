import os
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.database.pg_platform import (
    ensure_platform_database_exists,
    PlatformSessionLocal,
    get_platform_db_url,
    get_platform_db_url_source,
    mask_db_url,
)
from backend.database.db_init_platform import init_platform_db
from backend.config.messages import popup_multi_choice
from backend.config.settings import APP_NAME
from backend.config.templates import templates, get_brand_context
from backend.models_admin.account import Client
from backend.routers import auth, store, dashboard, common
from backend.routers import platform_store

app = FastAPI(title=f"{APP_NAME} API")


def _set_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


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


# ✅ Startup 이벤트 (PostgreSQL awsum_platform 초기화 + Seed 데이터 등록)
@app.on_event("startup")
def on_startup():
    source = get_platform_db_url_source()
    masked_url = mask_db_url(get_platform_db_url())
    if source == "default":
        print("[WARN] AWSUM_PLATFORM_DATABASE_URL is not set. Falling back to default PostgreSQL URL.")
    else:
        print(f"[INFO] Platform DB URL source: {source}")
    print(f"[INFO] Platform DB URL (masked): {masked_url}")

    ensure_platform_database_exists()
    init_platform_db()


# ✅ 플랫폼 홈
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    resp = templates.TemplateResponse("platform/platform_home.html", {"request": request})

    # If user moves from protected dashboard/platform pages to home, force logout.
    if request.cookies.get("access_token") and _came_from_protected_page(request):
        resp.delete_cookie("access_token")

    return _set_no_cache_headers(resp)


def _normalize_client_code(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _client_brand_code(client_id: int | None) -> str | None:
    if not client_id:
        return None
    return f"CLT_{int(client_id):05d}"


@app.get("/client/{client_code}", response_class=HTMLResponse)
async def client_home(request: Request, client_code: str):
    db: Session = PlatformSessionLocal()
    try:
        clients = db.query(Client).all()

        matched_client = next(
            (
                client
                for client in clients
                if _normalize_client_code(client.c_client_code or "") == _normalize_client_code(client_code)
                or _normalize_client_code(client.c_client_name or "") == _normalize_client_code(client_code)
            ),
            None,
        )

        if not matched_client:
            return templates.TemplateResponse(
                "shared/client_not_found.html",
                {"request": request, "client_code": client_code},
                status_code=404,
            )

        client_name = matched_client.c_client_name or APP_NAME
        return templates.TemplateResponse(
            "shared/client_home.html",
            {
                "request": request,
                "client_name": client_name,
                "hero_title": f"Elegance. Style. {client_name}",
                "hero_subtitle": "Premium formalwear rentals for your most memorable moments.",
                "contact_email": matched_client.c_email or "support@awsumsolution.com",
                "contact_phone": matched_client.c_phone or "+1 (000) 000-0000",
                **get_brand_context(
                    context_type="client",
                    brand_display_name=client_name,
                    client_code=_client_brand_code(matched_client.i_client_id),
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
app.include_router(store.router, prefix="/store", tags=["Store"])
app.include_router(platform_store.router, prefix="", tags=["PlatformStore"])
app.include_router(dashboard.router, prefix="", tags=["Dashboard"])
app.include_router(common.router, prefix="", tags=["Common"])
