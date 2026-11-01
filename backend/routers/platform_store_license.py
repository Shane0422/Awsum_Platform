"""Store Devices & Licenses: contracted quantities, license issuance, activation and device replacement.

Concepts
- License: a usage right (POS / KIOSK / CUSTOMER_ORDERING service) granted to a store.
- Device: a physical unit. It only exists after a field activation call; nothing is pre-created.
- LicenseDeviceLink: history of which device held which license (one active link per license/device).

Every mutation takes a row lock on the store (SELECT ... FOR UPDATE) so concurrent requests are serialized.
"""
import hashlib
import json
import re
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models_admin.account import Account
from backend.models_admin.agency import Agency
from backend.models_admin.business_type import BusinessType
from backend.models_admin.contract import Contract
from backend.models_admin.device import Device
from backend.models_admin.license import License
from backend.models_admin.license_device_link import LicenseDeviceLink
from backend.models_admin.license_email_log import LicenseEmailLog
from backend.models_admin.store import Store
from backend.models_admin.store_entitlement import StoreEntitlement
from backend.models_admin.subscription import Subscription
from backend.services.license_email import (
    build_license_email,
    load_company_branding,
    render_license_email_preview,
    send_license_email,
    validate_email_content,
)
from backend.services.mail_common import EmailConfigurationError, MailDeliveryError, validate_email_address
from backend.routers.platform_store import (
    _actor_label,
    _generate_store_device_code,
    _log_device_event,
    _resolve_request_ip,
    _serialize_device_log,
    _set_no_cache_headers,
    _stamp_updated,
    get_current_user,
    get_db,
    require_platform_admin,
)
from backend.models_admin.device_log import DeviceLog

router = APIRouter()

TYPE_POS = "POS"
TYPE_KIOSK = "KIOSK"
TYPE_ORDERING = "CUSTOMER_ORDERING"
DEVICE_LICENSE_TYPES = (TYPE_POS, TYPE_KIOSK)
ISSUABLE_TYPES = (TYPE_POS, TYPE_KIOSK, TYPE_ORDERING)
MAX_QTY = 999
MAX_ISSUE_PER_REQUEST = 100
# Licenses in these states no longer count against the contracted quantity.
RELEASED_STATUSES = {"revoked", "expired", "terminated", "cancelled", "inactive"}
ACTIVATABLE_STATUSES = {"active", "trial"}
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _license_type(lic: License) -> str:
    return str(lic.c_license_type or "").strip().upper()


def _is_expired(lic: License) -> bool:
    return bool(lic.dt_end and lic.dt_end < datetime.now())


def _consumes_quota(lic: License) -> bool:
    status = str(lic.c_status or "active").strip().lower()
    return status not in RELEASED_STATUSES and not _is_expired(lic)


def _license_label(lic: License) -> str:
    ltype = _license_type(lic)
    if lic.i_slot_no and ltype in DEVICE_LICENSE_TYPES:
        return f"{ltype}-{int(lic.i_slot_no):02d}"
    if ltype == TYPE_ORDERING:
        return "Customer Ordering"
    return f"{ltype or 'LICENSE'}-{lic.i_license_id}"


def _license_state(lic: License, has_active_device: bool) -> tuple[str, str]:
    """Returns (code, label) shown in the Status column."""
    status = str(lic.c_status or "active").strip().lower()
    if _is_expired(lic):
        return "expired", "Expired"
    if status in RELEASED_STATUSES:
        return status, status.title()
    if status == "suspended":
        return "suspended", "Suspended"
    if _license_type(lic) == TYPE_ORDERING:
        return "active", "Active"
    return ("activated", "Activated") if has_active_device else ("issued", "Not Activated")


def _lock_store(db: Session, store_id: int) -> Store:
    store = db.query(Store).filter(Store.i_store_id == store_id).with_for_update().first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")
    return store


def _get_store(db: Session, store_id: int) -> Store:
    store = db.query(Store).filter(Store.i_store_id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found.")
    return store


def _get_entitlement(db: Session, store_id: int, *, create: bool = False) -> Optional[StoreEntitlement]:
    ent = db.query(StoreEntitlement).filter(StoreEntitlement.i_store_id == store_id).first()
    if ent is None and create:
        ent = StoreEntitlement(i_store_id=store_id, i_pos_qty=0, i_kiosk_qty=0, b_customer_ordering=False)
        db.add(ent)
        db.flush()
    return ent


def _active_links(db: Session, store_id: int) -> dict[int, LicenseDeviceLink]:
    rows = (
        db.query(LicenseDeviceLink)
        .filter(LicenseDeviceLink.i_store_id == store_id, LicenseDeviceLink.dt_unlinked_at.is_(None))
        .all()
    )
    return {row.i_license_id: row for row in rows}


def _os_text(device: Device) -> Optional[str]:
    parts = [str(device.c_os or "").strip(), str(device.c_os_version or "").strip()]
    text_value = " ".join(p for p in parts if p)
    return text_value or None


def _serialize_license_row(lic: License, link: Optional[LicenseDeviceLink], device: Optional[Device]) -> dict:
    code, label = _license_state(lic, link is not None)
    return {
        "license_id": lic.i_license_id,
        "name": _license_label(lic),
        "device_name": device.c_device_name if device else None,
        "type": _license_type(lic),
        "license_key": lic.c_license_key,
        "os": _os_text(device) if device else None,
        "status": code,
        "status_label": label,
        "device_id": device.i_device_id if device else None,
        "issued_at": lic.dt_issued_at.isoformat() if lic.dt_issued_at else None,
        "activated_at": link.dt_linked_at.isoformat() if link and link.dt_linked_at else None,
    }


def _build_workspace(db: Session, store: Store) -> dict:
    store_id = store.i_store_id
    merchant = db.query(Account).filter(Account.i_client_id == store.i_account_id).first() if store.i_account_id else None
    agency = db.query(Agency).filter(Agency.i_agency_id == store.i_installed_by_agency_id).first() if store.i_installed_by_agency_id else None
    business_type = db.query(BusinessType).filter(BusinessType.i_business_type_id == store.i_business_type).first() if store.i_business_type else None

    licenses = db.query(License).filter(License.i_store_id == store_id).order_by(License.i_license_id).all()
    links = _active_links(db, store_id)
    device_ids = [lk.i_device_id for lk in links.values()]
    devices = {d.i_device_id: d for d in db.query(Device).filter(Device.i_device_id.in_(device_ids)).all()} if device_ids else {}

    ent = _get_entitlement(db, store_id)
    pos_qty = int(ent.i_pos_qty) if ent else 0
    kiosk_qty = int(ent.i_kiosk_qty) if ent else 0
    ordering_enabled = bool(ent.b_customer_ordering) if ent else False

    rows = []
    service_license = None
    counts = {t: {"issued": 0, "activated": 0} for t in ISSUABLE_TYPES}
    for lic in licenses:
        ltype = _license_type(lic)
        link = links.get(lic.i_license_id)
        device = devices.get(link.i_device_id) if link else None
        row = _serialize_license_row(lic, link, device)
        if ltype == TYPE_ORDERING:
            # Keep the newest service license that still counts, else the newest one for display.
            if _consumes_quota(lic) or service_license is None:
                service_license = row
        else:
            rows.append(row)
        if ltype in counts and _consumes_quota(lic):
            counts[ltype]["issued"] += 1
            if link is not None:
                counts[ltype]["activated"] += 1

    def _summary(contracted: int, ltype: str) -> dict:
        issued = counts[ltype]["issued"]
        return {
            "contracted": contracted,
            "issued": issued,
            "activated": counts[ltype]["activated"],
            "remaining": max(0, contracted - issued),
        }

    ordering_issued = counts[TYPE_ORDERING]["issued"]
    contract = (
        db.query(Contract)
        .filter(Contract.i_store_id == store_id, Contract.c_status == "active")
        .order_by(Contract.i_contract_id.desc())
        .first()
    )
    contract_info = None
    if contract:
        sub = db.query(Subscription).filter(Subscription.i_subscription_id == contract.i_subscription_id).first()
        contract_info = {
            "contract_id": contract.i_contract_id,
            "subscription_id": contract.i_subscription_id,
            "status": contract.c_status,
            "plan_name": sub.c_plan_name if sub else None,
            "plan_included_pos": sub.i_included_pos_count if sub else None,
            "plan_included_kiosk": sub.i_included_kiosk_count if sub else None,
        }

    return {
        "store": {
            "store_id": store_id,
            "store_name": store.c_store_name,
            "store_code": store.c_store_code,
            "merchant_id": store.i_account_id,
            "merchant_name": merchant.c_client_name if merchant else None,
            "agency_id": store.i_installed_by_agency_id,
            "agency_name": agency.c_agency_name if agency else None,
            "agency_code": agency.c_agency_code if agency else None,
            "agency_email": agency.c_email if agency else None,
            "business_type": business_type.c_name if business_type else None,
            "status": store.c_status,
        },
        "contract": contract_info,
        "summary": {
            TYPE_POS: _summary(pos_qty, TYPE_POS),
            TYPE_KIOSK: _summary(kiosk_qty, TYPE_KIOSK),
            TYPE_ORDERING: {
                "enabled": ordering_enabled,
                "issued": ordering_issued,
                "remaining": 1 if ordering_enabled and ordering_issued == 0 else 0,
                "license_status": service_license["status_label"] if service_license else "Not Issued",
            },
        },
        "licenses": rows,
        "service_license": service_license,
    }


def _parse_license_ids(payload: dict) -> list[int]:
    raw_ids = payload.get("license_ids")
    if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 100:
        raise HTTPException(status_code=400, detail="Select between 1 and 100 licenses.")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in raw_ids):
        raise HTTPException(status_code=400, detail="license_ids must contain positive integers.")
    if len(set(raw_ids)) != len(raw_ids):
        raise HTTPException(status_code=400, detail="Duplicate license IDs are not allowed.")
    return raw_ids


def _email_context(db: Session, store_id: int, raw_ids: list[int]) -> tuple[dict, list[dict]]:
    """Store info and full-key license rows, verified to belong to the store."""
    store = _get_store(db, store_id)
    licenses_by_id = {
        license_row.i_license_id: license_row
        for license_row in db.query(License)
        .filter(License.i_store_id == store_id, License.i_license_id.in_(raw_ids))
        .all()
    }
    if len(licenses_by_id) != len(raw_ids):
        raise HTTPException(status_code=400, detail="One or more selected licenses do not belong to this store.")

    merchant = db.query(Account).filter(Account.i_client_id == store.i_account_id).first() if store.i_account_id else None
    agency = (
        db.query(Agency)
        .filter(Agency.i_agency_id == store.i_installed_by_agency_id)
        .first()
        if store.i_installed_by_agency_id
        else None
    )
    selected_licenses = [licenses_by_id[license_id] for license_id in raw_ids]
    selected_links = _active_links(db, store_id)
    selected_device_ids = {
        selected_links[license_id].i_device_id
        for license_id in raw_ids
        if license_id in selected_links
    }
    selected_devices = {
        device.i_device_id: device
        for device in db.query(Device).filter(Device.i_device_id.in_(selected_device_ids)).all()
    } if selected_device_ids else {}
    selected_device_names = {
        license_id: selected_devices[link.i_device_id].c_device_name
        for license_id, link in selected_links.items()
        if license_id in licenses_by_id
        and link.i_device_id in selected_devices
        and selected_devices[link.i_device_id].c_device_name
    }
    store_info = {
        "merchant_name": merchant.c_client_name if merchant else "—",
        "store_name": store.c_store_name or "—",
        "agency_name": agency.c_agency_name if agency else "—",
        # Existing Store License values only; nothing is generated here.
        "store_license": {
            "key": store.c_store_license_key or "—",
            "status": (store.c_store_license_status or "—").upper(),
            "valid_from": store.dt_store_license_start.strftime("%m/%d/%Y") if store.dt_store_license_start else "—",
            "valid_until": store.dt_license_expire.strftime("%m/%d/%Y") if store.dt_license_expire else "—",
            "device_licenses": str(store.n_license_count) if store.n_license_count is not None else "—",
        },
    }
    email_licenses = [
        {
            "license_id": lic.i_license_id,
            "name": selected_device_names.get(lic.i_license_id) or _license_label(lic),
            "type": _license_type(lic),
            "license_key": lic.c_license_key,
        }
        for lic in selected_licenses
    ]
    return store_info, email_licenses


@router.post("/platform/store/{store_id}/licenses/email/preview")
def preview_store_license_email(store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    """Renders the exact HTML that a real send would produce; sends nothing and logs nothing."""
    user = get_current_user(request, db)
    require_platform_admin(user)
    raw_ids = _parse_license_ids(payload)
    message = str(payload.get("message") or "").strip()
    if len(message) > 10000:
        raise HTTPException(status_code=400, detail="Message must be 10,000 characters or fewer.")
    store_info, email_licenses = _email_context(db, store_id, raw_ids)
    company = load_company_branding(db)
    html_body = render_license_email_preview(
        store_info=store_info, licenses=email_licenses, custom_message=message, company=company,
    )
    return _set_no_cache_headers(JSONResponse({"html": html_body}))


@router.post("/platform/store/{store_id}/licenses/email")
def email_store_licenses(store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Email request must be a JSON object.")

    request_id = str(payload.get("request_id") or "").strip()
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise HTTPException(status_code=400, detail="A valid request_id is required.")

    raw_ids = _parse_license_ids(payload)

    try:
        recipient = validate_email_address(payload.get("recipient"))
        subject, custom_message = validate_email_content(payload.get("subject"), payload.get("message"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store_info, email_licenses = _email_context(db, store_id, raw_ids)
    fingerprint_data = {
        "sender_id": user.i_platform_user_id,
        "store_id": store_id,
        "license_ids": raw_ids,
        "recipient": recipient.casefold(),
        "subject": subject,
        "message": custom_message,
    }
    request_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    existing = db.query(LicenseEmailLog).filter(LicenseEmailLog.c_request_id == request_id).first()
    if existing:
        if existing.c_request_fingerprint != request_fingerprint:
            raise HTTPException(status_code=409, detail="request_id was already used with different email content.")
        if existing.c_status == "accepted":
            return JSONResponse({
                "accepted": True,
                "replayed": True,
                "message_id": existing.c_provider_message_id,
            })
        if existing.c_status == "sending":
            raise HTTPException(status_code=409, detail="This email request is already being processed.")
        raise HTTPException(status_code=502, detail="The previous email attempt failed. Retry with a new request.")

    log = LicenseEmailLog(
        c_request_id=request_id,
        c_request_fingerprint=request_fingerprint,
        i_sender_user_id=user.i_platform_user_id,
        c_sender_email=str(user.c_email or ""),
        i_store_id=store_id,
        c_recipient_email=recipient,
        c_subject=subject,
        c_license_ids=json.dumps(raw_ids),
        c_status="sending",
    )
    db.add(log)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        duplicate = db.query(LicenseEmailLog).filter(LicenseEmailLog.c_request_id == request_id).first()
        if duplicate and duplicate.c_request_fingerprint == request_fingerprint and duplicate.c_status == "accepted":
            return JSONResponse({
                "accepted": True,
                "replayed": True,
                "message_id": duplicate.c_provider_message_id,
            })
        raise HTTPException(status_code=409, detail="This email request is already being processed.") from exc

    email_message = build_license_email(
        recipient=recipient,
        subject=subject,
        custom_message=custom_message,
        store_info=store_info,
        licenses=email_licenses,
        sender=None,
        company=load_company_branding(db),
    )
    try:
        message_id, provider_result = send_license_email(email_message, db)
    except EmailConfigurationError as exc:
        log.c_status = "failed"
        log.c_provider_result = str(exc)[:2000]
        log.dt_sent_at = datetime.now()
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MailDeliveryError as exc:
        log.c_status = "failed"
        log.c_provider_result = str(exc)[:2000]
        log.c_provider_message_id = str(email_message["Message-ID"])
        log.dt_sent_at = datetime.now()
        db.commit()
        raise HTTPException(status_code=502, detail="Mail server did not accept the message. " + str(exc)) from exc

    log.c_status = "accepted"
    log.c_provider_result = provider_result
    log.c_provider_message_id = message_id
    log.dt_sent_at = datetime.now()
    db.commit()
    return JSONResponse({
        "accepted": True,
        "replayed": False,
        "message_id": message_id,
        "recipient": recipient,
        "delivery_note": "Accepted by the mail server; recipient delivery is not confirmed.",
    })


@router.get("/platform/store/{store_id}/devices-licenses")
def get_store_devices_licenses(store_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    store = _get_store(db, store_id)
    return _set_no_cache_headers(JSONResponse(_build_workspace(db, store)))


@router.put("/platform/store/{store_id}/entitlement")
def update_store_entitlement(store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    def _qty(name: str) -> int:
        raw = payload.get(name)
        if isinstance(raw, bool) or raw is None or str(raw).strip() == "":
            raise HTTPException(status_code=400, detail=f"{name} is required.")
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{name} must be a whole number.")
        if value < 0 or value > MAX_QTY:
            raise HTTPException(status_code=400, detail=f"{name} must be between 0 and {MAX_QTY}.")
        return value

    pos_qty = _qty("pos_qty")
    kiosk_qty = _qty("kiosk_qty")
    ordering = payload.get("customer_ordering")
    if not isinstance(ordering, bool):
        raise HTTPException(status_code=400, detail="customer_ordering must be true or false.")

    store = _lock_store(db, store_id)
    ent = _get_entitlement(db, store_id, create=True)
    ent.i_pos_qty = pos_qty
    ent.i_kiosk_qty = kiosk_qty
    ent.b_customer_ordering = ordering
    _stamp_updated(ent, user)
    db.commit()
    db.refresh(store)
    # Lowering a quantity never revokes issued licenses; the summary shows any excess.
    return _set_no_cache_headers(JSONResponse(_build_workspace(db, store)))


def _new_license_key(db: Session, ltype: str) -> str:
    prefix = {"POS": "POS", "KIOSK": "KSK", "CUSTOMER_ORDERING": "ORD"}[ltype]
    for _ in range(20):
        groups = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(4)]
        key = f"AWS-{prefix}-{'-'.join(groups)}"
        if not db.query(License.i_license_id).filter(License.c_license_key == key).first():
            return key
    raise HTTPException(status_code=500, detail="Unable to generate a unique license key.")


@router.post("/platform/store/{store_id}/licenses/issue")
def issue_store_licenses(store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    ltype = str(payload.get("license_type") or "").strip().upper()
    if ltype not in ISSUABLE_TYPES:
        raise HTTPException(status_code=400, detail="license_type must be POS, KIOSK or CUSTOMER_ORDERING.")

    raw_qty = payload.get("quantity")
    if isinstance(raw_qty, bool):
        raise HTTPException(status_code=400, detail="quantity must be a whole number.")
    try:
        quantity = int(str(raw_qty).strip())
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="quantity must be a whole number.")
    if quantity < 1 or quantity > MAX_ISSUE_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"quantity must be between 1 and {MAX_ISSUE_PER_REQUEST}.")

    request_id = str(payload.get("request_id") or "").strip()
    if not REQUEST_ID_RE.match(request_id):
        raise HTTPException(status_code=400, detail="request_id is required.")

    store = _lock_store(db, store_id)

    # A repeated request (double click / retry) returns the original result without issuing again.
    replay = (
        db.query(License)
        .filter(License.c_issue_request_id == request_id)
        .order_by(License.i_license_id)
        .all()
    )
    if replay:
        if any(lic.i_store_id != store_id for lic in replay):
            raise HTTPException(status_code=409, detail="request_id was already used.")
        db.rollback()
        return _set_no_cache_headers(JSONResponse({
            "replayed": True,
            "issued": [lic.i_license_id for lic in replay],
            "workspace": _build_workspace(db, store),
        }))

    ent = _get_entitlement(db, store_id)
    existing = db.query(License).filter(License.i_store_id == store_id).all()
    consumed = sum(1 for lic in existing if _license_type(lic) == ltype and _consumes_quota(lic))

    if ltype == TYPE_ORDERING:
        if not (ent and ent.b_customer_ordering):
            raise HTTPException(status_code=409, detail="Customer Ordering is not enabled for this store.")
        contracted = 1
    else:
        contracted = int((ent.i_pos_qty if ltype == TYPE_POS else ent.i_kiosk_qty) if ent else 0)

    remaining = max(0, contracted - consumed)
    if quantity > remaining:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot issue {quantity} {ltype} license(s). Contracted {contracted}, already issued {consumed}, remaining {remaining}.",
        )

    next_slot = max([int(lic.i_slot_no or 0) for lic in existing if _license_type(lic) == ltype] or [0])
    now = datetime.now()
    actor = _actor_label(user)
    plan_name = {"POS": "POS License", "KIOSK": "Kiosk License", "CUSTOMER_ORDERING": "Customer Ordering Service"}[ltype]
    created = []
    for i in range(quantity):
        lic = License(
            i_store_id=store_id,
            i_account_id=store.i_account_id,
            i_agency_id=store.i_installed_by_agency_id,
            c_license_key=_new_license_key(db, ltype),
            c_license_type=ltype,
            c_plan_name=plan_name,
            i_max_devices=None if ltype == TYPE_ORDERING else 1,
            c_status="active",
            i_slot_no=next_slot + i + 1,
            c_issue_request_id=request_id,
            dt_issued_at=now,
            c_issued_by=actor,
        )
        if hasattr(lic, "i_created_by"):
            lic.i_created_by = getattr(user, "i_platform_user_id", None)
        db.add(lic)
        created.append(lic)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="License issuance conflicted with another request. Please retry.")

    db.refresh(store)
    return _set_no_cache_headers(JSONResponse({
        "replayed": False,
        "issued": [lic.i_license_id for lic in created],
        "workspace": _build_workspace(db, store),
    }))


@router.get("/platform/store/{store_id}/licenses/{license_id}")
def get_store_license_detail(store_id: int, license_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)
    _get_store(db, store_id)

    lic = db.query(License).filter(License.i_license_id == license_id, License.i_store_id == store_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="License not found for this store.")

    links = (
        db.query(LicenseDeviceLink)
        .filter(LicenseDeviceLink.i_license_id == license_id)
        .order_by(LicenseDeviceLink.dt_linked_at.desc(), LicenseDeviceLink.i_link_id.desc())
        .all()
    )
    device_ids = {lk.i_device_id for lk in links}
    devices = {d.i_device_id: d for d in db.query(Device).filter(Device.i_device_id.in_(device_ids)).all()} if device_ids else {}
    active = next((lk for lk in links if lk.dt_unlinked_at is None), None)
    active_device = devices.get(active.i_device_id) if active else None

    def _device_payload(device: Device) -> dict:
        fingerprint = str(device.c_bound_hardware_id or "")
        return {
            "device_id": device.i_device_id,
            "device_code": device.c_device_uuid,
            "name": device.c_device_name,
            "os": device.c_os,
            "os_version": device.c_os_version,
            "model": device.c_model,
            "app_version": device.c_app_version,
            "fingerprint_tail": fingerprint[-8:] if fingerprint else None,
            "last_seen": device.dt_last_seen.isoformat() if device.dt_last_seen else None,
            "last_ip": device.c_last_ip,
            "first_activated_at": device.dt_first_activated_at.isoformat() if device.dt_first_activated_at else None,
            "status": device.c_status,
        }

    history = []
    for lk in links:
        dev = devices.get(lk.i_device_id)
        history.append({
            "link_id": lk.i_link_id,
            "device_id": lk.i_device_id,
            "device_code": dev.c_device_uuid if dev else None,
            "device_name": dev.c_device_name if dev else None,
            "linked_at": lk.dt_linked_at.isoformat() if lk.dt_linked_at else None,
            "linked_by": lk.c_linked_by,
            "unlinked_at": lk.dt_unlinked_at.isoformat() if lk.dt_unlinked_at else None,
            "unlinked_by": lk.c_unlinked_by,
            "unlink_reason": lk.c_unlink_reason,
        })

    logs = []
    if active_device:
        logs = [
            _serialize_device_log(log)
            for log in db.query(DeviceLog)
            .filter(DeviceLog.i_device_id == active_device.i_device_id)
            .order_by(DeviceLog.dt_event_time.desc(), DeviceLog.i_log_id.desc())
            .limit(20)
            .all()
        ]

    return _set_no_cache_headers(JSONResponse({
        "license": _serialize_license_row(lic, active, active_device),
        "device": _device_payload(active_device) if active_device else None,
        "history": history,
        "logs": logs,
    }))


@router.post("/platform/store/{store_id}/licenses/{license_id}/unlink")
def unlink_license_device(store_id: int, license_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    require_platform_admin(user)

    expected_device_id = payload.get("device_id")
    try:
        expected_device_id = int(expected_device_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="device_id is required to confirm the unlink.")
    reason = str(payload.get("reason") or "").strip()[:255] or None

    _lock_store(db, store_id)
    lic = db.query(License).filter(License.i_license_id == license_id, License.i_store_id == store_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="License not found for this store.")

    link = (
        db.query(LicenseDeviceLink)
        .filter(LicenseDeviceLink.i_license_id == license_id, LicenseDeviceLink.dt_unlinked_at.is_(None))
        .first()
    )
    if not link:
        raise HTTPException(status_code=409, detail="This license has no active device.")
    if link.i_device_id != expected_device_id:
        raise HTTPException(status_code=409, detail="The linked device has changed. Reload and try again.")

    device = db.query(Device).filter(Device.i_device_id == link.i_device_id).first()
    actor = _actor_label(user)
    link.dt_unlinked_at = datetime.now()
    link.c_unlinked_by = actor
    link.c_unlink_reason = reason
    if device:
        device.c_status = "unlinked"
        device.i_license_id = None
        _stamp_updated(device, user)
        _log_device_event(
            db, device, "UNLINKED", request=request, action_by=actor,
            note=f"Unlinked from {_license_label(lic)}." + (f" Reason: {reason}" if reason else ""),
        )
    if lic.i_device_id == link.i_device_id:
        lic.i_device_id = None
    _stamp_updated(lic, user)
    db.commit()

    store = _get_store(db, store_id)
    return _set_no_cache_headers(JSONResponse({"success": True, "workspace": _build_workspace(db, store)}))


@router.post("/platform/license/activate")
def activate_license(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Called by the field device. A Device record is created here, never before."""
    license_key = str(payload.get("license_key") or "").strip().upper()
    fingerprint = str(payload.get("device_fingerprint") or payload.get("hardware_id") or "").strip()
    if not license_key:
        raise HTTPException(status_code=400, detail="license_key is required.")
    if not fingerprint or len(fingerprint) > 255:
        raise HTTPException(status_code=400, detail="device_fingerprint is required.")

    def _clean(name: str, limit: int = 120) -> Optional[str]:
        return str(payload.get(name) or "").strip()[:limit] or None

    probe = db.query(License.i_license_id, License.i_store_id).filter(License.c_license_key == license_key).first()
    if not probe:
        raise HTTPException(status_code=404, detail="Invalid license key.")

    _lock_store(db, probe.i_store_id)
    lic = db.query(License).filter(License.i_license_id == probe.i_license_id).with_for_update().first()
    ltype = _license_type(lic)
    if ltype not in DEVICE_LICENSE_TYPES:
        raise HTTPException(status_code=409, detail="This license does not activate a device.")
    status = str(lic.c_status or "active").strip().lower()
    if status not in ACTIVATABLE_STATUSES or _is_expired(lic):
        raise HTTPException(status_code=403, detail="License is not active.")

    active = (
        db.query(LicenseDeviceLink)
        .filter(LicenseDeviceLink.i_license_id == lic.i_license_id, LicenseDeviceLink.dt_unlinked_at.is_(None))
        .first()
    )
    if active:
        current = db.query(Device).filter(Device.i_device_id == active.i_device_id).first()
        if current and current.c_bound_hardware_id == fingerprint:
            db.rollback()
            return JSONResponse(_activation_response(lic, current, already_activated=True))
        raise HTTPException(status_code=409, detail="License is already in use by another device.")

    device = (
        db.query(Device)
        .filter(Device.i_store_id == lic.i_store_id, Device.c_bound_hardware_id == fingerprint)
        .order_by(Device.i_device_id)
        .first()
    )
    if device:
        other = (
            db.query(LicenseDeviceLink)
            .filter(LicenseDeviceLink.i_device_id == device.i_device_id, LicenseDeviceLink.dt_unlinked_at.is_(None))
            .first()
        )
        if other:
            raise HTTPException(status_code=409, detail="This device already holds another license.")
    now = datetime.now()
    if device is None:
        device = Device(
            i_store_id=lic.i_store_id,
            i_installed_by_agency_id=lic.i_agency_id,
            c_device_uuid=_generate_store_device_code(db, lic.i_store_id),
            c_device_name=_clean("device_name") or _license_label(lic),
            c_device_type=ltype,
            c_bound_hardware_id=fingerprint,
            dt_first_activated_at=now,
        )
        db.add(device)
    device.i_license_id = lic.i_license_id
    device.c_status = "activated"
    device.dt_last_seen = now
    device.dt_first_activated_at = device.dt_first_activated_at or now
    device.c_activated_by = _clean("activated_by", 255) or fingerprint
    device.c_last_ip = _resolve_request_ip(request, payload)
    device.c_os = _clean("os") or device.c_os
    device.c_os_version = _clean("os_version") or device.c_os_version
    device.c_model = _clean("model") or device.c_model
    device.c_app_version = _clean("app_version") or device.c_app_version
    db.flush()

    db.add(LicenseDeviceLink(
        i_license_id=lic.i_license_id,
        i_device_id=device.i_device_id,
        i_store_id=lic.i_store_id,
        dt_linked_at=now,
        c_linked_by=device.c_activated_by,
    ))
    lic.i_device_id = device.i_device_id
    _log_device_event(
        db, device, "ACTIVATED", request=request,
        payload={"hardware_id": fingerprint, "os": device.c_os, "app_version": device.c_app_version},
        action_by=device.c_activated_by, note=f"Activated with {_license_label(lic)}.",
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Activation conflicted with another request. Please retry.")
    db.refresh(device)
    return JSONResponse(_activation_response(lic, device, already_activated=False))


def _activation_response(lic: License, device: Device, *, already_activated: bool) -> dict:
    return {
        "success": True,
        "already_activated": already_activated,
        "device_id": device.i_device_id,
        "device_code": device.c_device_uuid,
        "store_id": device.i_store_id,
        "license_id": lic.i_license_id,
        "license_type": _license_type(lic),
        "status": device.c_status,
    }
