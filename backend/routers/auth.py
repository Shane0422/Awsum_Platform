from datetime import datetime
from typing import Any

from fastapi import APIRouter, Form, Depends, Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
import bcrypt
import re
import logging
import json
from sqlalchemy.exc import SQLAlchemyError

from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.platform_user import PlatformUser, AGENCY_ADMIN_ROLE_NAME
from backend.models_admin.agency import Agency
from backend.models_admin.role import Role
from backend.models_admin.store import Store   # ✅ Store model import required
from backend.config.templates import templates
from backend.services import web_sessions
from fastapi import HTTPException

# Platform admin store ID (fixed)
ADMIN_STORE_ID = 1000001
PLATFORM_ADMIN_ACCESS_CODE = "ADMIN"
PLATFORM_ADMIN_ROLE_NAME = "superadmin"
MERCHANT_CODE_PATTERNS = (
    re.compile(r"^(?:STR|CLT)_?(\d{5})$", re.IGNORECASE),
    re.compile(r"^CUST_?(\d{5})$", re.IGNORECASE),
)

DASHBOARD_TYPE_TO_URL = {
    "PLATFORM": "/platform/dashboard",
    "STANDARD": "/merchant/dashboard/standard",
    "RESTAURANT": "/merchant/dashboard/restaurant",
    "DELI": "/merchant/dashboard/deli",
    "TUXEDO_RENTAL": "/merchant/dashboard/tuxedo",
}

# bcrypt max password length is 72 bytes; validate early for a clear error response
MAX_BCRYPT_PASSWORD_BYTES = 72
logger = logging.getLogger(__name__)


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

def password_matches(user: PlatformUser, password: str) -> bool:
    if not validate_bcrypt_password(password):
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), user.c_password.encode("utf-8"))
    except (ValueError, TypeError):
        logger.warning("Unable to verify password for user %s", user.i_platform_user_id)
        return False


def _finish_login(db: Session, user: PlatformUser, request: Request, claims: dict, target: str):
    try:
        token = web_sessions.create_session(db, user, request, claims)
    except web_sessions.ActiveSessionExists as exc:
        return _login_error(str(exc), status_code=409)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Unable to create web login session")
        return _login_error("An error occurred. Please try again later.", status_code=500)
    response = RedirectResponse(url="/change-password" if user.i_must_change_password == 1 else target, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(key="access_token", value=token, httponly=True, secure=False, samesite="lax")
    if not request.cookies.get("awsum_web_client"):
        response.set_cookie("awsum_web_client", request.state.web_client_id, httponly=True, samesite="lax", max_age=31536000)
    return response


def _normalize_dashboard_type(raw_value: str | None) -> str:
    value = (raw_value or "").strip().upper()
    if not value:
        return "STANDARD"
    if value not in DASHBOARD_TYPE_TO_URL:
        return "STANDARD"
    return value


def _extract_merchant_seq(store_code: str | None) -> int | None:
    if not store_code:
        return None
    normalized = store_code.strip().upper()
    for pattern in MERCHANT_CODE_PATTERNS:
        matched = pattern.match(normalized)
        if matched:
            return int(matched.group(1))
    return None


def _is_merchant_store_code(store_code: str | None) -> bool:
    return _extract_merchant_seq(store_code) is not None


def _is_test_store_code(store_code: str | None) -> bool:
    seq = _extract_merchant_seq(store_code)
    if seq is None:
        return False
    return 1 <= seq <= 99999


def _is_real_merchant_store_code(store_code: str | None) -> bool:
    seq = _extract_merchant_seq(store_code)
    if seq is None:
        return False
    return seq >= 11001


def _resolve_store_dashboard_redirect(db: Session, store_id: int) -> str:
    store_obj = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store_obj:
        return "/merchant/dashboard/standard"

    store_code = (store_obj.c_store_code or "").strip().upper()

        # Route merchant-affiliated stores by dashboard_type (default: STANDARD)
    if _is_merchant_store_code(store_code):
        # Explicit classification per policy for test/real customer stores.
        if _is_test_store_code(store_code) or _is_real_merchant_store_code(store_code):
            dashboard_type = _normalize_dashboard_type(store_obj.c_dashboard_type)
            return DASHBOARD_TYPE_TO_URL[dashboard_type]
        dashboard_type = _normalize_dashboard_type(store_obj.c_dashboard_type)
        return DASHBOARD_TYPE_TO_URL[dashboard_type]

    return "/merchant/dashboard/standard"


def _resolve_post_login_redirect(db: Session, user: Any, store_id: int) -> str:
    if store_id == ADMIN_STORE_ID:
        return "/platform/dashboard"

    store_obj = db.query(Store).filter(Store.i_store_id == store_id).first()
    if store_obj and _is_merchant_store_code(store_obj.c_store_code):
        return _resolve_store_dashboard_redirect(db, store_id)

    # Fallback for non-merchant stores (backwards compatibility)
    role_id = user.i_role_id
    if role_id in [2, 3]:
        return "/dashboard/store"
    if role_id == 4:
        return "/dashboard/staff"
    if role_id == 5:
        return "/dashboard/client"
    return "/"

router = APIRouter()
platform_admin_router = APIRouter(tags=["Platform Admin"])

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
    try:
        return web_sessions.authenticate(request, db, allow_password_change=True)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise


def _log_platform_forbidden(request: Request, db: Session, location: str, reason: str,
                            *, payload: dict | None = None, user: PlatformUser | None = None,
                            role: Role | None = None) -> None:
    from backend.models_admin.session import SessionTbl
    claims = payload if payload is not None else getattr(request.state, "platform_auth_diagnostics", {})
    session_key = claims.get("sid") or getattr(request.state, "web_session_key", None)
    with db.no_autoflush:
        session = db.query(SessionTbl).filter(SessionTbl.c_session_key == session_key).first() if session_key else None
        if user is None:
            identity = claims.get("uid")
            if isinstance(identity, int) or (isinstance(identity, str) and identity.isdecimal() and len(identity) <= 10):
                user = db.get(PlatformUser, int(identity))
        if role is None and user is not None:
            role = db.get(Role, user.i_role_id)

    def known(value, allowed):
        return value if isinstance(value, str) and value in allowed else ("other" if value is not None else None)

    token_role = claims.get("role_id")
    if not isinstance(token_role, int):
        token_role = int(token_role) if isinstance(token_role, str) and token_role.isdecimal() and len(token_role) <= 10 else None
    access_code = claims.get("access_code")
    logger.warning("PLATFORM_AUTH_FORBIDDEN %s", json.dumps({
        "path": request.url.path, "location": location, "reason": reason,
        "session_exists": session is not None,
        "login_context": known(session.c_login_context if session else claims.get("login_context"),
                               {"PLATFORM", "AGENCY", "MERCHANT", "STORE"}),
        "principal_type": known(claims.get("principal_type"), {"platform_user"}),
        "scope_type": known(claims.get("scope_type"), {"platform", "agency", "merchant", "store"}),
        "role_id": user.i_role_id if user is not None else token_role,
        "token_role_id": token_role,
        "user_status": known(user.c_status if user is not None else None, {"active", "inactive"}),
        "role_status": known(role.c_status if role is not None else None, {"active", "inactive"}),
        "access_code_matches": (
            isinstance(access_code, str) and access_code.strip().casefold() == PLATFORM_ADMIN_ACCESS_CODE.casefold()
            if payload is not None else claims.get("access_code_matches")
        ),
    }, ensure_ascii=True))


def get_platform_admin_session(request: Request, db: Session) -> tuple[str, PlatformUser | None]:
    token = request.cookies.get("access_token")
    if not token:
        return "anonymous", None

    from jose import JWTError, jwt
    from backend.utils.jwt_handler import SECRET_KEY, ALGORITHM

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return "anonymous", None

    request.state.platform_auth_diagnostics = {
        key: payload.get(key) for key in ("sid", "uid", "login_context", "principal_type", "scope_type", "role_id")
    }
    request.state.platform_auth_diagnostics["access_code_matches"] = (
        isinstance(payload.get("access_code"), str)
        and payload["access_code"].strip().casefold() == PLATFORM_ADMIN_ACCESS_CODE.casefold()
    )

    if payload.get("principal_type") != "platform_user":
        _log_platform_forbidden(request, db, "get_platform_admin_session", "principal_type_mismatch", payload=payload)
        return "forbidden", None
    if payload.get("scope_type") != "platform":
        _log_platform_forbidden(request, db, "get_platform_admin_session", "scope_type_mismatch", payload=payload)
        return "forbidden", None
    if (payload.get("access_code") or "").strip().casefold() != PLATFORM_ADMIN_ACCESS_CODE.casefold():
        _log_platform_forbidden(request, db, "get_platform_admin_session", "access_code_mismatch", payload=payload)
        return "forbidden", None

    try:
        token_role_id = int(payload.get("role_id"))
    except (TypeError, ValueError):
        return "anonymous", None

    try:
        user = web_sessions.authenticate(request, db, allow_password_change=True)
    except HTTPException:
        return "anonymous", None
    if not user:
        return "anonymous", None
    if (user.c_status or "").strip().lower() != "active":
        _log_platform_forbidden(request, db, "get_platform_admin_session", "user_inactive", payload=payload, user=user)
        return "forbidden", None
    if user.i_agency_id is not None:
        _log_platform_forbidden(request, db, "get_platform_admin_session", "agency_user", payload=payload, user=user)
        return "forbidden", None
    if user.i_merchant_id is not None:
        _log_platform_forbidden(request, db, "get_platform_admin_session", "merchant_user", payload=payload, user=user)
        return "forbidden", None

    try:
        if int(user.i_store_id) != ADMIN_STORE_ID:
            _log_platform_forbidden(request, db, "get_platform_admin_session", "store_id_mismatch", payload=payload, user=user)
            return "forbidden", None
    except (TypeError, ValueError):
        _log_platform_forbidden(request, db, "get_platform_admin_session", "invalid_store_id", payload=payload, user=user)
        return "forbidden", None

    role = db.query(Role).filter(Role.i_role_id == user.i_role_id).first()
    if not role or (role.c_status or "").strip().lower() != "active":
        _log_platform_forbidden(request, db, "get_platform_admin_session", "role_missing_or_inactive", payload=payload, user=user, role=role)
        return "forbidden", None
    if token_role_id != user.i_role_id:
        _log_platform_forbidden(request, db, "get_platform_admin_session", "token_role_mismatch", payload=payload, user=user, role=role)
        return "forbidden", None
    if (role.c_name or "").strip().casefold() != PLATFORM_ADMIN_ROLE_NAME:
        _log_platform_forbidden(request, db, "get_platform_admin_session", "role_name_mismatch", payload=payload, user=user, role=role)
        return "forbidden", None

    return "authorized", user


def _platform_admin_login_page(request: Request, db: Session):
    from backend.services.branding import resolve_branding
    request.state.login_branding = resolve_branding(db, "PLATFORM")
    return templates.TemplateResponse(
        request,
        "platform/platform_home.html",
        {"request": request, "login_mode": "platform_admin"},
    )


@platform_admin_router.get("/platform-admin", response_class=HTMLResponse)
def platform_admin_login_page(request: Request, db: Session = Depends(get_db)):
    resp = _platform_admin_login_page(request, db)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@platform_admin_router.post("/platform-admin/login", response_class=HTMLResponse)
def platform_admin_login(
    request: Request,
    access_code: str = Form(...),
    login_mode: str = Form("platform_admin"),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if login_mode != "platform_admin":
        return _login_error("Invalid login mode.", status_code=400)
    if (access_code or "").strip().casefold() != PLATFORM_ADMIN_ACCESS_CODE.casefold():
        return _login_error("Invalid credentials.", status_code=401)

    email_norm = normalize_email(email)
    try:
        user = (
            db.query(PlatformUser)
            .filter(func.lower(PlatformUser.c_email) == email_norm,
                    PlatformUser.i_store_id == ADMIN_STORE_ID, PlatformUser.i_agency_id.is_(None),
                    PlatformUser.i_merchant_id.is_(None))
            .first()
        )
        role = db.query(Role).filter(Role.i_role_id == user.i_role_id).first() if user else None
    except Exception:
        return _login_error("An error occurred. Please try again later.", status_code=500)

    if not user or (user.c_status or "").strip().lower() != "active":
        return _login_error("Invalid credentials.", status_code=401)
    if not validate_bcrypt_password(password):
        return _login_error(f"Password is too long (max {MAX_BCRYPT_PASSWORD_BYTES} bytes).", status_code=400)

    if not password_matches(user, password):
        return _login_error("Invalid credentials.", status_code=401)
    if not role or (role.c_status or "").strip().lower() != "active":
        return _login_error("This account does not have an active Platform administrator Role.", status_code=403)
    if (role.c_name or "").strip().casefold() != PLATFORM_ADMIN_ROLE_NAME:
        return _login_error("This account does not have Platform administrator permission.", status_code=403)

    claims = {
        "uid": user.i_platform_user_id,
        "user_id": user.i_platform_user_id,
        "sub": user.c_email,
        "email": user.c_email,
        "store": ADMIN_STORE_ID,
        "role_id": user.i_role_id,
        "role": role.c_name,
        "scope_type": "platform",
        "access_code": PLATFORM_ADMIN_ACCESS_CODE,
        "principal_type": "platform_user",
    }

    return _finish_login(db, user, request, claims, "/platform/dashboard")

# ==========================
# 🔑 Login Page (GET)
# ==========================
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    suffix = "?reason=session_expired" if request.query_params.get("reason") == "session_expired" else ""
    return RedirectResponse("/login" + suffix, status_code=303)


@router.get("/branding")
def login_branding(access_code: str, context_type: str | None = None, db: Session = Depends(get_db)):
    from backend.services.branding import resolve_access_code
    response = JSONResponse(resolve_access_code(db, access_code, context_type).public())
    response.headers["Cache-Control"] = "no-store"
    return response


@platform_admin_router.get("/login", response_class=HTMLResponse)
@platform_admin_router.get("/login/{context_type}/{access_code}", response_class=HTMLResponse)
def organization_login_page(
    request: Request, context_type: str | None = None, access_code: str | None = None,
    db: Session = Depends(get_db),
):
    from backend.services.branding import resolve_access_code, resolve_branding
    brand = resolve_access_code(db, access_code, context_type) if access_code else resolve_branding(db)
    if context_type and context_type.upper() not in {"AGENCY", "MERCHANT", "STORE"}:
        raise HTTPException(404, "Login entry not found.")
    request.state.login_branding = brand
    response = templates.TemplateResponse(
        request, "platform/platform_home.html",
        {"request": request, "login_mode": "general", "login_entry_page": True,
         "login_entry_fixed": bool(access_code)},
    )
    response.headers["Cache-Control"] = "no-store"
    return response

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

    if store_identifier_norm in {"AWSUM", PLATFORM_ADMIN_ACCESS_CODE}:
        return _login_error("Platform administrators must sign in at /platform-admin.", status_code=400)

    from backend.services.organization_users import REGISTRATION_OWNERS, organization_admin_query
    for owner_type, (model, field, code_field, _, _) in REGISTRATION_OWNERS.items():
        owner = db.query(model).filter(
            func.upper(getattr(model, code_field)) == store_identifier_norm, model.c_status == "active",
        ).first()
        if owner is None:
            continue
        owner_id = getattr(owner, field)
        if owner_type == "STORE" and owner_id == ADMIN_STORE_ID:
            return _login_error("Invalid credentials.")
        user = organization_admin_query(db, owner_type, owner_id).filter(
            func.lower(PlatformUser.c_email) == normalize_email(email),
        ).first()
        role = db.get(Role, user.i_role_id) if user else None
        if (not user or user.c_status != "active" or not role or role.c_status != "active"
                or not password_matches(user, password)):
            return _login_error("Invalid credentials.")
        claims = {
            "uid": user.i_platform_user_id, "sub": user.c_email, "email": user.c_email,
            "store": user.i_store_id, "role_id": user.i_role_id, "role": role.c_name,
            "scope_type": owner_type.lower(), f"{owner_type.lower()}_id": owner_id,
            "access_code": getattr(owner, code_field), "principal_type": "platform_user",
        }
        return _finish_login(db, user, request, claims, _organization_login_target(db, user))

    # Store operation-user authentication remains unchanged.
    return _login_error(
        "Platform DB does not manage Store users. Please use your Merchant/Store operation DB login path.",
        status_code=400,
    )


def _organization_login_target(db: Session, user: PlatformUser) -> str:
    if user.i_agency_id is not None:
        return "/agency/dashboard"
    if user.i_merchant_id is not None:
        return "/merchant/dashboard/standard"
    from backend.models_admin.platform_user import STORE_ADMIN_ROLE_NAME
    role = db.get(Role, user.i_role_id)
    if role and role.c_name == STORE_ADMIN_ROLE_NAME:
        return _resolve_store_dashboard_redirect(db, user.i_store_id)
    return _resolve_post_login_redirect(db, user, user.i_store_id)


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request, db: Session = Depends(get_db)):
    user = _get_authenticated_user(request, db)
    if not user:
        return RedirectResponse(url="/platform-admin" if _is_platform_admin_logout(request) else "/", status_code=303)

    return templates.TemplateResponse(
        request,
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
        return RedirectResponse(url="/platform-admin" if _is_platform_admin_logout(request) else "/", status_code=303)

    if new_password != confirm_password:
        return templates.TemplateResponse(
            request,
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
            request,
            "platform/change_password.html",
            {
                "request": request,
                "user": user,
                "must_change": user.i_must_change_password == 1,
                "error": f"New password too long (max {MAX_BCRYPT_PASSWORD_BYTES} bytes).",
            },
            status_code=400,
        )

    if not password_matches(user, current_password):
        return templates.TemplateResponse(
            request,
            "platform/change_password.html",
            {
                "request": request,
                "user": user,
                "must_change": user.i_must_change_password == 1,
                "error": "Current password is invalid.",
            },
            status_code=401,
        )

    if user.i_must_change_password == 1 and password_matches(user, new_password):
        return templates.TemplateResponse(
            request, "platform/change_password.html",
            {"request": request, "user": user, "must_change": True,
             "error": "Choose a password different from the temporary password."},
            status_code=400,
        )

    user.c_password = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    user.i_must_change_password = 0
    user.dt_password_changed = datetime.now()
    if hasattr(user, "dt_updated"):
        user.dt_updated = datetime.now()
    if hasattr(user, "i_updated_by") and hasattr(user, "i_platform_user_id"):
        user.i_updated_by = user.i_platform_user_id
    db.commit()

    return RedirectResponse(url=_organization_login_target(db, user), status_code=303)


@router.get("/registration-completed", response_class=HTMLResponse)
def registration_completed(request: Request, db: Session = Depends(get_db)):
    user = web_sessions.authenticate(request, db, allow_password_change=True)
    if user.i_agency_id is None:
        raise HTTPException(403, "Agency login required.")
    if user.i_must_change_password == 1:
        return RedirectResponse("/change-password", status_code=303)
    return RedirectResponse("/agency/dashboard", status_code=303, headers={"Cache-Control": "no-store"})

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
            "message": "Platform DB does not register operation users. Register users in each Merchant/Store operation DB.",
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
def _is_platform_admin_logout(request: Request) -> bool:
    """True when the session being closed belongs to the HQ admin (token, or Referer if the token already expired)."""
    from urllib.parse import urlparse
    from jose import JWTError, jwt
    from backend.utils.jwt_handler import SECRET_KEY, ALGORITHM

    token = request.cookies.get("access_token")
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return (
                payload.get("principal_type") == "platform_user"
                and payload.get("scope_type") == "platform"
                and (payload.get("access_code") or "").strip().casefold() == PLATFORM_ADMIN_ACCESS_CODE.casefold()
            )
        except JWTError:
            pass
    try:
        path = urlparse(request.headers.get("referer") or "").path or ""
    except ValueError:
        return False
    return path == "/platform" or path.startswith("/platform/") or path.startswith("/platform-admin")


@router.get("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    from backend.services.web_session_http import session_login_path
    target = session_login_path(request, db)
    web_sessions.logout_session(request, db)
    resp = RedirectResponse(url=target, status_code=303)
    resp.delete_cookie("access_token")
    resp.delete_cookie("platform_dashboard_first_logout_done")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp
