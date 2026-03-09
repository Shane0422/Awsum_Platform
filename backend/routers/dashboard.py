# ==========================
# 대시보드
# backend/templates/
# ├── dashboard_admin.html      # SuperAdmin 전용
# ├── dashboard_store.html      # Store 관리자/Manager 전용
# ├── dashboard_staff.html      # 직원 전용
# └── dashboard_customer.html   # 고객 전용
# 📌 정리
# SuperAdmin → dashboard_admin.html (Store/Role 관리)
# Store(Admin/Manager) → dashboard_store.html (예약/재고/매출 KPI)
# Staff → dashboard_staff.html (간단 업무 안내)
# Customer → dashboard_customer.html (내 예약/내 정보)
# ==========================

# backend/routers/dashboard.py
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from backend.database.admin_db import AdminSessionLocal
from backend.models_admin.user import User
from backend.config.templates import templates
from backend.config.settings import APP_NAME
from backend.utils.jwt_handler import SECRET_KEY, ALGORITHM

router = APIRouter()

# ==========================
# DB 연결 헬퍼
# ==========================
def get_db():
    db = AdminSessionLocal()
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
        store: str = payload.get("store")
        role: int = payload.get("role")
        if uid is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.i_user_id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    user.login_store = store
    user.login_role = role
    return user

# ==========================
# SuperAdmin Dashboard
# ==========================
@router.get("/dashboard/admin", response_class=HTMLResponse)
def dashboard_admin(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not (user.login_role == 1 or user.login_store == "admin"):
        raise HTTPException(status_code=403, detail="Not authorized")

    from backend.models_admin.store import Store
    from backend.models_admin.role import Role
    stores = db.query(Store).all()
    roles = db.query(Role).all()

    return templates.TemplateResponse("dashboard_admin.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "SuperAdmin",
        "stores": stores,
        "roles": roles,
        "login_store": user.login_store
    })

# ==========================
# Store Dashboard
# ==========================
@router.get("/dashboard/store", response_class=HTMLResponse)
def dashboard_store(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user.login_role not in [2, 3]:
        raise HTTPException(status_code=403, detail="Not authorized")

    return templates.TemplateResponse("dashboard_store.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Store",
        "login_store": user.login_store
    })

# ==========================
# Staff Dashboard
# ==========================
@router.get("/dashboard/staff", response_class=HTMLResponse)
def dashboard_staff(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user.login_role != 4:
        raise HTTPException(status_code=403, detail="Not authorized")

    return templates.TemplateResponse("dashboard_staff.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Staff",
        "login_store": user.login_store
    })

# ==========================
# Customer Dashboard
# ==========================
@router.get("/dashboard/customer", response_class=HTMLResponse)
def dashboard_customer(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user.login_role != 5:
        raise HTTPException(status_code=403, detail="Not authorized")

    return templates.TemplateResponse("dashboard_customer.html", {
        "request": request,
        "APP_NAME": APP_NAME,
        "user": user,
        "role": "Customer",
        "login_store": user.login_store
    })
