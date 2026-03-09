# store_provision.py
from backend.utils.safe_schema_migrate import safe_schema_migrate
from backend.database.store_db import StoreBase, get_store_engine

def provision_store(store_id):
    engine = get_store_engine(store_id)
    safe_schema_migrate(StoreBase, engine, f"./backend/database/stores/backups")
