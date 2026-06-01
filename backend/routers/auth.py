from datetime import datetime
from typing import Any

from fastapi import APIRouter, Form, Depends, Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
import bcrypt
import re

from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.store import Store   # ✅ Store model import required
from backend.utils.jwt_handler import create_access_token
from backend.config.templates import templates

# Platform admin store ID (fixed)
ADMIN_STORE_ID = 1000001
CLIENT_CODE_PATTERNS = (
    re.compile(r"^STR_(\d{5})$", re.IGNORECASE),   # Current store code format
    re.compile(r"^CLT_(\d{5})$", re.IGNORECASE),   # Legacy store code format
    re.compile(r"^CUST_(\d{5})$", re.IGNORECASE),  # Legacy support
)

DASHBOARD_TYPE_TO_URL = {
    "PLATFORM": "/platform/dashboard",
    "STANDARD": "/client/dashboard/standard",
    "RESTAURANT": "/client/dashboard/restaurant",
    "DELI": "/client/dashboard/deli",
    "TUXEDO_RENTAL": "/client/dashboard/tuxedo",
}

# bcrypt max password length is 72 bytes; validate early for a clear error response
MAX_BCRYPT_PASSWORD_BYTES = 72


def _login_error(message: str, status_code: int = 401) -> JSONResponse:
    return JSONResponse({"success": False, "message": message}, status_code=status_code)

def validate_bcrypt_password(password: str, field_name: str = "password") -> bool:
    """Returns True if the password is within the 72-byte bcrypt limit, False otherwise."""
    if len(password.encode("utf-8")) > MAX_BCRYPT_PASSWORD_BYTES:
        return False
    return True


def normalize_email(email: str) -> str:
    """Normalises the email address to lowercase with leading/trailing whitespace stripped."""
    return (email or "").strip().lower()


def _normalize_dashboard_type(raw_value: str | None) -> str:
    value = (raw_value or "").strip().upper()
    if not value:
        return "STANDARD"
    if value not in DASHBOARD_TYPE_TO_URL:
        return "STANDARD"
    return value


def _extract_client_seq(store_code: str | None) -> int | None:
    if not store_code:
        return None
    normalized = store_code.strip().upper()
    for pattern in CLIENT_CODE_PATTERNS:
        matched = pattern.match(normalized)
        if matched:
            return int(matched.group(1))
    return None


def _is_client_store_code(store_code: str | None) -> bool:
    return _extract_client_seq(store_code) is not None


def _is_test_store_code(store_code: str | None) -> bool:
    seq = _extract_client_seq(store_code)
    if seq is None:
        return False
    return 1 <= seq <= 99999


def _is_real_client_store_code(store_code: str | None) -> bool:
    seq = _extract_client_seq(store_code)
    if seq is None:
        return False
    return seq >= 11001


def _resolve_store_dashboard_redirect(db: Session, store_id: int) -> str:
    store_obj = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store_obj:
        return "/client/dashboard/standard"

    store_code = (store_obj.c_store_code or "").strip().upper()

        # Route client-affiliated stores by dashboard_type (default: STANDARD)
    if _is_client_store_code(store_code):
        # Explicit classification per policy for test/real customer stores.
        if _is_test_store_code(store_code) or _is_real_client_store_code(store_code):
            dashboard_type = _normalize_dashboard_type(store_obj.c_dashboard_type)
            return DASHBOARD_TYPE_TO_URL[dashboard_type]
        dashboard_type = _normalize_dashboard_type(store_obj.c_dashboard_type)
        return DASHBOARD_TYPE_TO_URL[dashboard_type]

    return "/client/dashboard/standard"


def _resolve_post_login_redirect(db: Session, user: Any, store_id: int) -> str:
    if store_id == ADMIN_STORE_ID:
        return "/platform/dashboard"

    store_obj = db.query(Store).filter(Store.i_store_id == store_id).first()
    if store_obj and _is_client_store_code(store_obj.c_store_code):
        return _resolve_store_dashboard_redirect(db, store_id)

    # Fallback for non-client stores (backwards compatibility)
    role_id = user.i_role_id
    if role_id in [2, 3]:
        return "/dashboard/store"
    if role_id == 4:
        return "/dashboard/staff"
    if role_id == 5:
        return "/dashboard/client"
    return "/"

router = APIRouter()

# ==========================
# DB Helpers
# ==========================
def get_db():
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_authenticated_user(request: Request, db: Session):
    token = request.cookies.get("access_token")
    if not token:
        return None

    from jose import JWTError, jwt
    from backend.utils.jwt_handler import SECRET_KEY, ALGORITHM

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid = payload.get("uid")
        principal_type = payload.get("principal_type", "platform_user")
        if uid is None:
            return None
    except JWTError:
        return None

    if principal_type == "platform_user":
        return db.query(PlatformUser).filter(PlatformUser.i_platform_user_id == uid).first()

    return None

# ==========================
# 🔑 Login Page (GET)
# ==========================
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    reason = (request.query_params.get("reason") or "").strip().lower()
    notice_map = {
        "session_expired": "Your session has expired. Please log in again.",
        "platform_admin_required": "Platform admin access is required to open that page.",
    }
    login_notice = notice_map.get(reason)

    return templates.TemplateResponse("platform/login.html", {
        "request": request,
        "login_notice": login_notice,
        "force_open_login_modal": bool(login_notice),
    })

# ==========================
# 🔑 Login (POST)
# ==========================
@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    response: Response,
    store_code: str = Form(None),
    store_id: str = Form(None),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # Normalise store identifier: strip whitespace, case-insensitive comparison
    store_identifier = (store_code or store_id or "").strip()
    store_identifier_norm = store_identifier.upper()
    if not store_identifier:
        return _login_error("Invalid credentials.", status_code=400)

    # Normalise email for case-insensitive login
    email_norm = normalize_email(email)

    # Admin login is fully isolated; never falls back to regular store logic
    if store_identifier_norm == "ADMIN":
        try:
            user = (
                db.query(PlatformUser)
                .filter(func.lower(PlatformUser.c_email) == email_norm, PlatformUser.i_store_id == ADMIN_STORE_ID)
                .first()
            )
        except Exception:
            return _login_error("An error occurred. Please try again later.", status_code=500)

        # bcrypt 72-byte limit; validate before running the hash check
        if not validate_bcrypt_password(password):
            return _login_error(
                f"Password is too long (max {MAX_BCRYPT_PASSWORD_BYTES} bytes). Please use a shorter password.",
                status_code=400,
            )

        password_ok = False
        if user:
            try:
                password_ok = bcrypt.checkpw(password.encode("utf-8"), user.c_password.encode("utf-8"))
            except ValueError:
                password_ok = False

        if not user or not password_ok:
            return _login_error("Invalid credentials.", status_code=401)

        token = create_access_token({
            "uid": user.i_platform_user_id,
            "sub": user.c_email,
            "store": ADMIN_STORE_ID,
            "role": 1,
            "principal_type": "platform_user",
        })

        if user.i_must_change_password == 1:
            redirect_url = "/change-password"
        else:
            redirect_url = "/platform/dashboard"

        resp = RedirectResponse(url=f"/auth/redirector?target={redirect_url}", status_code=303)
        resp.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            secure=False,
            samesite="lax"
        )
        return resp

    # Non-admin login paths are intentionally blocked in this platform DB auth endpoint.
    return _login_error(
        "Platform DB does not manage Store users. Please use your Client/Store operation DB login path.",
        status_code=400,
    )


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request, db: Session = Depends(get_db)):
    user = _get_authenticated_user(request, db)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "platform/change_password.html",
        {
            "request": request,
            "user": user,
            "must_change": user.i_must_change_password == 1,
        },
    )


@router.post("/change-password", response_class=HTMLResponse)
def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_authenticated_user(request, db)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    if new_password != confirm_password:
        return templates.TemplateResponse(
            "platform/change_password.html",
            {
                "request": request,
                "user": user,
                "must_change": user.i_must_change_password == 1,
                "error": "Passwords do not match.",
            },
            status_code=400,
        )

    if not validate_bcrypt_password(new_password):
        return templates.TemplateResponse(
            "platform/change_password.html",
            {
                "request": request,
                "user": user,
                "must_change": user.i_must_change_password == 1,
                "error": f"New password too long (max {MAX_BCRYPT_PASSWORD_BYTES} bytes).",
            },
            status_code=400,
        )

    try:
        current_ok = bcrypt.checkpw(current_password.encode("utf-8"), user.c_password.encode("utf-8"))
    except Exception:
        current_ok = False

    if not current_ok:
        return templates.TemplateResponse(
            "platform/change_password.html",
            {
                "request": request,
                "user": user,
                "must_change": user.i_must_change_password == 1,
                "error": "Current password is invalid.",
            },
            status_code=401,
        )

    user.c_password = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    user.i_must_change_password = 0
    user.dt_password_changed = datetime.now()
    if hasattr(user, "dt_updated"):
        user.dt_updated = datetime.now()
    if hasattr(user, "i_updated_by") and hasattr(user, "i_platform_user_id"):
        user.i_updated_by = user.i_platform_user_id
    db.commit()

    if isinstance(user, PlatformUser):
        return RedirectResponse(url="/platform/dashboard", status_code=303)

    return RedirectResponse(url=_resolve_post_login_redirect(db, user, user.i_store_id), status_code=303)

# ==========================
# 📝 Register (POST)
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
    return JSONResponse(
        {
            "success": False,
            "message": "Platform DB does not register operation users. Register users in each Client/Store operation DB.",
        },
        status_code=410,
    )

# ==========================
# 🔑 Redirector (clears browser history)
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
# 🔑 Logout
# ==========================
@router.get("/logout")
def logout(response: Response):
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie("access_token")
    resp.delete_cookie("platform_dashboard_first_logout_done")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp
