import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# D:\Awsum_Projects\tuxedo_rental\db_store\store_1001\store_1001.db
# D:\Awsum_Projects\tuxedo_rental\db_store\store_1001\backups\
# ⚙️ backend/database/ → ../../ 올라가면 프로젝트 루트 (tuxedo_rental)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

StoreBase = declarative_base()


def get_store_engine(store_code: str):
    """
    특정 매장의 DB 엔진을 반환.
    store_code 예: "1001"
    """
    store_dir = os.path.join(PROJECT_ROOT, "db_store", f"store_{store_code}")
    os.makedirs(store_dir, exist_ok=True)

    db_path = os.path.join(store_dir, f"store_{store_code}.db")
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


def get_store_session(store_code: str):
    """
    특정 매장의 세션팩토리 반환.
    """
    engine = get_store_engine(store_code)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_store_backup_dir(store_code: str):
    """
    특정 매장의 백업 디렉토리 반환 (자동 생성).
    """
    store_dir = os.path.join(PROJECT_ROOT, "db_store", f"store_{store_code}", "backups")
    os.makedirs(store_dir, exist_ok=True)
    return store_dir
