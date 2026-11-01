"""Initialize/repair basic owner roots only, without modifying DB rows or existing files."""
import argparse
import json

from backend.core.local_env import load_local_env

load_local_env()

from backend.database.pg_platform import PlatformSessionLocal
from backend.services.resource_storage import AwsumStorageService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Resolve saved owners without creating directories.")
    args = parser.parse_args()
    with PlatformSessionLocal() as db:
        result = AwsumStorageService(db).initialize_missing_storage(dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
