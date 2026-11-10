import csv
import io
from datetime import datetime
from html import escape as esc

from fastapi import APIRouter, Depends, HTTPException, Request, Response, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.config.settings import APP_NAME
from backend.config.templates import get_brand_context, templates
from backend.routers.dashboard import _auth_user_or_home_redirect, _is_super_admin
from backend.routers.platform_store import (
    _set_no_cache_headers,
    get_current_user,
    get_db,
    require_platform_admin,
)
from backend.routers.system_settings import _validate_origin
from backend.services import master_group as svc
from backend.services import store_locations
from backend.services.images import image_mutation_form
from backend.services.reporting import table_report_html
from backend.services.app_navigation import resolve_app_scope
from backend.services.branding import resolve_branding
from backend.services.store_item_features import item_features, without_disabled_item_features
from backend.services.store_locations import location_management_enabled
from backend.models_admin.store import Store
from backend.models_admin.merchant import Merchant
from backend.models_admin.store_location import StoreLocation, StoreMdLocation
from sqlalchemy import or_

router = APIRouter()

PAGES = {
    "tax": ("tax", "Tax"),
    "category-1": ("category1", "Category I"),
    "category-2": ("category2", "Category II"),
    "category-3": ("category3", "Category III"),
    "md-master": ("md", "MD Master"),
    "item-kind": ("item_kind", "Item Kind"),
    "location-master": ("location", "Location Master"),
    "common-maintenance": ("common_code", "Common Maintenance"),
}
# Name of one record where it differs from the page title (Add button, form titles); default = page title.
RECORD_TITLES = {"location": "Location", "common_code": "Code"}
# Earlier page slugs -> current slug (old links and bookmarks keep working).
# The Color screen was merged into Common Maintenance (COLOR step 5-2; tb_color removed in step 6): its old URL
# keeps redirecting so bookmarks still work.
PAGE_REDIRECTS = {"shelf-location": "location-master", "color": "common-maintenance?group=COLOR"}
ENTITY_TO_SLUG = {entity: slug for slug, (entity, _title) in PAGES.items()}
PLATFORM_ONLY_ENTITIES = {"md": "MD Master", "item_kind": "Item Kind", "common_code": "Common Maintenance"}
# System Masters managed only by Platform Super Admin (screen, list, export and every write).
# Other Platform Admins still read active Item Kinds through /options to select them on MD Master.
SUPER_ADMIN_ENTITIES = {"item_kind": "Item Kind"}


def _admin(request: Request, db: Session):
    user = get_current_user(request, db)
    prefix = request.url.path.strip("/").split("/")[0]
    if prefix == "platform":
        require_platform_admin(user)
        request.state.master_merchant_id = None
    else:
        scope = resolve_app_scope(request, db, user)
        if prefix.upper() != scope.context:
            raise HTTPException(403, "Login scope does not match this route.")
        action = {"GET": "read", "POST": "create", "PUT": "update", "DELETE": "delete"}[request.method]
        scope.require("export" if request.url.path.endswith("/export") else action)
        request.state.master_merchant_id = scope.merchant_id
        request.state.app_scope = scope
    return user


def _can_view_cost(db: Session, user) -> bool:
    # Cost is restricted to Super Admin. Change this single rule to widen or narrow access.
    return bool(_is_super_admin(db, user))


def _item_features(request: Request, db: Session, user) -> dict:
    # Platform Admin / Merchant Portal: every MD field. Store login: its own Store Options > Item Features.
    scope = getattr(request.state, "app_scope", None)
    if scope is None or scope.context != "STORE":
        return item_features()
    return item_features(db.get(Store, user.i_store_id) or Store())


LOCATION_PAYLOAD_KEYS = ("sales_location", "warehouse_location")


def _md_payload(entity: str, payload: dict, request: Request, db: Session, user) -> dict:
    # OFF features are ignored (saved values kept), never cleared. Permission checks are unchanged.
    if entity == "md":
        payload = without_disabled_item_features(payload, _item_features(request, db, user))
    # Common recommended positions: a Store login with Use Location Management OFF never changes them.
    scope = getattr(request.state, "app_scope", None)
    if entity in ("md", "category3") and scope is not None and scope.context == "STORE"             and not location_management_enabled(db.get(Store, user.i_store_id)):
        payload = {key: value for key, value in payload.items() if key not in LOCATION_PAYLOAD_KEYS}
    return payload


# ---------- Store scope (Location Management) ----------


def _authorized_store_id(request: Request, db: Session, user, requested: int | None) -> int:
    """The Store whose Location data this request may use; checked on the server for every scope.

    Platform: any existing Store. Merchant: only its own Stores. Store login: only its own Store, and only
    while its Use Location Management option is ON (saved data is kept while OFF).
    """
    scope = getattr(request.state, "app_scope", None)
    if scope is not None and scope.context == "STORE":
        store = db.get(Store, user.i_store_id)
        if store is None or not location_management_enabled(store):
            raise HTTPException(403, "Use Location Management is off for this Store.")
        if requested is not None and requested != store.i_store_id:
            raise HTTPException(403, "A Store login can only use its own Store.")
        return store.i_store_id
    if requested is None:
        raise HTTPException(400, "Select a Store.")
    store = db.get(Store, requested)
    if store is None:
        raise HTTPException(404, "Store not found.")
    if scope is not None and store.i_merchant_id != scope.merchant_id:
        raise HTTPException(403, "This Store does not belong to your Merchant.")
    return store.i_store_id


def _store_scope(entity: str, request: Request, db: Session, user, requested: int | None) -> int | None:
    return _authorized_store_id(request, db, user, requested) if svc.entity_config(entity).get("store_scoped") else None


def _group_scope(entity: str, db: Session, group: str | None) -> str | None:
    """Common Maintenance: the requested Common Group (COLOR / UNIT), else 404. Other masters: None."""
    if not svc.entity_config(entity).get("group_scoped"):
        return None
    try:
        return svc.common_group(db, group).c_group_code
    except svc.NotFound as exc:
        raise HTTPException(404, str(exc)) from None


def _store_choices(request: Request, db: Session, user) -> list[dict]:
    """Stores offered by the Store selector: active ones, plus inactive ones that already have Location data."""
    scope = getattr(request.state, "app_scope", None)
    query = db.query(Store, Merchant.c_merchant_name).outerjoin(Merchant, Merchant.i_merchant_id == Store.i_merchant_id)
    if scope is not None and scope.context == "STORE":
        query = query.filter(Store.i_store_id == user.i_store_id)
    else:
        if scope is not None:
            query = query.filter(Store.i_merchant_id == scope.merchant_id)
        has_data = or_(
            db.query(StoreLocation.i_location_id).filter(StoreLocation.i_store_id == Store.i_store_id).exists(),
            db.query(StoreMdLocation.i_store_md_location_id).filter(StoreMdLocation.i_store_id == Store.i_store_id).exists(),
        )
        query = query.filter(or_(Store.c_status == "active", has_data))
    return [
        {"id": store.i_store_id, "code": store.c_store_code, "name": store.c_store_name, "merchant_name": merchant_name,
         "active": store.c_status == "active", "location_management": location_management_enabled(store)}
        for store, merchant_name in query.order_by(Merchant.c_merchant_name, Store.c_store_name, Store.i_store_id)
    ]


def _fail(exc: Exception) -> JSONResponse:
    if isinstance(exc, svc.FieldErrors):
        return JSONResponse({"detail": exc.message, "errors": exc.errors}, status_code=422)
    if isinstance(exc, svc.Conflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)
    if isinstance(exc, svc.NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)
    raise exc


def _entity(entity: str, request: Request | None = None, db: Session | None = None, user=None) -> str:
    if entity not in svc.ENTITIES:
        raise HTTPException(status_code=404, detail="Unknown master type.")
    if request is not None and getattr(request.state, "master_merchant_id", None) is not None and entity in PLATFORM_ONLY_ENTITIES:
        raise HTTPException(403, f"{PLATFORM_ONLY_ENTITIES[entity]} is Platform-only.")
    if entity in SUPER_ADMIN_ENTITIES and not (db is not None and user is not None and _is_super_admin(db, user)):
        raise HTTPException(403, f"{SUPER_ADMIN_ENTITIES[entity]} is managed by Platform Super Admin only.")
    return entity


@router.get("/platform/master-group/{slug}", response_class=HTMLResponse)
@router.get("/merchant/master-group/{slug}", response_class=HTMLResponse)
@router.get("/store/master-group/{slug}", response_class=HTMLResponse)
def master_group_page(slug: str, request: Request, db: Session = Depends(get_db)):
    user, redirect = _auth_user_or_home_redirect(request, db)
    if redirect:
        return redirect
    user = _admin(request, db)
    if slug in PAGE_REDIRECTS:
        target = request.url.path.rsplit("/", 1)[0] + "/" + PAGE_REDIRECTS[slug]
        if request.url.query and "?" not in target:  # a target with its own query (Color -> group=COLOR) keeps it
            target += f"?{request.url.query}"
        return RedirectResponse(target, status_code=307)
    if slug not in PAGES:
        raise HTTPException(status_code=404, detail="Page not found.")
    entity, title = PAGES[slug]
    _entity(entity, request, db, user)
    scope = getattr(request.state, "app_scope", None)
    if svc.entity_config(entity).get("store_scoped") and scope is not None and scope.context == "STORE":
        _authorized_store_id(request, db, user, None)  # Use Location Management OFF: the page is not available
    brand = resolve_branding(db, scope.context, merchant_id=scope.merchant_id,
                             store_id=user.i_store_id if scope.context == "STORE" else None) if scope else None
    return _set_no_cache_headers(templates.TemplateResponse(
        request,
        "platform/master_group.html",
        {
            "request": request,
            "APP_NAME": APP_NAME,
            "user": user,
            "role": scope.role_name if scope else "Platform Admin",
            "is_super_admin": _is_super_admin(db, user),
            "active_page": "master-group",
            "active_module": slug,
            "mg_entity": entity,
            "mg_title": title,
            "mg_record_title": RECORD_TITLES.get(entity, title),
            "mg_api_base": f"/{scope.context.lower() if scope else 'platform'}/api/master-group",
            "app_navigation": scope.navigation() if scope else None,
            **get_brand_context(context_type="platform", brand_display_name=APP_NAME),
            **(brand.template() if brand else {}),
        },
    ))


@router.get("/platform/api/master-group/options")
@router.get("/merchant/api/master-group/options")
@router.get("/store/api/master-group/options")
def master_group_options(request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    scope = getattr(request.state, "app_scope", None)
    return _set_no_cache_headers(JSONResponse({**svc.lookup_options(
        db, _can_view_cost(db, user), merchant_id=request.state.master_merchant_id,
    ), "item_features": _item_features(request, db, user),
        # Store selector for Store-scoped masters; a Store login is fixed to its own Store.
        "stores": _store_choices(request, db, user),
        "store_fixed": user.i_store_id if scope is not None and scope.context == "STORE" else None,
        # Use Location Management: Platform / Merchant always; a Store login follows its own option.
        "location_management": (location_management_enabled(db.get(Store, user.i_store_id))
                                if scope is not None and scope.context == "STORE" else True)}))


@router.get("/platform/api/master-group/{entity}")
@router.get("/merchant/api/master-group/{entity}")
@router.get("/store/api/master-group/{entity}")
def master_group_list(
    entity: str, request: Request, q: str = "", active: str = "", sort: str = "",
    direction: str = "asc", page: int = 1, size: int = 20, db: Session = Depends(get_db), store_id: int | None = None,
    group: str | None = None, location_area: str | None = None,
):
    user = _admin(request, db)
    _entity(entity, request, db, user)
    scoped_store = _store_scope(entity, request, db, user, store_id)
    try:
        data = svc.list_items(
            db, entity, q=q, active=active, sort=sort, direction=direction, page=page, size=size,
            can_cost=_can_view_cost(db, user),
            merchant_id=request.state.master_merchant_id,
            store_id=scoped_store, group=_group_scope(entity, db, group),
            location_area=location_area,  # Location Master: optional Area filter (two-column list)
        )
    except svc.FieldErrors as exc:
        return _fail(exc)
    return _set_no_cache_headers(JSONResponse(data))


def _safe_cell(value: str) -> str:
    # Stop spreadsheet apps from running user text as a formula.
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@router.get("/platform/api/master-group/{entity}/export")
@router.get("/merchant/api/master-group/{entity}/export")
@router.get("/store/api/master-group/{entity}/export")
def master_group_export(
    entity: str, request: Request, format: str = "csv", q: str = "", active: str = "",
    sort: str = "", direction: str = "asc", db: Session = Depends(get_db), store_id: int | None = None,
    group: str | None = None,
):
    user = _admin(request, db)
    _entity(entity, request, db, user)
    scoped_store = _store_scope(entity, request, db, user, store_id)
    group = _group_scope(entity, db, group)
    fmt = (format or "").lower()
    if fmt not in ("csv", "excel", "pdf"):
        raise HTTPException(status_code=400, detail="Unsupported export format.")
    can_cost = _can_view_cost(db, user)  # Cost column is omitted entirely without permission
    headers, rows = svc.export_table(db, entity, q=q, active=active, sort=sort, direction=direction,
                                    can_cost=can_cost, merchant_id=request.state.master_merchant_id,
                                    store_id=scoped_store, group=group)
    title = PAGES[ENTITY_TO_SLUG[entity]][1]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    base = f"master-{ENTITY_TO_SLUG[entity]}-{stamp}"
    if svc.entity_config(entity).get("group_scoped"):  # one Common Group per export (already validated)
        selected = svc.common_group(db, group)
        title = f"{title} - {selected.c_group_name}"
        base = f"master-{ENTITY_TO_SLUG[entity]}-{selected.c_group_code.lower()}-{stamp}"
    no_store = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"}

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        writer.writerow(headers)
        for row in rows:
            writer.writerow([_safe_cell(c) for c in row])
        body = "\ufeff" + buf.getvalue()  # BOM so Excel reads Korean text as UTF-8
        return Response(body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={**no_store, "Content-Disposition": f'attachment; filename="{base}.csv"'})

    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    if fmt == "excel":
        # Same HTML-table .xls approach as the existing list exports; text format keeps codes and prices as typed.
        body_rows = "".join(
            "<tr>" + "".join(f'<td style="mso-number-format:\'\\@\'">{esc(_safe_cell(c))}</td>' for c in row) + "</tr>"
            for row in rows
        )
        html = (f'<html><head><meta charset="UTF-8"></head><body><table border="1">'
                f"<thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table></body></html>")
        return Response(html.encode("utf-8"), media_type="application/vnd.ms-excel; charset=utf-8",
                        headers={**no_store, "Content-Disposition": f'attachment; filename="{base}.xls"'})

    return HTMLResponse(table_report_html(f"{title} Report", headers, rows), headers=no_store)

@router.post("/platform/api/master-group/{entity}")
@router.post("/merchant/api/master-group/{entity}")
@router.post("/store/api/master-group/{entity}")
def master_group_create(entity: str, payload: dict, request: Request, db: Session = Depends(get_db),
                        store_id: int | None = None, group: str | None = None):
    user = _admin(request, db)
    _validate_origin(request)
    _entity(entity, request, db, user)
    scoped_store = _store_scope(entity, request, db, user, store_id)
    try:
        item = svc.create_item(db, entity, _md_payload(entity, payload, request, db, user), user.i_platform_user_id,
                               _can_view_cost(db, user), merchant_id=request.state.master_merchant_id,
                               store_id=scoped_store, group=group)
    except (svc.FieldErrors, svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"item": item, "message": "Saved."}, status_code=201)


@router.post("/platform/api/master-group/md/with-image")
def md_create_with_image(
    request: Request, payload: str = Form(...), image: UploadFile | None = File(None),
    remove_image: bool = Form(False), db: Session = Depends(get_db),
):
    user = _admin(request, db)
    _validate_origin(request)
    values, data = image_mutation_form(payload, remove_image, image)
    try:
        item = svc.create_item(db, "md", values, user.i_platform_user_id, _can_view_cost(db, user), image_data=data)
    except (svc.FieldErrors, svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"item": item, "message": "Saved."}, status_code=201)


@router.put("/platform/api/master-group/md/{item_id}/with-image")
def md_update_with_image(
    item_id: int, request: Request, payload: str = Form(...), image: UploadFile | None = File(None),
    remove_image: bool = Form(False), db: Session = Depends(get_db),
):
    user = _admin(request, db)
    _validate_origin(request)
    values, data = image_mutation_form(payload, remove_image, image)
    try:
        item = svc.update_item(db, "md", item_id, values, user.i_platform_user_id,
                               _can_view_cost(db, user), image_data=data, remove_image=remove_image)
    except (svc.FieldErrors, svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"item": item, "message": "Saved."})


@router.get("/platform/api/master-group/md/barcode-check")
def md_barcode_check(request: Request, barcode: str = "", md_id: int | None = None, db: Session = Depends(get_db)):
    """Read-only: is this barcode free for the MD's Default Detail? Saving still re-checks on the server."""
    _admin(request, db)
    _entity("md", request)
    return _set_no_cache_headers(JSONResponse(svc.check_default_barcode(db, md_id, barcode)))


@router.get("/platform/api/master-group/md/{item_id}/details")
def md_detail_list(item_id: int, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    _entity("md", request)
    try:
        return _set_no_cache_headers(JSONResponse({"items": svc.list_md_details(db, item_id)}))
    except svc.NotFound as exc:
        return _fail(exc)


@router.post("/platform/api/master-group/md/{item_id}/details")
def md_detail_create(item_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    _validate_origin(request)
    _entity("md", request)
    try:
        item = svc.create_md_detail(db, item_id, _md_payload("md", payload, request, db, user), user.i_platform_user_id)
    except (svc.FieldErrors, svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"item": item, "message": "Saved."}, status_code=201)


@router.put("/platform/api/master-group/md/{item_id}/details/{detail_id}")
def md_detail_update(
    item_id: int, detail_id: int, payload: dict, request: Request, db: Session = Depends(get_db),
):
    user = _admin(request, db)
    _validate_origin(request)
    _entity("md", request)
    try:
        item = svc.update_md_detail(db, item_id, detail_id, _md_payload("md", payload, request, db, user),
                                    user.i_platform_user_id)
    except (svc.FieldErrors, svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"item": item, "message": "Saved."})


@router.delete("/platform/api/master-group/md/{item_id}/details/{detail_id}")
def md_detail_delete(item_id: int, detail_id: int, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    _validate_origin(request)
    _entity("md", request)
    try:
        svc.delete_md_detail(db, item_id, detail_id)
    except (svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"message": "Deleted."})


@router.post("/platform/api/master-group/location/initialize")
@router.post("/merchant/api/master-group/location/initialize")
@router.post("/store/api/master-group/location/initialize")
def location_initialize(request: Request, db: Session = Depends(get_db), store_id: int | None = None):
    """Location Master first visit of a Store: default Locations once (no button; idempotent; same Store scope)."""
    user = _admin(request, db)
    _validate_origin(request)
    _entity("location", request, db, user)
    scoped_store = _store_scope("location", request, db, user, store_id)
    try:
        data = svc.initialize_store_locations(db, user.i_platform_user_id, merchant_id=request.state.master_merchant_id,
                                              store_id=scoped_store)
    except (svc.FieldErrors, svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return _set_no_cache_headers(JSONResponse(data))


@router.post("/platform/api/master-group/location/range/preview")
@router.post("/merchant/api/master-group/location/range/preview")
@router.post("/store/api/master-group/location/range/preview")
def location_range_preview(payload: dict, request: Request, db: Session = Depends(get_db), store_id: int | None = None):
    """Location Master range form: the codes it would create and which already exist (read only)."""
    user = _admin(request, db)
    _validate_origin(request)
    _entity("location", request, db, user)
    scoped_store = _store_scope("location", request, db, user, store_id)
    try:
        data = svc.preview_location_range(db, payload, merchant_id=request.state.master_merchant_id, store_id=scoped_store)
    except (svc.FieldErrors, svc.NotFound) as exc:
        return _fail(exc)
    return _set_no_cache_headers(JSONResponse(data))


@router.post("/platform/api/master-group/location/range")
@router.post("/merchant/api/master-group/location/range")
@router.post("/store/api/master-group/location/range")
def location_range_create(payload: dict, request: Request, db: Session = Depends(get_db), store_id: int | None = None):
    """Location Master range form: create the new codes of the range in one transaction (existing codes skipped)."""
    user = _admin(request, db)
    _validate_origin(request)
    _entity("location", request, db, user)
    scoped_store = _store_scope("location", request, db, user, store_id)
    try:
        data = svc.create_location_range(db, payload, user.i_platform_user_id,
                                         merchant_id=request.state.master_merchant_id, store_id=scoped_store)
    except (svc.FieldErrors, svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({**data, "message": "Saved."}, status_code=201 if data["created"] else 200)


@router.get("/platform/api/master-group/location/codes")
@router.get("/merchant/api/master-group/location/codes")
@router.get("/store/api/master-group/location/codes")
def location_codes(request: Request, location_area: str = "", q: str = "", active: str = "active", page: int = 1,
                   size: int = 20, store_id: int | None = None, db: Session = Depends(get_db)):
    """Common Location Selector (Category III / MD Master): Location Codes of the Stores in scope, each code once.

    No store_id = All Stores of the scope (Platform: every Store; Merchant: its Stores; Store login: its own Store,
    only with Use Location Management ON). store_id = one Store, checked like Location Master.
    """
    user = _admin(request, db)
    _entity("location", request, db, user)
    scope = getattr(request.state, "app_scope", None)
    if store_id is not None or (scope is not None and scope.context == "STORE"):
        store_ids = [_authorized_store_id(request, db, user, store_id)]
    elif scope is not None:  # Merchant: its own Stores
        store_ids = [i for (i,) in db.query(Store.i_store_id).filter(Store.i_merchant_id == scope.merchant_id)]
    else:  # Platform
        store_ids = None
    try:
        data = store_locations.location_code_lookup(db, store_ids, location_area=location_area, q=q, active=active,
                                                    page=page, size=size)
    except ValueError as exc:
        field = str(exc)
        message = "Select Sales Floor or Warehouse." if field == "location_area" else "Select Active, Inactive or All."
        return _fail(svc.FieldErrors({field: message}))
    return _set_no_cache_headers(JSONResponse({**data, "store_id": store_id}))


@router.get("/platform/api/master-group/location/lookup")
@router.get("/merchant/api/master-group/location/lookup")
@router.get("/store/api/master-group/location/lookup")
def location_lookup(request: Request, store_id: int | None = None, db: Session = Depends(get_db)):
    """Read-only list of one Store's locations for selectors (same Store scope rules as Location Master)."""
    user = _admin(request, db)
    store = _authorized_store_id(request, db, user, store_id)
    return _set_no_cache_headers(JSONResponse({"store_id": store, "items": store_locations.store_location_lookup(db, store)}))


@router.get("/platform/api/master-group/category3/{item_id}/inventory-impact")
@router.get("/merchant/api/master-group/category3/{item_id}/inventory-impact")
@router.get("/store/api/master-group/category3/{item_id}/inventory-impact")
def category3_inventory_impact(item_id: int, request: Request, db: Session = Depends(get_db)):
    """Read-only counts for the Category III Inventory Type scope confirmation."""
    _admin(request, db)
    try:
        return _set_no_cache_headers(JSONResponse(
            svc.category3_inventory_impact(db, item_id, merchant_id=request.state.master_merchant_id)))
    except svc.NotFound as exc:
        return _fail(exc)


@router.put("/platform/api/master-group/{entity}/{item_id}")
@router.put("/merchant/api/master-group/{entity}/{item_id}")
@router.put("/store/api/master-group/{entity}/{item_id}")
def master_group_update(entity: str, item_id: int, payload: dict, request: Request, db: Session = Depends(get_db),
                        store_id: int | None = None, group: str | None = None):
    user = _admin(request, db)
    _validate_origin(request)
    _entity(entity, request, db, user)
    scoped_store = _store_scope(entity, request, db, user, store_id)
    if payload.get("apply_inventory_to_md") and request.state.master_merchant_id is not None:
        # MD Master is Platform-only: a Merchant / Store scope can never bulk-change MD items.
        raise HTTPException(403, "Applying to MD Master is available only in Platform Admin.")
    try:
        item = svc.update_item(db, entity, item_id, _md_payload(entity, payload, request, db, user),
                               user.i_platform_user_id, _can_view_cost(db, user),
                               merchant_id=request.state.master_merchant_id, store_id=scoped_store, group=group)
    except (svc.FieldErrors, svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"item": item, "message": "Saved."})


@router.delete("/platform/api/master-group/{entity}/{item_id}")
@router.delete("/merchant/api/master-group/{entity}/{item_id}")
@router.delete("/store/api/master-group/{entity}/{item_id}")
def master_group_delete(entity: str, item_id: int, request: Request, db: Session = Depends(get_db),
                        store_id: int | None = None, group: str | None = None):
    user = _admin(request, db)
    _validate_origin(request)
    _entity(entity, request, db, user)
    scoped_store = _store_scope(entity, request, db, user, store_id)
    try:
        warning = svc.delete_item(db, entity, item_id, merchant_id=request.state.master_merchant_id,
                                  store_id=scoped_store, group=group)
    except (svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"message": warning or "Deleted."})
