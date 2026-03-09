# backend/routers/store.py
from fastapi import APIRouter
from backend.database.store_db import StoreBase, get_store_engine, get_store_backup_dir
from backend.utils.safe_schema_migrate import safe_schema_migrate

router = APIRouter()

@router.post("/migrate/{store_code}")
def migrate_store(store_code: str):
    """특정 매장의 DB를 자동 마이그레이션"""
    engine = get_store_engine(store_code)
    backup_dir = get_store_backup_dir(store_code)
    safe_schema_migrate(StoreBase, engine, backup_dir)
    return {"message": f"✅ Store {store_code} DB migrated successfully."}
