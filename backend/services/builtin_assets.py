"""Central Server built-in Platform assets; never a runtime/tenant storage location.

BUILTIN_ROOT is the single authoritative physical root, served at /images/platform.
"""
from pathlib import Path

from backend.core.data_paths import PROJECT_ROOT


BUILTIN_URL_PREFIX = "/images/platform"
BUILTIN_ROOT = PROJECT_ROOT / "static" / "images" / "platform"
CATEGORIES = frozenset({"logo", "background", "favicon", "defaults", "ui"})
FAVICON_ICO = f"{BUILTIN_URL_PREFIX}/favicon/favicon.ico"


def builtin_asset_file(reference: str) -> Path:
    parts = reference.split("/")
    if len(parts) != 5 or parts[:3] != ["", "images", "platform"] or parts[3] not in CATEGORIES:
        raise ValueError("Invalid built-in Platform asset.")
    if not parts[4] or any(value in parts[4] for value in ("/", "\\", "..", "%", "?", "#", ":")):
        raise ValueError("Invalid built-in asset filename.")
    target = BUILTIN_ROOT / parts[3] / parts[4]
    if not target.resolve().is_relative_to(BUILTIN_ROOT.resolve()):
        raise ValueError("Built-in asset escapes its program directory.")
    return target


def builtin_asset_url(category: str, filename: str) -> str:
    reference = f"/images/platform/{category}/{filename}"
    target = builtin_asset_file(reference)
    if not target.is_file():
        raise FileNotFoundError(f"Required built-in asset is missing: {reference}")
    return f"{reference}?v={target.stat().st_mtime_ns}"


def favicon_url(logo_url: str | None = None) -> str:
    """Browser-tab icon: a custom runtime logo when configured, else the built-in favicon."""
    if logo_url and not logo_url.startswith(f"{BUILTIN_URL_PREFIX}/"):
        return logo_url
    return builtin_asset_url("favicon", "favicon.png")
