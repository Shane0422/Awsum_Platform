import os
import re
from typing import Optional

from fastapi import Request

from fastapi.templating import Jinja2Templates
from backend.config.settings import APP_NAME

# Jinja2 템플릿 엔진 초기화
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
BRANDS_STATIC_PREFIX = "/static/images/brands"
DEFAULT_BRAND_ICON_PATH = "/static/images/platform/icon.png"
DEFAULT_BRAND_LOGO_PATH = DEFAULT_BRAND_ICON_PATH


def _sanitize_brand_code(value: Optional[str]) -> Optional[str]:
	if not value:
		return None
	normalized = re.sub(r"[^A-Za-z0-9_-]", "", str(value).strip())
	if not normalized:
		return None
	return normalized.upper()


def _to_fs_path(static_url_path: str) -> str:
	relative = static_url_path.replace("/static/", "", 1).replace("/", os.sep)
	return os.path.join(STATIC_DIR, relative)


def _first_existing_static_path(*paths: str) -> Optional[str]:
	for candidate in paths:
		if candidate and os.path.exists(_to_fs_path(candidate)):
			return candidate
	return None


def resolve_brand_asset_paths(
	*,
	context_type: str,
	client_code: Optional[str] = None,
	account_code: Optional[str] = None,
	store_code: Optional[str] = None,
) -> dict:
	"""Store -> Client -> Platform 순서로 아이콘/로고 URL을 결정합니다."""
	normalized_context = (context_type or "platform").strip().lower()
	normalized_client_code = _sanitize_brand_code(client_code) or _sanitize_brand_code(account_code)
	normalized_store_code = _sanitize_brand_code(store_code)

	platform_icon = f"{BRANDS_STATIC_PREFIX}/platform/icon.png"
	platform_logo = f"{BRANDS_STATIC_PREFIX}/platform/logo.png"

	client_icon = (
		f"{BRANDS_STATIC_PREFIX}/clients/{normalized_client_code}/icon.png"
		if normalized_client_code
		else None
	)
	client_logo = (
		f"{BRANDS_STATIC_PREFIX}/clients/{normalized_client_code}/logo.png"
		if normalized_client_code
		else None
	)

	legacy_customer_icon = (
		f"{BRANDS_STATIC_PREFIX}/customers/{normalized_client_code}/icon.png"
		if normalized_client_code
		else None
	)
	legacy_customer_logo = (
		f"{BRANDS_STATIC_PREFIX}/customers/{normalized_client_code}/logo.png"
		if normalized_client_code
		else None
	)

	client_store_icon = (
		f"{BRANDS_STATIC_PREFIX}/clients/{normalized_client_code}/stores/{normalized_store_code}/icon.png"
		if normalized_client_code and normalized_store_code
		else None
	)
	client_store_logo = (
		f"{BRANDS_STATIC_PREFIX}/clients/{normalized_client_code}/stores/{normalized_store_code}/logo.png"
		if normalized_client_code and normalized_store_code
		else None
	)

	store_icon = client_store_icon
	store_logo = client_store_logo
	legacy_customer_store_icon = (
		f"{BRANDS_STATIC_PREFIX}/customers/{normalized_client_code}/stores/{normalized_store_code}/icon.png"
		if normalized_client_code and normalized_store_code
		else None
	)
	legacy_customer_store_logo = (
		f"{BRANDS_STATIC_PREFIX}/customers/{normalized_client_code}/stores/{normalized_store_code}/logo.png"
		if normalized_client_code and normalized_store_code
		else None
	)

	if normalized_context == "store":
		icon_candidates = (
			client_store_icon,
			store_icon,
			legacy_customer_store_icon,
			client_icon,
			legacy_customer_icon,
			platform_icon,
			DEFAULT_BRAND_ICON_PATH,
		)
		logo_candidates = (
			client_store_logo,
			store_logo,
			legacy_customer_store_logo,
			client_logo,
			legacy_customer_logo,
			platform_logo,
			DEFAULT_BRAND_LOGO_PATH,
		)
	elif normalized_context in {"client", "account"}:
		icon_candidates = (client_icon, legacy_customer_icon, platform_icon, DEFAULT_BRAND_ICON_PATH)
		logo_candidates = (client_logo, legacy_customer_logo, platform_logo, DEFAULT_BRAND_LOGO_PATH)
	else:
		icon_candidates = (platform_icon, DEFAULT_BRAND_ICON_PATH)
		logo_candidates = (platform_logo, DEFAULT_BRAND_LOGO_PATH)

	resolved_icon = _first_existing_static_path(*icon_candidates) or DEFAULT_BRAND_ICON_PATH
	resolved_logo = _first_existing_static_path(*logo_candidates) or resolved_icon
	return {
		"brand_icon_path": resolved_icon,
		"brand_logo_path": resolved_logo,
	}


def get_brand_context(
	*,
	context_type: str = "platform",
	brand_display_name: Optional[str] = None,
	brand_icon_path: Optional[str] = None,
	client_code: Optional[str] = None,
	account_code: Optional[str] = None,
	store_code: Optional[str] = None,
):
	"""그 UI 컨텍스트에 맞은 표준화된 템플릿 브랜드 변수를 반환합니다."""
	normalized_context = (context_type or "platform").strip().lower()
	display_name = (brand_display_name or "").strip() or APP_NAME
	resolved_assets = resolve_brand_asset_paths(
		context_type=normalized_context,
		client_code=client_code,
		account_code=account_code,
		store_code=store_code,
	)
	normalized_client_code = _sanitize_brand_code(client_code) or _sanitize_brand_code(account_code)
	icon_path = (brand_icon_path or "").strip() or resolved_assets["brand_icon_path"]
	return {
		"brand_context": normalized_context,
		"brand_display_name": display_name,
		"brand_icon_path": icon_path,
		"brand_logo_path": resolved_assets["brand_logo_path"],
		"brand_client_code": normalized_client_code,
		"brand_store_code": _sanitize_brand_code(store_code),
	}


def _default_brand_context(_: Request):
	return get_brand_context(context_type="platform")

templates = Jinja2Templates(
	directory=TEMPLATES_DIR,
	context_processors=[_default_brand_context],
)

# 전역 변수 등록
templates.env.globals["APP_NAME"] = APP_NAME
