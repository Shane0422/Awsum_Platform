from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.database.admin_db import AdminBase, admin_engine, get_admin_backup_dir, init_admin_db
from backend.utils.safe_schema_migrate import safe_schema_migrate
from backend.config.messages import popup_multi_choice
from backend.config.settings import APP_NAME
from backend.routers import auth, store, dashboard, common

app = FastAPI(title=f"{APP_NAME} API")

# ✅ 정적 파일 & 템플릿 경로
app.mount("/static", StaticFiles(directory="backend/static"), name="static")
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["APP_NAME"] = APP_NAME


# ✅ Startup 이벤트 (AdminDB 자동 마이그레이션 + Seed 데이터 등록)
@app.on_event("startup")
def on_startup():
    backup_dir = get_admin_backup_dir()
    safe_schema_migrate(AdminBase, admin_engine, backup_dir)  # 구조 변경 자동 보정 + 백업
    init_admin_db()  # 기본 Seed (StoreType, BusinessType, Role, SuperAdmin) 삽입


# ✅ 루트 페이지 → Home.html
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("Home.html", {"request": request})

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
app.include_router(dashboard.router, prefix="", tags=["Dashboard"])
app.include_router(common.router, prefix="", tags=["Common"])
