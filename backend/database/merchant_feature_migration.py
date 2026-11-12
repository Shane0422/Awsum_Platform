"""Initial Merchant Feature Entitlements (tb_merchant_feature) from the Stores' current usage (STEP 3-2A).

Policy:
- Every available ADDON feature gets one explicit row per Merchant: b_enabled = true when at least one of the
  Merchant's Stores uses it today, else false (c_source = 'MIGRATION'). BASE features get no row (tier default =
  entitled). Explicit rows also mark "already seeded": the seed only runs while the table is empty, so a later Store
  change by Platform never grants an entitlement by itself.
- "Uses it today": the Store's assignment is ON (saved value, or the registry default for NULL); KIOSK = KIOSK
  quantity > 0 or an active KIOSK license; Online Ordering = Customer Ordering ON or an active CUSTOMER_ORDERING
  license. Active and inactive Stores both count (keep rather than block); the Admin Store (ADMIN_STORE_ID) and
  Stores without a Merchant are excluded from the calculation.
- Nothing else is written: tb_store, tb_store_entitlement and tb_license are only read (verified in apply).
- verify: every feature a Store uses before the migration must still be usable after it (Admin Store: not checked
  against Merchant entitlements). Any problem stops apply before commit.

plan_merchant_features() / feature_comparison() are read-only (dry run). init_platform_db() calls
seed_new_merchant_feature_table() only in the run that creates the table (STEP 3-2B), so the seed happens once:
afterwards rows change only through Platform.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import inspect, text

from backend.services.features import (ADDON, LAYER_TABLES, REGISTRY, FeatureSnapshot, entitlement_exempt, features_in,
                                       resolve_features, store_assignment)

TABLE = "tb_merchant_feature"
SOURCE = "MIGRATION"
SEED_FEATURES = tuple(f for f in REGISTRY if f.tier == ADDON and f.available)
CHANNEL_LICENSE_TYPES = {"kiosk": "KIOSK", "online_ordering": "CUSTOMER_ORDERING"}


class FeatureMigrationError(RuntimeError):
    """A check failed; nothing was committed."""


class _StoreRow:
    """Read-only Store values for the resolver (attribute access like the Store model)."""

    def __init__(self, values: dict):
        self.__dict__.update(values)

    def __getattr__(self, name):  # columns that do not exist in this database read as NULL
        return None


def _tables(conn) -> set:
    return set(inspect(conn).get_table_names())


def read_usage(conn) -> dict:
    """Merchants, Stores (with their option values), quantities and active licenses. SELECT only."""
    tables = _tables(conn)
    store_columns = {c["name"] for c in inspect(conn).get_columns("tb_store")}
    option_columns = [f.store_column for f in REGISTRY if f.store_column and f.store_column in store_columns]
    merchants = [dict(r._mapping) for r in conn.execute(text(
        "SELECT i_merchant_id, c_merchant_code, c_merchant_name, c_status FROM tb_merchant ORDER BY i_merchant_id"))]
    stores = [dict(r._mapping) for r in conn.execute(text(
        "SELECT i_store_id, i_merchant_id, c_store_code, c_store_name, c_status"
        + "".join(f", {c}" for c in option_columns) + " FROM tb_store ORDER BY i_store_id"))]
    quantities = {}
    if "tb_store_entitlement" in tables:
        quantities = {r.i_store_id: {"kiosk_qty": r.i_kiosk_qty or 0, "customer_ordering": bool(r.b_customer_ordering)}
                      for r in conn.execute(text(
                          "SELECT i_store_id, i_kiosk_qty, b_customer_ordering FROM tb_store_entitlement"))}
    licenses = {}
    if "tb_license" in tables:
        for store_id, license_type, count in conn.execute(text(
                "SELECT i_store_id, upper(c_license_type), count(*) FROM tb_license "
                "WHERE lower(c_status) = 'active' GROUP BY i_store_id, upper(c_license_type)")):
            licenses.setdefault(store_id, {})[license_type] = count
    return {"merchants": merchants, "stores": stores, "quantities": quantities, "licenses": licenses,
            "option_columns": option_columns}


def store_usage(store: dict, usage: dict) -> dict:
    """ADDON feature key -> evidence text when this Store uses it today (absent = not used)."""
    row = _StoreRow(store)
    quantities = usage["quantities"].get(store["i_store_id"], {})
    licenses = usage["licenses"].get(store["i_store_id"], {})
    used = {}
    for definition in SEED_FEATURES:
        if definition.assignable:
            assigned, source = store_assignment(row, definition)
            if assigned:
                used[definition.key] = f"{definition.store_column}={'NULL(default ON)' if source == 'default' else 'true'}"
        elif definition.key == "kiosk":
            parts = ([f"KIOSK qty {quantities['kiosk_qty']}"] if quantities.get("kiosk_qty", 0) > 0 else []) \
                + ([f"{licenses['KIOSK']} active KIOSK license(s)"] if licenses.get("KIOSK") else [])
            if parts:
                used["kiosk"] = ", ".join(parts)
        elif definition.key == "online_ordering":
            parts = (["Customer Ordering ON"] if quantities.get("customer_ordering") else []) \
                + ([f"{licenses['CUSTOMER_ORDERING']} active ordering license(s)"] if licenses.get("CUSTOMER_ORDERING") else [])
            if parts:
                used["online_ordering"] = ", ".join(parts)
    return used


def _counted(store: dict) -> bool:
    return not entitlement_exempt(store) and store["i_merchant_id"] is not None


def plan_merchant_features(conn, usage: dict | None = None) -> dict:
    """Rows the seed would insert, per Merchant, with the Stores that justify each ON row. Read-only."""
    usage = usage or read_usage(conn)
    existing = 0
    if TABLE in _tables(conn):
        existing = conn.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar() or 0
    merchants = []
    for merchant in usage["merchants"]:
        mid = merchant["i_merchant_id"]
        evidence: dict[str, list] = {}
        for store in usage["stores"]:
            if store["i_merchant_id"] != mid or not _counted(store):
                continue
            for key, why in store_usage(store, usage).items():
                evidence.setdefault(key, []).append(
                    f"{store['c_store_code']}{'' if store['c_status'] == 'active' else ' (inactive)'}: {why}")
        rows = [{"feature": f.key, "enabled": f.key in evidence, "evidence": evidence.get(f.key, [])} for f in SEED_FEATURES]
        merchants.append({**merchant, "rows": rows,
                          "stores": [s["c_store_code"] for s in usage["stores"] if s["i_merchant_id"] == mid and _counted(s)]})
    return {
        "table_exists": TABLE in _tables(conn), "existing_rows": existing, "will_seed": existing == 0,
        "seed_features": [f.key for f in SEED_FEATURES],
        "base_features": [f.key for f in REGISTRY if f.tier != ADDON],
        "not_available": [f.key for f in REGISTRY if not f.available],
        "merchants": merchants,
        "excluded_stores": [{"store": s["c_store_code"], "id": s["i_store_id"],
                             "reason": "Admin Store" if entitlement_exempt(s) else "no Merchant",
                             "uses": sorted(store_usage(s, usage))} for s in usage["stores"] if not _counted(s)],
    }


def entitlements_from_plan(plan: dict) -> dict:
    return {m["i_merchant_id"]: {r["feature"]: r["enabled"] for r in m["rows"]} for m in plan["merchants"]}


def feature_comparison(usage: dict, entitlements_by_merchant: dict) -> dict:
    """Per Store: features usable before (LEGACY = today) vs after (with the Merchant entitlements).

    "Usable" = assignable features: effective; channels: in use today and entitled (quantities / licenses are
    judged separately and never changed). problems = usable before but not after (must be empty).
    """
    stores, problems = [], []
    for store in usage["stores"]:
        row = _StoreRow(store)
        uses = store_usage(store, usage)
        before_states = resolve_features(row, None)
        admin = entitlement_exempt(store)
        # Admin Store: Platform internal, not checked against Merchant entitlements (stays LEGACY).
        ent = None if admin else entitlements_by_merchant.get(store["i_merchant_id"], {})
        after_states = resolve_features(row, ent)
        before, after = {}, {}
        for definition in REGISTRY:
            key = definition.key
            if definition.assignable:
                before[key], after[key] = before_states[key].effective, after_states[key].effective
            else:
                before[key] = key in uses or (definition.tier != ADDON and before_states[key].effective)
                after[key] = before[key] and after_states[key].effective
        lost = [k for k in before if before[k] and not after[k]]
        if lost:
            problems.append({"store": store["c_store_code"], "id": store["i_store_id"], "lost": lost})
        stores.append({"store": store["c_store_code"], "id": store["i_store_id"], "merchant_id": store["i_merchant_id"],
                       "status": store["c_status"], "admin": admin,
                       "before": sorted(k for k, v in before.items() if v), "after": sorted(k for k, v in after.items() if v),
                       "lost": lost})
    return {"stores": stores, "problems": problems}


def _store_fingerprint(conn, usage: dict) -> list:
    columns = ", ".join(["i_store_id", "i_merchant_id"] + usage["option_columns"] + ["c_customer_scope", "c_loyalty_scope"])
    rows = [tuple(r) for r in conn.execute(text(f"SELECT {columns} FROM tb_store ORDER BY i_store_id"))]
    tables = _tables(conn)
    if "tb_store_entitlement" in tables:
        rows += [tuple(r) for r in conn.execute(text(
            "SELECT i_store_id, i_pos_qty, i_kiosk_qty, b_customer_ordering FROM tb_store_entitlement ORDER BY i_store_id"))]
    if "tb_license" in tables:
        rows += [tuple(r) for r in conn.execute(text("SELECT i_license_id, c_status FROM tb_license ORDER BY i_license_id"))]
    return rows


def apply_merchant_features(engine, actor_id: int | None = None) -> dict:
    """Seed tb_merchant_feature once (only while it is empty), verified, in one transaction.

    The table must already exist (created by the approved schema migration). Any check failure rolls back.
    """
    with engine.begin() as conn:
        if TABLE not in _tables(conn):
            raise FeatureMigrationError(f"{TABLE} does not exist; run the schema migration first.")
        usage = read_usage(conn)
        plan = plan_merchant_features(conn, usage)
        if not plan["will_seed"]:
            return {"seeded": 0, "skipped": f"{TABLE} already has {plan['existing_rows']} row(s)"}
        planned = entitlements_from_plan(plan)
        check = feature_comparison(usage, planned)
        if check["problems"]:
            raise FeatureMigrationError(f"Features would be blocked: {check['problems']}")
        fingerprint = _store_fingerprint(conn, usage)
        now = datetime.now()
        rows = [{"m": m["i_merchant_id"], "k": r["feature"], "e": r["enabled"], "s": SOURCE, "t": now, "u": actor_id}
                for m in plan["merchants"] for r in m["rows"]]
        if rows:
            conn.execute(text(f"INSERT INTO {TABLE} (i_merchant_id, c_feature_key, b_enabled, c_source, dt_created, i_created_by) "
                              "VALUES (:m, :k, :e, :s, :t, :u)"), rows)
        saved: dict = {}
        for r in conn.execute(text(f"SELECT i_merchant_id, c_feature_key, b_enabled FROM {TABLE}")):
            saved.setdefault(r.i_merchant_id, {})[r.c_feature_key] = bool(r.b_enabled)
        if saved != {m: e for m, e in planned.items() if e}:
            raise FeatureMigrationError("Saved entitlements differ from the plan.")
        after = feature_comparison(usage, saved)
        if after["problems"]:
            raise FeatureMigrationError(f"Features blocked after insert: {after['problems']}")
        if _store_fingerprint(conn, usage) != fingerprint:
            raise FeatureMigrationError("Store options / quantities / licenses changed during the migration.")
        return {"seeded": len(rows), "enabled": sum(1 for r in rows if r["e"]), "comparison": after}


def merchant_feature_table_exists(engine) -> bool:
    with engine.connect() as conn:
        return TABLE in _tables(conn)


def seed_new_merchant_feature_table(engine, existed_before: bool) -> dict | None:
    """init_platform_db: seed only when this run created the table (once per database)."""
    if existed_before or not merchant_feature_table_exists(engine):
        return None
    result = apply_merchant_features(engine)
    print(f"[FEATURES] tb_merchant_feature created: {result.get('seeded', 0)} initial entitlement row(s), "
          f"{result.get('enabled', 0)} ON.")
    return result


# ---------- STEP 3-3B: impact of the three-layer policy (read-only) ----------

# What a Merchant login can use today without any Merchant-level check: every MD Item Feature field and
# Location Master / the Category III and MD Location fields.
MERCHANT_LEVEL_TODAY = tuple(f.key for f in features_in("item")) + ("location_management",)


def saved_layers(conn) -> dict:
    """Merchant id -> FeatureSnapshot from the saved tables (a missing choice table = no saved choices yet)."""
    tables = _tables(conn)
    layers: dict = {}
    for name, table in LAYER_TABLES.items():
        if table not in tables:
            continue
        for merchant_id, key, enabled in conn.execute(text(f"SELECT i_merchant_id, c_feature_key, b_enabled FROM {table}")):
            layers.setdefault(merchant_id, {"business": {}, "choices": {}})[name][key] = bool(enabled)
    return layers


def location_evidence(conn) -> dict:
    """Store id -> Location data counts (Locations, default initialization)."""
    tables, out = _tables(conn), {}
    for table, label in (("tb_store_location", "locations"), ("tb_store_location_init", "initialized")):
        if table in tables:
            for store_id, count in conn.execute(text(f"SELECT i_store_id, count(*) FROM {table} GROUP BY i_store_id")):
                out.setdefault(store_id, {})[label] = count
    return out


def policy_impact(conn) -> dict:
    """Today (no Merchant-level check, Store Options only) vs the three-layer policy with the saved layers.

    merchants: what a Merchant login loses (MERCHANT_LEVEL_TODAY not entitled) with the Location data behind it.
    stores: feature_comparison with the full snapshot (Business Option + Merchant choice + Store Option + Status).
    """
    usage = read_usage(conn)
    saved = saved_layers(conn)
    snapshots = {m["i_merchant_id"]: FeatureSnapshot(merchant_id=m["i_merchant_id"], **saved.get(m["i_merchant_id"], {"business": {}, "choices": {}}))
                 for m in usage["merchants"]}
    evidence = location_evidence(conn)
    merchants = []
    for merchant in usage["merchants"]:
        mid = merchant["i_merchant_id"]
        states = resolve_features(None, snapshots[mid])
        lost = [key for key in MERCHANT_LEVEL_TODAY if not states[key].entitled]
        stores = [s for s in usage["stores"] if s["i_merchant_id"] == mid and not entitlement_exempt(s)]
        merchants.append({
            "id": mid, "code": merchant["c_merchant_code"], "name": merchant["c_merchant_name"],
            "entitled": sorted(k for k, s in states.items() if s.entitled),
            "lost_at_merchant_level": lost, "reasons": {k: states[k].reason for k in lost},
            "location_data": {s["c_store_code"]: evidence[s["i_store_id"]] for s in stores if s["i_store_id"] in evidence},
            "stores": [s["c_store_code"] for s in stores],
        })
    comparison = feature_comparison(usage, snapshots)
    for store in comparison["stores"]:  # Store Status: an Inactive Store has no operation today either
        store["inactive"] = store["status"] != "active"
    problems = [p for p in comparison["problems"]
                if next(s for s in comparison["stores"] if s["id"] == p["id"])["status"] == "active"]
    return {"merchants": merchants, "stores": comparison["stores"], "problems": problems,
            "choice_rows": sum(len(v["choices"]) for v in saved.values()),
            "business_rows": sum(len(v["business"]) for v in saved.values())}


def dry_run(engine) -> dict:
    """Plan + comparison inside a READ ONLY transaction that is rolled back (never changes the DB)."""
    with engine.connect() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SET TRANSACTION READ ONLY"))
        usage = read_usage(conn)
        plan = plan_merchant_features(conn, usage)
        comparison = feature_comparison(usage, entitlements_from_plan(plan))
        conn.rollback()
    return {"plan": plan, "comparison": comparison}
