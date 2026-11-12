"""Common Feature Policy: one Feature Registry and one resolver for Business Options, Item Features and Channels.

Layers (User Permission and Device License are separate checks, never part of this resolver):

    Platform -> Merchant : BUSINESS OPTIONS      tb_merchant_feature (contract; no row = tier default)
    Merchant (own choice): FEATURE ENTITLEMENTS  tb_merchant_feature_choice (no row = ON, follows the contract)
    Merchant -> Store    : STORE OPTIONS         tb_store.b_opt_* (NULL = the feature's store default)
    Store Status         : the Store must be Active (only with a loaded FeatureSnapshot)

    effective = available AND contracted AND merchant ON AND store ON AND requires AND store active

Rules that every caller relies on:
- Resolving never writes anything. An upper layer turning OFF only changes the *effective* result; lower layers'
  saved values and all business data stay, so turning the upper layer back ON restores the same result.
- Option screens show every feature. A feature that is not effective is shown Disabled with the reason code
  (``FeatureState.reason``); hiding is a screen decision (the MD form keeps hiding OFF Item Feature fields).
- BASE / ADDON is a registry attribute only (default contract for a Merchant without a saved Business Option);
  it is not a final commercial policy and can be changed here without touching any table.
- Fail-closed: saved layers are read only through load_feature_snapshot(); a database error or a missing table raises
  FeatureCheckUnavailable (callers refuse the operation), never "everything allowed".

``entitlements`` argument of the resolver:
- a FeatureSnapshot (load_feature_snapshot): every layer including Store Status (the policy from STEP 3-3);
- a mapping key -> bool: the Business Option layer only (earlier callers / tests);
- None: LEGACY, every available feature contracted and chosen (Platform Admin screens, tests). Since STEP 3-3C the
  Merchant / Store operational checks pass the login's FeatureSnapshot (services.app_navigation.AppScope:
  Use Location Management and the MD Item Features); only the Admin Store stays exempt (entitlement_exempt).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

BASE = "BASE"    # included by default (entitled when the Merchant has no saved entitlement)
ADDON = "ADDON"  # separate contract (not entitled when the Merchant has no saved entitlement)
GROUPS = ("business", "item", "channel")

# Disabled reason codes, in this priority (screens map them to text; the server reports them as-is).
NOT_AVAILABLE = "not_available"        # not built yet: shown as "Coming soon"
NOT_CONTRACTED = "not_contracted"      # Platform Business Option OFF for the Merchant
NOT_ENTITLED = NOT_CONTRACTED          # earlier name of the same reason
MERCHANT_OFF = "merchant_off"          # contracted, but the Merchant turned its Feature Entitlement OFF
STORE_OFF = "store_off"                # not assigned to this Store (Store Option OFF)
REQUIRES = "requires"                  # a required feature is not effective (see FeatureState.blocked_by)
STORE_INACTIVE = "store_inactive"      # the Store is Inactive (no operation)
REASON_TEXT = {
    NOT_AVAILABLE: "Coming soon.",
    NOT_CONTRACTED: "Not included in your contract (Business Options).",
    MERCHANT_OFF: "Turned off by your Merchant (Feature Entitlements).",
    STORE_OFF: "Turned off for this Store.",
    REQUIRES: "Requires another feature.",
    STORE_INACTIVE: "This Store is Inactive.",
}


@dataclass(frozen=True)
class FeatureDef:
    key: str
    label: str
    group: str                          # business | item | channel
    tier: str                           # BASE | ADDON
    store_column: Optional[str] = None  # tb_store column of the Store Assignment; None = not assigned per Store
    store_default: bool = False         # assignment while the Store column is NULL
    payload_key: Optional[str] = None   # Store Options form / API key
    scope_key: Optional[str] = None     # Store Options scope payload key (Customer / Loyalty)
    scope_column: Optional[str] = None
    md_fields: tuple = ()               # MD / MD Detail payload keys controlled by an Item Feature
    requires: tuple = ()                # feature keys that must be effective too
    available: bool = True              # False = not implemented yet (Coming soon)

    @property
    def assignable(self) -> bool:
        return self.store_column is not None


# Order = display order inside each group. Defaults are today's defaults (unchanged).
REGISTRY: tuple[FeatureDef, ...] = (
    # Business Options (tb_store; Platform Store form > Business Options)
    FeatureDef("customer", "Customer Management", "business", ADDON, "b_opt_customer", False, "opt_customer",
               "customer_scope", "c_customer_scope"),
    FeatureDef("loyalty", "Loyalty / Points", "business", ADDON, "b_opt_loyalty", False, "opt_loyalty",
               "loyalty_scope", "c_loyalty_scope"),
    FeatureDef("gift_card", "Gift Card", "business", ADDON, available=False),
    FeatureDef("time_attendance", "Time & Attendance", "business", ADDON, "b_opt_time_attendance", False,
               "opt_time_attendance"),
    FeatureDef("inventory", "Inventory Management", "business", ADDON, "b_opt_inventory", False, "opt_inventory"),
    # Use Location Management: kept column of the earlier Shelf Location option; independent of Inventory.
    FeatureDef("location_management", "Use Location Management", "business", ADDON, "b_opt_use_shelf_location", False,
               "opt_location_management"),
    FeatureDef("accounting", "Accounting", "business", ADDON, "b_opt_accounting", False, "opt_accounting"),
    # Item Features (tb_store; Platform Store form > Item Features). Bottle Deposit / Sugar Tax have no MD field yet.
    FeatureDef("color", "Use Color", "item", BASE, "b_opt_use_color", False, "opt_use_color", md_fields=("color_code_id",)),
    FeatureDef("modifier", "Use Modifier", "item", BASE, "b_opt_use_modifier", True, "opt_use_modifier", md_fields=("modifier",)),
    FeatureDef("bottle_deposit", "Use Bottle Deposit", "item", BASE, "b_opt_use_bottle_deposit", False, "opt_use_bottle_deposit"),
    FeatureDef("sugar_tax", "Use Sugar Tax", "item", BASE, "b_opt_use_sugar_tax", False, "opt_use_sugar_tax"),
    FeatureDef("output_device", "Use Output Device", "item", BASE, "b_opt_use_output_device", True, "opt_use_output_device",
               md_fields=("output_devices",)),
    # Channels: the entitlement only. Device / ordering quantities stay in tb_store_entitlement + Device Licenses and
    # are judged separately by the license code (not assigned per Store here).
    FeatureDef("kiosk", "KIOSK", "channel", ADDON),
    FeatureDef("online_ordering", "Online Ordering", "channel", ADDON),
)
FEATURES: dict[str, FeatureDef] = {feature.key: feature for feature in REGISTRY}


def _check_registry(registry) -> None:
    keys = [feature.key for feature in registry]
    assert len(keys) == len(set(keys)), "duplicate feature key"
    for feature in registry:
        assert feature.group in GROUPS and feature.tier in (BASE, ADDON), feature.key
        assert all(req in keys and req != feature.key for req in feature.requires), feature.key
        assert not feature.store_column or feature.payload_key, feature.key


_check_registry(REGISTRY)


def feature(key: str) -> FeatureDef:
    return FEATURES[key]


def features_in(group: str) -> tuple[FeatureDef, ...]:
    return tuple(f for f in REGISTRY if f.group == group)


# ---------- Store Assignment (existing tb_store columns) ----------

def store_assignment(store, definition: FeatureDef) -> tuple[bool, Optional[str]]:
    """(assigned, source). source: 'store' = saved value, 'default' = NULL column, None = not assigned per Store."""
    if not definition.assignable:
        return True, None
    value = getattr(store, definition.store_column, None) if store is not None else None
    return (definition.store_default, "default") if value is None else (bool(value), "store")


def default_entitlement(definition: FeatureDef) -> bool:
    return definition.tier == BASE


def entitled(definition: FeatureDef, entitlements: Optional[Mapping[str, bool]]) -> bool:
    """Business Option (contract) of one feature: saved value, else the tier default; None = LEGACY (all)."""
    if entitlements is None:
        return True
    value = entitlements.get(definition.key)
    return default_entitlement(definition) if value is None else bool(value)


@dataclass(frozen=True)
class FeatureSnapshot:
    """Saved Merchant layers for the resolver (from load_feature_snapshot; also built directly in tests)."""
    merchant_id: Optional[int] = None
    business: Optional[Mapping[str, bool]] = None   # Platform Business Options (tb_merchant_feature)
    choices: Optional[Mapping[str, bool]] = None    # Merchant Feature Entitlements (tb_merchant_feature_choice)
    exempt: bool = False                            # Admin Store: not checked against Merchant layers

    def contracted(self, definition: FeatureDef) -> bool:
        return True if self.exempt else entitled(definition, self.business or {})

    def merchant_choice(self, definition: FeatureDef) -> tuple[bool, Optional[str]]:
        """(ON, source). No saved choice = ON ('default'): the Merchant follows its contract."""
        if self.exempt:
            return True, None
        value = (self.choices or {}).get(definition.key)
        return (True, "default") if value is None else (bool(value), "merchant")


def _layers(entitlements):
    """(contracted(def), merchant_choice(def), checks Store Status) for any accepted entitlements argument."""
    if isinstance(entitlements, FeatureSnapshot):
        return entitlements.contracted, entitlements.merchant_choice, True
    return (lambda d: entitled(d, entitlements)), (lambda d: (True, None)), False


# ---------- Resolver ----------

@dataclass(frozen=True)
class FeatureState:
    key: str
    available: bool
    entitled: bool                    # Merchant level: contracted AND merchant choice ON
    assignable: bool
    assigned: bool                    # Store Option (True when not assigned per Store / no Store given)
    assigned_source: Optional[str]    # 'store' | 'default' | None
    effective: bool
    reason: Optional[str]             # None when effective; else a reason code (priority order above)
    blocked_by: tuple = ()            # REQUIRES: the required features that are not effective
    contracted: bool = True           # Platform Business Option
    merchant_enabled: bool = True     # Merchant Feature Entitlement
    merchant_source: Optional[str] = None  # 'merchant' (saved) | 'default' (no row = ON) | None (LEGACY / exempt)
    store_active: bool = True         # Store Status (True when not checked)

    def as_dict(self) -> dict:
        definition = FEATURES.get(self.key)
        return {
            "key": self.key, "label": definition.label if definition else self.key,
            "group": definition.group if definition else None, "tier": definition.tier if definition else None,
            "available": self.available, "contracted": self.contracted, "merchant_enabled": self.merchant_enabled,
            "merchant_source": self.merchant_source, "entitled": self.entitled, "assignable": self.assignable,
            "assigned": self.assigned, "assigned_source": self.assigned_source, "store_active": self.store_active,
            "effective": self.effective, "reason": self.reason, "blocked_by": list(self.blocked_by),
        }


def resolve_features(store=None, entitlements: Optional[Mapping[str, bool]] = None, *, registry=REGISTRY) -> dict:
    """Every feature's FeatureState, in registry order.

    store: the Store whose Store Options apply (None = Merchant / Platform level: Store Options and Status are not
    considered). entitlements: FeatureSnapshot | Business Option mapping | None (see the module docstring).
    Reads only the given objects; never writes, never queries the database.
    """
    by_key = {definition.key: definition for definition in registry}
    states: dict[str, FeatureState] = {}
    contracted_of, choice_of, check_status = _layers(entitlements)
    store_active = not (check_status and store is not None
                        and (getattr(store, "c_status", None) or "").lower() != "active")

    def resolve(key: str, visiting: tuple = ()) -> FeatureState:
        if key in states:
            return states[key]
        if key in visiting:
            raise ValueError(f"Feature dependency cycle: {' -> '.join(visiting + (key,))}")
        definition = by_key[key]
        contracted = definition.available and contracted_of(definition)
        merchant_on, merchant_source = choice_of(definition)
        is_entitled = contracted and merchant_on
        if store is None:
            assigned, source = True, None
        else:
            assigned, source = store_assignment(store, definition)
        blocked = tuple(req for req in definition.requires if not resolve(req, visiting + (key,)).effective)
        if not definition.available:
            reason = NOT_AVAILABLE
        elif not contracted:
            reason = NOT_CONTRACTED
        elif not merchant_on:
            reason = MERCHANT_OFF
        elif not assigned:
            reason = STORE_OFF
        elif blocked:
            reason = REQUIRES
        elif not store_active:
            reason = STORE_INACTIVE
        else:
            reason = None
        state = FeatureState(key, definition.available, is_entitled, definition.assignable, assigned, source,
                             reason is None, reason, blocked if reason == REQUIRES else (),
                             contracted, merchant_on, merchant_source, store_active)
        states[key] = state
        return state

    for definition in registry:
        resolve(definition.key)
    return {definition.key: states[definition.key] for definition in registry}


def resolve_feature(key: str, store=None, entitlements: Optional[Mapping[str, bool]] = None) -> FeatureState:
    return resolve_features(store, entitlements)[key]


def feature_enabled(key: str, store=None, entitlements: Optional[Mapping[str, bool]] = None) -> bool:
    """Effective status of one feature (backend checks)."""
    return resolve_feature(key, store, entitlements).effective


# ---------- Saved Merchant layers (tb_merchant_feature + tb_merchant_feature_choice), fail-closed ----------
# STEP 3-3C: read once per Merchant / Store request by services.app_navigation.resolve_app_scope (enforcement).

def entitlement_exempt(store) -> bool:
    """Central exception: the Platform-internal Admin Store is never checked against Merchant entitlements and is
    left out of the initial entitlement calculation. Accepts a Store, a Store row (dict) or a Store id."""
    from backend.database.pg_platform import ADMIN_STORE_ID

    store_id = store.get("i_store_id") if isinstance(store, dict) else getattr(store, "i_store_id", store)
    return store_id == ADMIN_STORE_ID


LAYER_TABLES = {"business": "tb_merchant_feature", "choices": "tb_merchant_feature_choice"}


class FeatureCheckUnavailable(RuntimeError):
    """The saved feature layers could not be read (database error / missing table). Callers refuse the operation."""


def _read_layer(db, table: str, merchant_id: int) -> dict:
    """One saved layer (shared by Business Options and Feature Entitlements): key -> enabled."""
    from sqlalchemy import text

    rows = db.execute(text(f"SELECT c_feature_key, b_enabled FROM {table} WHERE i_merchant_id = :m"), {"m": merchant_id})
    return {key: bool(enabled) for key, enabled in rows}


def load_feature_snapshot(db, *, store=None, merchant_id=None) -> FeatureSnapshot:
    """Every saved Merchant layer for resolve_features(), read together. Fail-closed: never "everything allowed".

    Admin Store: exempt (central rule). A Store without a Merchant: no saved layers (tier defaults: BASE only).
    Any database error or missing table raises FeatureCheckUnavailable; the read runs in a SAVEPOINT so the caller's
    transaction stays usable.
    """
    if store is not None:
        if entitlement_exempt(store):
            return FeatureSnapshot(merchant_id=getattr(store, "i_merchant_id", None), business={}, choices={}, exempt=True)
        merchant_id = store.i_merchant_id
    if merchant_id is None:
        return FeatureSnapshot(merchant_id=None, business={}, choices={})
    from sqlalchemy.exc import SQLAlchemyError

    try:
        with db.begin_nested():
            layers = {name: _read_layer(db, table, merchant_id) for name, table in LAYER_TABLES.items()}
    except SQLAlchemyError as exc:
        print(f"[FEATURES][ERROR] Feature layers unavailable for Merchant {merchant_id}: {type(exc).__name__}")
        raise FeatureCheckUnavailable("Feature check is temporarily unavailable.") from exc
    return FeatureSnapshot(merchant_id=merchant_id, **layers)


def merchant_entitlements(db, *, store=None, merchant_id=None) -> Optional[dict]:
    """The Merchant's saved Business Options (contract layer only). None only for the Admin Store (exempt).

    Fail-closed like load_feature_snapshot (a missing table / database error raises FeatureCheckUnavailable).
    """
    snapshot = load_feature_snapshot(db, store=store, merchant_id=merchant_id)
    return None if snapshot.exempt else dict(snapshot.business)


# ---------- Store Options form fields (single definition for the Store form API) ----------

def store_option_fields() -> dict:
    """Store Options payload key -> (tb_store column, scope payload key, scope column), registry order."""
    return {f.payload_key: (f.store_column, f.scope_key, f.scope_column) for f in REGISTRY if f.assignable}


STORE_OPTION_DEFAULTS = {f.payload_key: f.store_default for f in REGISTRY if f.assignable}
