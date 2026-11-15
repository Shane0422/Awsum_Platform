"""Store Override of the Merchant-common MD (SO-1b): per-Store Detail price and MD-level POS visibility.

One Merchant MD Master / MD Detail is shared by all its Stores; a Store only adds override rows
(models_admin.store_md). This module is the one place that reads, saves and resolves them; the API / screen (SO-1c /
SO-1d) and later Master Sync use it.

Rules
- Detail price: a row = the Store price (a value equal to the Merchant price is still an override; $0.00 is valid).
  No row = the Merchant Detail price (inherit). Only "revert" (price null) removes the row.
- POS visibility (MD level): no row = visible. Hidden creates a tb_store_md row; Visible again removes it.
  POS visible = the common MD is Active AND the Store has not hidden it. Hidden is not a sales block (barcode sale
  stays allowed); selling stop and a Store Tax override are out of scope.
- Only the changed cells are sent. Each carries the version token it was read with: "0" = no row, else
  "<row id>.<i_row_version>". The row id is part of the token, so a row that was reverted (deleted) and created again
  in between never matches an old token (no lost update after delete + re-create).
- One request = one transaction. Any token mismatch, a concurrent INSERT of the same cell (unique key) or a row
  removed by a concurrent MD / Detail / Store change refuses the whole request (VersionConflict / Conflict -> 409);
  nothing is saved.
- Scope is decided by the caller from the login (never from the payload): merchant_id (the MD owner) and the Store
  ids it may read / write. Every Store, MD and Detail is checked here too, and the database enforces the same
  through composite foreign keys (models_admin.store_md).
- Audit: the common AuditMixin columns, written by master_group._stamp with the server login user. The MD Master /
  MD Detail rows are never touched (their Updated By / At stay).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models_admin.master_group import MdDetail, MdMaster
from backend.models_admin.store import Store
from backend.models_admin.store_md import StoreMd, StoreMdPrice
from backend.services.master_group import MAX_PRICE, Conflict, FieldErrors, NotFound, _decimal, _stamp

NO_ROW = "0"
CONFLICT_MESSAGE = "Another user changed these Store settings. Reload to see the latest values, then save again."
CHANGED_MESSAGE = "This MD, a Detail or a Store changed while saving. Reload and try again."
STORE_MERCHANT_LOCKED = ("This Store has Store prices or POS settings for its Merchant's MD items. "
                         "Remove them before changing the Store's Merchant.")


class VersionConflict(Conflict):
    """409: another user changed one of the sent cells. conflicts = the current value / token of each such cell."""

    def __init__(self, conflicts: list[dict]):
        super().__init__(CONFLICT_MESSAGE)
        self.conflicts = conflicts


class StoreReadOnly(Exception):
    """403: the Store may be viewed but not changed (e.g. an Inactive Store of a Merchant login)."""


def token(row) -> str:
    return NO_ROW if row is None else f"{row.i_store_md_price_id if isinstance(row, StoreMdPrice) else row.i_store_md_id}.{row.i_row_version}"


# ---------- common resolvers (also for Master Sync later) ----------

def resolve_store_prices(db: Session, store_id: int, detail_ids) -> dict[int, dict]:
    """Effective price of each Detail for one Store: {detail_id: {"price", "source": "store" | "merchant"}}."""
    detail_ids = list(detail_ids)
    if not detail_ids:
        return {}
    overrides = {row.i_md_detail_id: row.n_price for row in db.query(StoreMdPrice).filter(
        StoreMdPrice.i_store_id == store_id, StoreMdPrice.i_md_detail_id.in_(detail_ids))}
    result = {}
    for detail_id, merchant_price in db.query(MdDetail.i_md_detail_id, MdDetail.n_price).filter(
            MdDetail.i_md_detail_id.in_(detail_ids)):
        if detail_id in overrides:
            result[detail_id] = {"price": overrides[detail_id], "source": "store"}
        else:
            result[detail_id] = {"price": merchant_price, "source": "merchant"}
    return result


def resolve_pos_visible(db: Session, store_id: int, md_ids) -> dict[int, bool]:
    """POS visibility of each MD for one Store: the common MD is Active and the Store has not hidden it."""
    md_ids = list(md_ids)
    if not md_ids:
        return {}
    hidden = {md_id for (md_id,) in db.query(StoreMd.i_md_id).filter(
        StoreMd.i_store_id == store_id, StoreMd.i_md_id.in_(md_ids), StoreMd.b_pos_visible.is_(False))}
    return {md_id: bool(active) and md_id not in hidden
            for md_id, active in db.query(MdMaster.i_md_id, MdMaster.b_active).filter(MdMaster.i_md_id.in_(md_ids))}


def store_override_count(db: Session, store_id: int) -> int:
    """Override rows of a Store (any MD). A Store with rows cannot change its Merchant (they belong to its MD)."""
    return (db.query(StoreMdPrice).filter(StoreMdPrice.i_store_id == store_id).count()
            + db.query(StoreMd).filter(StoreMd.i_store_id == store_id).count())


def ensure_store_merchant_change_allowed(db: Session, store_id: int) -> None:
    """Business check before a Store's Merchant changes (the database refuses it anyway through the composite FK)."""
    if store_override_count(db, store_id):
        raise Conflict(STORE_MERCHANT_LOCKED)


# ---------- read ----------

def _md(db: Session, md_id: int, merchant_id: int | None, *, lock: bool = False) -> MdMaster:
    if merchant_id is None:  # Platform MD (owner 0) has no Stores
        raise NotFound("Record not found.")
    query = db.query(MdMaster).filter(MdMaster.i_md_id == md_id, MdMaster.i_owner_key == merchant_id)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise NotFound("Record not found.")
    return row


def _stores(db: Session, merchant_id: int, store_ids) -> list[Store]:
    return (db.query(Store).filter(Store.i_merchant_id == merchant_id, Store.i_store_id.in_(list(store_ids)))
            .order_by(Store.c_store_name, Store.i_store_id).all())


def get_store_settings(db: Session, md_id: int, *, merchant_id: int | None, store_ids, read_only_store_ids=()) -> dict:
    """The Store Settings grid of one MD: its Details with the Merchant price and, per Store in scope, each Detail's
    effective price (with its override token) and the MD's POS visibility. Stores outside the Merchant are dropped."""
    md = _md(db, md_id, merchant_id)
    details = (db.query(MdDetail).filter(MdDetail.i_md_id == md.i_md_id)
               .order_by(MdDetail.b_is_default.desc(), MdDetail.i_detail_no).all())
    stores = _stores(db, merchant_id, store_ids)
    ids = [store.i_store_id for store in stores]
    prices = {(row.i_store_id, row.i_md_detail_id): row for row in db.query(StoreMdPrice).filter(
        StoreMdPrice.i_md_id == md.i_md_id, StoreMdPrice.i_store_id.in_(ids))} if ids else {}
    hidden = {row.i_store_id: row for row in db.query(StoreMd).filter(
        StoreMd.i_md_id == md.i_md_id, StoreMd.i_store_id.in_(ids))} if ids else {}
    read_only = set(read_only_store_ids)
    return {
        "md_id": md.i_md_id,
        "md_active": bool(md.b_active),
        "details": [{"id": d.i_md_detail_id, "detail_no": d.i_detail_no, "is_default": bool(d.b_is_default),
                     "barcode": d.c_barcode, "unit": d.c_unit, "pk_size": d.c_pk_size, "size": d.c_size,
                     "merchant_price": _money(d.n_price)} for d in details],
        "stores": [{
            "id": store.i_store_id, "code": store.c_store_code, "name": store.c_store_name,
            "active": (store.c_status or "").lower() == "active", "read_only": store.i_store_id in read_only,
            "pos_visible": hidden.get(store.i_store_id) is None or bool(hidden[store.i_store_id].b_pos_visible),
            "pos_version": token(hidden.get(store.i_store_id)),
            "prices": {str(d.i_md_detail_id): _cell(prices.get((store.i_store_id, d.i_md_detail_id)), d)
                       for d in details},
        } for store in stores],
    }


def _money(value) -> str:
    return f"{Decimal(value):.2f}"


def _cell(row, detail) -> dict:
    if row is None:
        return {"price": _money(detail.n_price), "source": "merchant", "override": None, "version": NO_ROW}
    return {"price": _money(row.n_price), "source": "store", "override": _money(row.n_price), "version": token(row)}


# ---------- save ----------

def _changes(payload: dict) -> tuple[list[dict], list[dict]]:
    prices, visibility = payload.get("prices") or [], payload.get("visibility") or []
    if not isinstance(prices, list) or not isinstance(visibility, list):
        raise FieldErrors({"prices": "Invalid Store settings."})
    errors: dict[str, str] = {}
    price_changes, seen = [], set()
    for index, item in enumerate(prices):
        key = f"prices.{index}"
        if not isinstance(item, dict):
            errors[key] = "Invalid price change."
            continue
        store_id, detail_id = _int(item.get("store_id")), _int(item.get("detail_id"))
        if store_id is None or detail_id is None:
            errors[key] = "Store and Detail are required."
            continue
        if (store_id, detail_id) in seen:
            errors[key] = "The same Store price is sent twice."
            continue
        seen.add((store_id, detail_id))
        cell_errors: dict[str, str] = {}
        price = None if item.get("price") is None else _decimal(
            item, "price", cell_errors, places=2, maximum=MAX_PRICE, label="Price")
        if cell_errors:
            errors[key] = cell_errors["price"]
            continue
        price_changes.append({"store_id": store_id, "detail_id": detail_id, "price": price,
                              "base": str(item.get("base_version", NO_ROW))})
    visibility_changes, seen = [], set()
    for index, item in enumerate(visibility):
        key = f"visibility.{index}"
        store_id = _int(item.get("store_id")) if isinstance(item, dict) else None
        if store_id is None or not isinstance(item.get("pos_visible"), bool):
            errors[key] = "Store and POS Visible (true / false) are required."
            continue
        if store_id in seen:
            errors[key] = "The same Store is sent twice."
            continue
        seen.add(store_id)
        visibility_changes.append({"store_id": store_id, "pos_visible": item["pos_visible"],
                                   "base": str(item.get("base_version", NO_ROW))})
    if errors:
        raise FieldErrors(errors)
    return price_changes, visibility_changes


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def save_store_settings(db: Session, md_id: int, payload: dict, user_id: int, *, merchant_id: int | None,
                        store_ids, read_only_store_ids=()) -> dict:
    """Save the changed cells of one MD for Stores in scope, all or nothing; returns the refreshed grid.

    payload: {"prices": [{store_id, detail_id, price (null = revert), base_version}],
              "visibility": [{store_id, pos_visible, base_version}]}
    """
    price_changes, visibility_changes = _changes(payload)
    md = _md(db, md_id, merchant_id, lock=True)  # serializes saves of one MD's settings with MD deletes
    allowed = {store.i_store_id for store in _stores(db, merchant_id, store_ids)}
    read_only = set(read_only_store_ids)
    for change in price_changes + visibility_changes:
        if change["store_id"] not in allowed:
            raise NotFound("Store not found.")
        if change["store_id"] in read_only:
            raise StoreReadOnly("This Store is view only.")
    detail_ids = {d for (d,) in db.query(MdDetail.i_md_detail_id).filter(MdDetail.i_md_id == md.i_md_id)}
    if any(change["detail_id"] not in detail_ids for change in price_changes):
        raise NotFound("MD Detail not found.")

    pairs = [(c["store_id"], c["detail_id"]) for c in price_changes]
    price_rows = {(r.i_store_id, r.i_md_detail_id): r for r in db.query(StoreMdPrice).filter(
        tuple_(StoreMdPrice.i_store_id, StoreMdPrice.i_md_detail_id).in_(pairs)).with_for_update()} if pairs else {}
    visibility_stores = [c["store_id"] for c in visibility_changes]
    visibility_rows = {r.i_store_id: r for r in db.query(StoreMd).filter(
        StoreMd.i_md_id == md.i_md_id, StoreMd.i_store_id.in_(visibility_stores)).with_for_update()} if visibility_stores else {}

    conflicts = [_price_conflict(c, price_rows.get((c["store_id"], c["detail_id"])), db) for c in price_changes
                 if token(price_rows.get((c["store_id"], c["detail_id"]))) != c["base"]]
    conflicts += [_visibility_conflict(c, visibility_rows.get(c["store_id"])) for c in visibility_changes
                  if token(visibility_rows.get(c["store_id"])) != c["base"]]
    if conflicts:
        db.rollback()
        raise VersionConflict(conflicts)

    for change in price_changes:
        row = price_rows.get((change["store_id"], change["detail_id"]))
        if change["price"] is None:  # revert to the Merchant price
            if row is not None:
                db.delete(row)
        elif row is None:
            row = StoreMdPrice(i_store_id=change["store_id"], i_md_id=md.i_md_id, i_md_detail_id=change["detail_id"],
                               i_owner_key=md.i_owner_key, n_price=change["price"], i_row_version=1)
            _stamp(row, user_id, created=True)
            db.add(row)
        elif row.n_price != change["price"]:
            row.n_price = change["price"]
            row.i_row_version += 1
            _stamp(row, user_id, created=False)
    for change in visibility_changes:
        row = visibility_rows.get(change["store_id"])
        if change["pos_visible"]:  # visible = the default: no row
            if row is not None:
                db.delete(row)
        elif row is None:
            row = StoreMd(i_store_id=change["store_id"], i_md_id=md.i_md_id, i_owner_key=md.i_owner_key,
                          b_pos_visible=False, i_row_version=1)
            _stamp(row, user_id, created=True)
            db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # A concurrent save created one of these cells first (unique key: it now has another token), or the MD /
        # Detail / Store changed under the composite FKs. Nothing of this request was saved.
        conflicts = _current_conflicts(db, md_id, price_changes, visibility_changes)
        if conflicts:
            raise VersionConflict(conflicts) from None
        raise Conflict(CHANGED_MESSAGE) from None
    return get_store_settings(db, md_id, merchant_id=merchant_id, store_ids=store_ids,
                              read_only_store_ids=read_only_store_ids)


def _price_conflict(change: dict, row, db: Session) -> dict:
    current = row.n_price if row is not None else db.get(MdDetail, change["detail_id"]).n_price
    return {"kind": "price", "store_id": change["store_id"], "detail_id": change["detail_id"],
            "price": _money(current), "source": "store" if row is not None else "merchant", "version": token(row)}


def _visibility_conflict(change: dict, row) -> dict:
    return {"kind": "visibility", "store_id": change["store_id"],
            "pos_visible": row is None or bool(row.b_pos_visible), "version": token(row)}


def _current_conflicts(db: Session, md_id: int, price_changes: list[dict], visibility_changes: list[dict]) -> list[dict]:
    conflicts = []
    for change in price_changes:
        row = db.query(StoreMdPrice).filter(StoreMdPrice.i_store_id == change["store_id"],
                                            StoreMdPrice.i_md_detail_id == change["detail_id"]).first()
        if token(row) != change["base"] and db.get(MdDetail, change["detail_id"]) is not None:
            conflicts.append(_price_conflict(change, row, db))
    for change in visibility_changes:
        row = db.query(StoreMd).filter(StoreMd.i_store_id == change["store_id"], StoreMd.i_md_id == md_id).first()
        if token(row) != change["base"]:
            conflicts.append(_visibility_conflict(change, row))
    return conflicts
