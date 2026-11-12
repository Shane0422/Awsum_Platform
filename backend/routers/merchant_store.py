"""Merchant Store Management: a Merchant login views its own Stores and edits their allowed operating information.

Reuses the Platform Store Management screen (templates/platform/master_management.html, module "store") and its
processing (routers.platform_store: list query, detail, _update_store, Store Logo save, BusinessContactService).
Only the authorization and the field policy below are Merchant specific.

Policy (server side; the screen only mirrors it):
- Platform Admin only: Store registration / deletion / deactivation, Store ID / Code, Merchant / Agency assignment,
  Store Status, License, contract information, max user / terminal count, Test flag, Dashboard Type, Devices.
  There is no Merchant create / delete route.
- MERCHANT_STORE_EDITABLE: the only fields a Merchant save applies.
- MERCHANT_STORE_PROTECTED: shown read-only; a request that changes one is refused (403), the same value passes
  (the common form sends the whole form).
- MERCHANT_STORE_HIDDEN: Platform internal values (Notes / Memo / Remark, License Key, contract terms ...). Never
  returned to a Merchant; sending one is refused (403) without comparing, so the value cannot be probed.
- Any other payload key: 422.
- Inactive Store: view only (AppScope.require_store_access). A Store login has no Store Management (403, no menu).
- Business Options / Item Features: a feature that is not contracted or that the Merchant turned OFF cannot be newly
  turned ON (403). Saved values are kept; the effective status still follows the Common Feature Resolver.
- Store Logo and Business Contacts: every read and write checks that the Store belongs to the login's Merchant.
"""
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
    _set_no_cache_headers,
    _store_detail_data,
    _update_store,
    get_current_user,
    get_db,
    lookup_zip,
)
from backend.routers.system_settings import _validate_origin
from backend.services.app_navigation import STORE_MANAGEMENT_PERMISSION, resolve_app_scope
from backend.services.branding import resolve_branding
from backend.services.business_contact import BusinessContactService
from backend.services.features import FEATURES, REASON_TEXT, REGISTRY, resolve_features
from backend.services.images import image_mutation_form
from backend.services.merchant_features import business_option_defs
from backend.services.store_item_features import store_option_enabled

router = APIRouter()

API = "/merchant/api"
LABEL = "Store Management"
_OPTION_KEYS = frozenset(STORE_OPTION_FIELDS)
_SCOPE_KEYS = frozenset(scope_key for _col, scope_key, _scol in STORE_OPTION_FIELDS.values() if scope_key)
_FEATURE_OF_OPTION = {f.payload_key: f.key for f in REGISTRY if f.payload_key}

MERCHANT_STORE_EDITABLE = frozenset({
    "store_name", "business_type", "operation_type", "store_purpose",
    "zip", "city", "state", "address", "address_line1", "address_line2", "country", "timezone", "default_tax_rate",
    "contact_name", "owner_name", "phone", "email",
    "receipt_store_name", "receipt_phone", "receipt_email", "receipt_website_url", "receipt_message",
}) | _OPTION_KEYS | _SCOPE_KEYS
# payload key -> the Merchant view key it is compared with
MERCHANT_STORE_PROTECTED = {
    "id": "store_id", "store_id": "store_id", "store_code": "store_code", "store_seq": "store_seq",
    "merchant_id": "merchant_id", "installed_by_agency_id": "installed_by_agency_id",
    "status": "store_status", "store_status": "store_status",
    "store_license_status": "store_license_status", "store_license_start_date": "store_license_start_date",
    "license_expire_date": "license_expire_date", "license_count": "license_count", "tax_source": "tax_source",
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
_CASELESS_KEYS = {"store_status", "store_license_status", "store_code"}
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
    if "store_name" in values and not str(values["store_name"] or "").strip():
        raise HTTPException(422, "Store Name is required.")
    access = None
    for option_key in sorted(_OPTION_KEYS & set(values)):
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
    # Store Logo: the common uploader + save of the Store form (owner = this Store, checked before anything is read).
    _user, scope = _merchant_scope(request, db, "update")
    _merchant_store(db, scope, store_id, "PUT")
    values, data = image_mutation_form(payload, remove_image, image)
    return _save(store_id, values, request, db, image_data=data, remove_image=remove_image)


@router.get(f"{API}/zip-lookup/{{zip_code}}")
def merchant_zip_lookup(zip_code: str, request: Request, db: Session = Depends(get_db)):
    # The Store form's Address ZIP autofill (the same common lookup as Platform; read only, no Store data).
    _merchant_scope(request, db, "read")
    return JSONResponse(lookup_zip(zip_code))


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
