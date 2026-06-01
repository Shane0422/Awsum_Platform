"""
Device Category & Device Type 마스터 관리 API
- GET/POST/PATCH/DELETE /platform/device-categories
- GET/POST/PATCH/DELETE /platform/device-types
업종 공통 마스터: 식당, 세탁소, 스파, 리테일, 턱시도 렌탈 등 모든 업종에서 재사용
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.device_category import DeviceCategory
from backend.models_admin.device_type import DeviceType

router = APIRouter(tags=["device-master"])


def get_db():
    db = PlatformSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ------------------------------------------------------------------
# Serializers
# ------------------------------------------------------------------

def _ser_category(cat: DeviceCategory) -> dict:
    return {
        "device_category_id": cat.i_device_category_id,
        "category_code": cat.c_category_code,
        "category_name": cat.c_category_name,
        "description": cat.c_description or "",
        "sort_order": cat.i_sort_order,
        "status": cat.c_status,
    }


def _ser_type(dt: DeviceType, include_category: bool = True) -> dict:
    d = {
        "device_type_id": dt.i_device_type_id,
        "device_category_id": dt.i_device_category_id,
        "device_type_code": dt.c_device_type_code,
        "device_type_name": dt.c_device_type_name,
        "description": dt.c_description or "",
        "billable_yn": dt.c_billable_yn,
        "default_monthly_fee": float(dt.n_default_monthly_fee or 0),
        "sort_order": dt.i_sort_order,
        "status": dt.c_status,
    }
    if include_category:
        # Inline category info for UI dropdowns
        d["category_code"] = ""
        d["category_name"] = ""
    return d


# ------------------------------------------------------------------
# Device Category endpoints
# ------------------------------------------------------------------

@router.get("/platform/device-categories")
def list_device_categories(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(DeviceCategory)
    if status:
        q = q.filter(DeviceCategory.c_status == status)
    cats = q.order_by(DeviceCategory.i_sort_order, DeviceCategory.i_device_category_id).all()
    return [_ser_category(c) for c in cats]


@router.get("/platform/device-categories/{category_id}")
def get_device_category(category_id: int, db: Session = Depends(get_db)):
    cat = db.query(DeviceCategory).filter(DeviceCategory.i_device_category_id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Device category not found")
    return _ser_category(cat)


@router.post("/platform/device-categories")
def create_device_category(payload: dict, db: Session = Depends(get_db)):
    if not payload.get("category_code") or not payload.get("category_name"):
        raise HTTPException(status_code=400, detail="category_code and category_name are required")
    existing = db.query(DeviceCategory).filter_by(c_category_code=payload["category_code"].upper().strip()).first()
    if existing:
        raise HTTPException(status_code=409, detail="category_code already exists")
    cat = DeviceCategory(
        c_category_code=payload["category_code"].upper().strip(),
        c_category_name=payload["category_name"].strip(),
        c_description=payload.get("description") or None,
        i_sort_order=int(payload.get("sort_order") or 100),
        c_status=payload.get("status") or "active",
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return _ser_category(cat)


@router.patch("/platform/device-categories/{category_id}")
def update_device_category(category_id: int, payload: dict, db: Session = Depends(get_db)):
    cat = db.query(DeviceCategory).filter(DeviceCategory.i_device_category_id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Device category not found")
    if "category_name" in payload:
        cat.c_category_name = payload["category_name"].strip()
    if "description" in payload:
        cat.c_description = payload.get("description") or None
    if "sort_order" in payload:
        cat.i_sort_order = int(payload["sort_order"] or 100)
    if "status" in payload:
        cat.c_status = payload["status"]
    db.commit()
    db.refresh(cat)
    return _ser_category(cat)


@router.delete("/platform/device-categories/{category_id}")
def delete_device_category(category_id: int, db: Session = Depends(get_db)):
    cat = db.query(DeviceCategory).filter(DeviceCategory.i_device_category_id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Device category not found")
    # Soft-delete only: prevent breaking FK references from tb_device_type
    in_use = db.query(DeviceType).filter(
        DeviceType.i_device_category_id == category_id,
        DeviceType.c_status == "active",
    ).first()
    if in_use:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: active Device Types are referencing this category. Deactivate them first."
        )
    cat.c_status = "inactive"
    db.commit()
    return {"ok": True, "device_category_id": category_id, "status": "inactive"}


# ------------------------------------------------------------------
# Device Type endpoints
# ------------------------------------------------------------------

@router.get("/platform/device-types")
def list_device_types(
    status: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = (
        db.query(DeviceType, DeviceCategory.c_category_code, DeviceCategory.c_category_name)
        .join(DeviceCategory, DeviceType.i_device_category_id == DeviceCategory.i_device_category_id)
    )
    if status:
        q = q.filter(DeviceType.c_status == status)
    if category_id:
        q = q.filter(DeviceType.i_device_category_id == category_id)
    rows = q.order_by(DeviceCategory.i_sort_order, DeviceType.i_sort_order, DeviceType.i_device_type_id).all()
    result = []
    for dt, cat_code, cat_name in rows:
        d = _ser_type(dt, include_category=False)
        d["category_code"] = cat_code
        d["category_name"] = cat_name
        result.append(d)
    return result


@router.get("/platform/device-types/{type_id}")
def get_device_type(type_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(DeviceType, DeviceCategory.c_category_code, DeviceCategory.c_category_name)
        .join(DeviceCategory, DeviceType.i_device_category_id == DeviceCategory.i_device_category_id)
        .filter(DeviceType.i_device_type_id == type_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Device type not found")
    dt, cat_code, cat_name = row
    d = _ser_type(dt, include_category=False)
    d["category_code"] = cat_code
    d["category_name"] = cat_name
    return d


@router.post("/platform/device-types")
def create_device_type(payload: dict, db: Session = Depends(get_db)):
    if not payload.get("device_type_code") or not payload.get("device_type_name"):
        raise HTTPException(status_code=400, detail="device_type_code and device_type_name are required")
    if not payload.get("device_category_id"):
        raise HTTPException(status_code=400, detail="device_category_id is required")
    cat = db.query(DeviceCategory).filter(DeviceCategory.i_device_category_id == int(payload["device_category_id"])).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Device category not found")
    existing = db.query(DeviceType).filter_by(c_device_type_code=payload["device_type_code"].upper().strip()).first()
    if existing:
        raise HTTPException(status_code=409, detail="device_type_code already exists")
    dt = DeviceType(
        i_device_category_id=int(payload["device_category_id"]),
        c_device_type_code=payload["device_type_code"].upper().strip(),
        c_device_type_name=payload["device_type_name"].strip(),
        c_description=payload.get("description") or None,
        c_billable_yn=payload.get("billable_yn") or "yes",
        n_default_monthly_fee=float(payload.get("default_monthly_fee") or 0),
        i_sort_order=int(payload.get("sort_order") or 100),
        c_status=payload.get("status") or "active",
    )
    db.add(dt)
    db.commit()
    db.refresh(dt)
    d = _ser_type(dt, include_category=False)
    d["category_code"] = cat.c_category_code
    d["category_name"] = cat.c_category_name
    return d


@router.patch("/platform/device-types/{type_id}")
def update_device_type(type_id: int, payload: dict, db: Session = Depends(get_db)):
    dt = db.query(DeviceType).filter(DeviceType.i_device_type_id == type_id).first()
    if not dt:
        raise HTTPException(status_code=404, detail="Device type not found")
    if "device_category_id" in payload:
        cat = db.query(DeviceCategory).filter(DeviceCategory.i_device_category_id == int(payload["device_category_id"])).first()
        if not cat:
            raise HTTPException(status_code=404, detail="Device category not found")
        dt.i_device_category_id = int(payload["device_category_id"])
    if "device_type_name" in payload:
        dt.c_device_type_name = payload["device_type_name"].strip()
    if "description" in payload:
        dt.c_description = payload.get("description") or None
    if "billable_yn" in payload:
        dt.c_billable_yn = payload["billable_yn"]
    if "default_monthly_fee" in payload:
        dt.n_default_monthly_fee = float(payload["default_monthly_fee"] or 0)
    if "sort_order" in payload:
        dt.i_sort_order = int(payload["sort_order"] or 100)
    if "status" in payload:
        dt.c_status = payload["status"]
    db.commit()
    db.refresh(dt)
    cat = db.query(DeviceCategory).filter(DeviceCategory.i_device_category_id == dt.i_device_category_id).first()
    d = _ser_type(dt, include_category=False)
    d["category_code"] = cat.c_category_code if cat else ""
    d["category_name"] = cat.c_category_name if cat else ""
    return d


@router.delete("/platform/device-types/{type_id}")
def delete_device_type(type_id: int, db: Session = Depends(get_db)):
    from backend.models_admin.device import Device as DeviceModel
    dt = db.query(DeviceType).filter(DeviceType.i_device_type_id == type_id).first()
    if not dt:
        raise HTTPException(status_code=404, detail="Device type not found")
    in_use = db.query(DeviceModel).filter(
        DeviceModel.i_device_type_id == type_id,
        DeviceModel.c_status == "active",
    ).first()
    if in_use:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: active Devices are using this device type. Deactivate them first."
        )
    dt.c_status = "inactive"
    db.commit()
    return {"ok": True, "device_type_id": type_id, "status": "inactive"}
