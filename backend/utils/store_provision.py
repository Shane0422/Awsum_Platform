# store_provision.py
from backend.utils.safe_schema_migrate import safe_schema_migrate
from backend.database.store_db import StoreBase, get_store_engine, get_store_backup_dir

def provision_store(merchant_code, store_code, *, db):
    engine = get_store_engine(merchant_code, store_code, db=db)
    try:
        safe_schema_migrate(StoreBase, engine, get_store_backup_dir(merchant_code, store_code, db=db))
    finally:
        engine.dispose()
