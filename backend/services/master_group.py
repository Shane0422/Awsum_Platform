"""Master Group (Tax / Category I-III / MD Master) business rules.

All validation lives here so the API enforces it regardless of what the UI sends.
Money and rates use Decimal only.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import wraps

from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models_admin.invoice import InvoiceLine
from backend.models_admin.master_group import Category1, Category2, Category3, ItemKind, MdDetail, MdMaster, Tax
from backend.services import images
from backend.services.paths import AwsumPathResolver
from backend.services.money import format_money
from backend.services.image_service import commit_entity_image, master_image_lock

# tb_license.c_license_type values (see platform_store_license TYPE_*).
LICENSE_TYPES = ("POS", "KIOSK", "CUSTOMER_ORDERING")
DEFAULT_ITEM_KIND = "ITEM"  # tb_item_kind System Code
BILLING_CYCLES = ("ONE_TIME", "MONTHLY", "ANNUAL")

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
    # System Master: rows are seeded; codes are logic keys and cannot be added, changed or deleted.
    "item_kind": dict(model=ItemKind, pk="i_item_kind_id", code="c_item_kind_code", name="c_item_kind_name",
                      prefix=None, system=True),
}
PLATFORM_ONLY_MODELS = (MdMaster, ItemKind)
CODE_DIGITS = 5


def entity_config(entity: str) -> dict:
    cfg = ENTITIES.get(entity)
    if not cfg:
        raise NotFound("Unknown master type.")
    return cfg


def _scoped_query(db: Session, model, merchant_id: int | None):
    if model in PLATFORM_ONLY_MODELS:
        if merchant_id is not None:
            raise NotFound("This master is not available in this scope.")
        return db.query(model)
    return db.query(model).filter(model.i_merchant_id == merchant_id)


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
                      merchant_id: int | None = None) -> dict:
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
        values.update(_validate_common(payload, errors, ordered=True))

    else:  # md
        _validate_md(db, payload, current, values, errors, can_cost)

    if errors:
        raise FieldErrors(errors)
    return values


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


def _validate_md(db: Session, payload: dict, current, values: dict, errors: dict, can_cost: bool = False) -> None:
    values["c_item_name"] = _text(payload, "name", 200, errors, required=True, label="Item Name")
    values["c_second_name"] = _text(payload, "second_name", 200, errors, label="Second Name")
    values["c_description"] = _text(payload, "description", 2000, errors)

    kind = str(payload.get("item_kind") or DEFAULT_ITEM_KIND).strip().upper()
    kind_row = db.query(ItemKind).filter(ItemKind.c_item_kind_code == kind).first()
    keeps_current = current is not None and current.c_item_kind == kind
    if kind_row is None or not (kind_row.b_active or keeps_current):
        errors["item_kind"] = "Select an active Item Kind."
    values["c_item_kind"] = kind

    cycle = str(payload.get("billing_cycle") or "").strip().upper()
    if cycle not in BILLING_CYCLES:
        errors["billing_cycle"] = "Select One-time, Monthly or Annual."
    values["c_billing_cycle"] = cycle

    values["c_unit"] = _text(payload, "unit", 30, errors, required=True, label="Unit")
    values["n_default_unit_price"] = _decimal(
        payload, "retail_price", errors, places=2, maximum=MAX_PRICE, label="Retail Price"
    )
    # Always re-derived, so a Retail change refreshes DISCOUNT levels while FIXED levels keep their amount.
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
        p1 = _scoped_parent(db, Category1, c1, None)
        p2 = _scoped_parent(db, Category2, c2, None)
        p3 = _scoped_parent(db, Category3, c3, None)
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
        tax = _scoped_parent(db, Tax, tax_id, None)
        if not tax:
            errors["default_tax_id"] = "Tax does not exist."
        elif not tax.b_active and (current is None or current.i_default_tax_id != tax_id):
            errors["default_tax_id"] = "Tax is inactive."

    license_required = _bool(payload, "license_required", False)
    values["b_license_required"] = license_required
    values["b_serial_required"] = _bool(payload, "serial_required", False)
    if license_required:
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
    }


def _md_parent_for_detail(db: Session, md_id: int, *, lock: bool = False) -> MdMaster:
    query = db.query(MdMaster).filter(MdMaster.i_md_id == md_id)
    if lock:
        query = query.with_for_update()
    row = query.first()
    if row is None:
        raise NotFound("MD Master record not found.")
    return row


def list_md_details(db: Session, md_id: int) -> list[dict]:
    _md_parent_for_detail(db, md_id)
    rows = db.query(MdDetail).filter(MdDetail.i_md_id == md_id).order_by(MdDetail.i_detail_no).all()
    return [_serialize_md_detail(row) for row in rows]


def _validate_md_detail(payload: dict, current: MdDetail | None, errors: dict) -> dict:
    values = {}
    values["c_barcode"] = _text(payload, "barcode", 100, errors)
    values["c_unit"] = _text(payload, "unit", 30, errors, required=True, label="Unit")
    values["c_pk_size"] = _text(payload, "pk_size", 100, errors)
    values["c_size"] = _text(payload, "size", 100, errors)
    values["n_cost"] = _decimal(
        payload, "cost", errors, places=2, maximum=MAX_PRICE, label="Cost", required=False,
    )
    values["n_price"] = _decimal(
        payload, "price", errors, places=2, maximum=MAX_PRICE, label="Price",
    )
    values["c_modifier"] = _text(payload, "modifier", 2000, errors)
    if "is_default" in payload:
        values["b_is_default"] = _bool(payload, "is_default", False)
    elif current is not None:
        values["b_is_default"] = current.b_is_default
    else:
        values["b_is_default"] = False
    if errors:
        raise FieldErrors(errors)
    return values


def create_md_detail(db: Session, md_id: int, payload: dict, user_id: int) -> dict:
    parent = _md_parent_for_detail(db, md_id, lock=True)
    current_default = db.query(MdDetail).filter(
        MdDetail.i_md_id == md_id, MdDetail.b_is_default.is_(True),
    ).first()
    values = _validate_md_detail(payload, None, {})
    if current_default is None:
        values["b_is_default"] = True
    next_no = (db.query(func.max(MdDetail.i_detail_no))
               .filter(MdDetail.i_md_id == md_id).scalar() or 0) + 1
    if values["b_is_default"] and current_default is not None:
        current_default.b_is_default = False
        _stamp(current_default, user_id, created=False)
        db.flush()
    row = MdDetail(i_md_id=md_id, i_detail_no=next_no, **values)
    _stamp(row, user_id, created=True)
    db.add(row)
    if row.b_is_default:
        parent.c_unit = row.c_unit
        parent.n_default_unit_price = row.n_price
        parent.n_cost = row.n_cost
        _stamp(parent, user_id, created=False)
    db.commit()
    db.refresh(row)
    return _serialize_md_detail(row)


def update_md_detail(db: Session, md_id: int, detail_id: int, payload: dict, user_id: int) -> dict:
    parent = _md_parent_for_detail(db, md_id, lock=True)
    row = db.query(MdDetail).filter(
        MdDetail.i_md_detail_id == detail_id, MdDetail.i_md_id == md_id,
    ).with_for_update().first()
    if row is None:
        raise NotFound("MD Detail not found.")
    values = _validate_md_detail(payload, row, {})
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
    db.commit()
    db.refresh(row)
    return _serialize_md_detail(row)


def delete_md_detail(db: Session, md_id: int, detail_id: int) -> None:
    _md_parent_for_detail(db, md_id, lock=True)
    row = db.query(MdDetail).filter(
        MdDetail.i_md_detail_id == detail_id, MdDetail.i_md_id == md_id,
    ).with_for_update().first()
    if row is None:
        raise NotFound("MD Detail not found.")
    if row.b_is_default:
        raise Conflict("The Default Detail cannot be deleted.")
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
    if entity == "item_kind":
        return False
    return bool(db.query(InvoiceLine.i_invoice_line_id).filter(InvoiceLine.i_md_id == row.i_md_id).first())


# ---------- serialization ----------

def _dec(value) -> str | None:
    return None if value is None else format(value, "f")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


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
        base.update(display_order=row.i_display_order, system=True)
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
    "item_kind": {"code": "c_item_kind_code", "name": "c_item_kind_name", "display_order": "i_display_order", "active": "b_active"},
}
DEFAULT_SORT = {"tax": "code", "category1": "display_order", "category2": "display_order", "category3": "display_order", "md": "code",
                "item_kind": "display_order"}


def _filtered_query(db: Session, entity: str, q, active, sort, direction, merchant_id: int | None = None):
    """Search / status filter / sort shared by the list screen and Export."""
    cfg = entity_config(entity)
    model = cfg["model"]
    query = _scoped_query(db, model, merchant_id)

    term = (q or "").strip()
    if term:
        like = f"%{term.replace('%', '').replace('_', ' ')}%"
        code_col, name_col = getattr(model, cfg["code"]), getattr(model, cfg["name"])
        query = query.filter(or_(code_col.ilike(like), name_col.ilike(like)))
    if active in ("active", "inactive"):
        query = query.filter(model.b_active.is_(active == "active"))

    cols = SORT_COLUMNS[entity]
    key = sort if sort in cols else DEFAULT_SORT[entity]
    column = getattr(model, cols[key])
    ordered = column.desc() if direction == "desc" else column.asc()
    query = query.order_by(ordered, getattr(model, cfg["pk"]).asc())
    return query, key


def list_items(db: Session, entity: str, *, q="", active="", sort="", direction="asc", page=1, size=20,
               can_cost=False, merchant_id: int | None = None) -> dict:
    query, key = _filtered_query(db, entity, q, active, sort, direction, merchant_id)

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
    return commit_entity_image(db, row, AwsumStorageService(db).resolve_owner("PLATFORM"), row.c_item_code,
                               category="master", image_data=image_data, remove_image=remove_image, delete=delete)


@_serialize_md_writes
def create_item(db: Session, entity: str, payload: dict, user_id, can_cost: bool = False,
                *, image_data: bytes | None = None, merchant_id: int | None = None) -> dict:
    _scoped_query(db, entity_config(entity)["model"], merchant_id)
    if entity_config(entity).get("system"):
        raise Conflict("System codes are predefined and cannot be added.")
    if entity == "md":
        _lock_md_images(db)
    cfg = entity_config(entity)
    values = _validate_payload(db, entity, payload, None, can_cost, merchant_id)
    if entity != "md":
        values["i_merchant_id"] = merchant_id
    for _ in range(5):
        if entity == "md":
            _lock_md_images(db)
        row = cfg["model"](**values)
        setattr(row, cfg["code"], _next_code(db, cfg))
        _stamp(row, user_id, created=True)
        db.add(row)
        try:
            if entity == "md":
                db.flush()
                detail = MdDetail(
                    i_md_id=row.i_md_id,
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
            continue
        warning = _commit_md(db, row, image_data=image_data) if entity == "md" else None
        db.refresh(row)
        item = serialize(db, entity, row, can_cost)
        if warning:
            item["image_warning"] = warning
        return item
    raise Conflict("Could not allocate a unique code. Please try again.")



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
                 merchant_id: int | None = None) -> tuple[list[str], list[list[str]]]:
    """Every row matching the current search/filter/sort (not just one page), as display strings.

    Cost is only included when can_cost is true; otherwise the column does not exist.
    """
    query, _key = _filtered_query(db, entity, q, active, sort, direction, merchant_id)
    rows = query.all()
    status = lambda r: "Active" if r.b_active else "Inactive"

    if entity == "tax":
        headers = ["Tax Code", "Tax Name", "Tax Rate (%)", "Status", "Notes"]
        data = [[r.c_tax_code, r.c_tax_name, _pct(r.n_tax_rate), status(r), r.c_notes or ""] for r in rows]
        return headers, data

    if entity == "item_kind":
        headers = ["System Code", "Display Name", "Display Order", "Status", "Notes"]
        data = [[r.c_item_kind_code, r.c_item_kind_name, str(r.i_display_order), status(r), r.c_notes or ""] for r in rows]
        return headers, data

    if entity in ("category1", "category2", "category3"):
        headers = ["Code", "Name"]
        if entity != "category1":
            headers += ["Category I Code", "Category I Name"]
        if entity == "category3":
            headers += ["Category II Code", "Category II Name"]
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
    headers += ["License Required", "License Type", "Licenses per Unit", "Serial Number Required", "Status", "Notes"]

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
        line += [
            _yn(r.b_license_required), (r.c_license_type or "") if r.b_license_required else "",
            str(r.i_licenses_per_unit) if r.b_license_required and r.i_licenses_per_unit else "",
            _yn(r.b_serial_required), status(r), r.c_notes or "",
        ]
        data.append(line)
    return headers, data

def _get(db: Session, entity: str, item_id: int, merchant_id: int | None = None):
    cfg = entity_config(entity)
    if entity == "md":
        row = db.query(MdMaster).filter(MdMaster.i_md_id == item_id).populate_existing().with_for_update().first()
    else:
        row = _scoped_query(db, cfg["model"], merchant_id).filter(
            getattr(cfg["model"], cfg["pk"]) == item_id,
        ).first()
    if entity == "md" and merchant_id is not None:
        raise NotFound("Record not found.")
    if not row:
        raise NotFound("Record not found.")
    return row


@_serialize_md_writes
def update_item(db: Session, entity: str, item_id: int, payload: dict, user_id, can_cost: bool = False,
                *, image_data: bytes | None = None, remove_image: bool = False,
                merchant_id: int | None = None) -> dict:
    if entity == "md":
        _lock_md_images(db)
    row = _get(db, entity, item_id, merchant_id)
    values = _validate_payload(db, entity, payload, row, can_cost, merchant_id)
    for key, value in values.items():
        setattr(row, key, value)  # the auto code is never part of values
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
        db.commit()
    db.refresh(row)
    item = serialize(db, entity, row, can_cost)
    if warning:
        item["image_warning"] = warning
    return item


@_serialize_md_writes
def delete_item(db: Session, entity: str, item_id: int, *, merchant_id: int | None = None) -> str | None:
    if entity == "md":
        _lock_md_images(db)
    row = _get(db, entity, item_id, merchant_id)
    if entity_config(entity).get("system"):
        raise Conflict("System codes cannot be deleted. Deactivate it instead.")
    if is_in_use(db, entity, row):
        raise Conflict("This record is in use and cannot be deleted. Deactivate it instead.")
    if entity == "md":
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
             **{f"tax{n}_id": getattr(r, f"i_tax{n}_id") for n in (1, 2, 3)}}
            for r in _scoped_query(db, Category3, merchant_id).order_by(Category3.i_display_order, Category3.i_category3_id)
        ],
        "taxes": [
            {"id": r.i_tax_id, "code": r.c_tax_code, "name": r.c_tax_name, "rate": _dec(r.n_tax_rate), "active": bool(r.b_active)}
            for r in _scoped_query(db, Tax, merchant_id).order_by(Tax.c_tax_name)
        ],
        "can_view_cost": can_cost,
        "price_levels": list(PRICE_LEVELS),
        "item_kinds": [
            {"code": r.c_item_kind_code, "name": r.c_item_kind_name}
            for r in db.query(ItemKind).filter(ItemKind.b_active.is_(True))
            .order_by(ItemKind.i_display_order, ItemKind.i_item_kind_id)
        ],
        "default_item_kind": DEFAULT_ITEM_KIND,
        "billing_cycles": list(BILLING_CYCLES),
        "license_types": list(LICENSE_TYPES),
    }
