"""Read-only dry run of the initial Merchant Feature Entitlements (STEP 3-2A). Never changes the database.

    python -m tools.merchant_feature_dry_run [--json <file>]

Shows, per Merchant, the tb_merchant_feature rows the approved migration would insert and, per Store, the
features usable before / after. The actual seed runs only inside the approved migration
(backend.database.merchant_feature_migration.apply_merchant_features).
"""
from __future__ import annotations

import argparse
import json
import sys

from backend.core.local_env import load_local_env

load_local_env()

from backend.database import pg_platform  # noqa: E402
from backend.database.merchant_feature_migration import dry_run  # noqa: E402


def print_report(result: dict) -> None:
    plan, comparison = result["plan"], result["comparison"]
    print(f"[TABLE] {('exists, ' + str(plan['existing_rows']) + ' row(s)') if plan['table_exists'] else 'not created yet'}"
          f" -> seed {'would run' if plan['will_seed'] else 'skipped (already seeded)'}")
    print(f"[SEED] ADDON rows per Merchant: {', '.join(plan['seed_features'])}")
    print(f"[SEED] BASE (no row, entitled by default): {', '.join(plan['base_features'])}; "
          f"Coming soon (no row): {', '.join(plan['not_available'])}")
    total = on = 0
    for merchant in plan["merchants"]:
        rows = merchant["rows"]
        total += len(rows)
        on += sum(r["enabled"] for r in rows)
        enabled = [r for r in rows if r["enabled"]]
        print(f"\n[MERCHANT] {merchant['i_merchant_id']} {merchant['c_merchant_code']} {merchant['c_merchant_name']} "
              f"({merchant['c_status']}); Stores counted: {', '.join(merchant['stores']) or '-'}")
        for r in enabled:
            print(f"    ON   {r['feature']:<20} <- {'; '.join(r['evidence'])}")
        print(f"    OFF  {', '.join(r['feature'] for r in rows if not r['enabled']) or '-'}")
    for store in plan["excluded_stores"]:
        print(f"\n[EXCLUDED] {store['store']} ({store['id']}, {store['reason']}); uses today: {', '.join(store['uses']) or '-'}")
    print(f"\n[ROWS] {total} row(s) would be inserted: {on} ON, {total - on} OFF")
    print("\n[COMPARE] Store: usable before -> after")
    for store in comparison["stores"]:
        same = store["before"] == store["after"]
        print(f"    {store['store']:<10} {'ADMIN (not checked) ' if store['admin'] else ''}"
              f"{'same' if same else 'CHANGED'}: {', '.join(store['before'])}"
              + ("" if same else f"  ->  {', '.join(store['after'])}"))
    print(f"\n[RESULT] {'OK: no Store loses a feature it uses' if not comparison['problems'] else 'BLOCKED: ' + json.dumps(comparison['problems'])}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.merchant_feature_dry_run", description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", help="also write the full result to this JSON file")
    args = parser.parse_args(argv)
    result = dry_run(pg_platform.platform_engine)
    print_report(result)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
        print(f"[JSON] {args.json}")
    return 0 if not result["comparison"]["problems"] else 2


if __name__ == "__main__":
    sys.exit(main())
