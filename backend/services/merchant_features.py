"""Saved Merchant feature layers (read / save), one implementation for every layer.

- Business Options (tb_merchant_feature): Platform Admin only. Shown in the Platform Merchant form (BUSINESS OPTIONS
  tab) and read-only for a Merchant login (/merchant/api/business-options).
- Feature Entitlements (tb_merchant_feature_choice): the Merchant's own choice (STEP 3-3C-2, same functions).

Rules: the definitions come from the Common Feature Registry (services.features); only changed values are written
(an unchanged default keeps "no row" = the registry default); rows are never deleted; a Coming soon feature cannot be
turned ON; lower layers (Merchant choices, Store Options) and business data are never touched. Reads are fail-closed
(services.features.FeatureCheckUnavailable). No contract / billing logic here.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.models_admin.merchant_feature import SOURCE_PLATFORM, MerchantFeature
from backend.services.features import FeatureCheckUnavailable, default_entitlement, features_in

# BUSINESS OPTIONS tab: the registry's business group (registry order and labels; Gift Card = Coming soon).
# KIOSK / Online Ordering (channel group) are handled with the Device License work (STEP 3-7).
BUSINESS_OPTIONS = features_in("business")


def business_option_defs() -> list[dict]:
    return [{"key": f.key, "label": f.label, "tier": f.tier, "available": f.available} for f in BUSINESS_OPTIONS]


def _read_rows(db: Session, model, merchant_ids) -> dict:
    """merchant id -> {key: enabled} for one layer (fail-closed)."""
    ids = [m for m in merchant_ids if m is not None]
    if not ids:
        return {}
    try:
        with db.begin_nested():
            rows = db.query(model.i_merchant_id, model.c_feature_key, model.b_enabled).filter(model.i_merchant_id.in_(ids)).all()
    except SQLAlchemyError as exc:
        raise FeatureCheckUnavailable("Feature settings are temporarily unavailable.") from exc
    out: dict = {}
    for merchant_id, key, enabled in rows:
        out.setdefault(merchant_id, {})[key] = bool(enabled)
    return out


def business_options_for(db: Session, merchant_ids) -> dict:
    """merchant id -> [{key, label, tier, available, enabled, saved}] in registry order.

    enabled = the saved Business Option, else the registry default (BASE ON, ADDON OFF); Coming soon is always OFF.
    """
    saved = _read_rows(db, MerchantFeature, merchant_ids)
    result = {}
    for merchant_id in merchant_ids:
        rows = saved.get(merchant_id, {})
        result[merchant_id] = [
            {**d, "enabled": bool(d["available"] and (rows[d["key"]] if d["key"] in rows else default_entitlement(f))),
             "saved": d["key"] in rows}
            for f, d in zip(BUSINESS_OPTIONS, business_option_defs())
        ]
    return result


def _flag(key: str, value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("on", "true", "1", "yes"):
        return True
    if text in ("off", "false", "0", "no"):
        return False
    raise HTTPException(400, f"Invalid value for {key}: use on or off.")


def _save_layer(db: Session, model, definitions, merchant_id: int, values, actor_id, current_of, **extra) -> list[str]:
    """Write only the values that differ from the current state (upsert); returns the changed keys."""
    if values is None:
        return []
    if not isinstance(values, dict):
        raise HTTPException(400, "Feature settings must be an object of feature keys.")
    known = {f.key: f for f in definitions}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise HTTPException(400, f"Unknown feature: {', '.join(unknown)}.")
    rows = {row.c_feature_key: row for row in db.query(model).filter(model.i_merchant_id == merchant_id)}
    changed, now = [], datetime.now()
    for key, raw in values.items():
        definition, wanted = known[key], _flag(key, raw)
        if not definition.available:
            if wanted:
                raise HTTPException(400, f"{definition.label} is Coming soon and cannot be turned on.")
            continue
        row = rows.get(key)
        if wanted == current_of(definition, row):
            continue  # unchanged (a default stays "no row")
        if row is None:
            db.add(model(i_merchant_id=merchant_id, c_feature_key=key, b_enabled=wanted, i_created_by=actor_id, **extra))
        else:
            row.b_enabled = wanted
            row.dt_updated, row.i_updated_by = now, actor_id
            for name, value in extra.items():
                setattr(row, name, value)
        changed.append(key)
    if changed:
        db.flush()
    return changed


def save_business_options(db: Session, merchant_id: int, values, actor_id=None) -> list[str]:
    """Platform Admin: Business Options of one Merchant (the caller checks Platform Admin). Changed values only."""
    return _save_layer(db, MerchantFeature, BUSINESS_OPTIONS, merchant_id, values, actor_id,
                       lambda d, row: bool(row.b_enabled) if row is not None else default_entitlement(d),
                       c_source=SOURCE_PLATFORM)
