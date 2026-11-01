"""HTTP integration for all common web sessions, independent of module routes."""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from backend.database.pg_platform import PlatformSessionLocal
from backend.services.web_sessions import authenticate
from backend.services.branding import valid_login_entry
from backend.models_admin.session import SessionTbl


DEVICE_PATHS = frozenset({
    "/platform/device/activate", "/platform/device/validate", "/platform/license/activate",
})
PUBLIC_PATHS = frozenset({
    "/", "/platform-admin", "/platform-admin/login", "/auth/login", "/auth/logout",
    "/auth/register",
    "/auth/branding", "/login", "/favicon.ico",
})


def _validate(request):
    with PlatformSessionLocal() as db:
        return authenticate(request, db, allow_password_change=True)


def session_login_path(request: Request, db) -> str:
    # Signature is still verified; stale/expired sessions can select a login page,
    # but never obtain authorization this way.
    from backend.services.web_sessions import decode_web_token
    try:
        payload = decode_web_token(request.cookies.get("access_token") or "")
        row = db.query(SessionTbl).filter(
            SessionTbl.c_session_key == payload["sid"],
            SessionTbl.i_platform_user_id == int(payload["uid"]),
        ).first()
        if row and valid_login_entry(row.c_login_path):
            return row.c_login_path
        if payload.get("scope_type") == "platform":
            return "/platform-admin"
        entry = payload.get("login_entry")
        if valid_login_entry(entry):
            return entry
    except HTTPException:
        pass
    if request.url.path.startswith(("/platform", "/dashboard/admin")):
        return "/platform-admin"
    from backend.routers.auth import _is_platform_admin_logout
    return "/platform-admin" if _is_platform_admin_logout(request) else "/login"


def login_path(request: Request) -> str:
    with PlatformSessionLocal() as db:
        return session_login_path(request, db)


def session_error_response(request: Request, message: str):
    target = login_path(request) + "?reason=session_expired"
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        response = RedirectResponse(target, status_code=303)
    else:
        response = JSONResponse(
            {"detail": message, "code": "SESSION_ENDED", "login_url": target},
            status_code=401, headers={"X-Awsum-Login": target},
        )
    response.delete_cookie("access_token")
    response.headers["Cache-Control"] = "no-store"
    return response


async def enforce_web_session(request: Request, call_next):
    path = request.url.path
    from backend.services.images import ImagePath, PUBLIC_CATEGORIES, legacy_store_image_path
    try:
        image = ImagePath.parse(path)
    except ValueError:
        image = legacy_store_image_path(path, "LEGACY")
        public_image = request.method == "GET" and image is not None and image.image_category in PUBLIC_CATEGORIES
    else:
        public_image = request.method == "GET" and image.image_category in PUBLIC_CATEGORIES
    if (
        path not in PUBLIC_PATHS and path not in DEVICE_PATHS
        and not public_image
        and not path.startswith("/login/")
        and not path.startswith("/static/")
        and not path.startswith("/images/platform/")
        and request.cookies.get("access_token")
    ):
        try:
            await run_in_threadpool(_validate, request)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            return session_error_response(request, str(exc.detail))
    return await call_next(request)
