"""One-time setup of the System Settings encryption key.

Writes AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY to the Git-ignored ``.env`` file.
The key is never printed. Existing keys are never overwritten, and no key is
generated when encrypted data already exists in the database.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet

from backend.core.local_env import LOCAL_ENV_PATH, read_local_env
from backend.services.system_settings import ENCRYPTION_KEY_ENV


def _has_encrypted_data() -> bool:
    from backend.database.pg_platform import PlatformSessionLocal
    from backend.models_admin.system_settings import SystemSettings

    with PlatformSessionLocal() as db:
        return db.query(SystemSettings).filter(
            SystemSettings.c_smtp_password_encrypted.isnot(None),
            SystemSettings.c_smtp_password_encrypted != "",
        ).first() is not None


def main() -> int:
    if os.getenv(ENCRYPTION_KEY_ENV, "").strip() or read_local_env().get(ENCRYPTION_KEY_ENV, "").strip():
        print(f"{ENCRYPTION_KEY_ENV} is already configured. Nothing was changed.")
        return 0
    if _has_encrypted_data():
        print(
            "Encrypted SMTP data already exists but no key is configured. "
            "Restore the original key from backup; a new key cannot decrypt it.",
            file=sys.stderr,
        )
        return 1
    key = Fernet.generate_key().decode("ascii")
    needs_newline = LOCAL_ENV_PATH.exists() and not LOCAL_ENV_PATH.read_text(encoding="utf-8").endswith("\n")
    with LOCAL_ENV_PATH.open("a", encoding="utf-8") as stream:
        stream.write(("\n" if needs_newline else "") + f"{ENCRYPTION_KEY_ENV}={key}\n")
    print(f"Key saved to {LOCAL_ENV_PATH.name} (not displayed). Back it up separately from the database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
