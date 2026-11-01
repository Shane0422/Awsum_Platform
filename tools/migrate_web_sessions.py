"""Run from the project root: py -m tools.migrate_web_sessions"""
from backend.core.local_env import load_local_env

load_local_env()

from backend.database.pg_platform import platform_engine
from backend.database.web_session_migration import migrate_web_sessions


if __name__ == "__main__":
    migrate_web_sessions(platform_engine)
    print("Web session migration completed. Existing legacy JWTs require a fresh sign-in.")
