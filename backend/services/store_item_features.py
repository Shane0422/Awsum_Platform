"""Store Options > Item Features (Central authority, saved on tb_store with the other Store Options).

The definitions live in the Common Feature Registry (services.features); this module keeps the existing API.
Platform Admin and Merchant Portal always show every MD field. Only a Store login is limited by its own
Store Options. OFF only hides the field and ignores it on save: saved MD / order / output data is never
deleted or reset, so turning the option back ON shows the same values again.
"""
from backend.services.features import FEATURES, STORE_OPTION_DEFAULTS, features_in, resolve_features

# feature -> (Store Options payload key, tb_store column, value for stores without a saved value)
ITEM_FEATURES = {f.key: (f.payload_key, f.store_column, f.store_default) for f in features_in("item")}
# MD / MD Detail payload keys controlled by each feature. Bottle Deposit and Sugar Tax have no MD field yet.
ITEM_FEATURE_FIELDS = {f.key: f.md_fields for f in features_in("item") if f.md_fields}


def store_option_default(option_key: str) -> bool:
    """Default for a Store Option that has no saved value (every other Store Option defaults to OFF)."""
    return STORE_OPTION_DEFAULTS.get(option_key, False)


def store_option_enabled(store, option_key: str, column: str) -> bool:
    """The Store's saved assignment (NULL = default). Not the effective status: see services.features."""
    value = getattr(store, column, None)
    return store_option_default(option_key) if value is None else bool(value)


def item_features(store=None, entitlements=None) -> dict:
    """Feature flags for the MD form. No store (Platform Admin / Merchant Portal) = every entitled feature ON.

    entitlements=None is the LEGACY entitlement (every feature entitled), so the result equals today's.
    """
    states = resolve_features(store, entitlements)
    return {key: states[key].effective for key in ITEM_FEATURES}


def without_disabled_item_features(payload: dict, features: dict) -> dict:
    """Drop fields of OFF features so the services keep the saved value (key absent = unchanged)."""
    blocked = {key for feature, keys in ITEM_FEATURE_FIELDS.items() if not features.get(feature, True) for key in keys}
    return {key: value for key, value in payload.items() if key not in blocked} if blocked else payload


assert all(FEATURES[key].group == "item" for key in ITEM_FEATURES)
