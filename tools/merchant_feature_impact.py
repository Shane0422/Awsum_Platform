"""Read-only impact report of the three-layer Feature policy (STEP 3-3B). Never changes the database.

    python -m tools.merchant_feature_impact [--json <file>]

Per Merchant: what a Merchant login can use today without a Merchant-level check (MD Item Feature fields, Location
Master and the Location fields) that the policy would turn off, with the Location data behind it.
Per Store: features usable today vs with Business Options + Merchant Feature Entitlements + Store Options + Status.
"""
from __future__ import annotations

import argparse
import json
import sys

from backend.core.local_env import load_local_env

load_local_env()

from sqlalchemy import text  # noqa: E402

from backend.database import pg_platform  # noqa: E402
from backend.database.merchant_feature_migration import policy_impact  # noqa: E402


def report(engine) -> dict:
    with engine.connect() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SET TRANSACTION READ ONLY"))
        result = policy_impact(conn)
        conn.rollback()
    return result


def print_report(result: dict) -> None:
    print(f"[LAYERS] Business Option rows: {result['business_rows']}, Merchant Feature Entitlement rows: {result['choice_rows']}")
    print("\n[MERCHANT LEVEL] Merchant login today -> under the policy")
    for m in result["merchants"]:
        lost = m["lost_at_merchant_level"]
        print(f"    {m['id']:>3} {m['code']:<10} {m['name']:<14} "
              + ("no change" if not lost else "would lose: " + ", ".join(f"{k} ({m['reasons'][k]})" for k in lost))
              + f" | Stores: {', '.join(m['stores']) or '-'} | Location data: {m['location_data'] or 'none'}")
    print("\n[STORE LEVEL] usable today -> under the policy")
    for s in result["stores"]:
        same = s["before"] == s["after"]
        flag = "ADMIN (exempt) " if s["admin"] else ("INACTIVE " if s["inactive"] else "")
        print(f"    {s['store']:<10} {flag}{'same' if same else 'CHANGED'}: {', '.join(s['before'])}"
              + ("" if same else f"  ->  lost {', '.join(s['lost'])}"))
    print(f"\n[RESULT] {'OK: no active Store loses a feature it uses' if not result['problems'] else 'BLOCKED: ' + json.dumps(result['problems'])}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.merchant_feature_impact", description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", help="also write the full result to this JSON file")
    args = parser.parse_args(argv)
    result = report(pg_platform.platform_engine)
    print_report(result)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
        print(f"[JSON] {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
