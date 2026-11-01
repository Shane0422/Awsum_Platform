from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from backend.services.paths import AwsumPathResolver, resource_storage_path, validate_owner
from backend.services.resource_storage import ensure_resource_directory


StoreBase = declarative_base()


def _owner(db: Session, merchant_code: str, store_code: str) -> AwsumPathResolver:
    owner = AwsumPathResolver("STORE", merchant_code=merchant_code, store_code=store_code)
    validate_owner(db, owner)
    return owner


def get_store_engine(merchant_code: str, store_code: str, *, db: Session):
    """Provision only a DB-verified Merchant/Store namespace; never a standalone Store root."""
    from backend.core.data_paths import get_data_root
    owner = _owner(db, merchant_code, store_code)
    logical = owner.get_data_path("master", "main.db")
    store = validate_owner(db, owner)
    if store.c_db_path:
        try:
            stored_owner, parts = AwsumPathResolver.parse_resource(store.c_db_path)
        except ValueError as exc:
            raise RuntimeError("Stored database reference is legacy or non-SQLite; migrate or use its DB provider.") from exc
        if stored_owner != owner or parts[0] != "data" or not parts[-1].endswith((".db", ".sqlite", ".sqlite3")):
            raise RuntimeError("Stored database reference does not match the Merchant/Store SQLite owner.")
        logical = store.c_db_path
    db_path = resource_storage_path(logical)
    legacy = get_data_root() / "company" / store_code / "main.db"
    if legacy.exists() and not db_path.exists():
        raise RuntimeError("Legacy Store database requires tools.migrate_global_paths before opening.")
    ensure_resource_directory(logical)
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


def get_store_session(merchant_code: str, store_code: str, *, db: Session):
    engine = get_store_engine(merchant_code, store_code, db=db)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_store_backup_dir(merchant_code: str, store_code: str, *, db: Session) -> str:
    owner = _owner(db, merchant_code, store_code)
    # Backups share Store ownership; schema-migration files live in data/common.
    directory = ensure_resource_directory(owner.get_data_path("common", "storage.json"))
    return str(directory)
