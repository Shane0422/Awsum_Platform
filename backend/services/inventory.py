"""Common Inventory Type behavior.

The stored value is a fixed Inventory Type number (1-5). The numbers are permanent logic keys:
never renumber or reorder them, only append new ones. Programs must not branch on the raw number;
call ``inventory_behavior()`` and branch on the derived properties (tracks_onhand / deduct_on / ...).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

# Inventory Type numbers (permanent).
NONE = 1
STOCK = 2
RECIPE = 3
EXPENSE_ON_RECEIPT = 4
EXPENSE_ON_ISSUE = 5
DEFAULT_INVENTORY_TYPE = NONE

# Event at which on-hand is reduced, or (for no on-hand) the event that consumes the item.
DEDUCT_NONE = "none"        # not managed by inventory
DEDUCT_SALE = "sale"        # the item's own on-hand is reduced when sold
DEDUCT_BOM = "bom"          # selling reduces the Recipe/BOM component items instead
DEDUCT_RECEIPT = "receipt"  # consumed/expensed in the same step it is received
DEDUCT_ISSUE = "issue"      # stays on-hand until a separate Issue transaction consumes it


@dataclass(frozen=True)
class InventoryBehavior:
    type_id: int
    code: str
    name: str
    tracks_onhand: bool     # receipts increase the item's own on-hand quantity
    deduct_on: str          # DEDUCT_* above (on-hand / component movement)
    cost_recognition: str   # event at which cost is recognized as COGS/expense (DEDUCT_* event names)
    keep_required: bool     # a storage condition (Keep) must be set

    def as_dict(self) -> dict:
        return asdict(self)


_BEHAVIORS = {
    b.type_id: b for b in (
        # None: no on-hand quantity, but the item's cost is still recognized when it is sold.
        InventoryBehavior(NONE, "NONE", "None", False, DEDUCT_NONE, DEDUCT_SALE, False),
        InventoryBehavior(STOCK, "STOCK", "Stock", True, DEDUCT_SALE, DEDUCT_SALE, True),
        InventoryBehavior(RECIPE, "RECIPE", "Recipe", False, DEDUCT_BOM, DEDUCT_BOM, False),
        InventoryBehavior(EXPENSE_ON_RECEIPT, "EXPENSE_ON_RECEIPT", "Consume on Receipt", False,
                          DEDUCT_RECEIPT, DEDUCT_RECEIPT, False),
        InventoryBehavior(EXPENSE_ON_ISSUE, "EXPENSE_ON_ISSUE", "Consume on Issue", True,
                          DEDUCT_ISSUE, DEDUCT_ISSUE, False),
    )
}


def is_inventory_type(type_id) -> bool:
    return isinstance(type_id, int) and not isinstance(type_id, bool) and type_id in _BEHAVIORS


def inventory_behavior(type_id) -> InventoryBehavior:
    """Behavior for a stored Inventory Type number. Unknown numbers raise instead of guessing."""
    if not is_inventory_type(type_id):
        raise ValueError(f"Unknown Inventory Type: {type_id!r}")
    return _BEHAVIORS[type_id]


def inventory_types() -> list[InventoryBehavior]:
    return [_BEHAVIORS[key] for key in sorted(_BEHAVIORS)]


# Keep (storage condition): independent of Inventory Type. Stored as a code; NULL means "not set".
KEEP_TYPES = {"DRY": "Dry", "COLD": "Cold", "FROZEN": "Frozen"}
DEFAULT_KEEP = "DRY"  # system default when neither the MD nor Category III sets Keep


def keep_name(code: str | None) -> str:
    return KEEP_TYPES.get(code or "", "")


SOURCE_ITEM = "item"          # MD Master override
SOURCE_CATEGORY = "category"  # inherited from Category III
SOURCE_DEFAULT = "default"    # neither set: system default (DEFAULT_INVENTORY_TYPE / DEFAULT_KEEP)


@dataclass(frozen=True)
class ResolvedInventory:
    behavior: InventoryBehavior
    keep: str | None
    type_source: str
    keep_source: str


def resolve_inventory(item_type, item_keep, category_type=None, category_keep=None) -> ResolvedInventory:
    """Effective Inventory Type / Keep: the MD value (override) when set, else Category III, else the system default.

    NULL on the MD means "inherit"; every program reads the effective values through this function.
    """
    if item_type is not None:
        type_id, type_source = item_type, SOURCE_ITEM
    elif category_type is not None:
        type_id, type_source = category_type, SOURCE_CATEGORY
    else:
        type_id, type_source = DEFAULT_INVENTORY_TYPE, SOURCE_DEFAULT
    if item_keep:
        keep, keep_source = item_keep, SOURCE_ITEM
    elif category_keep:
        keep, keep_source = category_keep, SOURCE_CATEGORY
    else:
        keep, keep_source = DEFAULT_KEEP, SOURCE_DEFAULT
    return ResolvedInventory(inventory_behavior(type_id), keep, type_source, keep_source)
