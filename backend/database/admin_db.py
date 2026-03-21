from pathlib import Path

from sqlalchemy import create_engine, text, func
from sqlalchemy.orm import sessionmaker, declarative_base

# ==========================================
# 📂 경로 정의
# - 플랫폼 DB:  data/platform/admin.db
# - 플랫폼 백업: db_backups/platform/
# ==========================================

# 프로젝트 루트는 backend/ 의 상위 2단계
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 플랫폼 DB 전용 저장소
PLATFORM_DATA_DIR = PROJECT_ROOT / "data" / "platform"
PLATFORM_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = PLATFORM_DATA_DIR / "admin.db"

# ==========================================
# 📌 SQLAlchemy 세팅
# ==========================================
admin_engine = create_engine(f"sqlite:///{DB_PATH}", echo=True, future=True)
AdminSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=admin_engine)
AdminBase = declarative_base()


# ==========================================
# 📌 Admin DB 초기화 함수
# ==========================================

# Store ID rules
ADMIN_STORE_ID = 1000001
CUSTOMER_STORE_ID_START = 1100001


def init_admin_db():
    """AdminDB 테이블 생성 및 기본 Seed 데이터 삽입"""
    from backend.models_admin import account, business_type, store, platform_user, session, role

    # 테이블 생성
    AdminBase.metadata.create_all(bind=admin_engine)

    from sqlalchemy.orm import Session
    db = Session(bind=admin_engine)

    # ===============================
    # Account Seed
    # ===============================
    from backend.models_admin.account import Account
    default_accounts = [
        {"c_account_name": "SystemAccount"},
    ]
    for acc in default_accounts:
        if not db.query(Account).filter_by(c_account_name=acc["c_account_name"]).first():
            db.add(Account(
                c_account_name=acc["c_account_name"],
                c_first_name="System",
                c_last_name="Owner",
                c_email="account@system.local",
                c_status="active",
            ))

    # ===============================
    # BusinessType Seed
    # ===============================
    from backend.models_admin.business_type import BusinessType
    business_types = [
        {
            "c_business_code": "PLATFORM",
            "c_business_name": "Platform",
            "c_business_name_kr": "플랫폼",
            "c_name": "Platform",
            "c_description": "Platform",
        },
        {
            "c_business_code": "TUXEDO_RENTAL",
            "c_business_name": "Tuxedo Rental",
            "c_business_name_kr": "턱시도 렌탈",
            "c_name": "Tuxedo Rental",
            "c_description": "Tuxedo Rental",
        },
        {
            "c_business_code": "RESTAURANT",
            "c_business_name": "Restaurant",
            "c_business_name_kr": "레스토랑",
            "c_name": "Restaurant",
            "c_description": "Restaurant",
        },
        {
            "c_business_code": "LIQUOR_STORE",
            "c_business_name": "Liquor Store",
            "c_business_name_kr": "리커스토어",
            "c_name": "Liquor Store",
            "c_description": "Liquor Store",
        },
        {
            "c_business_code": "SUPERMARKET",
            "c_business_name": "Supermarket",
            "c_business_name_kr": "슈퍼마켓",
            "c_name": "Supermarket",
            "c_description": "Supermarket",
        },
        {
            "c_business_code": "DELI",
            "c_business_name": "Deli",
            "c_business_name_kr": "델리",
            "c_name": "Deli",
            "c_description": "Deli",
        },
    ]
    for bt in business_types:
        if not db.query(BusinessType).filter_by(c_business_code=bt["c_business_code"]).first():
            db.add(BusinessType(**bt))

    # ===============================
    # Role Seed
    # ===============================
    from backend.models_admin.role import Role
    default_roles = [
        {"c_name": "SuperAdmin", "c_description": "System Administrator"},
        {"c_name": "Admin",      "c_description": "Store Administrator"},
        {"c_name": "Manager",    "c_description": "Store Manager"},
        {"c_name": "Staff",      "c_description": "Store Staff"},
        {"c_name": "Customer",   "c_description": "End Customer"},
    ]
    for r in default_roles:
        if not db.query(Role).filter_by(c_name=r["c_name"]).first():
            db.add(Role(**r))

    db.commit()

    # ===============================
    # Ensure Admin Store (platform namespace)
    # ===============================
    from backend.models_admin.store import Store
    import bcrypt

    platform_business_type = db.query(BusinessType).filter_by(c_business_code="PLATFORM").first()

    admin_store = db.query(Store).filter_by(i_store_id=ADMIN_STORE_ID).first()
    admin_store_pw = bcrypt.hashpw("AdminStorePw!".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    if not admin_store:
        admin_store = Store(
            i_store_id=ADMIN_STORE_ID,
            c_store_name="Admin",
            c_store_code="Admin",
            c_store_pw=admin_store_pw,
            i_business_type=platform_business_type.i_business_type_id if platform_business_type else None,
            c_operation_type="Platform Admin",
            c_channel_type="Admin",
            c_device_type="Server",
            c_status="active"
        )
        db.add(admin_store)
        db.commit()
        print(f"[OK] Admin store (ID={ADMIN_STORE_ID}) created")
    else:
        updated = False
        if admin_store.c_store_name != "Admin":
            admin_store.c_store_name = "Admin"
            updated = True
        if admin_store.i_business_type != (platform_business_type.i_business_type_id if platform_business_type else None):
            admin_store.i_business_type = platform_business_type.i_business_type_id if platform_business_type else None
            updated = True
        if admin_store.c_operation_type != "Platform Admin":
            admin_store.c_operation_type = "Platform Admin"
            updated = True
        if admin_store.c_channel_type != "Admin":
            admin_store.c_channel_type = "Admin"
            updated = True
        if admin_store.c_device_type != "Server":
            admin_store.c_device_type = "Server"
            updated = True
        if admin_store.c_store_code != "Admin":
            admin_store.c_store_code = "Admin"
            updated = True
        if updated:
            db.commit()
            print(f"[OK] Admin store (ID={ADMIN_STORE_ID}) updated")

    # ===============================
    # Data backfill: code normalization
    # ===============================
    stores = db.query(Store).order_by(Store.i_store_id).all()

    def _prefix_for_store(row: Store) -> str:
        if row.i_store_id == ADMIN_STORE_ID:
            return "PLT"
        return "CLT"

    # If Admin code is used by non-admin rows, relocate those rows to next PLATFORM code.
    duplicate_admin_code_rows = (
        db.query(Store)
        .filter(Store.c_store_code == "Admin", Store.i_store_id != ADMIN_STORE_ID)
        .order_by(Store.i_store_id)
        .all()
    )

    def _next_code(prefix: str, start: int):
        used_codes = {row.c_store_code for row in db.query(Store.c_store_code).all() if row.c_store_code}
        n = start
        while True:
            code = f"{prefix}-{n:05d}"
            if code not in used_codes:
                return code
            n += 1

    def _start_for_prefix(prefix: str) -> int:
        return 11001 if prefix == "CUS" else 1

    for row in duplicate_admin_code_rows:
        row.c_store_code = _next_code("PLT", 2)

    db.flush()

    # Fill missing codes according to platform/client prefix rule.
    for s in stores:
        if s.c_store_code:
            continue
        prefix = _prefix_for_store(s)
        if prefix == "PLT" and s.i_store_id == ADMIN_STORE_ID:
            s.c_store_code = "Admin"
        else:
            s.c_store_code = _next_code(prefix, _start_for_prefix(prefix))

    db.commit()

    # Ensure auto-increment for customer stores starts at CUSTOMER_STORE_ID_START
    try:
        with admin_engine.begin() as conn:
            current_seq = conn.execute(
                text("SELECT seq FROM sqlite_sequence WHERE name = 'tb_store' LIMIT 1")
            ).scalar()
            desired_seq = CUSTOMER_STORE_ID_START - 1
            if current_seq is None or current_seq < desired_seq:
                conn.execute(
                    text(
                        "INSERT OR REPLACE INTO sqlite_sequence(name, seq) VALUES ('tb_store', :seq)"
                    ),
                    {"seq": desired_seq},
                )
    except Exception:
        # If sqlite_sequence does not exist or is not applicable, ignore
        pass

    # ===============================
    # PlatformUser Seed (Platform SuperAdmin 기본 계정)
    # ===============================
    from backend.models_admin.platform_user import PlatformUser

    default_admin_email = "is2ceo@gmail.com"
    default_admin_password = "Awsum123!"
    default_admin_hash = bcrypt.hashpw(default_admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    existing_admin = db.query(PlatformUser).filter_by(c_email=default_admin_email, i_store_id=ADMIN_STORE_ID).first()
    if existing_admin:
        updated = False
        if existing_admin.i_store_id != ADMIN_STORE_ID:
            existing_admin.i_store_id = ADMIN_STORE_ID
            updated = True
        try:
            if not bcrypt.checkpw(default_admin_password.encode("utf-8"), existing_admin.c_password.encode("utf-8")):
                existing_admin.c_password = default_admin_hash
                updated = True
        except Exception:
            existing_admin.c_password = default_admin_hash
            updated = True
        if updated:
            db.commit()
            print(f"[OK] Default Platform SuperAdmin user ensured (email={default_admin_email})")
    else:
        seed_user = PlatformUser(
            c_email=default_admin_email,
            c_password=default_admin_hash,
            c_first_name="Platform",
            c_last_name="Admin",
            c_status="active",
            i_store_id=ADMIN_STORE_ID,
            i_must_change_password=0,
        )
        db.add(seed_user)
        db.commit()
        print(f"[OK] Default Platform SuperAdmin user created: {default_admin_email} / {default_admin_password}")

    db.close()
    print("[OK] AdminDB initialized with default Seed data")


# ==========================================
# 📌 Store ID helper
# ==========================================
def get_next_customer_store_id(db):
    """Returns the next store ID for customer stores (>= CUSTOMER_STORE_ID_START)."""
    from backend.models_admin.store import Store

    max_id = db.query(func.max(Store.i_store_id)).filter(Store.i_store_id >= CUSTOMER_STORE_ID_START).scalar()
    return CUSTOMER_STORE_ID_START if not max_id else max_id + 1


# ==========================================
# 📌 백업 경로 반환
# ==========================================
def get_admin_backup_dir():
    """Admin DB 백업 경로"""
    backup_dir = PROJECT_ROOT / "db_backups" / "platform"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return str(backup_dir)
