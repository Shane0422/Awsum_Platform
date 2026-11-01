"""Preview/apply the global path standard without deleting sources or renaming business codes."""
import argparse

from backend.core.local_env import load_local_env

load_local_env()

from backend.database.pg_platform import platform_engine
from backend.database.image_storage_migration import migrate_image_storage
from backend.database.resource_path_migration import migrate_resource_paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    # Preflight both domains before either can update files or references.
    for migration in (migrate_image_storage, migrate_resource_paths):
        plan = migration(platform_engine, dry_run=True)
        print(f"{migration.__name__}: {len(plan['assets'])} files, {len(plan['references'])} references, "
              f"{len(plan['unmapped_legacy_assets'])} retained for review.")
    if not args.dry_run:
        for migration in (migrate_image_storage, migrate_resource_paths):
            result = migration(platform_engine)
            print(f"{migration.__name__}: {result['status']}; recovery map: {result['manifest']}")
