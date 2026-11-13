"""Common secret hashing for web passwords and POS PINs (bcrypt, the scheme every tb_platform_user password uses).

The plain value is never stored, logged or returned: callers keep only the hash. Login checks stay in
routers.auth (password_matches); this module is what new code uses to set / reset and verify a secret.
"""
import bcrypt

# bcrypt reads at most 72 bytes; longer values are refused instead of being silently truncated.
MAX_BCRYPT_PASSWORD_BYTES = 72


def secret_too_long(value: str) -> bool:
    return len(str(value or "").encode("utf-8")) > MAX_BCRYPT_PASSWORD_BYTES


def hash_secret(value: str) -> str:
    if not value:
        raise ValueError("A secret is required.")
    if secret_too_long(value):
        raise ValueError(f"Too long (max {MAX_BCRYPT_PASSWORD_BYTES} bytes).")
    return bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_secret(value: str, hashed: str | None) -> bool:
    if not value or not hashed or secret_too_long(value):
        return False
    try:
        return bcrypt.checkpw(value.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
