"""Shared web-user sessions. Device licensing and activation never use this service."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.role import Role
from backend.models_admin.session import SessionTbl
from backend.services.session_context import login_identity
from backend.services.session_metadata import TrustedClientMetadata, collect_session_metadata
from backend.utils.jwt_handler import ALGORITHM, SECRET_KEY, create_access_token


ALREADY_SIGNED_IN = (
    "This account is already signed in on another device. "
    "Please sign out from the other device first or contact an administrator."
)
SESSION_ENDED = "Your session has ended or expired. Please sign in again."


class ActiveSessionExists(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _minutes(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be a positive number of minutes.")
    return value


def idle_timeout() -> timedelta:
    return timedelta(minutes=_minutes("AWSUM_WEB_SESSION_IDLE_MINUTES", 30))


def max_minutes() -> int:
    return _minutes("AWSUM_WEB_SESSION_MAX_MINUTES", 480)


def expire_sessions(db: Session, *, user_id: int | None = None, now: datetime | None = None) -> int:
    now = now or utcnow()
    query = db.query(SessionTbl).filter(
        SessionTbl.c_status == "ACTIVE",
        or_(
            SessionTbl.dt_expires.is_(None), SessionTbl.dt_last_active.is_(None),
            SessionTbl.dt_expires <= now, SessionTbl.dt_last_active <= now - idle_timeout(),
        ),
    )
    if user_id is not None:
        query = query.filter(SessionTbl.i_platform_user_id == user_id)
    return query.update({"c_status": "EXPIRED", "dt_updated": now}, synchronize_session=False)


def create_session(
    db: Session, user: PlatformUser, request: Request, claims: dict,
    *, trusted_client: TrustedClientMetadata | None = None,
) -> str:
    """Call only after validating credentials and login permissions. Commits atomically."""
    now = utcnow()
    # Lock the persistent user, including when that user has no session yet.
    locked_user = db.query(PlatformUser).filter(
        PlatformUser.i_platform_user_id == user.i_platform_user_id,
    ).with_for_update().one()
    identity = login_identity(db, locked_user, claims)
    client_id = (request.cookies.get("awsum_web_client") or "")[:128] or str(uuid4())
    request.state.web_client_id = client_id
    expire_sessions(db, user_id=locked_user.i_platform_user_id, now=now)
    active_sessions = db.query(SessionTbl).filter(
        SessionTbl.i_platform_user_id == locked_user.i_platform_user_id,
        SessionTbl.c_status == "ACTIVE",
    ).all()
    if any(active.c_client_id != client_id for active in active_sessions):
        db.commit()  # persist expiry transitions, never terminate an active session
        raise ActiveSessionExists(ALREADY_SIGNED_IN)
    for active_session in active_sessions:
        _mark_session_terminated(active_session, locked_user.i_platform_user_id, now)
    # Release the ACTIVE uniqueness slot before inserting the replacement.
    db.flush()
    session_key = str(uuid4())
    lifetime = max_minutes()
    from backend.services.branding import identity_login_entry
    login_entry = identity_login_entry(db, identity)
    row = SessionTbl(
        i_platform_user_id=locked_user.i_platform_user_id, **identity,
        c_session_key=session_key, c_jwt_token="", c_status="ACTIVE",
        c_client_id=client_id,
        **collect_session_metadata(request, trusted_client=trusted_client),
        c_login_path=login_entry,
        dt_login=now, dt_last_active=now, dt_expires=now + timedelta(minutes=lifetime),
        dt_created=now, i_created_by=locked_user.i_platform_user_id,
    )
    db.add(row)
    token = create_access_token({
        **claims, "sid": session_key, "login_context": identity["c_login_context"],
        "login_entry": login_entry,
        "merchant_id": identity["i_account_id"], "agency_id": identity["i_agency_id"],
        "uid": locked_user.i_platform_user_id, "user_id": locked_user.i_platform_user_id,
        "store": locked_user.i_store_id, "role_id": locked_user.i_role_id,
        "principal_type": "platform_user",
    }, expires_minutes=lifetime)
    locked_user.dt_last_login = now
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # A partial UNIQUE index also protects against writers not taking the user lock.
        if db.query(SessionTbl.i_session_id).filter(
            SessionTbl.i_platform_user_id == user.i_platform_user_id,
            SessionTbl.c_status == "ACTIVE",
        ).first():
            raise ActiveSessionExists(ALREADY_SIGNED_IN) from exc
        raise
    return token


def decode_web_token(token: str) -> dict:
    try:
        # Session expiry is checked below so the row can be marked EXPIRED too.
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
        if payload.get("principal_type", "platform_user") != "platform_user":
            raise ValueError("Wrong principal")
        if not isinstance(payload.get("sid"), str) or not payload["sid"]:
            raise ValueError("Missing server session")
        int(payload["uid"])
        int(payload["role_id"])
        int(payload["store"])
        float(payload["exp"])
        return payload
    except (JWTError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=401, detail=SESSION_ENDED) from exc


def authenticate(request: Request, db: Session, *, allow_password_change=False) -> PlatformUser:
    cached = getattr(request.state, "web_user", None)
    if cached is not None:
        if cached.i_must_change_password == 1 and not allow_password_change:
            raise HTTPException(status_code=403, detail="Password change required.")
        return cached
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    payload = decode_web_token(token)
    user = db.get(PlatformUser, int(payload["uid"]))
    role = db.get(Role, user.i_role_id) if user else None
    if (
        user is None or (user.c_status or "").lower() != "active"
        or not role or (role.c_status or "").lower() != "active"
        or user.i_role_id != int(payload["role_id"]) or user.i_store_id != int(payload["store"])
    ):
        raise HTTPException(status_code=401, detail=SESSION_ENDED)
    row = db.query(SessionTbl).filter(
        SessionTbl.c_session_key == payload["sid"],
        SessionTbl.i_platform_user_id == user.i_platform_user_id,
    ).with_for_update().first()
    if row is None or row.c_status != "ACTIVE":
        db.rollback()
        raise HTTPException(status_code=401, detail=SESSION_ENDED)
    expected_context = str(payload.get("scope_type") or "").upper()
    if (
        row.c_login_context not in {"PLATFORM", "AGENCY", "MERCHANT", "STORE"}
        or row.c_login_context != expected_context
        or payload.get("login_context", expected_context) != row.c_login_context
        or (row.c_login_context == "STORE" and row.i_store_id != user.i_store_id)
        or (row.c_login_context != "STORE" and row.i_store_id is not None)
        or (row.c_login_context == "MERCHANT" and (
            row.i_account_id is None or row.i_account_id != payload.get("merchant_id")
        ))
        or (row.c_login_context == "AGENCY" and (
            row.i_agency_id is None or row.i_agency_id != payload.get("agency_id")
        ))
    ):
        db.rollback()
        raise HTTPException(status_code=401, detail=SESSION_ENDED)
    if expected_context in {"AGENCY", "PLATFORM"}:
        login_identity(db, user, payload)
    now = utcnow()
    if (
        row.dt_expires is None or row.dt_expires <= now
        or row.dt_last_active is None or row.dt_last_active <= now - idle_timeout()
        or float(payload["exp"]) <= now.replace(tzinfo=timezone.utc).timestamp()
    ):
        row.c_status = "EXPIRED"
        row.dt_updated = now
        db.commit()
        raise HTTPException(status_code=401, detail=SESSION_ENDED)
    row.dt_last_active = now
    db.commit()
    user.login_store = user.i_store_id
    user.login_role = user.i_role_id
    request.state.web_user = user
    request.state.web_session_key = payload["sid"]
    request.state.web_login_path = row.c_login_path or "/"
    if user.i_must_change_password == 1 and not allow_password_change:
        raise HTTPException(status_code=403, detail="Password change required.")
    return user


def logout_session(request: Request, db: Session) -> None:
    token = request.cookies.get("access_token")
    if not token:
        return
    try:
        payload = decode_web_token(token)
    except HTTPException:
        return  # invalid/legacy tokens have no server session to close
    expire_sessions(db, user_id=int(payload["uid"]))
    now = utcnow()
    db.query(SessionTbl).filter(
        SessionTbl.c_session_key == payload["sid"],
        SessionTbl.i_platform_user_id == int(payload["uid"]),
        SessionTbl.c_status == "ACTIVE",
    ).update({"c_status": "LOGGED_OUT", "dt_logout": now, "dt_updated": now}, synchronize_session=False)
    db.commit()


def _mark_session_terminated(row: SessionTbl, actor_id: int, now: datetime) -> None:
    row.c_status = "TERMINATED"
    row.dt_terminated = now
    row.i_terminated_by = actor_id
    row.i_updated_by = actor_id
    row.dt_updated = now


def terminate_session(db: Session, session_id: int, actor_id: int) -> None:
    expire_sessions(db)
    row = db.query(SessionTbl).filter(SessionTbl.i_session_id == session_id).with_for_update().first()
    if row is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Session not found.")
    if row.c_status != "ACTIVE":
        db.commit()
        raise HTTPException(status_code=409, detail="Only ACTIVE sessions can be terminated.")
    _mark_session_terminated(row, actor_id, utcnow())
    db.commit()
