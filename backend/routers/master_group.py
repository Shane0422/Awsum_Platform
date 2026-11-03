import csv
import io
from datetime import datetime
from html import escape as esc

from fastapi import APIRouter, Depends, HTTPException, Request, Response, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
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
from backend.services.images import image_mutation_form
from backend.services.reporting import table_report_html
from backend.services.app_navigation import resolve_app_scope
from backend.services.branding import resolve_branding

router = APIRouter()

PAGES = {
    "tax": ("tax", "Tax"),
    "category-1": ("category1", "Category I"),
    "category-2": ("category2", "Category II"),
    "category-3": ("category3", "Category III"),
    "md-master": ("md", "MD Master"),
    "item-kind": ("item_kind", "Item Kind"),
}
ENTITY_TO_SLUG = {entity: slug for slug, (entity, _title) in PAGES.items()}
PLATFORM_ONLY_ENTITIES = {"md": "MD Master", "item_kind": "Item Kind"}


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


def _fail(exc: Exception) -> JSONResponse:
    if isinstance(exc, svc.FieldErrors):
        return JSONResponse({"detail": exc.message, "errors": exc.errors}, status_code=422)
    if isinstance(exc, svc.Conflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)
    if isinstance(exc, svc.NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)
    raise exc


def _entity(entity: str, request: Request | None = None) -> str:
    if entity not in svc.ENTITIES:
        raise HTTPException(status_code=404, detail="Unknown master type.")
    if request is not None and getattr(request.state, "master_merchant_id", None) is not None and entity in PLATFORM_ONLY_ENTITIES:
        raise HTTPException(403, f"{PLATFORM_ONLY_ENTITIES[entity]} is Platform-only.")
    return entity


@router.get("/platform/master-group/{slug}", response_class=HTMLResponse)
@router.get("/merchant/master-group/{slug}", response_class=HTMLResponse)
@router.get("/store/master-group/{slug}", response_class=HTMLResponse)
def master_group_page(slug: str, request: Request, db: Session = Depends(get_db)):
    user, redirect = _auth_user_or_home_redirect(request, db)
    if redirect:
        return redirect
    user = _admin(request, db)
    if slug not in PAGES:
        raise HTTPException(status_code=404, detail="Page not found.")
    entity, title = PAGES[slug]
    _entity(entity, request)
    scope = getattr(request.state, "app_scope", None)
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
    return _set_no_cache_headers(JSONResponse(svc.lookup_options(
        db, _can_view_cost(db, user), merchant_id=request.state.master_merchant_id,
    )))


@router.get("/platform/api/master-group/{entity}")
@router.get("/merchant/api/master-group/{entity}")
@router.get("/store/api/master-group/{entity}")
def master_group_list(
    entity: str, request: Request, q: str = "", active: str = "", sort: str = "",
    direction: str = "asc", page: int = 1, size: int = 20, db: Session = Depends(get_db),
):
    user = _admin(request, db)
    _entity(entity, request)
    return _set_no_cache_headers(JSONResponse(
        svc.list_items(
            db, entity, q=q, active=active, sort=sort, direction=direction, page=page, size=size,
            can_cost=_can_view_cost(db, user),
            merchant_id=request.state.master_merchant_id,
        )
    ))


def _safe_cell(value: str) -> str:
    # Stop spreadsheet apps from running user text as a formula.
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@router.get("/platform/api/master-group/{entity}/export")
@router.get("/merchant/api/master-group/{entity}/export")
@router.get("/store/api/master-group/{entity}/export")
def master_group_export(
    entity: str, request: Request, format: str = "csv", q: str = "", active: str = "",
    sort: str = "", direction: str = "asc", db: Session = Depends(get_db),
):
    user = _admin(request, db)
    _entity(entity, request)
    fmt = (format or "").lower()
    if fmt not in ("csv", "excel", "pdf"):
        raise HTTPException(status_code=400, detail="Unsupported export format.")
    can_cost = _can_view_cost(db, user)  # Cost column is omitted entirely without permission
    headers, rows = svc.export_table(db, entity, q=q, active=active, sort=sort, direction=direction,
                                    can_cost=can_cost, merchant_id=request.state.master_merchant_id)
    title = PAGES[ENTITY_TO_SLUG[entity]][1]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    base = f"master-{ENTITY_TO_SLUG[entity]}-{stamp}"
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
def master_group_create(entity: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    _validate_origin(request)
    _entity(entity, request)
    try:
        item = svc.create_item(db, entity, payload, user.i_platform_user_id, _can_view_cost(db, user),
                               merchant_id=request.state.master_merchant_id)
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
        item = svc.create_md_detail(db, item_id, payload, user.i_platform_user_id)
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
        item = svc.update_md_detail(db, item_id, detail_id, payload, user.i_platform_user_id)
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


@router.put("/platform/api/master-group/{entity}/{item_id}")
@router.put("/merchant/api/master-group/{entity}/{item_id}")
@router.put("/store/api/master-group/{entity}/{item_id}")
def master_group_update(entity: str, item_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    _validate_origin(request)
    _entity(entity, request)
    try:
        item = svc.update_item(db, entity, item_id, payload, user.i_platform_user_id, _can_view_cost(db, user),
                               merchant_id=request.state.master_merchant_id)
    except (svc.FieldErrors, svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"item": item, "message": "Saved."})


@router.delete("/platform/api/master-group/{entity}/{item_id}")
@router.delete("/merchant/api/master-group/{entity}/{item_id}")
@router.delete("/store/api/master-group/{entity}/{item_id}")
def master_group_delete(entity: str, item_id: int, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    _validate_origin(request)
    _entity(entity, request)
    try:
        warning = svc.delete_item(db, entity, item_id, merchant_id=request.state.master_merchant_id)
    except (svc.Conflict, svc.NotFound) as exc:
        db.rollback()
        return _fail(exc)
    return JSONResponse({"message": warning or "Deleted."})
