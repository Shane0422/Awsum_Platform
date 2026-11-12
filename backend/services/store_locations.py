"""Location Management helpers for Store Locations (tb_store_location, the Store's own location system).

- location_management_enabled(): Business Options > Use Location Management (independent of Inventory).
- store_location_lookup(): read-only list of one Store's locations for selectors (Area, then Location Code).
- location_code_lookup(): read-only Location Codes of several Stores, each code once with its Store counts
  (Common Location Selector of Category III / MD Master).
- active_location_code_exists(): is a code an active location of an Area in some Store of a scope (save check).

Common recommended positions of products live on tb_category3 / tb_md_master (c_sales_location,
c_warehouse_location; services.master_group.resolve_md_location). The earlier MD Detail links
(tb_store_md_location) and Store Category III defaults (tb_store_category3_location) are deprecated:
no reads or writes here.
"""
from __future__ import annotations

import math

from sqlalchemy import case, distinct, func, or_
from sqlalchemy.orm import Session

from backend.models_admin.store import Store
from backend.models_admin.store_location import LOCATION_AREAS, StoreLocation
from backend.services.features import feature_enabled

LOOKUP_MAX_SIZE = 100
LOOKUP_STATUSES = ("active", "inactive", "")


def location_management_enabled(store, entitlements=None) -> bool:
    """Store Options > Business Options > Use Location Management (independent of Inventory Management).

    Stored in tb_store.b_opt_use_shelf_location (kept from the earlier Shelf Location option); NULL = OFF.
    Effective status from the Common Feature Registry (services.features); entitlements=None = LEGACY (all entitled).
    """
    return store is not None and feature_enabled("location_management", store, entitlements)


def store_location_lookup(db: Session, store_id: int) -> list[dict]:
    """Every location of one Store (active and inactive): Sales Floor first, then Warehouse, each by Location Code.

    Read-only lookup for selectors.
    """
    from backend.services.master_group import location_area_info
    rows = db.query(StoreLocation).filter(StoreLocation.i_store_id == store_id).all()
    items = [{"id": row.i_location_id, "code": row.c_location_code, "name": row.c_location_name,
              **location_area_info(row), "active": bool(row.b_active)} for row in rows]
    return sorted(items, key=lambda item: (item["location_area"] != "SALES_FLOOR", item["code"]))


def location_code_lookup(db: Session, store_ids: list[int] | None, *, location_area: str, q: str = "",
                         active: str = "active", page: int = 1, size: int = 20) -> dict:
    """Location Codes of one Area across the given Stores (None = every Store), each code once.

    stores = Stores where the code is registered; active_stores = Stores where it is active (a code is selectable
    when it is active in at least one of them). active: 'active' (default) | 'inactive' (active nowhere) | '' (all).
    Code / Name search, Location Code order, server paging. Raises ValueError for an unknown Area / status.
    """
    area = str(location_area or "").strip().upper()
    if area not in LOCATION_AREAS:
        raise ValueError("location_area")
    status = str(active or "").strip().lower()
    if status not in LOOKUP_STATUSES:
        raise ValueError("active")
    size = min(max(int(size or 20), 1), LOOKUP_MAX_SIZE)
    code = func.upper(StoreLocation.c_location_code)
    active_stores = func.count(distinct(case((StoreLocation.b_active.is_(True), StoreLocation.i_store_id))))
    query = db.query(code.label("code"), func.min(StoreLocation.c_location_name).label("name"),
                     func.count(distinct(StoreLocation.i_store_id)).label("stores"),
                     active_stores.label("active_stores")).filter(StoreLocation.c_location_area == area)
    if store_ids is not None:
        query = query.filter(StoreLocation.i_store_id.in_(store_ids or [-1]))
    term = str(q or "").strip()
    if term:
        like = f"%{term}%"
        query = query.filter(or_(StoreLocation.c_location_code.ilike(like), StoreLocation.c_location_name.ilike(like)))
    query = query.group_by(code)
    if status == "active":
        query = query.having(active_stores > 0)
    elif status == "inactive":
        query = query.having(active_stores == 0)
    total = db.query(func.count()).select_from(query.subquery()).scalar() or 0
    pages = max(math.ceil(total / size), 1)
    page = min(max(int(page or 1), 1), pages)
    rows = query.order_by(code).offset((page - 1) * size).limit(size).all()
    store_total = len(store_ids) if store_ids is not None else db.query(func.count(Store.i_store_id)).scalar()
    return {
        "items": [{"code": r.code, "name": r.name, "stores": r.stores, "active_stores": r.active_stores,
                   "active": r.active_stores > 0} for r in rows],
        "total": total, "page": page, "pages": pages, "size": size, "store_total": store_total,
        "location_area": area,
    }


def active_location_code_exists(db: Session, code: str, location_area: str, merchant_id: int | None) -> bool:
    """Is code an active location of the Area in some Store (of the Merchant when merchant_id is given)?"""
    query = db.query(StoreLocation.i_location_id).filter(
        func.upper(StoreLocation.c_location_code) == str(code).upper(),
        StoreLocation.c_location_area == location_area, StoreLocation.b_active.is_(True))
    if merchant_id is not None:
        query = query.join(Store, Store.i_store_id == StoreLocation.i_store_id).filter(Store.i_merchant_id == merchant_id)
    return query.first() is not None
