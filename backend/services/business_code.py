"""Shared registration-time Agency, Merchant and Store business codes."""
from datetime import datetime
import secrets

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models_admin.account import Account
from backend.models_admin.agency import Agency
from backend.models_admin.store import Store


BUSINESS_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ENTITIES = {
    "AGENCY": ("AGT", Agency.c_agency_code, 11001004),
    "MERCHANT": ("MER", Account.c_client_code, 11001002),
    "STORE": ("STR", Store.c_store_code, 11001003),
}


def generate_business_code(db: Session, entity: str) -> str:
    if entity not in _ENTITIES:
        raise ValueError(f"Unsupported business code entity: {entity}")
    prefix, column, lock_key = _ENTITIES[entity]
    # Keep the existing transaction lock and unique constraints as concurrency guards.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
    year = datetime.now().strftime("%y")
    for _ in range(1024):
        code = prefix + year + "".join(secrets.choice(BUSINESS_CODE_ALPHABET) for _ in range(4))
        if db.query(column).filter(column == code).first() is None:
            return code
    raise HTTPException(409, f"Unable to generate a unique {entity.lower()} code. Please try again.")
