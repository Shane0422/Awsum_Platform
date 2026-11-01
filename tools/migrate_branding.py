"""Run from the project root: py -m tools.migrate_branding."""
from backend.core.local_env import load_local_env

load_local_env()

from backend.database.pg_platform import platform_engine
from backend.database.branding_migration import migrate_branding


if __name__ == "__main__":
    migrate_branding(platform_engine)
    print("Branding migration completed; existing files and references preserved.")
