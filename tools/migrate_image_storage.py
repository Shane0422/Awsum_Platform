"""Run: py -m tools.migrate_image_storage [--dry-run]. Never deletes source images."""
import argparse

from backend.core.local_env import load_local_env

load_local_env()

from backend.database.pg_platform import platform_engine
from backend.database.image_storage_migration import migrate_image_storage


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = migrate_image_storage(platform_engine, dry_run=args.dry_run)
    print(f"Images: {len(result['assets'])}; DB references: {len(result['references'])}; "
          f"unclassified unchanged: {len(result['unmapped_legacy_assets'])}")
    if args.dry_run:
        print("Preflight only; no files or references changed.")
    elif result["status"] == "UNCHANGED":
        print("Already migrated; files and references unchanged. No new recovery map needed.")
    else:
        print(f"Recovery map: {result['manifest']}")
