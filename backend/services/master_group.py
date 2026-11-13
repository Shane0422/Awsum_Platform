"""Master Group (Tax / Category I-III / MD Master / Item Kind / Location Master / Common Maintenance) rules.

All validation lives here so the API enforces it regardless of what the UI sends.
Money and rates use Decimal only.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import wraps

from sqlalchemy import func, inspect, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models_admin.common_code import COMMON_VALUE_TYPES, CommonCode, CommonGroup, MerchantCommonInit
from backend.models_admin.invoice import InvoiceLine
from backend.models_admin.master_group import (Category1, Category2, Category3, ItemKind, MdDetail,
                                               MdMaster, Tax, md_owner_key)
from backend.models_admin.employee import Employee
from backend.database.item_kind_migration import SYSTEM_ITEM_KIND_CODES
from backend.models_admin.store_location import DEFAULT_LOCATION_AREA, LOCATION_AREAS, StoreLocation, StoreMdLocation
from backend.services import images
from backend.services import inventory
from backend.services import location_code
from backend.services import barcode as barcode_rules
from backend.services.paths import AwsumPathResolver
from backend.services.money import format_money
from backend.services.image_service import commit_entity_image, master_image_lock

# tb_license.c_license_type values (see platform_store_license TYPE_*).
LICENSE_TYPES = ("POS", "KIOSK", "CUSTOMER_ORDERING")
DEFAULT_ITEM_KIND = "ITEM"  # tb_item_kind System Code
BILLING_CYCLES = ("ONE_TIME", "MONTHLY", "ANNUAL")
# Inventory Type / Keep definitions and behavior live in the Common services.inventory module.

MAX_PRICE = Decimal("9999999999.99")
MAX_ORDER = 1_000_000

PRICE_LEVELS = ("RETAIL", "P1", "P2", "P3")
PRICE_MODES = ("FIXED", "DISCOUNT")
CENT = Decimal("0.01")


class PriceNotSet(Exception):
    pass


def calc_discount_price(retail: Decimal, discount_pct: Decimal) -> Decimal:
    """Supply price = retail x (1 - pct/100), USD cents, rounded half up."""
    return (retail * (Decimal(100) - discount_pct) / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP)


def resolve_unit_price(item: MdMaster, level: str) -> Decimal:
    """Unit price for an agency price level. An unset level raises instead of returning 0."""
    level = (level or "").strip().upper()
    if level not in PRICE_LEVELS:
        raise PriceNotSet("Unknown price level.")
    value = item.n_default_unit_price if level == "RETAIL" else getattr(item, f"n_{level.lower()}_price")
    if value is None:
        raise PriceNotSet(f"{level} price is not set for {item.c_item_code}.")
    return Decimal(value)


class FieldErrors(Exception):
    def __init__(self, errors: dict[str, str], message: str | None = None):
        super().__init__(message or "Please correct the highlighted fields.")
        self.errors = errors
        self.message = message or "Please correct the highlighted fields."


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


ENTITIES = {
    "tax": dict(model=Tax, pk="i_tax_id", code="c_tax_code", name="c_tax_name", prefix="TAX"),
    "category1": dict(model=Category1, pk="i_category1_id", code="c_category_code", name="c_category_name", prefix="CA1"),
    "category2": dict(model=Category2, pk="i_category2_id", code="c_category_code", name="c_category_name", prefix="CA2"),
    "category3": dict(model=Category3, pk="i_category3_id", code="c_category_code", name="c_category_name", prefix="CA3"),
    "md": dict(model=MdMaster, pk="i_md_id", code="c_item_code", name="c_item_name", prefix="MD"),
    # Super Admin System Master. code = auto Item Kind No (IKC00001); the System Code (c_item_kind_code) is the
    # program logic key MD stores. Required System Codes cannot be changed or deleted (is_system_row).
    "item_kind": dict(model=ItemKind, pk="i_item_kind_id", code="c_item_kind_no", name="c_item_kind_name",
                      prefix="IKC", search=("c_item_kind_code",)),
    # Location Master: one Store's own data (store_scoped) with a user-entered code (manual_code).
    "location": dict(model=StoreLocation, pk="i_location_id", code="c_location_code", name="c_location_name",
                     prefix=None, store_scoped=True, manual_code=True),
    # Common Maintenance: the codes of one Common Group (group_scoped, ?group=UNIT) in the request's scope:
    # Platform codes (i_merchant_id NULL) or one Merchant's own codes. Groups (definitions) are shared by every scope.
    # Code issue mode comes from the group (MANUAL typed / AUTO prefix + number).
    "common_code": dict(model=CommonCode, pk="i_common_code_id", code="c_code", name="c_name", prefix=None,
                        group_scoped=True, manual_code=True, search=("c_description",)),
    # Employee Master (Employee Management STEP 1A): Merchant Portal only. The common router / screen flow is reused;
    # its rules (per-Merchant numbering, status, termination, Store assignments) live in services.employees, which
    # the router calls for this entity (custom) instead of the generic CRUD below.
    "employee": dict(model=Employee, pk="i_employee_id", code="c_employee_code", name="c_last_name", prefix="EMP",
                     merchant_only=True, custom=True),
}
PLATFORM_ONLY_MODELS = (ItemKind,)  # Item Kind: one global System Master (Merchant MD selects from it)
# MD Master fields used only by the Platform (agency supply price levels, billing, license). A Merchant MD never
# shows them and the server never takes them from a Merchant payload: they stay at these "not set" values.
MERCHANT_MD_FIXED_VALUES = {
    "c_billing_cycle": "ONE_TIME", "b_license_required": False, "c_license_type": None, "i_licenses_per_unit": None,
    **{f"{prefix}{n}{suffix}": None for n in (1, 2, 3) for prefix, suffix in (("c_p", "_mode"),
                                                                             ("n_p", "_discount_pct"), ("n_p", "_price"))},
}
# The protected default Unit every Merchant gets with its first initialization (initialize_merchant_common_codes),
# even if the Platform EA were missing or inactive. EA is protected in every scope (PROTECTED_COMMON_CODES).
MERCHANT_DEFAULT_UNITS = (("EA", "Each"),)
# Common Maintenance groups shown now. SIZE and ITEM_KIND are never Common Maintenance groups.
# JOB_GRADE / JOB_TITLE: Employee Master Job Grade / Job Title (tb_employee i_job_grade_code_id / i_job_title_code_id).
COMMON_MAINTENANCE_GROUPS = ("COLOR", "UNIT", "JOB_GRADE", "JOB_TITLE")
# Employee column per Employee-referenced group (in-use check: any Employee status, terminated included).
EMPLOYEE_COMMON_GROUPS = {"JOB_GRADE": "i_job_grade_code_id", "JOB_TITLE": "i_job_title_code_id"}
# View-only Common Groups (none since COLOR step 5-2: COLOR is managed here; the mechanism stays for later groups).
READ_ONLY_COMMON_GROUPS: dict[str, str] = {}
# MD Detail Color is a COLOR common code (color_code_id); the removed tb_color id (color_id) is refused (422).
COLOR_ID_REMOVED = "color_id is no longer supported. Send color_code_id (a COLOR code from Common Maintenance)."
# Codes that can never be deactivated or deleted (EA = the default MD Unit).
PROTECTED_COMMON_CODES = {("UNIT", "EA"): "EA is the default Unit and cannot be deactivated or deleted."}
COMMON_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")
NUMERIC_18_6_MAX = Decimal("999999999999.999999")
SWATCH_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
ITEM_KIND_SYSTEM_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")
LOCATION_CODE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]*$")
LOCATION_CODE_FIXED = "The Location Code cannot be changed after it is saved (Category III / MD Master refer to it)."
CODE_DIGITS = 5


def is_system_row(entity: str, row) -> bool:
    """A protected System Code row: it can never be deleted (its System Code never changes for any row)."""
    return entity == "item_kind" and row.c_item_kind_code in SYSTEM_ITEM_KIND_CODES


def item_kind_logic_defined(code: str | None) -> bool:
    """Only System Codes whose processing is defined in the program can be used by MD Master.

    A System Code added by Super Admin has no processing yet: it is never treated as ITEM, so it stays
    out of the MD Item Kind list and MD saves reject it until its logic is added to the program.
    """
    return code in SYSTEM_ITEM_KIND_CODES


def entity_config(entity: str) -> dict:
    cfg = ENTITIES.get(entity)
    if not cfg:
        raise NotFound("Unknown master type.")
    return cfg


def _scoped_query(db: Session, model, merchant_id: int | None):
    if model is MdMaster:  # the scope's own MD only: Platform (owner 0) or exactly this Merchant, never mixed
        return db.query(model).filter(MdMaster.i_owner_key == md_owner_key(merchant_id))
    if model in PLATFORM_ONLY_MODELS:
        if merchant_id is not None:
            raise NotFound("This master is not available in this scope.")
        return db.query(model)
    return db.query(model).filter(model.i_merchant_id == merchant_id)


def common_group(db: Session, group: str | None) -> CommonGroup:
    """The active Common Maintenance group named by ?group= (COMMON_MAINTENANCE_GROUPS only)."""
    code = str(group or "").strip().upper()
    row = (db.query(CommonGroup).filter(CommonGroup.c_group_code == code).first()
           if code in COMMON_MAINTENANCE_GROUPS else None)
    if row is None or not row.b_active:
        raise NotFound("Select a Common Group.")
    return row


def _entity_query(db: Session, entity: str, merchant_id: int | None, store_id: int | None = None,
                  group: str | None = None):
    """Rows visible in the current scope: Store-scoped masters by the (already authorized) Store, others as before."""
    cfg = entity_config(entity)
    if cfg.get("group_scoped"):  # one group's codes of this scope only (Platform or this Merchant)
        return db.query(CommonCode).filter(CommonCode.i_common_group_id == common_group(db, group).i_common_group_id,
                                           common_code_scope(merchant_id))
    if cfg.get("store_scoped"):
        if store_id is None:
            raise NotFound("Select a Store.")
        return db.query(cfg["model"]).filter(cfg["model"].i_store_id == store_id)
    return _scoped_query(db, cfg["model"], merchant_id)


def common_code_scope(merchant_id: int | None):
    """Filter for one scope's Common Codes: Platform (NULL) or exactly this Merchant; never both, never another."""
    return CommonCode.i_merchant_id.is_(None) if merchant_id is None else CommonCode.i_merchant_id == merchant_id


def _scoped_parent(db: Session, model, item_id: int | None, merchant_id: int | None):
    row = db.get(model, item_id) if item_id is not None else None
    return row if row is not None and row.i_merchant_id == merchant_id else None


# ---------- helpers ----------

def _text(payload: dict, key: str, limit: int, errors: dict, *, required=False, label=None) -> str | None:
    raw = payload.get(key)
    value = "" if raw is None else str(raw).strip()
    if not value:
        if required:
            errors[key] = f"{label or key} is required."
        return None
    if len(value) > limit:
        errors[key] = f"Must be {limit} characters or fewer."
        return None
    return value


def _decimal(payload: dict, key: str, errors: dict, *, places: int, maximum: Decimal, label: str, required: bool = True):
    raw = payload.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            errors[key] = f"{label} is required."
        return None
    if isinstance(raw, bool):
        errors[key] = f"{label} must be a number."
        return None
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        errors[key] = f"{label} must be a number."
        return None
    if not value.is_finite():
        errors[key] = f"{label} must be a number."
        return None
    if value < 0:
        errors[key] = f"{label} cannot be negative."
        return None
    if value > maximum:
        errors[key] = f"{label} must be {maximum} or less."
        return None
    if -value.as_tuple().exponent > places:
        errors[key] = f"{label} allows up to {places} decimal places."
        return None
    return value


def _int(payload: dict, key: str, errors: dict, *, minimum: int, maximum: int, label: str, default=None):
    raw = payload.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if default is not None:
            return default
        errors[key] = f"{label} is required."
        return None
    if isinstance(raw, bool):
        errors[key] = f"{label} must be a whole number."
        return None
    try:
        dec = Decimal(str(raw).strip())
        if dec != dec.to_integral_value():
            raise InvalidOperation
        value = int(dec)
    except (InvalidOperation, ValueError):
        errors[key] = f"{label} must be a whole number."
        return None
    if value < minimum or value > maximum:
        errors[key] = f"{label} must be between {minimum} and {maximum}."
        return None
    return value


def _bool(payload: dict, key: str, default: bool) -> bool:
    raw = payload.get(key)
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().casefold() in {"1", "true", "yes", "y", "on"}
    return bool(raw)


def _inventory_type(payload: dict, errors: dict, current) -> int | None:
    """Key absent keeps the saved value (older clients); blank/null stores NULL (MD: inherit, Category III: not set)."""
    if "inventory_type" not in payload:
        return current.i_inventory_type if current is not None else None
    raw = payload["inventory_type"]
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        type_id = int(str(raw).strip()) if not isinstance(raw, bool) else None
    except ValueError:
        type_id = None
    if not inventory.is_inventory_type(type_id):
        errors["inventory_type"] = "Select a valid Inventory Type."
    return type_id


def _keep(payload: dict, errors: dict, current) -> str | None:
    """Keep code; key absent keeps the saved value, blank/null (or legacy NONE) stores NULL."""
    if "keep" not in payload:
        return current.c_keep if current is not None else None
    code = "" if payload["keep"] is None else str(payload["keep"]).strip().upper()
    if code in ("", "NONE"):
        return None
    if code not in inventory.KEEP_TYPES:
        errors["keep"] = "Select Dry, Cold or Frozen."
    return code


LOCATION_FIELDS = (("sales_location", "c_sales_location"), ("warehouse_location", "c_warehouse_location"))
# Common Location Code rule (backend/services/location_code.py): trimmed, upper-cased, blank = NULL, max 50.
RECOMMENDED_LOCATION_MAX = location_code.LOCATION_CODE_MAX
normalize_recommended_location = location_code.normalize


LOCATION_FIELD_AREAS = {"sales_location": "SALES_FLOOR", "warehouse_location": "WAREHOUSE"}


def _recommended_locations(db: Session, payload: dict, current, errors: dict, merchant_id: int | None = None) -> dict:
    """Key absent keeps the saved value (older clients, a Store with Location Management OFF); blank stores NULL.

    A saved value is kept as it is (even when no Store has that code any more), so other fields still save.
    A NEW value must be an active Location of its Area in some Store of the scope (the Common Location Selector
    lists exactly those); the Merchant's Stores for a Merchant Category III, every Store for Platform masters.
    """
    from backend.services.store_locations import active_location_code_exists

    values = {}
    for key, column in LOCATION_FIELDS:
        saved = getattr(current, column) if current is not None else None
        if key not in payload:
            values[column] = saved
            continue
        value = location_code.normalize(payload.get(key))
        message = location_code.length_error(value)
        area = LOCATION_FIELD_AREAS[key]
        if message:
            errors[key] = message
        elif value and value != saved and not active_location_code_exists(db, value, area, merchant_id):
            errors[key] = (f"{value} is not an active {LOCATION_AREAS[area]} Location in these Stores. "
                           "Select one with Search.")
        values[column] = value
    return values


def resolve_md_location(md_value: str | None, category_value: str | None) -> tuple[str | None, str]:
    """Recommended position in effect: the MD Override, else the Category III default, else not set."""
    if md_value:
        return md_value, "item"
    if category_value:
        return category_value, "category"
    return None, "none"


OUTPUT_DEVICE_NUMBERS = tuple(range(1, 9))  # Output Device #1-#8; stored as a bitmask (bit n-1 = #n)


def output_devices_list(mask: int | None) -> list[int]:
    """Bitmask -> sorted device numbers; 0/NULL = not output."""
    mask = int(mask or 0)
    return [n for n in OUTPUT_DEVICE_NUMBERS if mask & (1 << (n - 1))]


def output_devices_mask(numbers) -> int:
    return sum(1 << (n - 1) for n in set(numbers))


def _output_devices(payload: dict, errors: dict, current) -> int:
    """List of #1-#8; key absent keeps the saved value (older clients), empty/null stores 0 (not output)."""
    if "output_devices" not in payload:
        return int(current.i_output_devices or 0) if current is not None else 0
    raw = payload["output_devices"]
    if raw is None or raw == "":
        return 0
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    numbers = set()
    for value in raw:
        try:
            number = int(str(value).strip()) if not isinstance(value, bool) else None
        except ValueError:
            number = None
        if number not in OUTPUT_DEVICE_NUMBERS:
            errors["output_devices"] = "Select Output Device #1 to #8."
            return 0
        numbers.add(number)
    return output_devices_mask(numbers)


def _id(payload: dict, key: str, errors: dict, *, required: bool, label: str) -> int | None:
    raw = payload.get(key)
    if raw is None or raw == "":
        if required:
            errors[key] = f"{label} is required."
        return None
    try:
        if isinstance(raw, bool):
            raise ValueError
        return int(raw)
    except (TypeError, ValueError):
        errors[key] = f"{label} is invalid."
        return None


def _next_code(db: Session, cfg: dict) -> str:
    model, col, prefix = cfg["model"], getattr(cfg["model"], cfg["code"]), cfg["prefix"]
    pattern = re.compile(rf"^{prefix}(\d+)$")
    top = 0
    for (code,) in db.query(col).filter(col.like(f"{prefix}%")).all():
        match = pattern.match(code or "")
        if match:
            top = max(top, int(match.group(1)))
    return f"{prefix}{top + 1:0{CODE_DIGITS}d}"


def _stamp(row, user_id, *, created: bool):
    now = datetime.now()
    if created:
        row.i_created_by = user_id
    row.dt_updated = now
    row.i_updated_by = user_id


# ---------- validation per entity ----------

def _validate_common(payload: dict, errors: dict, *, ordered: bool) -> dict:
    values = {
        "b_active": _bool(payload, "active", True),
        "c_notes": _text(payload, "notes", 2000, errors),
    }
    if ordered:
        values["i_display_order"] = _int(
            payload, "display_order", errors, minimum=0, maximum=MAX_ORDER, label="Display Order", default=100
        )
    return values


def _validate_payload(db: Session, entity: str, payload: dict, current, can_cost: bool = False,
                      merchant_id: int | None = None, store_id: int | None = None, group: str | None = None) -> dict:
    errors: dict[str, str] = {}
    values: dict = {}

    if entity == "tax":
        values["c_tax_name"] = _text(payload, "name", 100, errors, required=True, label="Tax Name")
        values["n_tax_rate"] = _decimal(payload, "rate", errors, places=4, maximum=Decimal("100"), label="Tax Rate")
        values.update(_validate_common(payload, errors, ordered=False))

    elif entity == "category1":
        values["c_category_name"] = _text(payload, "name", 100, errors, required=True, label="Name")
        values.update(_validate_common(payload, errors, ordered=True))

    elif entity == "item_kind":
        values["c_item_kind_name"] = _text(payload, "name", 100, errors, required=True, label="Display Name")
        values.update(_validate_common(payload, errors, ordered=True))
        if current is None:  # System Code: entered once on create (separate from the auto IKC number), never changed
            system_code = (_text(payload, "system_code", 30, errors, required=True, label="System Code") or "").upper()
            if system_code and "system_code" not in errors:
                if not ITEM_KIND_SYSTEM_CODE.match(system_code):
                    errors["system_code"] = "Use A-Z, 0-9 and _ only, starting with a letter."
                elif system_code.startswith("IKC"):
                    errors["system_code"] = "IKC is reserved for the auto-issued Item Kind No."
                elif db.query(ItemKind.i_item_kind_id).filter(func.upper(ItemKind.c_item_kind_code) == system_code).first():
                    errors["system_code"] = "This System Code already exists."
            values["c_item_kind_code"] = system_code
        # The default Item Kind keeps new MD items valid, so it always stays active.
        if not values["b_active"] and current is not None and current.c_item_kind_code == DEFAULT_ITEM_KIND:
            errors["active"] = f"{DEFAULT_ITEM_KIND} is the default Item Kind and cannot be deactivated."

    elif entity == "location":
        _validate_location(db, payload, current, values, errors, store_id)

    elif entity == "common_code":
        _validate_common_code(db, payload, current, values, errors, common_group(db, group), merchant_id)

    elif entity == "category2":
        values["c_category_name"] = _text(payload, "name", 100, errors, required=True, label="Name")
        c1 = _id(payload, "category1_id", errors, required=True, label="Category I")
        values["i_category1_id"] = c1
        if c1 is not None and "category1_id" not in errors:
            parent = _scoped_parent(db, Category1, c1, merchant_id)
            if not parent:
                errors["category1_id"] = "Category I does not exist."
            elif current is None or current.i_category1_id != c1:
                if not parent.b_active:
                    errors["category1_id"] = "Category I is inactive."
                elif current is not None and _category2_in_use(db, current.i_category2_id):
                    errors["category1_id"] = "This Category II is in use by lower categories or items; its Category I cannot change."
        values.update(_validate_common(payload, errors, ordered=True))

    elif entity == "category3":
        values["c_category_name"] = _text(payload, "name", 100, errors, required=True, label="Name")
        c1 = _id(payload, "category1_id", errors, required=True, label="Category I")
        c2 = _id(payload, "category2_id", errors, required=True, label="Category II")
        values["i_category1_id"], values["i_category2_id"] = c1, c2
        if c1 is not None and c2 is not None and not errors.get("category1_id") and not errors.get("category2_id"):
            p1 = _scoped_parent(db, Category1, c1, merchant_id)
            p2 = _scoped_parent(db, Category2, c2, merchant_id)
            if not p1:
                errors["category1_id"] = "Category I does not exist."
            elif not p2:
                errors["category2_id"] = "Category II does not exist."
            elif p2.i_category1_id != c1:
                errors["category2_id"] = "Category II does not belong to the selected Category I."
            else:
                changed = current is None or (current.i_category1_id, current.i_category2_id) != (c1, c2)
                if changed and not p1.b_active:
                    errors["category1_id"] = "Category I is inactive."
                elif changed and not p2.b_active:
                    errors["category2_id"] = "Category II is inactive."
                elif current is not None and changed and _category3_in_use(db, current.i_category3_id):
                    errors["category2_id"] = "This Category III is used by items; its parents cannot change."
        selected_taxes = set()
        for n in (1, 2, 3):
            key, column = f"tax{n}_id", f"i_tax{n}_id"
            tax_id = _id(payload, key, errors, required=False, label=f"Tax {n}") if key in payload else (
                getattr(current, column) if current is not None else None
            )
            values[column] = tax_id
            if tax_id is None or key in errors:
                continue
            tax = _scoped_parent(db, Tax, tax_id, merchant_id)
            if tax is None:
                errors[key] = "Tax does not exist in this scope."
            elif not tax.b_active and (current is None or getattr(current, column) != tax_id):
                errors[key] = "Tax is inactive."
            if tax_id in selected_taxes:
                errors[key] = "Select each Tax only once."
            selected_taxes.add(tax_id)
        # Inventory Type / Keep defaults inherited by MD items that leave them NULL.
        values["i_inventory_type"] = _inventory_type(payload, errors, current)
        values["c_keep"] = _keep(payload, errors, current)
        values.update(_recommended_locations(db, payload, current, errors, merchant_id))
        values.update(_validate_common(payload, errors, ordered=True))

    else:  # md
        _validate_md(db, payload, current, values, errors, can_cost, merchant_id)

    if errors:
        raise FieldErrors(errors)
    return values


# ---------- Common Maintenance ----------

def common_read_only(db: Session, group: str | None) -> None:
    message = READ_ONLY_COMMON_GROUPS.get(common_group(db, group).c_group_code)
    if message:
        raise Conflict(message)


def _plain(value: Decimal) -> str:
    text_value = format(value, "f")
    return text_value.rstrip("0").rstrip(".") if "." in text_value else text_value


def _bounded_decimal(payload: dict, key: str, errors: dict, *, places: int, minimum, maximum, label: str,
                     required: bool):
    """Exact decimal Value (NUMBER / CURRENCY): comma-free text, the group's range and decimal places; '' = NULL."""
    raw = payload.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            errors[key] = f"{label} is required."
        return None
    try:
        value = None if isinstance(raw, bool) else Decimal(str(raw).strip())
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite():
        errors[key] = f"{label} must be a number."
        return None
    low = Decimal(minimum) if minimum is not None else Decimal(0)
    high = min(Decimal(maximum), NUMERIC_18_6_MAX) if maximum is not None else NUMERIC_18_6_MAX
    if value < low:
        errors[key] = f"{label} cannot be negative." if low == 0 else f"{label} must be {_plain(low)} or more."
    elif value > high:
        errors[key] = f"{label} must be {_plain(high)} or less."
    elif -value.as_tuple().exponent > places:
        errors[key] = f"{label} allows up to {places} decimal places." if places else f"{label} must be a whole number."
    return None if key in errors else value


def common_value_scale(group: CommonGroup) -> int:
    return 2 if group.c_value_type == "CURRENCY" else int(group.i_value_scale or 0)


def common_value(payload: dict, group: CommonGroup, errors: dict) -> dict:
    """The typed Value for the group's Value Type: TEXT / COLOR -> c_value_text, NUMBER / CURRENCY -> n_value."""
    kind = group.c_value_type
    if kind not in COMMON_VALUE_TYPES:  # NULL: the group has no Value
        return {"c_value_text": None, "n_value": None}
    label, required = group.c_value_label or "Value", bool(group.b_value_required)
    if kind == "TEXT":
        limit = min(int(group.i_text_max_length or 200), 200)
        return {"c_value_text": _text(payload, "value", limit, errors, required=required, label=label), "n_value": None}
    if kind == "COLOR":
        value = _text(payload, "value", 7, errors, required=required, label=label)
        if value and not SWATCH_HEX.match(value):
            errors["value"] = "Enter a hex color like #1F2A44" + ("." if required else ", or leave it blank.")
        return {"c_value_text": value.upper() if value and "value" not in errors else None, "n_value": None}
    number = _bounded_decimal(payload, "value", errors, places=common_value_scale(group), minimum=group.n_value_min,
                              maximum=group.n_value_max, label=label, required=required)
    return {"c_value_text": None, "n_value": number}


def common_value_text(row: CommonCode, group: CommonGroup) -> str | None:
    """API / export Value: decimal text without commas (NULL stays None; 0 stays "0")."""
    if group.c_value_type in ("NUMBER", "CURRENCY"):
        if row.n_value is None:
            return None
        return format(Decimal(row.n_value).quantize(Decimal(1).scaleb(-common_value_scale(group))), "f")
    return row.c_value_text if group.c_value_type in COMMON_VALUE_TYPES else None


def _next_common_code(db: Session, group: CommonGroup, merchant_id: int | None = None) -> str:
    prefix = group.c_code_prefix or ""
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    top = 0
    for (code,) in db.query(CommonCode.c_code).filter(CommonCode.i_common_group_id == group.i_common_group_id,
                                                     common_code_scope(merchant_id)):
        match = pattern.match(code or "")
        if match:
            top = max(top, int(match.group(1)))
    return f"{prefix}{top + 1:0{CODE_DIGITS}d}"


def _validate_common_code(db: Session, payload: dict, current, values: dict, errors: dict, group: CommonGroup,
                          merchant_id: int | None = None) -> None:
    # Code / Name are unique per group within one scope (Platform, or one Merchant); other scopes may reuse them.
    same_group = db.query(CommonCode.i_common_code_id).filter(
        CommonCode.i_common_group_id == group.i_common_group_id, common_code_scope(merchant_id))
    if current is not None:
        same_group = same_group.filter(CommonCode.i_common_code_id != current.i_common_code_id)
    if current is None:  # the Code is set once on create and never changes (MD stores UNIT codes as text)
        if group.c_code_mode == "AUTO":
            values["c_code"] = _next_common_code(db, group, merchant_id)
        else:
            limit = min(int(group.i_code_max_length or 30), 30)
            code = (_text(payload, "code", limit, errors, required=True, label="Code") or "").upper()
            if code and "code" not in errors:
                if not COMMON_CODE_PATTERN.match(code):
                    errors["code"] = "Use A-Z, 0-9, _ and - only."
                elif same_group.filter(func.upper(CommonCode.c_code) == code).first():
                    errors["code"] = "This code already exists in this group."
            values["c_code"] = code
    name = _text(payload, "name", 100, errors, required=True, label="Name")
    if name and "name" not in errors and same_group.filter(func.lower(CommonCode.c_name) == name.lower()).first():
        errors["name"] = "This name already exists in this group."
    values["c_name"] = name
    values["c_description"] = _text(payload, "description", 200, errors)
    values.update(common_value(payload, group, errors))
    values.update(_validate_common(payload, errors, ordered=True))
    protected = PROTECTED_COMMON_CODES.get((group.c_group_code, current.c_code)) if current is not None else None
    if protected and not values["b_active"]:
        errors["active"] = protected


def set_common_group_value_type(db: Session, group_code: str, value_type: str | None, user_id) -> CommonGroup:
    """Change a group's Value Type; refused once the group has codes (existing Values are never reinterpreted)."""
    group = db.query(CommonGroup).filter(CommonGroup.c_group_code == str(group_code or "").strip().upper()).first()
    if group is None:
        raise NotFound("Common Group not found.")
    new = str(value_type or "").strip().upper() or None
    if new is not None and new not in COMMON_VALUE_TYPES:
        raise FieldErrors({"value_type": f"Value Type must be one of {', '.join(COMMON_VALUE_TYPES)} or empty."})
    if new != group.c_value_type and db.query(CommonCode.i_common_code_id).filter(
            CommonCode.i_common_group_id == group.i_common_group_id).first():
        raise FieldErrors({"value_type": "The Value Type cannot change while this group has codes."})
    group.c_value_type = new
    _stamp(group, user_id, created=False)
    db.commit()
    return group


def common_code_in_use(db: Session, group_code: str, row: CommonCode) -> bool:
    owner = md_owner_key(row.i_merchant_id)  # only the code's own scope's MD can use it
    if group_code == "UNIT":  # MD Master and MD Detail store the UNIT code as text
        return bool(db.query(MdMaster.i_md_id).filter(MdMaster.i_owner_key == owner, MdMaster.c_unit == row.c_code).first()
                    or db.query(MdDetail.i_md_detail_id).filter(MdDetail.i_owner_key == owner,
                                                                MdDetail.c_unit == row.c_code).first())
    if group_code == "COLOR":  # MD Detail references the COLOR code (i_color_code_id)
        return bool(db.query(MdDetail.i_md_detail_id).filter(MdDetail.i_color_code_id == row.i_common_code_id).first())
    if group_code in EMPLOYEE_COMMON_GROUPS:  # Employee Job Grade / Job Title, terminated Employees included
        column = getattr(Employee, EMPLOYEE_COMMON_GROUPS[group_code])
        return bool(db.query(Employee.i_employee_id).filter(column == row.i_common_code_id).first())
    return False


def common_code_options(db: Session, group_code: str, merchant_id: int | None = None) -> list[dict]:
    """One group's codes of the scope for a Common selector (inactive ones too, so a saved code keeps its name;
    the screen offers active codes plus the saved one). Empty while the Common tables are not migrated."""
    if not inspect(db.get_bind()).has_table(CommonGroup.__tablename__):
        return []
    rows = common_code_query(db, group_code, merchant_id).order_by(CommonCode.i_display_order, CommonCode.c_code)
    return [{"id": r.i_common_code_id, "code": r.c_code, "name": r.c_name, "active": bool(r.b_active)} for r in rows]


def common_code_query(db: Session, group_code: str, merchant_id: int | None = None):
    """The scope's own codes of one group (Platform, or exactly this Merchant)."""
    return (db.query(CommonCode).join(CommonGroup, CommonGroup.i_common_group_id == CommonCode.i_common_group_id)
            .filter(CommonGroup.c_group_code == group_code, common_code_scope(merchant_id)))


def color_code_options(db: Session, merchant_id: int | None = None) -> list[dict]:
    """COLOR codes of the scope for the MD Color selector (inactive ones too, so a saved Color keeps its name)."""
    if not inspect(db.get_bind()).has_table(CommonGroup.__tablename__):
        return []
    rows = _color_code_query(db, merchant_id).order_by(CommonCode.i_display_order, CommonCode.c_code)
    return [{"id": r.i_common_code_id, "code": r.c_code, "name": r.c_name, "swatch_hex": r.c_value_text,
             "active": bool(r.b_active)} for r in rows]


def _color_code_query(db: Session, merchant_id: int | None = None):
    return common_code_query(db, "COLOR", merchant_id)



def active_unit_codes(db: Session, merchant_id: int | None = None) -> set[str] | None:
    """Active UNIT codes of the scope (Platform or one Merchant), or None when the Common UNIT group is not
    available (not migrated / inactive)."""
    if not inspect(db.get_bind()).has_table(CommonGroup.__tablename__):
        return None
    group = db.query(CommonGroup).filter(CommonGroup.c_group_code == "UNIT").first()
    if group is None or not group.b_active:
        return None
    return {code for (code,) in db.query(CommonCode.c_code).filter(
        CommonCode.i_common_group_id == group.i_common_group_id, common_code_scope(merchant_id),
        CommonCode.b_active.is_(True))}


def unit_options(db: Session, merchant_id: int | None = None) -> list[dict]:
    """UNIT codes of the scope for the MD Unit selector (inactive ones too, so a saved inactive Unit keeps its name)."""
    if active_unit_codes(db, merchant_id) is None:
        return []
    rows = (db.query(CommonCode).join(CommonGroup, CommonGroup.i_common_group_id == CommonCode.i_common_group_id)
            .filter(CommonGroup.c_group_code == "UNIT", common_code_scope(merchant_id))
            .order_by(CommonCode.i_display_order, CommonCode.c_code))
    return [{"code": r.c_code, "name": r.c_name, "active": bool(r.b_active)} for r in rows]


def _unit(db: Session | None, payload: dict, saved: str | None, errors: dict,
          merchant_id: int | None = None) -> str | None:
    """MD Master / MD Detail Unit (c_unit text, no FK). A new or changed value must be an active UNIT code; the
    unchanged saved value is kept even when inactive or not in the UNIT list. Without the Common UNIT group
    (not migrated) only the original text rule applies."""
    value = _text(payload, "unit", 30, errors, required=True, label="Unit")
    if not value or "unit" in errors or db is None or (saved is not None and value == saved):
        return value
    active = active_unit_codes(db, merchant_id)  # the scope's own UNIT codes (Platform or this Merchant)
    if active is not None and value not in active:
        errors["unit"] = "Select an active Unit."
    return value


def _copy_platform_group(db: Session, group: CommonGroup, merchant_id: int, user_id) -> dict:
    """Copy this group's active Platform codes to the Merchant once and record it (no commit).

    The init row is written first: a concurrent first visit for the same Merchant and group stops on its primary
    key. Codes the Merchant already has (same Code, or same Name) are skipped and kept exactly as they are.
    """
    record = MerchantCommonInit(i_merchant_id=merchant_id, c_group_code=group.c_group_code)
    _stamp(record, user_id, created=True)
    db.add(record)
    db.flush()  # claims the group (PK); raises IntegrityError when another request already did
    mine = db.query(CommonCode.c_code, CommonCode.c_name).filter(
        CommonCode.i_common_group_id == group.i_common_group_id, common_code_scope(merchant_id)).all()
    codes = {str(code).upper() for code, _name in mine}
    names = {str(name).lower() for _code, name in mine}
    source = [(r.c_code, r.c_name, r.c_description, r.c_value_text, r.n_value, r.i_display_order)
              for r in db.query(CommonCode).filter(
                  CommonCode.i_common_group_id == group.i_common_group_id, CommonCode.i_merchant_id.is_(None),
                  CommonCode.b_active.is_(True)).order_by(CommonCode.i_display_order, CommonCode.c_code)]
    if group.c_group_code == "UNIT":  # the protected default Unit always exists, even without a Platform EA
        known = {str(code).upper() for code, *_rest in source}
        source += [(code, name, None, None, None, order * 10)
                   for order, (code, name) in enumerate(MERCHANT_DEFAULT_UNITS, start=1) if code not in known]
    copied = skipped = 0
    for code, name, description, value_text, value, order in source:
        if code.upper() in codes or name.lower() in names:
            skipped += 1
            continue
        row = CommonCode(i_common_group_id=group.i_common_group_id, i_merchant_id=merchant_id, c_code=code,
                         c_name=name, c_description=description, c_value_text=value_text, n_value=value,
                         i_display_order=order, b_active=True)
        _stamp(row, user_id, created=True)
        db.add(row)
        codes.add(code.upper())
        names.add(name.lower())
        copied += 1
    record.i_copied_count, record.i_skipped_count = copied, skipped
    db.flush()
    return {"copied": copied, "skipped": skipped}


def initialize_merchant_common_codes(db: Session, merchant_id: int | None, user_id, *, commit: bool = True) -> dict:
    """First initialization of a Merchant's Common Maintenance: each active group (COMMON_MAINTENANCE_GROUPS) with no
    tb_merchant_common_init row gets the active Platform codes copied once. Returns {group: counts} for the
    groups initialized now ({} when every group was already done).

    The init row, never a code count, decides: codes deleted later are not copied again and Platform changes
    never reach an initialized Merchant. Existing Merchant codes are never changed (same Code / Name skipped).
    commit=False (Merchant registration): runs inside the caller's transaction, errors propagate.
    commit=True (first Merchant visit): each group commits on its own; a concurrent request that initialized the
    group first only rolls this attempt back (no duplicate, no error for the user).
    """
    if merchant_id is None or not inspect(db.connection()).has_table(MerchantCommonInit.__tablename__):  # same connection: never ends the caller's transaction
        return {}
    done = {code for (code,) in db.query(MerchantCommonInit.c_group_code).filter(
        MerchantCommonInit.i_merchant_id == merchant_id)}
    groups = [g for g in db.query(CommonGroup).filter(CommonGroup.c_group_code.in_(COMMON_MAINTENANCE_GROUPS),
                                                       CommonGroup.b_active.is_(True))
              .order_by(CommonGroup.i_display_order) if g.c_group_code not in done]
    result = {}
    for group in groups:
        code = group.c_group_code
        if not commit:
            result[code] = _copy_platform_group(db, group, merchant_id, user_id)
            continue
        try:
            result[code] = _copy_platform_group(db, group, merchant_id, user_id)
            db.commit()
        except IntegrityError:  # initialized by a concurrent request first: its copy stands
            db.rollback()
    return result


def common_groups(db: Session) -> list[dict]:
    """Common Maintenance groups for the screen (COMMON_MAINTENANCE_GROUPS, active), with their code and Value rules.

    Empty while the Common tables are not migrated yet (e.g. Production before its approved migration), so the
    shared /options response used by MD keeps working.
    """
    if not inspect(db.get_bind()).has_table(CommonGroup.__tablename__):
        return []
    rows = db.query(CommonGroup).filter(CommonGroup.c_group_code.in_(COMMON_MAINTENANCE_GROUPS),
                                        CommonGroup.b_active.is_(True)).order_by(CommonGroup.i_display_order)
    result = []
    for g in rows:
        kind = g.c_value_type if g.c_value_type in COMMON_VALUE_TYPES else None
        result.append({
            "code": g.c_group_code, "name": g.c_group_name, "description": g.c_description,
            "code_mode": g.c_code_mode, "code_prefix": g.c_code_prefix,
            "code_max_length": min(int(g.i_code_max_length or 30), 30),
            "value_type": kind, "value_label": g.c_value_label, "value_required": bool(g.b_value_required),
            "value_scale": common_value_scale(g) if kind in ("NUMBER", "CURRENCY") else None,
            "value_min": _dec(g.n_value_min), "value_max": _dec(g.n_value_max),
            "text_max_length": min(int(g.i_text_max_length or 200), 200) if kind == "TEXT" else None,
            "read_only": g.c_group_code in READ_ONLY_COMMON_GROUPS,
            "read_only_message": READ_ONLY_COMMON_GROUPS.get(g.c_group_code),
        })
    return result


def normalize_location_code(value) -> str:
    return "" if value is None else str(value).strip().upper()


def _validate_location(db: Session, payload: dict, current, values: dict, errors: dict, store_id: int | None) -> None:
    if current is not None:
        # Fixed after create: Category III / MD Master keep Location Codes as text, so a rename would break them.
        # Key absent (or the same code) keeps the saved code; any other value is refused.
        code = current.c_location_code
        if "code" in payload and normalize_location_code(payload.get("code")) != code:
            errors["code"] = LOCATION_CODE_FIXED
    elif not (code := normalize_location_code(payload.get("code"))):
        errors["code"] = "Location Code is required."
    elif len(code) > 30:
        errors["code"] = "Must be 30 characters or fewer."
    elif not LOCATION_CODE.match(code):
        errors["code"] = "Use letters, numbers and - _ . / only (no spaces)."
    elif store_id is not None:
        same = db.query(StoreLocation.i_location_id).filter(
            StoreLocation.i_store_id == store_id, func.upper(StoreLocation.c_location_code) == code)
        if same.first():
            errors["code"] = f"Location Code {code} already exists in this Store."
    values["c_location_code"] = code
    # Optional: an empty Location Name is saved as the Location Code (the column stays NOT NULL).
    values["c_location_name"] = _text(payload, "name", 100, errors) or code
    # Sales Floor / Warehouse (required on the form; unchanged API: absent = the saved Area, or Sales Floor when new).
    area = str(payload.get("location_area") or (current.c_location_area if current is not None else DEFAULT_LOCATION_AREA)).strip().upper()
    if area not in LOCATION_AREAS:
        errors["location_area"] = "Select Sales Floor or Warehouse."
    elif current is not None and area != current.c_location_area and _location_referenced(db, current.i_location_id):
        errors["location_area"] = "The area cannot change while this location is in use."
    values["c_location_area"] = area
    # Earlier free-text position fields: kept unless sent (not shown on the screen).
    for key, column in (("zone", "c_zone"), ("aisle", "c_aisle"), ("bay", "c_bay"), ("level", "c_level")):
        values[column] = _text(payload, key, 50, errors) if key in payload else (getattr(current, column) if current is not None else None)
    values.update(_validate_common(payload, errors, ordered=True))


def _location_referenced(db: Session, location_id: int) -> bool:
    """Linked to an MD Detail."""
    return bool(db.query(StoreMdLocation.i_store_md_location_id).filter(StoreMdLocation.i_location_id == location_id).first())


def _validate_pricing(payload: dict, values: dict, errors: dict, retail) -> None:
    for n in (1, 2, 3):
        mode_key, pct_key, price_key = f"p{n}_mode", f"p{n}_discount_pct", f"p{n}_price"
        mode = str(payload.get(mode_key) or "").strip().upper()
        pct = price = None
        if mode == "":
            mode = None  # not set; deliberately different from $0
        elif mode == "FIXED":
            price = _decimal(payload, price_key, errors, places=2, maximum=MAX_PRICE, label=f"P{n} Price")
        elif mode == "DISCOUNT":
            pct = _decimal(payload, pct_key, errors, places=2, maximum=Decimal("100"), label=f"P{n} Discount %")
            if pct is not None and retail is not None:
                price = calc_discount_price(retail, pct)
        else:
            errors[mode_key] = "Select Fixed Amount or Discount %."
            mode = None
        values[f"c_p{n}_mode"] = mode
        values[f"n_p{n}_discount_pct"] = pct
        values[f"n_p{n}_price"] = price


def _validate_md(db: Session, payload: dict, current, values: dict, errors: dict, can_cost: bool = False,
                 merchant_id: int | None = None) -> None:
    """merchant_id None = Platform MD. A Merchant MD uses that Merchant's own Category I-III / Tax / UNIT, the global
    Item Kind, and never takes the Platform-only fields (MERCHANT_MD_FIXED_VALUES) or Recommended Locations."""
    values["c_item_name"] = _text(payload, "name", 200, errors, required=True, label="Item Name")
    values["c_second_name"] = _text(payload, "second_name", 200, errors, label="Second Name")
    values["c_description"] = _text(payload, "description", 2000, errors)

    kind = str(payload.get("item_kind") or DEFAULT_ITEM_KIND).strip().upper()
    kind_row = db.query(ItemKind).filter(ItemKind.c_item_kind_code == kind).first()
    keeps_current = current is not None and current.c_item_kind == kind
    if kind_row is None or not (kind_row.b_active or keeps_current):
        errors["item_kind"] = "Select an active Item Kind."
    elif not item_kind_logic_defined(kind) and not keeps_current:
        errors["item_kind"] = "This Item Kind has no program logic yet and cannot be used."
    values["c_item_kind"] = kind

    if merchant_id is None:
        cycle = str(payload.get("billing_cycle") or "").strip().upper()
        if cycle not in BILLING_CYCLES:
            errors["billing_cycle"] = "Select One-time, Monthly or Annual."
        values["c_billing_cycle"] = cycle

    # Item overrides; NULL inherits the Category III default (checked against it after the category chain below).
    values["i_inventory_type"] = _inventory_type(payload, errors, current)
    values["c_keep"] = _keep(payload, errors, current)
    values["i_output_devices"] = _output_devices(payload, errors, current)
    if merchant_id is None:
        values.update(_recommended_locations(db, payload, current, errors))  # NULL = inherit the Category III value
    else:  # Merchant MD: Recommended Location waits for the Merchant shared Location Master; payload keys ignored
        values.update({column: getattr(current, column) if current is not None else None
                       for _key, column in LOCATION_FIELDS})

    values["c_unit"] = _unit(db, payload, current.c_unit if current is not None else None, errors, merchant_id)
    values["n_default_unit_price"] = _decimal(
        payload, "retail_price", errors, places=2, maximum=MAX_PRICE, label="Retail Price"
    )
    # Always re-derived, so a Retail change refreshes DISCOUNT levels while FIXED levels keep their amount.
    if merchant_id is None:
        _validate_pricing(payload, values, errors, values["n_default_unit_price"])
    if can_cost:
        values["n_cost"] = _decimal(payload, "cost", errors, places=2, maximum=MAX_PRICE, label="Cost", required=False)

    c1 = _id(payload, "category1_id", errors, required=True, label="Category I")
    c2 = _id(payload, "category2_id", errors, required=False, label="Category II")
    c3 = _id(payload, "category3_id", errors, required=False, label="Category III")
    tax_id = _id(payload, "default_tax_id", errors, required=False, label="Default Tax")
    values.update(i_category1_id=c1, i_category2_id=c2, i_category3_id=c3, i_default_tax_id=tax_id)

    if c3 is not None and c2 is None and "category3_id" not in errors:
        errors["category3_id"] = "Select Category II before Category III."
    cat_bad = {"category1_id", "category2_id", "category3_id"} & set(errors)
    if not cat_bad:
        p1 = _scoped_parent(db, Category1, c1, merchant_id)  # another scope's category is "does not exist"
        p2 = _scoped_parent(db, Category2, c2, merchant_id)
        p3 = _scoped_parent(db, Category3, c3, merchant_id)
        if not p1:
            errors["category1_id"] = "Category I does not exist."
        elif c2 is not None and not p2:
            errors["category2_id"] = "Category II does not exist."
        elif p2 and p2.i_category1_id != c1:
            errors["category2_id"] = "Category II does not belong to the selected Category I."
        elif c3 is not None and not p3:
            errors["category3_id"] = "Category III does not exist."
        elif p3 and (p3.i_category2_id != c2 or p3.i_category1_id != c1):
            errors["category3_id"] = "Category III does not belong to the selected Category II."
        else:
            for key, parent, new, old in (
                ("category1_id", p1, c1, current.i_category1_id if current else None),
                ("category2_id", p2, c2, current.i_category2_id if current else None),
                ("category3_id", p3, c3, current.i_category3_id if current else None),
            ):
                if parent is not None and not parent.b_active and new != old:
                    errors[key] = "This category is inactive."

    if tax_id is not None and "default_tax_id" not in errors:
        tax = _scoped_parent(db, Tax, tax_id, merchant_id)
        if not tax:
            errors["default_tax_id"] = "Tax does not exist."
        elif not tax.b_active and (current is None or current.i_default_tax_id != tax_id):
            errors["default_tax_id"] = "Tax is inactive."

    values["b_serial_required"] = _bool(payload, "serial_required", False)  # every scope (not tied to licenses)
    license_required = _bool(payload, "license_required", False) if merchant_id is None else False
    values["b_license_required"] = license_required
    if merchant_id is not None:
        values.update(MERCHANT_MD_FIXED_VALUES)  # whatever the payload says
    elif license_required:
        ltype = str(payload.get("license_type") or "").strip().upper()
        if ltype not in LICENSE_TYPES:
            errors["license_type"] = "Select POS, KIOSK or Customer Ordering."
        values["c_license_type"] = ltype
        values["i_licenses_per_unit"] = _int(
            payload, "licenses_per_unit", errors, minimum=1, maximum=1000, label="Licenses per Unit", default=1
        )
    else:
        values["c_license_type"] = None
        values["i_licenses_per_unit"] = None
    values.update(_validate_common(payload, errors, ordered=False))


def _serialize_md_detail(row: MdDetail) -> dict:
    return {
        "id": row.i_md_detail_id,
        "md_id": row.i_md_id,
        "detail_no": row.i_detail_no,
        "is_default": bool(row.b_is_default),
        "barcode": row.c_barcode,
        "unit": row.c_unit,
        "pk_size": row.c_pk_size,
        "size": row.c_size,
        "cost": _dec(row.n_cost),
        "price": _dec(row.n_price),
        "modifier": row.c_modifier,
        "color_code_id": row.i_color_code_id,  # COLOR common code (Common Maintenance)
    }


def _md_parent_for_detail(db: Session, md_id: int, *, lock: bool = False, merchant_id: int | None = None) -> MdMaster:
    """The MD of this scope (Platform when merchant_id is None); another owner's MD is "not found"."""
    query = db.query(MdMaster).filter(MdMaster.i_md_id == md_id, MdMaster.i_owner_key == md_owner_key(merchant_id))
    if lock:
        query = query.with_for_update()
    row = query.first()
    if row is None:
        raise NotFound("MD Master record not found.")
    return row


def list_md_details(db: Session, md_id: int, merchant_id: int | None = None) -> list[dict]:
    _md_parent_for_detail(db, md_id, merchant_id=merchant_id)
    rows = db.query(MdDetail).filter(MdDetail.i_md_id == md_id).order_by(MdDetail.i_detail_no).all()
    return [_serialize_md_detail(row) for row in rows]


def _validate_md_detail(payload: dict, current: MdDetail | None, errors: dict, db: Session | None = None,
                        merchant_id: int | None = None) -> dict:
    values = {}
    values["c_barcode"] = _text(payload, "barcode", 100, errors)
    values["c_unit"] = _unit(db, payload, current.c_unit if current is not None else None, errors, merchant_id)
    values["c_pk_size"] = _text(payload, "pk_size", 100, errors)
    values["c_size"] = _text(payload, "size", 100, errors)
    values["n_cost"] = _decimal(
        payload, "cost", errors, places=2, maximum=MAX_PRICE, label="Cost", required=False,
    )
    values["n_price"] = _decimal(
        payload, "price", errors, places=2, maximum=MAX_PRICE, label="Price",
    )
    # Key absent keeps the saved Modifier (e.g. a Store with Use Modifier OFF); empty still clears it.
    values["c_modifier"] = (_text(payload, "modifier", 2000, errors)
                            if "modifier" in payload or current is None else current.c_modifier)
    # Same rule for Color (a Store with Use Color OFF omits color_code_id: saved value kept); blank/null clears it.
    values["i_color_code_id"] = _detail_color(db, payload, current, errors, merchant_id)
    if "is_default" in payload:
        values["b_is_default"] = _bool(payload, "is_default", False)
    elif current is not None:
        values["b_is_default"] = current.b_is_default
    else:
        values["b_is_default"] = False
    if errors:
        raise FieldErrors(errors)
    return values


def _detail_color(db: Session | None, payload: dict, current: MdDetail | None, errors: dict,
                  merchant_id: int | None = None) -> int | None:
    """i_color_code_id of an MD Detail: a COLOR common code; must exist, and be active unless it is the detail's
    saved Color (kept when inactive). Key absent (Use Color OFF) keeps the saved value. The removed tb_color id
    (color_id) is refused, whatever its value, so an earlier client never believes it saved a Color."""
    saved = current.i_color_code_id if current is not None else None
    if "color_id" in payload:
        errors["color_id"] = COLOR_ID_REMOVED
    if "color_code_id" not in payload:
        return saved
    code_id = _id(payload, "color_code_id", errors, required=False, label="Color")
    if "color_code_id" in errors:
        return saved
    if code_id is None:
        return None
    code = (_color_code_query(db, merchant_id).filter(CommonCode.i_common_code_id == code_id).first()
            if db is not None else None)  # the scope's own COLOR codes only
    if code is None:
        errors["color_code_id"] = "Color does not exist."
    elif not code.b_active and code_id != saved:
        errors["color_code_id"] = "Color is inactive."
    return code_id


def _ensure_barcode_free(db: Session, barcode: str | None, detail: MdDetail | None, owner_key: int) -> None:
    """Unique within the MD's owner (Common services.barcode); the message names the owner's Item that has it."""
    message = barcode_rules.barcode_conflict(
        db, barcode, owner_key=owner_key, exclude_detail_id=detail.i_md_detail_id if detail is not None else None,
        current_barcode=detail.c_barcode if detail is not None else None,
    )
    if message:
        raise FieldErrors({"barcode": message}, message)


def _commit_detail(db: Session, barcode: str | None, owner_key: int) -> None:
    """Commit; a concurrent save that took the same barcode first hits the DB UNIQUE index instead."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        message = barcode_rules.barcode_conflict(db, barcode, owner_key=owner_key)
        if message:
            raise FieldErrors({"barcode": message}, message) from None
        raise


def check_default_barcode(db: Session, md_id: int | None, barcode, merchant_id: int | None = None) -> dict:
    """Pre-save check for the MD form's Barcode (the Default Detail's barcode) within the scope's owner; read-only."""
    owner = md_owner_key(merchant_id)
    default = None
    if md_id is not None:
        default = db.query(MdDetail).filter(MdDetail.i_md_id == md_id, MdDetail.i_owner_key == owner,
                                            MdDetail.b_is_default.is_(True)).first()
    message = barcode_rules.barcode_conflict(
        db, barcode, owner_key=owner, exclude_detail_id=default.i_md_detail_id if default is not None else None,
        current_barcode=default.c_barcode if default is not None else None,
    )
    return {"barcode": barcode_rules.normalize_barcode(barcode), "available": message is None, "message": message}


def create_md_detail(db: Session, md_id: int, payload: dict, user_id: int, merchant_id: int | None = None) -> dict:
    parent = _md_parent_for_detail(db, md_id, lock=True, merchant_id=merchant_id)
    current_default = db.query(MdDetail).filter(
        MdDetail.i_md_id == md_id, MdDetail.b_is_default.is_(True),
    ).first()
    values = _validate_md_detail(payload, None, {}, db, parent.i_merchant_id)
    _ensure_barcode_free(db, values["c_barcode"], None, parent.i_owner_key)
    if current_default is None:
        values["b_is_default"] = True
    next_no = (db.query(func.max(MdDetail.i_detail_no))
               .filter(MdDetail.i_md_id == md_id).scalar() or 0) + 1
    if values["b_is_default"] and current_default is not None:
        current_default.b_is_default = False
        _stamp(current_default, user_id, created=False)
        db.flush()
    row = MdDetail(i_md_id=md_id, i_owner_key=parent.i_owner_key, i_detail_no=next_no, **values)
    _stamp(row, user_id, created=True)
    db.add(row)
    if row.b_is_default:
        parent.c_unit = row.c_unit
        parent.n_default_unit_price = row.n_price
        parent.n_cost = row.n_cost
        _stamp(parent, user_id, created=False)
    _commit_detail(db, values["c_barcode"], parent.i_owner_key)
    db.refresh(row)
    return _serialize_md_detail(row)


def update_md_detail(db: Session, md_id: int, detail_id: int, payload: dict, user_id: int,
                     merchant_id: int | None = None) -> dict:
    parent = _md_parent_for_detail(db, md_id, lock=True, merchant_id=merchant_id)
    row = db.query(MdDetail).filter(
        MdDetail.i_md_detail_id == detail_id, MdDetail.i_md_id == md_id,
    ).with_for_update().first()
    if row is None:
        raise NotFound("MD Detail not found.")
    values = _validate_md_detail(payload, row, {}, db, parent.i_merchant_id)
    _ensure_barcode_free(db, values["c_barcode"], row, parent.i_owner_key)
    if row.b_is_default and not values["b_is_default"]:
        raise FieldErrors({"is_default": "An MD must have one Default Detail."})
    if values["b_is_default"] and not row.b_is_default:
        defaults = db.query(MdDetail).filter(
            MdDetail.i_md_id == md_id, MdDetail.b_is_default.is_(True),
        ).all()
        for default in defaults:
            default.b_is_default = False
            _stamp(default, user_id, created=False)
        db.flush()
    for key, value in values.items():
        setattr(row, key, value)
    _stamp(row, user_id, created=False)
    if row.b_is_default:
        parent.c_unit = row.c_unit
        parent.n_default_unit_price = row.n_price
        parent.n_cost = row.n_cost
        _stamp(parent, user_id, created=False)
    _commit_detail(db, values["c_barcode"], parent.i_owner_key)
    db.refresh(row)
    return _serialize_md_detail(row)


def delete_md_detail(db: Session, md_id: int, detail_id: int, merchant_id: int | None = None) -> None:
    _md_parent_for_detail(db, md_id, lock=True, merchant_id=merchant_id)
    row = db.query(MdDetail).filter(
        MdDetail.i_md_detail_id == detail_id, MdDetail.i_md_id == md_id,
    ).with_for_update().first()
    if row is None:
        raise NotFound("MD Detail not found.")
    if row.b_is_default:
        raise Conflict("The Default Detail cannot be deleted.")
    # Only this detail's Location links go; the Store Location Master rows stay (the DB FK also cascades).
    db.query(StoreMdLocation).filter(StoreMdLocation.i_md_detail_id == row.i_md_detail_id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()


# ---------- usage ----------

def _category2_in_use(db: Session, cid: int) -> bool:
    return bool(
        db.query(Category3.i_category3_id).filter(Category3.i_category2_id == cid).first()
        or db.query(MdMaster.i_md_id).filter(MdMaster.i_category2_id == cid).first()
    )


def _category3_in_use(db: Session, cid: int) -> bool:
    return bool(db.query(MdMaster.i_md_id).filter(MdMaster.i_category3_id == cid).first())


def is_in_use(db: Session, entity: str, row) -> bool:
    if entity == "tax":
        return bool(
            db.query(MdMaster.i_md_id).filter(MdMaster.i_default_tax_id == row.i_tax_id).first()
            or db.query(Category3.i_category3_id).filter(or_(
                Category3.i_tax1_id == row.i_tax_id,
                Category3.i_tax2_id == row.i_tax_id,
                Category3.i_tax3_id == row.i_tax_id,
            )).first()
        )
    if entity == "category1":
        cid = row.i_category1_id
        return bool(
            db.query(Category2.i_category2_id).filter(Category2.i_category1_id == cid).first()
            or db.query(Category3.i_category3_id).filter(Category3.i_category1_id == cid).first()
            or db.query(MdMaster.i_md_id).filter(MdMaster.i_category1_id == cid).first()
        )
    if entity == "category2":
        return _category2_in_use(db, row.i_category2_id)
    if entity == "category3":
        return _category3_in_use(db, row.i_category3_id)
    if entity == "item_kind":  # MD Master stores the System Code
        return bool(db.query(MdMaster.i_md_id).filter(MdMaster.c_item_kind == row.c_item_kind_code).first())
    if entity == "location":  # linked to an MD Detail
        return _location_referenced(db, row.i_location_id)
    if entity == "common_code":
        group = db.get(CommonGroup, row.i_common_group_id)
        return common_code_in_use(db, group.c_group_code if group else "", row)
    return bool(db.query(InvoiceLine.i_invoice_line_id).filter(InvoiceLine.i_md_id == row.i_md_id).first())


# ---------- serialization ----------

def location_area_info(row: StoreLocation) -> dict:
    """Sales Floor / Warehouse of a location (a flat list per Store; no parent tree)."""
    return {
        "location_area": row.c_location_area,
        "location_area_name": LOCATION_AREAS.get(row.c_location_area, row.c_location_area),
    }


def _dec(value) -> str | None:
    return None if value is None else format(value, "f")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _resolve_md_inventory(row: MdMaster, category3) -> inventory.ResolvedInventory:
    return inventory.resolve_inventory(
        row.i_inventory_type, row.c_keep,
        category3.i_inventory_type if category3 is not None else None,
        category3.c_keep if category3 is not None else None,
    )


def _effective_inventory(row: MdMaster, category3) -> dict:
    resolved = _resolve_md_inventory(row, category3)
    return {
        "effective_inventory_type": resolved.behavior.type_id,
        "inventory_type_source": resolved.type_source,
        "effective_keep": resolved.keep,
        "keep_source": resolved.keep_source,
    }


def _md_locations(row: MdMaster, category3) -> dict:
    """MD Override values (None = inherit) plus the recommended positions in effect and their source."""
    out = {}
    for key, column in LOCATION_FIELDS:
        value, source = resolve_md_location(getattr(row, column), getattr(category3, column) if category3 is not None else None)
        out.update({key: getattr(row, column), f"effective_{key}": value, f"{key}_source": source})
    return out


def serialize(db: Session, entity: str, row, include_cost: bool = False) -> dict:
    base = {
        "id": getattr(row, ENTITIES[entity]["pk"]),
        "code": getattr(row, ENTITIES[entity]["code"]),
        "active": bool(row.b_active),
        "notes": row.c_notes,
        "updated_at": _iso(row.dt_updated),
        "in_use": is_in_use(db, entity, row),
    }
    if entity == "tax":
        base.update(name=row.c_tax_name, rate=_dec(row.n_tax_rate))
        return base
    base["name"] = getattr(row, ENTITIES[entity]["name"])
    if entity == "item_kind":
        base.update(display_order=row.i_display_order, system_code=row.c_item_kind_code,
                    system=is_system_row(entity, row), active_locked=row.c_item_kind_code == DEFAULT_ITEM_KIND,
                    logic_defined=item_kind_logic_defined(row.c_item_kind_code))
        return base
    if entity == "common_code":
        group = db.get(CommonGroup, row.i_common_group_id)
        protected = (group.c_group_code, row.c_code) in PROTECTED_COMMON_CODES
        base.update(group=group.c_group_code, description=row.c_description, display_order=row.i_display_order,
                    value_type=group.c_value_type, value=common_value_text(row, group),
                    read_only=group.c_group_code in READ_ONLY_COMMON_GROUPS,
                    # Protected codes (EA) stay active and cannot be deleted (same flags as System Codes).
                    protected=protected, active_locked=protected, system=protected)
        return base
    if entity == "location":
        base.update(store_id=row.i_store_id, **location_area_info(row),
                    zone=row.c_zone, aisle=row.c_aisle, bay=row.c_bay, level=row.c_level,
                    display_order=row.i_display_order)
        return base
    if entity in ("category1", "category2", "category3"):
        base["display_order"] = row.i_display_order
    if entity in ("category2", "category3"):
        c1 = _scoped_parent(db, Category1, row.i_category1_id, row.i_merchant_id)
        base.update(category1_id=row.i_category1_id, category1_name=c1.c_category_name if c1 else None)
    if entity == "category3":
        c2 = _scoped_parent(db, Category2, row.i_category2_id, row.i_merchant_id)
        base.update(category2_id=row.i_category2_id, category2_name=c2.c_category_name if c2 else None)
        for n in (1, 2, 3):
            base[f"tax{n}_id"] = getattr(row, f"i_tax{n}_id")
        base.update(inventory_type=row.i_inventory_type, keep=row.c_keep,
                    sales_location=row.c_sales_location, warehouse_location=row.c_warehouse_location)
    if entity == "md":
        c1 = db.get(Category1, row.i_category1_id)
        c2 = db.get(Category2, row.i_category2_id) if row.i_category2_id else None
        c3 = db.get(Category3, row.i_category3_id) if row.i_category3_id else None
        tax = db.get(Tax, row.i_default_tax_id) if row.i_default_tax_id else None
        base.update(
            second_name=row.c_second_name,
            description=row.c_description,
            image_path=row.c_image_path,
            image_url=images.image_url(row.c_image_path),
            item_kind=row.c_item_kind,
            category1_id=row.i_category1_id,
            category1_name=c1.c_category_name if c1 else None,
            category2_id=row.i_category2_id,
            category2_name=c2.c_category_name if c2 else None,
            category3_id=row.i_category3_id,
            category3_name=c3.c_category_name if c3 else None,
            unit=row.c_unit,
            retail_price=_dec(row.n_default_unit_price),
            p1_mode=row.c_p1_mode, p1_discount_pct=_dec(row.n_p1_discount_pct), p1_price=_dec(row.n_p1_price),
            p2_mode=row.c_p2_mode, p2_discount_pct=_dec(row.n_p2_discount_pct), p2_price=_dec(row.n_p2_price),
            p3_mode=row.c_p3_mode, p3_discount_pct=_dec(row.n_p3_discount_pct), p3_price=_dec(row.n_p3_price),
            default_tax_id=row.i_default_tax_id,
            default_tax_name=tax.c_tax_name if tax else None,
            default_tax_rate=_dec(tax.n_tax_rate) if tax else None,
            billing_cycle=row.c_billing_cycle,
            # inventory_type / keep are the MD overrides (None = inherit); effective_* are resolved via Category III.
            inventory_type=row.i_inventory_type,
            keep=row.c_keep,
            **_effective_inventory(row, c3),
            output_devices=output_devices_list(row.i_output_devices),
            **_md_locations(row, c3),
            license_required=bool(row.b_license_required),
            license_type=row.c_license_type,
            licenses_per_unit=row.i_licenses_per_unit,
            serial_required=bool(row.b_serial_required),
        )
        if include_cost:  # the only place cost leaves the server (API, and any future export)
            base["cost"] = _dec(row.n_cost)
    return base


# ---------- queries / commands ----------

SORT_COLUMNS = {
    "tax": {"code": "c_tax_code", "name": "c_tax_name", "rate": "n_tax_rate", "active": "b_active"},
    "category1": {"code": "c_category_code", "name": "c_category_name", "display_order": "i_display_order", "active": "b_active"},
    "category2": {"code": "c_category_code", "name": "c_category_name", "display_order": "i_display_order", "active": "b_active"},
    "category3": {"code": "c_category_code", "name": "c_category_name", "display_order": "i_display_order", "active": "b_active"},
    "md": {
        "code": "c_item_code", "name": "c_item_name", "item_kind": "c_item_kind",
        "retail_price": "n_default_unit_price", "p1_price": "n_p1_price", "p2_price": "n_p2_price",
        "p3_price": "n_p3_price", "billing_cycle": "c_billing_cycle", "active": "b_active",
    },
    "item_kind": {"code": "c_item_kind_no", "system_code": "c_item_kind_code", "name": "c_item_kind_name",
                  "display_order": "i_display_order", "active": "b_active"},
    "location": {"code": "c_location_code", "name": "c_location_name", "location_area": "c_location_area",
                 "display_order": "i_display_order", "active": "b_active"},
    "common_code": {"code": "c_code", "name": "c_name", "display_order": "i_display_order", "active": "b_active"},
}
DEFAULT_SORT = {"tax": "code", "category1": "display_order", "category2": "display_order", "category3": "display_order", "md": "code",
                "item_kind": "display_order", "location": "display_order",
                "common_code": "display_order"}


def _filtered_query(db: Session, entity: str, q, active, sort, direction, merchant_id: int | None = None,
                    store_id: int | None = None, group: str | None = None):
    """Search / status filter / sort shared by the list screen and Export."""
    cfg = entity_config(entity)
    model = cfg["model"]
    query = _entity_query(db, entity, merchant_id, store_id, group)

    term = (q or "").strip()
    if term:
        like = f"%{term.replace('%', '').replace('_', ' ')}%"
        columns = [getattr(model, col) for col in (cfg["code"], cfg["name"], *cfg.get("search", ()))]
        query = query.filter(or_(*(col.ilike(like) for col in columns)))
    if active in ("active", "inactive"):
        query = query.filter(model.b_active.is_(active == "active"))

    cols = SORT_COLUMNS[entity]
    key = sort if sort in cols else DEFAULT_SORT[entity]
    column = getattr(model, cols[key])
    ordered = column.desc() if direction == "desc" else column.asc()
    query = query.order_by(ordered, getattr(model, cfg["pk"]).asc())
    return query, key


def list_items(db: Session, entity: str, *, q="", active="", sort="", direction="asc", page=1, size=20,
               can_cost=False, merchant_id: int | None = None, store_id: int | None = None,
               group: str | None = None, location_area: str | None = None) -> dict:
    query, key = _filtered_query(db, entity, q, active, sort, direction, merchant_id, store_id, group)
    if location_area:  # Location Master two-column list: one Area per request (Sales Floor / Warehouse)
        area = str(location_area).strip().upper()
        if entity != "location" or area not in LOCATION_AREAS:
            raise FieldErrors({"location_area": "Select Sales Floor or Warehouse."})
        query = query.filter(StoreLocation.c_location_area == area)

    size = min(max(int(size or 20), 1), 100)
    total = query.order_by(None).count()
    pages = max((total + size - 1) // size, 1)
    page = min(max(int(page or 1), 1), pages)
    rows = query.offset((page - 1) * size).limit(size).all()
    return {
        "items": [serialize(db, entity, r, can_cost) for r in rows],
        "total": total, "page": page, "pages": pages, "size": size,
        "sort": key, "direction": "desc" if direction == "desc" else "asc",
    }


def _lock_md_images(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        # Also serializes MD code allocation and generic writes to the same canonical files.
        db.execute(text("SELECT pg_advisory_xact_lock(781962147)"))


def _serialize_md_writes(command):
    @wraps(command)
    def locked(db: Session, entity: str, *args, **kwargs):
        if entity != "md":
            return command(db, entity, *args, **kwargs)
        with master_image_lock():
            return command(db, entity, *args, **kwargs)
    return locked


def _commit_md(db: Session, row: MdMaster, *, image_data: bytes | None = None,
               remove_image: bool = False, delete: bool = False) -> str | None:
    from backend.services.resource_storage import AwsumStorageService
    storage = AwsumStorageService(db)
    owner = (storage.resolve_owner("MERCHANT", row.i_merchant_id) if row.i_merchant_id is not None
             else storage.resolve_owner("PLATFORM"))
    return commit_entity_image(db, row, owner, row.c_item_code,
                               category="master", image_data=image_data, remove_image=remove_image, delete=delete)


@_serialize_md_writes
def create_item(db: Session, entity: str, payload: dict, user_id, can_cost: bool = False,
                *, image_data: bytes | None = None, merchant_id: int | None = None, store_id: int | None = None,
                group: str | None = None) -> dict:
    _entity_query(db, entity, merchant_id, store_id, group)
    if entity_config(entity).get("system"):
        raise Conflict("System codes are predefined and cannot be added.")
    if entity_config(entity).get("group_scoped"):
        common_read_only(db, group)
    if entity == "md":
        _lock_md_images(db)
    cfg = entity_config(entity)
    values = _validate_payload(db, entity, payload, None, can_cost, merchant_id, store_id, group)
    if hasattr(cfg["model"], "i_merchant_id"):  # the scope's owner, never a payload value
        values["i_merchant_id"] = merchant_id
    if entity == "md":
        values["i_owner_key"] = md_owner_key(merchant_id)  # = COALESCE(i_merchant_id, 0) (DB CHECK)
    if cfg.get("group_scoped"):
        values["i_common_group_id"] = common_group(db, group).i_common_group_id  # never a payload value
    if cfg.get("store_scoped"):
        values["i_store_id"] = store_id  # always the authorized Store, never a payload value
    for _ in range(5):
        if entity == "md":
            _lock_md_images(db)
        row = cfg["model"](**values)
        if not cfg.get("manual_code"):
            setattr(row, cfg["code"], _next_code(db, cfg))
        _stamp(row, user_id, created=True)
        db.add(row)
        try:
            if entity == "md":
                db.flush()
                detail = MdDetail(
                    i_md_id=row.i_md_id,
                    i_owner_key=row.i_owner_key,
                    i_detail_no=1,
                    b_is_default=True,
                    c_unit=row.c_unit,
                    n_cost=row.n_cost,
                    n_price=row.n_default_unit_price,
                )
                _stamp(detail, user_id, created=True)
                db.add(detail)
            else:
                db.commit()
        except IntegrityError:
            db.rollback()
            if cfg.get("manual_code"):  # a concurrent save took the same code first (DB unique index)
                raise FieldErrors({"code": "This code already exists."}) from None
            continue
        warning = _commit_md(db, row, image_data=image_data) if entity == "md" else None
        db.refresh(row)
        item = serialize(db, entity, row, can_cost)
        if warning:
            item["image_warning"] = warning
        return item
    raise Conflict("Could not allocate a unique code. Please try again.")


# ---------- Location Master: range registration (Zone + two From/To ranges -> every combination) ----------

# Area -> its two range parts: (payload key, label, code letter). Sales Floor S01-B01-C01, Warehouse W01-R01-C01.
LOCATION_RANGE_PARTS = {
    "SALES_FLOOR": (("bay", "Bay", "B"), ("shelf", "Shelf", "C")),
    "WAREHOUSE": (("rack", "Rack", "R"), ("level", "Level", "C")),
}
LOCATION_RANGE_ZONE = re.compile(r"^[A-Z0-9]{1,10}$")
LOCATION_RANGE_MAX_NUMBER = 99    # two digits: B01 ... B99
LOCATION_RANGE_MAX_CODES = 500    # per save (one transaction)


def location_range_codes(payload: dict) -> tuple[str, list[str]]:
    """Validate the range form and return (area, codes). From = To gives one code; otherwise every combination,
    in code order. Raises FieldErrors with the form's field names."""
    errors: dict[str, str] = {}
    area = str(payload.get("location_area") or "").strip().upper()
    if area not in LOCATION_RANGE_PARTS:
        raise FieldErrors({"location_area": "Select Sales Floor or Warehouse."})
    zone = normalize_location_code(payload.get("zone"))
    if not zone:
        errors["zone"] = "Zone is required."
    elif not LOCATION_RANGE_ZONE.match(zone):
        errors["zone"] = "Use letters and numbers only (up to 10), e.g. S01 or W01."
    ranges = []
    for key, label, letter in LOCATION_RANGE_PARTS[area]:
        bounds = []
        for side in ("from", "to"):
            name = f"{key}_{side}"
            number = _int(payload, name, errors, minimum=1, maximum=LOCATION_RANGE_MAX_NUMBER,
                          label=f"{label} {side.title()}")
            if number is None and name not in errors:
                errors[name] = f"{label} {side.title()} is required."
            bounds.append(number)
        low, high = bounds
        if low is not None and high is not None and low > high:
            errors[f"{key}_to"] = f"{label} To must be {label} From or greater."
        ranges.append((letter, low, high))
    if errors:
        raise FieldErrors(errors)
    total = 1
    for _letter, low, high in ranges:
        total *= high - low + 1
    if total > LOCATION_RANGE_MAX_CODES:
        key = LOCATION_RANGE_PARTS[area][0][0]
        raise FieldErrors({f"{key}_to": f"{total} locations: up to {LOCATION_RANGE_MAX_CODES} can be created at a time."})
    (first, a_low, a_high), (second, b_low, b_high) = ranges
    codes = [f"{zone}-{first}{a:02d}-{second}{b:02d}" for a in range(a_low, a_high + 1) for b in range(b_low, b_high + 1)]
    return area, codes


def _existing_location_codes(db: Session, store_id: int, codes: list[str]) -> set[str]:
    if not codes:
        return set()
    return {code.upper() for (code,) in db.query(StoreLocation.c_location_code).filter(
        StoreLocation.i_store_id == store_id, func.upper(StoreLocation.c_location_code).in_(codes))}


def preview_location_range(db: Session, payload: dict, *, merchant_id: int | None = None,
                           store_id: int | None = None) -> dict:
    """Read only: every code of the range, which already exist in this Store (excluded) and which are new."""
    _entity_query(db, "location", merchant_id, store_id)
    area, codes = location_range_codes(payload)
    existing = _existing_location_codes(db, store_id, codes)
    return {"location_area": area, "codes": [{"code": c, "exists": c in existing} for c in codes],
            "total": len(codes), "duplicates": len(existing), "new": len(codes) - len(existing)}


def create_location_range(db: Session, payload: dict, user_id, *, merchant_id: int | None = None,
                          store_id: int | None = None) -> dict:
    """Save only the new codes of the range in one transaction (all or nothing). Existing codes are skipped; the
    Store + Location Code unique index is the final guard: a code added concurrently rolls everything back."""
    _entity_query(db, "location", merchant_id, store_id)
    area, codes = location_range_codes(payload)
    errors: dict[str, str] = {}
    name = _text(payload, "name", 100, errors)
    common = _validate_common(payload, errors, ordered=True)
    if errors:
        raise FieldErrors(errors)
    existing = _existing_location_codes(db, store_id, codes)
    rows = []
    for code in (c for c in codes if c not in existing):
        row = StoreLocation(i_store_id=store_id, c_location_code=code, c_location_name=name or code,
                            c_location_area=area, **common)
        _stamp(row, user_id, created=True)
        rows.append(row)
    if rows:
        db.add_all(rows)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise Conflict("Some of these Location Codes were just added in this Store. Nothing was saved; "
                           "check the preview and save again.") from None
    return {"items": [serialize(db, "location", row) for row in rows],
            "skipped": sorted(existing), "total": len(codes), "created": len(rows), "duplicates": len(existing)}



# ---------- Location Master: default Locations of a Store (once, on the first visit) ----------

# Created only when the Store has no Location at all and was never initialized (the range form's codes).
DEFAULT_LOCATION_RANGES = (
    {"location_area": "SALES_FLOOR", "zone": "S01", "bay_from": 1, "bay_to": 10, "shelf_from": 1, "shelf_to": 1},
    {"location_area": "WAREHOUSE", "zone": "W01", "rack_from": 1, "rack_to": 10, "level_from": 1, "level_to": 1},
)


def initialize_store_locations(db: Session, user_id, *, merchant_id: int | None = None,
                               store_id: int | None = None) -> dict:
    """First visit of a Store's Location Master: S01-B01-C01 ... S01-B10-C01 and W01-R01-C01 ... W01-R10-C01.

    - Already initialized (tb_store_location_init row): nothing happens, even if every Location was deleted since.
    - Any Location exists (either Area): only the record is written (0 created); nothing is generated.
    - Otherwise the 20 Locations and the record are saved in one transaction.
    The record's primary key (Store id) and the Store + Location Code unique index make a concurrent first visit
    fail as a whole (rolled back), so a Store never gets the defaults twice.
    """
    from backend.models_admin.store_location import StoreLocationInit

    _entity_query(db, "location", merchant_id, store_id)
    if db.get(StoreLocationInit, store_id) is not None:
        return {"initialized": False, "created": 0, "reason": "already_initialized"}
    has_locations = db.query(StoreLocation.i_location_id).filter(StoreLocation.i_store_id == store_id).first() is not None
    rows = []
    if not has_locations:
        for spec in DEFAULT_LOCATION_RANGES:
            area, codes = location_range_codes(spec)
            for code in codes:
                row = StoreLocation(i_store_id=store_id, c_location_code=code, c_location_name=code, c_location_area=area,
                                    **_validate_common({}, {}, ordered=True))
                _stamp(row, user_id, created=True)
                rows.append(row)
    record = StoreLocationInit(i_store_id=store_id, i_created_count=len(rows))
    _stamp(record, user_id, created=True)
    db.add(record)
    try:
        db.flush()  # the record first: a concurrent first visit stops here (primary key) before any Location is written
        db.add_all(rows)
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"initialized": False, "created": 0, "reason": "concurrent"}
    return {"initialized": True, "created": len(rows), "reason": "created" if rows else "existing_locations"}


# ---------- export ----------

NOT_SET = "Not set"  # an unset price is not the same as 0.00
_CYCLE_LABEL = {"ONE_TIME": "One-time", "MONTHLY": "Monthly", "ANNUAL": "Annual"}
_MODE_LABEL = {"FIXED": "Fixed Amount", "DISCOUNT": "Discount %"}


def _money(value) -> str:
    return NOT_SET if value is None else format_money(value)


def _pct(value) -> str:
    return "" if value is None else format(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP), "f")


def _yn(value) -> str:
    return "Yes" if value else "No"


def export_table(db: Session, entity: str, *, q="", active="", sort="", direction="asc", can_cost=False,
                 merchant_id: int | None = None, store_id: int | None = None,
                 group: str | None = None) -> tuple[list[str], list[list[str]]]:
    """Every row matching the current search/filter/sort (not just one page), as display strings.

    Cost is only included when can_cost is true; otherwise the column does not exist.
    """
    query, _key = _filtered_query(db, entity, q, active, sort, direction, merchant_id, store_id, group)
    rows = query.all()
    status = lambda r: "Active" if r.b_active else "Inactive"

    if entity == "tax":
        headers = ["Tax Code", "Tax Name", "Tax Rate (%)", "Status", "Notes"]
        data = [[r.c_tax_code, r.c_tax_name, _pct(r.n_tax_rate), status(r), r.c_notes or ""] for r in rows]
        return headers, data

    if entity == "item_kind":
        headers = ["Code", "System Code", "Display Name", "Display Order", "Status", "Notes"]
        data = [[r.c_item_kind_no or "", r.c_item_kind_code, r.c_item_kind_name, str(r.i_display_order), status(r),
                 r.c_notes or ""] for r in rows]
        return headers, data

    if entity == "location":
        headers = ["Location Code", "Location Name", "Area", "Display Order", "Status", "Notes"]
        data = [[r.c_location_code, r.c_location_name, location_area_info(r)["location_area_name"],
                 str(r.i_display_order), status(r), r.c_notes or ""] for r in rows]
        return headers, data

    if entity == "common_code":
        selected = common_group(db, group)
        valued = selected.c_value_type in COMMON_VALUE_TYPES
        headers = ["Code", "Name", "Description", *([selected.c_value_label or "Value"] if valued else []),
                   "Display Order", "Status", "Notes"]
        data = []
        for r in rows:
            value = common_value_text(r, selected) or ""
            if value and selected.c_value_type == "CURRENCY":
                value = format_money(Decimal(value))
            data.append([r.c_code, r.c_name, r.c_description or "", *([value] if valued else []),
                         str(r.i_display_order), status(r), r.c_notes or ""])
        return headers, data

    if entity in ("category1", "category2", "category3"):
        headers = ["Code", "Name"]
        if entity != "category1":
            headers += ["Category I Code", "Category I Name"]
        if entity == "category3":
            headers += ["Category II Code", "Category II Name"]
        if entity == "category3":
            headers += ["Inventory Type", "Keep", "Sales Floor Location", "Warehouse Location"]
        headers += ["Display Order", "Status", "Notes"]
        data = []
        for r in rows:
            line = [r.c_category_code, r.c_category_name]
            if entity != "category1":
                c1 = _scoped_parent(db, Category1, r.i_category1_id, r.i_merchant_id)
                line += [c1.c_category_code if c1 else "", c1.c_category_name if c1 else ""]
            if entity == "category3":
                c2 = _scoped_parent(db, Category2, r.i_category2_id, r.i_merchant_id)
                line += [c2.c_category_code if c2 else "", c2.c_category_name if c2 else ""]
                line += [inventory.inventory_behavior(r.i_inventory_type).name if r.i_inventory_type else "",
                         inventory.keep_name(r.c_keep), r.c_sales_location or "", r.c_warehouse_location or ""]
            line += [str(r.i_display_order), status(r), r.c_notes or ""]
            data.append(line)
        return headers, data

    headers = [
        "Item Code", "Item Name", "Description", "Item Kind",
        "Category I Code", "Category I Name", "Category II Code", "Category II Name",
        "Category III Code", "Category III Name",
        "Tax Code", "Tax Name", "Tax Rate (%)", "Billing Cycle", "Unit", "Retail Price",
    ]
    if can_cost:
        headers.append("Cost")
    for n in (1, 2, 3):
        headers += [f"P{n} Mode", f"P{n} Discount %", f"P{n} Price"]
    headers += ["Inventory Type", "Keep", "Sales Floor Location", "Warehouse Location", "Output Device",
                "License Required", "License Type", "Licenses per Unit", "Serial Number Required", "Status", "Notes"]

    kind_names = {k.c_item_kind_code: k.c_item_kind_name for k in db.query(ItemKind)}
    data = []
    for r in rows:
        c1 = db.get(Category1, r.i_category1_id)
        c2 = db.get(Category2, r.i_category2_id) if r.i_category2_id else None
        c3 = db.get(Category3, r.i_category3_id) if r.i_category3_id else None
        tax = db.get(Tax, r.i_default_tax_id) if r.i_default_tax_id else None
        line = [
            r.c_item_code, r.c_item_name, r.c_description or "", kind_names.get(r.c_item_kind, r.c_item_kind or ""),
            c1.c_category_code if c1 else "", c1.c_category_name if c1 else "",
            c2.c_category_code if c2 else "", c2.c_category_name if c2 else "",
            c3.c_category_code if c3 else "", c3.c_category_name if c3 else "",
            tax.c_tax_code if tax else "", tax.c_tax_name if tax else "", _pct(tax.n_tax_rate) if tax else "",
            _CYCLE_LABEL.get(r.c_billing_cycle, r.c_billing_cycle or ""), r.c_unit or "", _money(r.n_default_unit_price),
        ]
        if can_cost:
            line.append(_money(r.n_cost))
        for n in (1, 2, 3):
            mode = getattr(r, f"c_p{n}_mode")
            line += [
                _MODE_LABEL.get(mode, "") if mode else NOT_SET,
                _pct(getattr(r, f"n_p{n}_discount_pct")) if mode == "DISCOUNT" else "",
                _money(getattr(r, f"n_p{n}_price")),
            ]
        resolved = _resolve_md_inventory(r, c3)  # effective values; inherited ones are marked
        inherited = lambda source: " (Category III)" if source == inventory.SOURCE_CATEGORY else ""
        line += [
            resolved.behavior.name + inherited(resolved.type_source),
            inventory.keep_name(resolved.keep) + inherited(resolved.keep_source),
            *[(lambda v, src: (v or "") + (" (Category III)" if src == "category" else ""))(
                *resolve_md_location(getattr(r, column), getattr(c3, column) if c3 is not None else None))
              for _key, column in LOCATION_FIELDS],
            ", ".join(f"#{n}" for n in output_devices_list(r.i_output_devices)),
            _yn(r.b_license_required), (r.c_license_type or "") if r.b_license_required else "",
            str(r.i_licenses_per_unit) if r.b_license_required and r.i_licenses_per_unit else "",
            _yn(r.b_serial_required), status(r), r.c_notes or "",
        ]
        data.append(line)
    if merchant_id is not None:  # Merchant MD: no Platform-only columns (not used) and no Recommended Location yet
        keep = [i for i, name in enumerate(headers) if name not in MERCHANT_MD_HIDDEN_EXPORT_COLUMNS]
        headers = [headers[i] for i in keep]
        data = [[line[i] for i in keep] for line in data]
    return headers, data


MERCHANT_MD_HIDDEN_EXPORT_COLUMNS = frozenset({
    "Billing Cycle", "License Required", "License Type", "Licenses per Unit", "Sales Floor Location",
    "Warehouse Location", *(f"P{n} {part}" for n in (1, 2, 3) for part in ("Mode", "Discount %", "Price")),
})

def _get(db: Session, entity: str, item_id: int, merchant_id: int | None = None, store_id: int | None = None,
         group: str | None = None):
    cfg = entity_config(entity)
    if entity == "md":
        row = (db.query(MdMaster).filter(MdMaster.i_md_id == item_id, MdMaster.i_owner_key == md_owner_key(merchant_id))
               .populate_existing().with_for_update().first())
    else:
        row = _entity_query(db, entity, merchant_id, store_id, group).filter(
            getattr(cfg["model"], cfg["pk"]) == item_id,
        ).first()
    if not row:
        raise NotFound("Record not found.")
    return row


def category3_inventory_impact(db: Session, category3_id: int, merchant_id: int | None = None) -> dict:
    """Read-only counts for the Category III Inventory Type scope confirmation."""
    row = _get(db, "category3", category3_id, merchant_id)
    items = db.query(MdMaster.i_md_id).filter(MdMaster.i_owner_key == md_owner_key(merchant_id),
                                              MdMaster.i_category3_id == row.i_category3_id)
    return {
        "id": row.i_category3_id,
        "name": row.c_category_name,
        "inventory_type": row.i_inventory_type,
        "md_total": items.count(),
        "override_total": items.filter(MdMaster.i_inventory_type.isnot(None)).count(),
        # "Apply to all MD Master" stays Platform-only in STEP 2 (a Merchant version comes later).
        "can_apply_to_md": merchant_id is None,
    }


def _apply_category3_inventory_to_md(db: Session, row: Category3, user_id) -> int:
    """Clear the Inventory Type override of this Category III's MD items so they inherit it (NULL = inherit).

    Only i_inventory_type of MDs in this Category III changes; Keep, Tax, other columns and other
    Category III items are untouched. Runs in the caller's transaction (no commit here).
    """
    _lock_md_images(db)  # same lock as MD saves, so a concurrent MD save cannot interleave
    return db.query(MdMaster).filter(
        MdMaster.i_category3_id == row.i_category3_id, MdMaster.i_inventory_type.isnot(None),
    ).update({MdMaster.i_inventory_type: None, MdMaster.dt_updated: datetime.now(), MdMaster.i_updated_by: user_id},
             synchronize_session=False)


@_serialize_md_writes
def update_item(db: Session, entity: str, item_id: int, payload: dict, user_id, can_cost: bool = False,
                *, image_data: bytes | None = None, remove_image: bool = False,
                merchant_id: int | None = None, store_id: int | None = None, group: str | None = None) -> dict:
    if entity == "md":
        _lock_md_images(db)
    if entity_config(entity).get("group_scoped"):
        common_read_only(db, group)
    # Category III "Apply to all MD Master": opt-in flag, Platform scope only.
    apply_to_md = entity == "category3" and _bool(payload, "apply_inventory_to_md", False)
    if apply_to_md and merchant_id is not None:
        raise Conflict("Applying to MD Master is available only in Platform Admin.")
    row = _get(db, entity, item_id, merchant_id, store_id, group)
    values = _validate_payload(db, entity, payload, row, can_cost, merchant_id, store_id, group)
    for key, value in values.items():
        setattr(row, key, value)  # the auto code is never part of values (a manual code is)
    md_reset = _apply_category3_inventory_to_md(db, row, user_id) if apply_to_md else None
    if entity == "md":
        default_detail = db.query(MdDetail).filter(
            MdDetail.i_md_id == row.i_md_id, MdDetail.b_is_default.is_(True),
        ).with_for_update().first()
        if default_detail is not None:
            default_detail.c_unit = row.c_unit
            default_detail.n_price = row.n_default_unit_price
            if "n_cost" in values:
                default_detail.n_cost = row.n_cost
            _stamp(default_detail, user_id, created=False)
    _stamp(row, user_id, created=False)
    warning = _commit_md(db, row, image_data=image_data, remove_image=remove_image) if entity == "md" else None
    if entity != "md":
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if entity_config(entity).get("manual_code"):
                raise FieldErrors({"code": "This code already exists."}) from None
            raise
    db.refresh(row)
    item = serialize(db, entity, row, can_cost)
    if warning:
        item["image_warning"] = warning
    if md_reset is not None:
        item["md_inventory_reset"] = md_reset
    return item


@_serialize_md_writes
def delete_item(db: Session, entity: str, item_id: int, *, merchant_id: int | None = None,
                store_id: int | None = None, group: str | None = None) -> str | None:
    if entity == "md":
        _lock_md_images(db)
    if entity_config(entity).get("group_scoped"):
        common_read_only(db, group)
    row = _get(db, entity, item_id, merchant_id, store_id, group)
    if entity_config(entity).get("system") or is_system_row(entity, row):
        raise Conflict("System codes cannot be deleted. Deactivate it instead.")
    if entity == "common_code":
        protected = PROTECTED_COMMON_CODES.get((common_group(db, group).c_group_code, row.c_code))
        if protected:
            raise Conflict(protected)
    if is_in_use(db, entity, row):
        raise Conflict("This record is in use and cannot be deleted. Deactivate it instead.")
    if entity == "md":
        detail_ids = db.query(MdDetail.i_md_detail_id).filter(MdDetail.i_md_id == row.i_md_id)
        db.query(StoreMdLocation).filter(StoreMdLocation.i_md_detail_id.in_(detail_ids)).delete(synchronize_session=False)
        db.query(MdDetail).filter(MdDetail.i_md_id == row.i_md_id).delete(synchronize_session=False)
        return _commit_md(db, row, delete=True)
    db.delete(row)
    db.commit()


def lookup_options(db: Session, can_cost: bool = False, *, merchant_id: int | None = None) -> dict:
    def cats1():
        return [
            {"id": r.i_category1_id, "code": r.c_category_code, "name": r.c_category_name, "active": bool(r.b_active)}
            for r in _scoped_query(db, Category1, merchant_id).order_by(Category1.i_display_order, Category1.i_category1_id)
        ]

    return {
        "category1": cats1(),
        "category2": [
            {"id": r.i_category2_id, "category1_id": r.i_category1_id, "code": r.c_category_code,
             "name": r.c_category_name, "active": bool(r.b_active)}
            for r in _scoped_query(db, Category2, merchant_id).order_by(Category2.i_display_order, Category2.i_category2_id)
        ],
        "category3": [
            {"id": r.i_category3_id, "category1_id": r.i_category1_id, "category2_id": r.i_category2_id,
             "code": r.c_category_code, "name": r.c_category_name, "active": bool(r.b_active),
             **{f"tax{n}_id": getattr(r, f"i_tax{n}_id") for n in (1, 2, 3)},
             "inventory_type": r.i_inventory_type, "keep": r.c_keep,
             "sales_location": r.c_sales_location, "warehouse_location": r.c_warehouse_location}
            for r in _scoped_query(db, Category3, merchant_id).order_by(Category3.i_display_order, Category3.i_category3_id)
        ],
        "taxes": [
            {"id": r.i_tax_id, "code": r.c_tax_code, "name": r.c_tax_name, "rate": _dec(r.n_tax_rate), "active": bool(r.b_active)}
            for r in _scoped_query(db, Tax, merchant_id).order_by(Tax.c_tax_name)
        ],
        "can_view_cost": can_cost,
        "price_levels": list(PRICE_LEVELS),
        # code = System Code (the value MD stores); kind_no = the Item Kind No shown on the System Master screen.
        "item_kinds": [
            {"code": r.c_item_kind_code, "name": r.c_item_kind_name, "kind_no": r.c_item_kind_no}
            for r in db.query(ItemKind).filter(ItemKind.b_active.is_(True),
                                               ItemKind.c_item_kind_code.in_(SYSTEM_ITEM_KIND_CODES))
            .order_by(ItemKind.i_display_order, ItemKind.i_item_kind_id)
        ],
        "default_item_kind": DEFAULT_ITEM_KIND,
        # MD Color selector: COLOR codes from Common Maintenance (MD Detail color_code_id).
        "color_codes": color_code_options(db, merchant_id),
        # Employee Master Job Grade / Job Title selectors (Common Maintenance JOB_GRADE / JOB_TITLE).
        "job_grade_codes": common_code_options(db, "JOB_GRADE", merchant_id),
        "job_title_codes": common_code_options(db, "JOB_TITLE", merchant_id),
        "billing_cycles": list(BILLING_CYCLES),
        # Behavior flags come from the Common definition so the UI never re-derives them from numbers.
        "inventory_types": [b.as_dict() for b in inventory.inventory_types()],
        "default_inventory_type": inventory.DEFAULT_INVENTORY_TYPE,
        "default_keep": inventory.DEFAULT_KEEP,
        "keep_types": [{"code": code, "name": name} for code, name in inventory.KEEP_TYPES.items()],
        "license_types": list(LICENSE_TYPES),
        "location_areas": [{"code": code, "name": name} for code, name in LOCATION_AREAS.items()],
        # Common Maintenance groups (COLOR / UNIT) with their code / Value rules; additive key (MD unaffected).
        "common_groups": common_groups(db),  # group definitions are shared; their codes are per scope
        # MD Unit selector: UNIT codes from Common Maintenance (empty = not migrated: Unit stays free text).
        "units": unit_options(db, merchant_id),
    }
