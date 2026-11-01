"""Public-safe branding resolved from explicit entry context, never a user's session."""
from dataclasses import asdict, dataclass
import re
import logging

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.services import images
from backend.services.builtin_assets import favicon_url
from backend.models_admin.account import Account
from backend.models_admin.agency import Agency
from backend.models_admin.store import Store
from backend.models_admin.system_settings import SystemSettings


CONTEXTS = {"PLATFORM", "AGENCY", "MERCHANT", "STORE"}
ENTRY_PATTERN = re.compile(r"^/login/(agency|merchant|store)/[A-Za-z0-9_-]{1,32}$")


def versioned_logo(url: str | None) -> str | None:
    return images.logo_url(url)


def valid_login_entry(path: str | None) -> bool:
    return path in {"/", "/login", "/platform-admin"} or bool(ENTRY_PATTERN.fullmatch(path or ""))


@dataclass(frozen=True)
class BrandingContext:
    context_type: str
    organization_name: str
    logo_url: str | None
    brand_name: str
    login_entry: str
    access_code: str
    description: str

    def public(self) -> dict:
        return asdict(self)

    def template(self) -> dict:
        return {
            "login_branding": self.public(),
            "company_name": self.organization_name, "company_logo_path": self.logo_url,
            "brand_display_name": self.organization_name, "brand_logo_path": self.logo_url,
            "brand_icon_path": favicon_url(self.logo_url), "brand_context": self.context_type.lower(),
        }


def resolve_branding(
    db: Session, context_type: str = "PLATFORM", *,
    agency_id: int | None = None, merchant_id: int | None = None, store_id: int | None = None,
    include_inactive: bool = False,
) -> BrandingContext:
    context = context_type.upper()
    if context not in CONTEXTS:
        raise HTTPException(422, "Invalid login context.")
    settings = db.get(SystemSettings, 1)
    platform_name = (
        ((settings.c_display_name or "").strip() or (settings.c_company_name or "").strip())
        if settings else ""
    ) or "AWSUM PLATFORM"
    platform_logo = images.platform_logo_url(settings.c_logo_url if settings else None)
    if context == "PLATFORM":
        return BrandingContext(context, platform_name, platform_logo, platform_name,
                               "/platform-admin", "ADMIN", "Platform Administration")
    store = db.get(Store, store_id) if context == "STORE" and store_id else None
    merchant = db.get(Account, store.i_account_id) if store and store.i_account_id else (
        db.get(Account, merchant_id) if context == "MERCHANT" and merchant_id else None
    )
    agency = db.get(Agency, merchant.i_agency_id) if merchant and merchant.i_agency_id else (
        db.get(Agency, agency_id) if context == "AGENCY" and agency_id else None
    )
    selected = {"STORE": store, "MERCHANT": merchant, "AGENCY": agency}[context]
    if selected is None or (not include_inactive and (selected.c_status or "").lower() != "active"):
        raise HTTPException(404, "Login organization not found.")
    candidates = []
    for row in (store, merchant, agency):
        if row is None or not row.c_logo_url:
            continue
        try:
            image = images.ImagePath.parse(row.c_logo_url.split("?", 1)[0])
        except ValueError:
            # Legacy references remain readable until the copy-only migration runs.
            candidates.append(row.c_logo_url)
            continue
        if row is store:
            valid = (merchant is not None and image.context_type == "STORE"
                     and image.owner_code == store.c_store_code and image.merchant_code == merchant.c_client_code)
        elif row is merchant:
            valid = image.context_type == "MERCHANT" and image.owner_code == merchant.c_client_code
        else:
            valid = image.context_type == "AGENCY" and image.owner_code == agency.c_agency_code
        if valid:
            candidates.append(row.c_logo_url)
        else:
            logging.getLogger(__name__).warning("Ignoring branding image with mismatched owner: %s", row.c_logo_url)
    logo = next((asset for candidate in candidates if (asset := versioned_logo(candidate))), platform_logo)
    name = store.c_store_name if context == "STORE" else (
        merchant.c_account_name if context == "MERCHANT" else agency.c_agency_name
    )
    code = store.c_store_code if context == "STORE" else (
        merchant.c_client_code if context == "MERCHANT" else agency.c_agency_code
    )
    if not code or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code):
        raise HTTPException(422, "Organization has no valid login entry code.")
    return BrandingContext(context, name, logo, name, f"/login/{context.lower()}/{code}",
                           code, f"{context.title()} Sign In")


def resolve_access_code(
    db: Session, access_code: str, context_type: str | None = None, *, include_inactive: bool = False,
) -> BrandingContext:
    code = access_code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{1,32}", code):
        raise HTTPException(422, "Enter a valid Access Code.")
    context = context_type.upper() if context_type else None
    if context and context not in CONTEXTS:
        raise HTTPException(422, "Invalid login context.")
    if code == "ADMIN":
        if context not in {None, "PLATFORM"}:
            raise HTTPException(404, "Login organization not found.")
        return resolve_branding(db)
    matches = []
    for kind, model, column, key in (
        ("AGENCY", Agency, Agency.c_agency_code, "agency_id"),
        ("MERCHANT", Account, Account.c_client_code, "merchant_id"),
        ("STORE", Store, Store.c_store_code, "store_id"),
    ):
        if context and context != kind:
            continue
        query = db.query(model).filter(func.upper(column) == code)
        if not include_inactive:
            query = query.filter(func.lower(model.c_status) == "active")
        rows = query.all()
        for row in rows:
            identifier = row.i_agency_id if kind == "AGENCY" else row.i_client_id if kind == "MERCHANT" else row.i_store_id
            matches.append(resolve_branding(db, kind, include_inactive=include_inactive, **{key: identifier}))
    if not matches:
        raise HTTPException(404, "Login organization not found.")
    if len(matches) != 1:
        raise HTTPException(409, "Access Code is ambiguous. Use the organization-specific login entry.")
    return matches[0]


def identity_login_entry(db: Session, identity: dict) -> str:
    context = identity["c_login_context"]
    if context == "PLATFORM":
        return "/platform-admin"
    if context == "AGENCY":
        organization = db.get(Agency, identity["i_agency_id"])
        code = organization.c_agency_code
    elif context == "MERCHANT":
        organization = db.get(Account, identity["i_account_id"])
        code = organization.c_client_code
    else:
        organization = db.get(Store, identity["i_store_id"])
        code = organization.c_store_code
    entry = f"/login/{context.lower()}/{code}"
    if not valid_login_entry(entry):
        raise HTTPException(422, "Organization has no valid login entry code.")
    return entry
