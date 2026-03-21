from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# ==========================================
# 매장/회사 DB 경로
# - 회사 DB: data/company/{company_id}/main.db
# - 백업:   db_backups/company/{company_id}/
# ==========================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

StoreBase = declarative_base()


def get_store_engine(store_code: str):
    """특정 매장의 DB 엔진을 반환. store_code 예: "1001""" 
    store_dir = PROJECT_ROOT / "data" / "company" / store_code
    store_dir.mkdir(parents=True, exist_ok=True)

    db_path = store_dir / "main.db"
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


def get_store_session(store_code: str):
    """
    특정 매장의 세션팩토리 반환.
    """
    engine = get_store_engine(store_code)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_store_backup_dir(store_code: str):
    """특정 매장의 백업 디렉토리 반환 (자동 생성)."""
    backup_dir = PROJECT_ROOT / "db_backups" / "company" / store_code
    backup_dir.mkdir(parents=True, exist_ok=True)
    return str(backup_dir)
