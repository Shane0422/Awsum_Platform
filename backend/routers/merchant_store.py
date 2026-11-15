"""Merchant Store Management: a Merchant login views its own Stores and edits their allowed operating information.

Reuses the Platform Store Management screen (templates/platform/master_management.html, module "store") and its
processing (routers.platform_store: list query, detail, _update_store, Store Logo save, BusinessContactService).
Only the authorization and the field policy below are Merchant specific.

Policy (server side; the screen only mirrors it). AWSUM Platform controls the Store and its licensing:
- Platform Admin only: Store registration / deletion / deactivation, Store ID / Code, Merchant / Agency assignment,
  Store Status, Store basic information (Name, Business Type, Operation Type, Purpose), Address (incl. Time Zone and
  Default Tax Rate), the Store base Logo, Business Options with their Scopes, License, contract information,
  max user / terminal count, Test flag, Dashboard Type, Devices. There is no Merchant create / delete route.
- A Merchant changes its own Stores' Contacts, Receipt Info and Item Features (Feature Registry group "item"), an Item
  Feature only within the contract and Feature Entitlement (turning one ON beyond them is refused).
- MERCHANT_STORE_EDITABLE: the only fields a Merchant save applies.
- MERCHANT_STORE_PROTECTED: shown read-only; a request that changes one is refused (403), the same value passes
  (the common form sends the whole form).
- MERCHANT_STORE_HIDDEN: Platform internal values (Notes / Memo / Remark, License Key, contract terms ...). Never
  returned to a Merchant; sending one is refused (403) without comparing, so the value cannot be probed.
- Any other payload key: 422.
- Inactive Store: view only (AppScope.require_store_access). A Store login has no Store Management (403, no menu).
- Item Features: a feature that is not contracted or not in the Merchant Feature Entitlement cannot be newly turned
  ON (403). OFF keeps the saved item data. The effective status still follows the Common Feature Resolver.
- The whole request is checked before anything is written: one refused change refuses the request (nothing saved).
- Store Logo (view only for a Merchant) and Business Contacts: every read and write checks that the Store belongs to the
  login's Merchant.
"""
import hashlib
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from backend.config.settings import APP_NAME
from backend.config.templates import get_brand_context, templates
from backend.models_admin.agency import Agency
from backend.models_admin.business_contact import BusinessContact
from backend.models_admin.business_type import BusinessType
from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.routers.dashboard import _auth_user_or_home_redirect
from backend.routers.platform_store import (
    STORE_LIST_MAX_PAGE_SIZE,
    STORE_OPTION_FIELDS,
    _get_business_store,
    _parse_option_flag,
    _query_store_list,
    _query_store_tree,
    _set_no_cache_headers,
    _store_detail_data,
    _update_store,
    get_current_user,
    get_db,
    lookup_zip,
)
from backend.routers.platform_store_license import _build_workspace, _license_detail_data
from backend.routers.system_settings import _validate_origin
from backend.services.app_navigation import STORE_MANAGEMENT_PERMISSION, resolve_app_scope
from backend.services.branding import resolve_branding
from backend.services.business_contact import BusinessContactService
from backend.services.features import FEATURES, REASON_TEXT, REGISTRY, resolve_features
from backend.services.images import image_mutation_form
from backend.services.maps import map_client_config
from backend.services.merchant_features import business_option_defs
from backend.services.store_item_features import store_option_enabled

router = APIRouter()

API = "/merchant/api"
LABEL = "Store Management"
_OPTION_KEYS = frozenset(STORE_OPTION_FIELDS)
_FEATURE_OF_OPTION = {f.payload_key: f.key for f in REGISTRY if f.payload_key}
# Store Options split by the Common Feature Registry group: Business Options (with their Scopes) are Platform only;
# Item Features are the Merchant's own Store settings (within the contract / Feature Entitlement).
_BUSINESS_FLAG_KEYS = frozenset(f.payload_key for f in REGISTRY if f.group == "business" and f.payload_key in _OPTION_KEYS)
_BUSINESS_SCOPE_KEYS = frozenset(f.scope_key for f in REGISTRY if f.group == "business" and f.scope_key)
_ITEM_OPTION_KEYS = frozenset(f.payload_key for f in REGISTRY if f.group == "item" and f.payload_key in _OPTION_KEYS)
assert _BUSINESS_FLAG_KEYS | _ITEM_OPTION_KEYS == _OPTION_KEYS  # every Store Option is in exactly one group
_STORE_BASIC_KEYS = ("store_name", "business_type", "operation_type", "store_purpose")
_STORE_ADDRESS_KEYS = ("zip", "city", "state", "address", "address_line1", "address_line2", "country", "timezone",
                       "default_tax_rate")
MERCHANT_STORE_EDITABLE = frozenset({
    "contact_name", "owner_name", "phone", "email",
    "receipt_store_name", "receipt_phone", "receipt_email", "receipt_website_url", "receipt_message",
}) | _ITEM_OPTION_KEYS
# payload key -> the Merchant view key it is compared with
MERCHANT_STORE_PROTECTED = {
    "id": "store_id", "store_id": "store_id", "store_code": "store_code", "store_seq": "store_seq",
    "merchant_id": "merchant_id", "installed_by_agency_id": "installed_by_agency_id",
    "status": "store_status", "store_status": "store_status",
    "store_license_status": "store_license_status", "store_license_start_date": "store_license_start_date",
    "license_expire_date": "license_expire_date", "license_count": "license_count", "tax_source": "tax_source",
    **{key: key for key in _STORE_BASIC_KEYS + _STORE_ADDRESS_KEYS},
    **{key: key for key in sorted(_BUSINESS_FLAG_KEYS | _BUSINESS_SCOPE_KEYS)},
}
MERCHANT_STORE_HIDDEN = frozenset({
    "notes", "memo", "remark", "store_license_key", "license_type",
    "contract_date", "contract_start_date", "contract_end_date", "max_user_count", "max_terminal_count",
    "is_test", "dashboard_type",
})
# Keys of the Platform list / detail rows a Merchant may see (everything else is dropped).
MERCHANT_STORE_VISIBLE = (MERCHANT_STORE_EDITABLE | frozenset(MERCHANT_STORE_PROTECTED.values()) | {
    "status", "merchant_name", "merchant_code", "installed_by_agency_name", "installed_by_agency_code",
    "image_path", "image_url", "created_at",
}) - {"id"}
# List sorts of the Merchant scope (its Merchant is fixed; no Agency / Merchant ordering). The first is the default.
MERCHANT_STORE_SORTS = ("store_asc", "store_desc", "newest", "oldest")
_DATE_KEYS = {"store_license_start_date", "license_expire_date"}
_CASELESS_KEYS = {"store_status", "store_license_status", "store_code"} | set(_BUSINESS_SCOPE_KEYS)
assert not (MERCHANT_STORE_EDITABLE & (set(MERCHANT_STORE_PROTECTED) | MERCHANT_STORE_HIDDEN))
assert not (MERCHANT_STORE_VISIBLE & MERCHANT_STORE_HIDDEN)


# ---- authorization (the Master Group pattern: the login scope must match the /merchant route) ----
def _merchant_scope(request: Request, db: Session, action: str):
    user = get_current_user(request, db)
    scope = resolve_app_scope(request, db, user)
    if scope.context != "MERCHANT":
        raise HTTPException(403, f"{LABEL} is available to Merchant logins only.")
    scope.require(action, STORE_MANAGEMENT_PERMISSION, LABEL)
    request.state.app_scope = scope
    return user, scope


def _merchant_store(db: Session, scope, store_id: int, method: str = "GET") -> Store:
    """A Store of this Merchant (another Merchant's Store or the Platform Admin store: not found)."""
    try:
        store = _get_business_store(db, store_id)
    except HTTPException:
        raise HTTPException(404, "Store not found.")
    if store.i_merchant_id != scope.merchant_id:
        raise HTTPException(404, "Store not found.")
    scope.require_store_access(store, method)  # Inactive Store: view only
    return store


# ---- Merchant view of a Store (Platform rows filtered by MERCHANT_STORE_VISIBLE) ----
def _option_access(scope, store: Store | None) -> dict:
    """Per Store Option: can this Merchant turn it on (contract + Feature Entitlement), the reason when not, and
    whether it is in effect for the Store (full Common Feature Resolver policy)."""
    states = resolve_features(None, scope.feature_snapshot) if scope.feature_snapshot is not None else {}
    access = {}
    for option_key, feature_key in _FEATURE_OF_OPTION.items():
        allowed = scope.feature_on(feature_key)
        reason = None if allowed else REASON_TEXT.get(
            getattr(states.get(feature_key), "reason", None), "Feature settings could not be checked.")
        access[option_key] = {"allowed": allowed, "reason": reason,
                              "effective": scope.feature_on(feature_key, store) if store is not None else allowed}
    return access


def _merchant_view(row: dict, scope, store: Store) -> dict:
    view = {key: value for key, value in row.items() if key in MERCHANT_STORE_VISIBLE}
    view.setdefault("store_status", row.get("status") or row.get("store_status"))
    view["id"] = view.get("store_id")
    view.update(scope.store_status_info(store))
    view["option_access"] = _option_access(scope, store)
    return view


# ---- field policy ----
def _norm(key: str, value) -> str:
    if key in _BUSINESS_FLAG_KEYS:  # on / off, true / false, 1 / 0 ... are one value (the common option flag rule)
        return "on" if _parse_option_flag(value) else "off"
    text = "" if value is None else str(value).strip()
    if key in _DATE_KEYS:
        text = text[:10]
    return text.upper() if key in _CASELESS_KEYS else text


def merchant_store_payload(db: Session, scope, store: Store, payload: dict) -> dict:
    """The allow-listed changes of a Merchant save (see the module policy). Raises 422 / 403; never writes."""
    if not isinstance(payload, dict):
        raise HTTPException(422, "Invalid Store payload.")
    unexpected = sorted(set(payload) - MERCHANT_STORE_EDITABLE - set(MERCHANT_STORE_PROTECTED) - MERCHANT_STORE_HIDDEN)
    if unexpected:
        raise HTTPException(422, f"Unexpected Store fields: {', '.join(unexpected)}.")
    hidden = sorted(set(payload) & MERCHANT_STORE_HIDDEN)
    if hidden:
        raise HTTPException(403, f"Managed by AWSUM Platform only: {', '.join(hidden)}.")
    saved = _store_detail_data(db, store)
    changed = sorted(key for key, view_key in MERCHANT_STORE_PROTECTED.items()
                     if key in payload and _norm(view_key, payload[key]) != _norm(view_key, saved.get(view_key)))
    if changed:
        raise HTTPException(403, f"Managed by AWSUM Platform only: {', '.join(changed)}.")
    values = {key: value for key, value in payload.items() if key in MERCHANT_STORE_EDITABLE}
    access = None
    for option_key in sorted(_ITEM_OPTION_KEYS & set(values)):
        column = STORE_OPTION_FIELDS[option_key][0]
        if _parse_option_flag(values[option_key]) and not store_option_enabled(store, option_key, column):
            access = access or _option_access(scope, None)
            if not access[option_key]["allowed"]:
                feature = FEATURES[_FEATURE_OF_OPTION[option_key]]
                raise HTTPException(403, f"{feature.label}: {access[option_key]['reason']}")
    return values


# ---- page ----
@router.get("/merchant/business/stores", response_class=HTMLResponse)
def merchant_store_page(request: Request, db: Session = Depends(get_db)):
    user, redirect = _auth_user_or_home_redirect(request, db)
    if redirect:
        return redirect
    user, scope = _merchant_scope(request, db, "read")
    merchant = db.get(Merchant, scope.merchant_id)
    stores = db.query(Store).filter(Store.i_merchant_id == scope.merchant_id).order_by(Store.i_store_id).all()
    agency_ids = {s.i_installed_by_agency_id for s in stores if s.i_installed_by_agency_id is not None}
    agencies = db.query(Agency).filter(Agency.i_agency_id.in_(agency_ids)).order_by(Agency.i_agency_id).all() \
        if agency_ids else []
    brand = resolve_branding(db, "MERCHANT", merchant_id=scope.merchant_id)
    return _set_no_cache_headers(templates.TemplateResponse(request, "platform/master_management.html", {
        "request": request, "APP_NAME": APP_NAME, "user": user, "role": scope.role_name, "is_super_admin": False,
        "active_page": "master", "active_module": "store",
        # Seed data: this Merchant, its Stores and their Agencies only (never other tenants or Platform lists).
        "roles": [], "merchants": [merchant], "agencies": agencies, "agency_types": [], "stores": stores,
        "business_types": db.query(BusinessType).filter(BusinessType.c_status == "active").all(),
        "business_option_defs": business_option_defs(), "pricing_plans": [], "device_categories": [],
        "device_types": [], "users": [],
        "merchant_store_policy": {
            "api_base": API,
            "editable_fields": sorted(MERCHANT_STORE_EDITABLE),
            "protected_fields": sorted(MERCHANT_STORE_PROTECTED),
            "hidden_fields": sorted(MERCHANT_STORE_HIDDEN),
            "sorts": list(MERCHANT_STORE_SORTS),
            "merchant_name": merchant.c_merchant_name,
        },
        "app_navigation": scope.navigation(),
        **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
        **brand.template(),
    }))


# ---- Stores ----
@router.get(f"{API}/stores")
def merchant_list_stores(
    request: Request, db: Session = Depends(get_db),
    search: Optional[str] = Query(None, alias="q"), status: Optional[str] = None,
    business_type: Optional[str] = None, operation_type: Optional[str] = None,
    sort: Optional[str] = None, page: Optional[int] = Query(None, ge=1),
    page_size: int = Query(20, ge=1, le=STORE_LIST_MAX_PAGE_SIZE), locate_id: Optional[int] = None,
):
    # The Merchant comes from the login scope only: merchant_id / agency_id in the request are not parameters here,
    # and the search covers Store Name / Store Code of this Merchant's Stores.
    _user, scope = _merchant_scope(request, db, "read")
    if sort is not None and sort not in MERCHANT_STORE_SORTS:
        raise HTTPException(400, "Invalid sort option.")
    if page is not None and sort is None:
        sort = MERCHANT_STORE_SORTS[0]
    rows, pagination = _query_store_list(
        db, search=search, status=status, business_type=business_type, operation_type=operation_type,
        merchant_id=scope.merchant_id, sort=sort, page=page, page_size=page_size, locate_id=locate_id,
        store_search_only=True,
    )
    stores = {s.i_store_id: s for s in db.query(Store).filter(Store.i_store_id.in_([r["store_id"] for r in rows]))} \
        if rows else {}
    items = [_merchant_view(row, scope, stores[row["store_id"]]) for row in rows
             if stores.get(row["store_id"]) is not None and stores[row["store_id"]].i_merchant_id == scope.merchant_id]
    if pagination is not None:
        return _set_no_cache_headers(JSONResponse({"items": items, **pagination}))
    return _set_no_cache_headers(JSONResponse(items))


@router.get(f"{API}/store-tree")
def merchant_store_tree(request: Request, db: Session = Depends(get_db)):
    # Common Store Management Tree in the Merchant scope: the login's Merchant and all its Stores (Active and Inactive),
    # no Agency level. The Merchant comes from the login scope only (no request parameter can change it).
    _user, scope = _merchant_scope(request, db, "read")
    merchants = [{key: node[key] for key in ("merchant_id", "merchant_code", "merchant_name", "merchant_status",
                                             "stores")}
                 for node in _query_store_tree(db, merchant_id=scope.merchant_id) if node["merchant_id"] is not None]
    return _set_no_cache_headers(JSONResponse({
        "merchants": merchants,
        "counts": {"merchants": len(merchants), "stores": sum(len(m["stores"]) for m in merchants)},
    }))


# ---- Store Licenses, read only (the common Devices & Licenses panel with readOnly) ----
# The Platform workspace / details are built by routers.platform_store_license and passed through an allow-list:
# a key added later is never shown to a Merchant until it is listed here. Viewing (incl. the full License Key of its own
# Stores, which the Merchant may copy) is separate from managing: no Merchant write route exists for licenses (issue,
# quantities, email, unlink and status changes stay Platform Admin only).
_MERCHANT_LICENSE_KEYS = ("license_id", "name", "device_name", "type", "license_key", "os", "status", "status_label",
                          "device_id", "issued_at", "activated_at", "valid_until", "email_sent_count",
                          "email_last_sent_at", "last_seen_at", "last_ip")
_MERCHANT_LICENSE_STORE_KEYS = ("store_id", "store_name", "store_code", "merchant_id", "merchant_name", "agency_name",
                                "business_type", "status")
_MERCHANT_LICENSE_DEVICE_KEYS = ("name", "os", "os_version", "model", "app_version", "last_seen", "last_ip",
                                 "first_activated_at", "status")
_MERCHANT_LICENSE_LINK_KEYS = ("device_name", "linked_at", "unlinked_at")


def _merchant_license_row(row: dict | None, store_id: int) -> dict | None:
    if not row:
        return None
    view = {key: row.get(key) for key in _MERCHANT_LICENSE_KEYS}
    # One issue = one request: an opaque batch key groups the Issue History without exposing the request ID.
    request_id = row.get("issue_request_id")
    view["issue_batch"] = hashlib.sha256(f"{store_id}:{request_id}".encode()).hexdigest()[:12] if request_id else None
    return view


def _merchant_license_workspace(workspace: dict) -> dict:
    store_id = workspace["store"]["store_id"]
    return {
        "store": {key: workspace["store"].get(key) for key in _MERCHANT_LICENSE_STORE_KEYS},
        "summary": workspace["summary"],
        "licenses": [_merchant_license_row(row, store_id) for row in workspace["licenses"]],
        "service_license": _merchant_license_row(workspace.get("service_license"), store_id),
    }


@router.get(f"{API}/store/{{store_id}}/devices-licenses")
def merchant_store_licenses(store_id: int, request: Request, db: Session = Depends(get_db)):
    _user, scope = _merchant_scope(request, db, "read")
    store = _merchant_store(db, scope, store_id, "GET")  # another Merchant's Store: 404
    return _set_no_cache_headers(JSONResponse(_merchant_license_workspace(_build_workspace(db, store))))


@router.get(f"{API}/store/{{store_id}}/licenses/{{license_id}}")
def merchant_store_license_detail(store_id: int, license_id: int, request: Request, db: Session = Depends(get_db)):
    _user, scope = _merchant_scope(request, db, "read")
    store = _merchant_store(db, scope, store_id, "GET")
    detail = _license_detail_data(db, store.i_store_id, license_id)  # a license of another Store: 404
    device = detail.get("device")
    return _set_no_cache_headers(JSONResponse({
        "license": _merchant_license_row(detail["license"], store.i_store_id),
        "device": {key: device.get(key) for key in _MERCHANT_LICENSE_DEVICE_KEYS} if device else None,
        "history": [{key: link.get(key) for key in _MERCHANT_LICENSE_LINK_KEYS} for link in detail["history"]],
        "logs": [],
    }))


@router.get(f"{API}/store/{{store_id}}")
def merchant_get_store(store_id: int, request: Request, db: Session = Depends(get_db)):
    _user, scope = _merchant_scope(request, db, "read")
    store = _merchant_store(db, scope, store_id)
    return _set_no_cache_headers(JSONResponse(_merchant_view(_store_detail_data(db, store), scope, store)))


def _save(store_id: int, payload: dict, request: Request, db: Session, *, image_data=None, remove_image=False):
    user, scope = _merchant_scope(request, db, "update")
    _validate_origin(request)
    store = _merchant_store(db, scope, store_id, "PUT")
    values = merchant_store_payload(db, scope, store, payload)
    return _update_store(store_id, values, request, db, image_data=image_data, remove_image=remove_image,
                         authorized_user=user, activate=False)


@router.put(f"{API}/store/{{store_id}}")
def merchant_update_store(store_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return _save(store_id, payload, request, db)


@router.put(f"{API}/store/{{store_id}}/with-image")
def merchant_update_store_with_image(
    store_id: int, request: Request, payload: str = Form(...), image: UploadFile | None = File(None),
    remove_image: bool = Form(False), db: Session = Depends(get_db),
):
    # The Store form's save with the common uploader (owner = this Store, checked before anything is read). The Store
    # base Logo is Platform only: a Merchant request that uploads or removes it is refused before anything is written.
    _user, scope = _merchant_scope(request, db, "update")
    _merchant_store(db, scope, store_id, "PUT")
    values, data = image_mutation_form(payload, remove_image, image)
    if data is not None or remove_image:
        raise HTTPException(403, "Managed by AWSUM Platform only: Store Logo.")
    return _save(store_id, values, request, db)


@router.get(f"{API}/zip-lookup/{{zip_code}}")
def merchant_zip_lookup(zip_code: str, request: Request, db: Session = Depends(get_db)):
    # The Store form's Address ZIP autofill (the same common lookup as Platform; read only, no Store data).
    _merchant_scope(request, db, "read")
    return JSONResponse(lookup_zip(zip_code))


@router.get(f"{API}/map-config")
def merchant_map_config(request: Request, db: Session = Depends(get_db)):
    # The common Show Map of an address form (static/js/awsum-map.js): any signed-in Portal user (the common Merchant /
    # Store login + active Merchant scope, no screen permission: it carries no tenant data); returns only enabled + the
    # browser Embed key, never a server key, another setting or any address. Address data keeps its own permissions.
    resolve_app_scope(request, db, get_current_user(request, db))
    return _set_no_cache_headers(JSONResponse(map_client_config()))


# ---- Business Contacts (Primary Contact): the common service, owner = a Store of this Merchant ----
def _contact_owner(request: Request, db: Session, owner_type: str, store_id: int, action: str):
    _user, scope = _merchant_scope(request, db, action)
    if str(owner_type or "").upper() != "STORE":
        raise HTTPException(404, "Contacts not found.")
    if action != "read":
        _validate_origin(request)
    _merchant_store(db, scope, store_id, "GET" if action == "read" else "PUT")
    return BusinessContactService(db)


def _owned_contact(request: Request, db: Session, contact_id: int) -> BusinessContactService:
    _user, scope = _merchant_scope(request, db, "update")
    _validate_origin(request)
    contact = db.get(BusinessContact, contact_id)
    if contact is None or contact.c_owner_type != "STORE":
        raise HTTPException(404, "Contact not found.")
    _merchant_store(db, scope, contact.i_owner_id, "PUT")
    return BusinessContactService(db)


@router.get(f"{API}/business-contacts/{{owner_type}}/{{store_id}}")
def merchant_list_contacts(owner_type: str, store_id: int, request: Request, db: Session = Depends(get_db)):
    return _contact_owner(request, db, owner_type, store_id, "read").get_contacts("STORE", store_id)


@router.post(f"{API}/business-contacts/{{owner_type}}/{{store_id}}")
def merchant_create_contact(owner_type: str, store_id: int, payload: dict, request: Request,
                            db: Session = Depends(get_db)):
    return _contact_owner(request, db, owner_type, store_id, "update").create_contact("STORE", store_id, payload)


@router.put(f"{API}/business-contacts/{{owner_type}}/{{store_id}}/order")
def merchant_reorder_contacts(owner_type: str, store_id: int, payload: dict, request: Request,
                              db: Session = Depends(get_db)):
    return _contact_owner(request, db, owner_type, store_id, "update").reorder(
        "STORE", store_id, list(payload.get("contact_ids") or []))


@router.put(f"{API}/business-contact/{{contact_id}}")
def merchant_update_contact(contact_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    return _owned_contact(request, db, contact_id).update_contact(contact_id, payload)


@router.post(f"{API}/business-contact/{{contact_id}}/primary")
def merchant_set_primary_contact(contact_id: int, request: Request, db: Session = Depends(get_db)):
    return _owned_contact(request, db, contact_id).set_primary(contact_id)


@router.delete(f"{API}/business-contact/{{contact_id}}")
def merchant_delete_contact(contact_id: int, request: Request, db: Session = Depends(get_db)):
    _owned_contact(request, db, contact_id).delete_contact(contact_id)
    return {"deleted": True}
