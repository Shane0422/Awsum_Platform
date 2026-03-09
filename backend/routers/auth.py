from fastapi import APIRouter, Form, Depends, Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from passlib.hash import bcrypt

from backend.database.admin_db import AdminSessionLocal
from backend.models_admin.user import User
from backend.models_admin.store import Store   # ✅ Store 모델 import 필요
from backend.utils.jwt_handler import create_access_token
from backend.config.templates import templates

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
# 🔑 로그인 화면 (GET)
# ==========================
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("Login.html", {"request": request})

# ==========================
# 🔑 로그인 처리 (POST)
# ==========================
@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    response: Response,
    store_id: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.c_email == email).first()

    if not user:
        return HTMLResponse("❌ User not found", status_code=401)

    if not bcrypt.verify(password, user.c_password):
        return HTMLResponse("❌ Invalid password", status_code=401)

    if store_id != "admin" and user.i_store_id is not None and str(user.i_store_id) != store_id:
        return HTMLResponse("❌ Invalid store for this user", status_code=401)

    # ✅ JWT 발급
    token = create_access_token({
        "uid": user.i_user_id,
        "sub": user.c_email,
        "store": store_id,
        "role": user.i_role_id
    })

    # ✅ 역할별 리다이렉트 URL
    role_id = user.i_role_id
    if store_id == "admin":
        redirect_url = "/dashboard/admin"
    elif role_id == 1:
        redirect_url = "/dashboard/admin"
    elif role_id in [2, 3]:
        redirect_url = "/dashboard/store"
    elif role_id == 4:
        redirect_url = "/dashboard/staff"
    elif role_id == 5:
        redirect_url = "/dashboard/customer"
    else:
        redirect_url = "/"

    resp = RedirectResponse(url=f"/auth/redirector?target={redirect_url}", status_code=303)
    resp.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=False,  # 운영환경에서는 True + HTTPS
        samesite="lax"
    )
    return resp

# ==========================
# 📝 회원가입 처리 (POST)
# ==========================
@router.post("/register")
def register(
    request: Request,
    store_id: str = Form(...),
    store_pw: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    # 1) 비밀번호 확인
    if password != confirm_password:
        return JSONResponse({"success": False, "message": "❌ Passwords do not match"}, status_code=400)

    # 2) Store 인증 확인
    store = db.query(Store).filter(Store.c_store_code == store_id).first()
    if not store:
        return JSONResponse({"success": False, "message": "❌ Invalid Store ID"}, status_code=400)

    if not store.c_store_pw or not bcrypt.verify(store_pw, store.c_store_pw):
        return JSONResponse({"success": False, "message": "❌ Invalid Store Password"}, status_code=400)

    # 3) 이메일 중복 체크 (같은 매장 내에서만 중복 불가)
    if (
        db.query(User)
        .filter(User.i_store_id == store.i_store_id, User.c_email == email)
        .first()
    ):
        return JSONResponse({"success": False, "message": "❌ Email already registered in this store"}, status_code=400)

    # 4) User 등록
    new_user = User(
        i_store_id=store.i_store_id,
        c_email=email,
        c_password=bcrypt.hash(password),
        c_first_name=first_name,
        c_last_name=last_name,
        c_phone=phone,
        i_role_id=3,   # 기본 Role = Store User
        c_status="active"
    )
    db.add(new_user)
    db.commit()

    # ✅ JSON 성공 응답
    return JSONResponse({"success": True, "message": "✅ User registered successfully"})

# ==========================
# 🔑 Redirector (히스토리 초기화)
# ==========================
@router.get("/redirector", response_class=HTMLResponse)
def redirector(request: Request, target: str):
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset='utf-8'><title>Redirecting...</title></head>
    <body>
      <script>
        window.history.replaceState(null, "", "/");
        window.location.replace("{target}");
      </script>
      <p>Redirecting to dashboard...</p>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

# ==========================
# 🔑 로그아웃
# ==========================
@router.get("/logout")
def logout(response: Response):
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie("access_token")
    return resp
