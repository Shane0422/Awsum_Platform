import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ==========================================
# 📂 경로 정의
# 실행 위치: D:\Awsum_Projects\tuxedo_rental\backend\main.py
# Admin DB 파일: D:\Awsum_Projects\tuxedo_rental\db_admin\db_Admin.db
# 백업 폴더:    D:\Awsum_Projects\tuxedo_rental\db_admin\backups\
# ==========================================

# ⚙️ backend/database → ../../ 로 올라가면 프로젝트 루트
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

# 📌 Admin DB 저장소 경로
ADMIN_DIR = os.path.join(PROJECT_ROOT, "db_admin")
os.makedirs(ADMIN_DIR, exist_ok=True)

DB_PATH = os.path.join(ADMIN_DIR, "db_Admin.db")

# ==========================================
# 📌 SQLAlchemy 세팅
# ==========================================
admin_engine = create_engine(f"sqlite:///{DB_PATH}", echo=True, future=True)
AdminSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=admin_engine)
AdminBase = declarative_base()


# ==========================================
# 📌 Admin DB 초기화 함수
# ==========================================
def init_admin_db():
    """AdminDB 테이블 생성 및 기본 Seed 데이터 삽입"""
    from backend.models_admin import account, store_type, business_type, store, user, session, role

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
    # StoreType Seed
    # ===============================
    from backend.models_admin.store_type import StoreType
    default_store_types = [
        {"c_name": "HeadOffice", "c_description": "Head Office"},
        {"c_name": "Branch",     "c_description": "Branch Office"},
        {"c_name": "Agency",     "c_description": "Agency"},
        {"c_name": "Partner",    "c_description": "Partner"},
    ]
    for st in default_store_types:
        if not db.query(StoreType).filter_by(c_name=st["c_name"]).first():
            db.add(StoreType(**st))

    # ===============================
    # BusinessType Seed
    # ===============================
    from backend.models_admin.business_type import BusinessType
    default_business_types = [
        {"c_name": "Laundry",    "c_description": "Laundry Service"},
        {"c_name": "Wedding",    "c_description": "Wedding & Tuxedo Rental"},
        {"c_name": "FlowerShop", "c_description": "Flower Shop"},
        {"c_name": "CakeShop",   "c_description": "Cake Shop"},
        {"c_name": "Other",      "c_description": "Other Services"},
    ]
    for bt in default_business_types:
        if not db.query(BusinessType).filter_by(c_name=bt["c_name"]).first():
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
    # User Seed (SuperAdmin 기본 계정)
    # ===============================
    from backend.models_admin.user import User
    from passlib.hash import bcrypt

    if not db.query(User).first():  # tb_user 비어있을 때만
        superadmin_role = db.query(Role).filter_by(c_name="SuperAdmin").first()
        seed_user = User(
            c_email="admin@system.local",
            c_password=bcrypt.hash("Admin123!"),
            c_first_name="System",
            c_last_name="Admin",
            c_status="active",
            i_role_id=superadmin_role.i_role_id if superadmin_role else 1
        )
        db.add(seed_user)
        db.commit()
        print("✅ Default SuperAdmin user created: admin@system.local / Admin123!")

    db.close()
    print("✅ AdminDB initialized with default Seed data")


# ==========================================
# 📌 백업 경로 반환
# ==========================================
def get_admin_backup_dir():
    """Admin DB 백업 경로"""
    backup_dir = os.path.join(ADMIN_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir
