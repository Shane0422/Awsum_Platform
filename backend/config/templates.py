import logging
import os
from typing import Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from backend.config.settings import APP_NAME
from backend.database.pg_platform import PlatformSessionLocal
from backend.services.branding import BrandingContext, resolve_branding, resolve_access_code
from backend.services.images import image_url, platform_logo_url
from backend.services.builtin_assets import builtin_asset_url, favicon_url


logger = logging.getLogger(__name__)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")


def _public_brand(context_type="platform", client_code=None, store_code=None) -> BrandingContext:
    engine = PlatformSessionLocal.kw.get("bind")
    if engine is not None and inspect(engine).has_table("tb_system_settings"):
        with PlatformSessionLocal() as db:
            if context_type == "store" and store_code:
                return resolve_access_code(db, store_code, "STORE")
            if context_type in {"client", "account", "merchant"} and client_code:
                return resolve_access_code(db, client_code, "MERCHANT")
            if context_type == "agency" and client_code:
                return resolve_access_code(db, client_code, "AGENCY")
            return resolve_branding(db)
    return BrandingContext("PLATFORM", APP_NAME, platform_logo_url(),
                           APP_NAME, "/platform-admin", "ADMIN", "Platform Administration")


def get_company_branding() -> dict:
    try:
        brand = _public_brand()
    except SQLAlchemyError:
        logger.exception("Unable to load public platform branding.")
        return {"company_logo_path": platform_logo_url(), "company_name": APP_NAME}
    return {"company_logo_path": brand.logo_url, "company_name": brand.organization_name}


def resolve_brand_asset_paths(
    *, context_type: str, client_code: Optional[str] = None,
    account_code: Optional[str] = None, store_code: Optional[str] = None,
) -> dict:
    brand = _public_brand(context_type, client_code or account_code, store_code)
    return {"brand_icon_path": favicon_url(brand.logo_url), "brand_logo_path": brand.logo_url}


def get_brand_context(
    *, context_type: str = "platform", brand_display_name: Optional[str] = None,
    brand_icon_path: Optional[str] = None, client_code: Optional[str] = None,
    account_code: Optional[str] = None, store_code: Optional[str] = None,
):
    assets = resolve_brand_asset_paths(context_type=context_type, client_code=client_code,
                                       account_code=account_code, store_code=store_code)
    return {
        "brand_context": context_type, "brand_display_name": brand_display_name or APP_NAME,
        **assets, "brand_icon_path": brand_icon_path or assets["brand_icon_path"],
        **get_company_branding(), "brand_client_code": client_code or account_code,
        "brand_store_code": store_code,
    }


def _default_brand_context(request: Request):
    # Entry routes set public context explicitly; cookies/local storage never select branding.
    brand = getattr(request.state, "login_branding", None)
    if brand is not None:
        return brand.template()
    if request.url.path == "/":
        return _public_brand().template()
    return get_brand_context(context_type="platform")


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[_default_brand_context])
templates.env.globals["APP_NAME"] = APP_NAME
templates.env.globals["image_url"] = image_url
templates.env.globals["builtin_asset_url"] = builtin_asset_url
