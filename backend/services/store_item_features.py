"""Store Options > Item Features (Central authority, saved on tb_store with the other Store Options).

Platform Admin and Merchant Portal always show every MD field. Only a Store login is limited by its own
Store Options. OFF only hides the field and ignores it on save: saved MD / order / output data is never
deleted or reset, so turning the option back ON shows the same values again.
"""

# feature -> (Store Options payload key, tb_store column, value for stores without a saved value)
ITEM_FEATURES = {
    "color": ("opt_use_color", "b_opt_use_color", False),
    "modifier": ("opt_use_modifier", "b_opt_use_modifier", True),
    "bottle_deposit": ("opt_use_bottle_deposit", "b_opt_use_bottle_deposit", False),
    "sugar_tax": ("opt_use_sugar_tax", "b_opt_use_sugar_tax", False),
    "output_device": ("opt_use_output_device", "b_opt_use_output_device", True),
}
# MD / MD Detail payload keys controlled by each feature. Bottle Deposit and Sugar Tax have no MD field yet.
ITEM_FEATURE_FIELDS = {"color": ("color_code_id",), "modifier": ("modifier",), "output_device": ("output_devices",)}

STORE_OPTION_DEFAULTS = {option_key: default for option_key, _col, default in ITEM_FEATURES.values()}


def store_option_default(option_key: str) -> bool:
    """Default for a Store Option that has no saved value (every other Store Option defaults to OFF)."""
    return STORE_OPTION_DEFAULTS.get(option_key, False)


def store_option_enabled(store, option_key: str, column: str) -> bool:
    value = getattr(store, column, None)
    return store_option_default(option_key) if value is None else bool(value)


def item_features(store=None) -> dict:
    """Feature flags for the MD form. No store (Platform Admin / Merchant Portal) = every feature ON."""
    return {
        feature: True if store is None else store_option_enabled(store, option_key, column)
        for feature, (option_key, column, _default) in ITEM_FEATURES.items()
    }


def without_disabled_item_features(payload: dict, features: dict) -> dict:
    """Drop fields of OFF features so the services keep the saved value (key absent = unchanged)."""
    blocked = {key for feature, keys in ITEM_FEATURE_FIELDS.items() if not features.get(feature, True) for key in keys}
    return {key: value for key, value in payload.items() if key not in blocked} if blocked else payload
