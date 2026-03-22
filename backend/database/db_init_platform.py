import bcrypt
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database.pg_platform import (
    ADMIN_STORE_ID,
    PlatformBase,
    PlatformSessionLocal,
    platform_engine,
)
from backend.utils.safe_schema_migrate import safe_schema_migrate


CLIENT_CODE_PREFIX = "CLT_"
CLIENT_CODE_SEQ_START = 11001


def _drop_legacy_tb_user_dependencies(db: Session) -> None:
    """Remove legacy FK constraints that still reference tb_user."""
    if db.execute(text("SELECT to_regclass('public.tb_user')")).scalar() is None:
        return

    legacy_fks = db.execute(
        text(
            """
            SELECT conname, conrelid::regclass::text AS table_name
            FROM pg_constraint
            WHERE confrelid = 'tb_user'::regclass
            ORDER BY conrelid::regclass::text, conname
            """
        )
    ).fetchall()

    for conname, table_name in legacy_fks:
        db.execute(text(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {conname}"))

    if legacy_fks:
        print(f"[CLEANUP] Dropped {len(legacy_fks)} legacy tb_user FK constraints")


def _drop_legacy_tb_user_table(db: Session) -> None:
    """Drop legacy tb_user table after constraints are removed."""
    if db.execute(text("SELECT to_regclass('public.tb_user')")).scalar() is None:
        return

    db.execute(text("DROP TABLE IF EXISTS tb_user"))
    print("[CLEANUP] Dropped legacy table tb_user")


def _drop_legacy_session_user_column(db: Session) -> None:
    """Drop obsolete tb_session.i_user_id to enforce platform_user-only sessions."""
    has_column = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'tb_session'
              AND column_name = 'i_user_id'
            LIMIT 1
            """
        )
    ).scalar()

    if not has_column:
        return

    db.execute(text("ALTER TABLE tb_session DROP COLUMN IF EXISTS i_user_id CASCADE"))
    print("[CLEANUP] Dropped legacy column tb_session.i_user_id")


def _drop_legacy_store_type_dependencies(db: Session) -> None:
    """Remove obsolete StoreType FK/column/table after moving classification to tb_store fields."""
    has_store_type_col = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name='tb_store'
              AND column_name='i_store_type_id'
            LIMIT 1
            """
        )
    ).scalar()

    if has_store_type_col:
        db.execute(text("ALTER TABLE tb_store DROP COLUMN IF EXISTS i_store_type_id CASCADE"))
        print("[CLEANUP] Dropped legacy column tb_store.i_store_type_id")

    if db.execute(text("SELECT to_regclass('public.tb_store_type')")).scalar() is not None:
        db.execute(text("DROP TABLE IF EXISTS tb_store_type CASCADE"))
        print("[CLEANUP] Dropped legacy table tb_store_type")


def _ensure_client_code_schema_and_backfill(db: Session) -> None:
    """Ensure tb_client.c_client_code exists, is populated, unique, and not null."""
    has_column = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'tb_client'
              AND column_name = 'c_client_code'
            LIMIT 1
            """
        )
    ).scalar()

    if not has_column:
        db.execute(text("ALTER TABLE tb_client ADD COLUMN c_client_code VARCHAR(20)"))
        print("[MIGRATE] Added tb_client.c_client_code")

    # Serialize startup-side migration so constraints/backfill are deterministic.
    db.execute(text("LOCK TABLE tb_client IN ACCESS EXCLUSIVE MODE"))

    # If there are accidental duplicates, null out all but the first so they can be re-generated.
    db.execute(
        text(
            """
            WITH ranked AS (
                SELECT i_client_id,
                       c_client_code,
                       ROW_NUMBER() OVER (PARTITION BY c_client_code ORDER BY i_client_id ASC) AS rn
                FROM tb_client
                WHERE c_client_code IS NOT NULL
                  AND btrim(c_client_code) <> ''
            )
            UPDATE tb_client AS tgt
            SET c_client_code = NULL
            FROM ranked
            WHERE tgt.i_client_id = ranked.i_client_id
              AND ranked.rn > 1
            """
        )
    )

    # Fill missing codes by i_client_id order starting at CLT_11001 and continuing after current max.
    db.execute(
        text(
            """
            WITH max_seq AS (
                SELECT COALESCE(
                    MAX(CAST(SUBSTRING(c_client_code FROM '([0-9]+)$') AS INTEGER)),
                    :seed_start - 1
                ) AS current_max
                FROM tb_client
                WHERE c_client_code ~ '^CLT_[0-9]+$'
            ),
            missing AS (
                SELECT i_client_id,
                       ROW_NUMBER() OVER (ORDER BY i_client_id ASC) AS rn
                FROM tb_client
                WHERE c_client_code IS NULL OR btrim(c_client_code) = ''
            )
            UPDATE tb_client AS tgt
            SET c_client_code = :prefix || LPAD((max_seq.current_max + missing.rn)::text, 5, '0')
            FROM missing, max_seq
            WHERE tgt.i_client_id = missing.i_client_id
            """
        ),
        {
            "prefix": CLIENT_CODE_PREFIX,
            "seed_start": CLIENT_CODE_SEQ_START,
        },
    )

    db.execute(
        text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'uq_tb_client_c_client_code'
                      AND conrelid = 'tb_client'::regclass
                ) THEN
                    ALTER TABLE tb_client
                    ADD CONSTRAINT uq_tb_client_c_client_code UNIQUE (c_client_code);
                END IF;
            END $$;
            """
        )
    )

    db.execute(text("ALTER TABLE tb_client ALTER COLUMN c_client_code SET NOT NULL"))

    migrated_rows = db.execute(
        text(
            """
            SELECT COUNT(*)
            FROM tb_client
            WHERE c_client_code ~ '^CLT_[0-9]+$'
            """
        )
    ).scalar() or 0
    print(f"[MIGRATE] tb_client.c_client_code populated rows: {migrated_rows}")


def _ensure_client_primary_agent_schema(db: Session) -> None:
    """Ensure tb_client.i_agent_id exists and links to tb_agent when available."""
    has_column = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'tb_client'
              AND column_name = 'i_agent_id'
            LIMIT 1
            """
        )
    ).scalar()

    if not has_column:
        db.execute(text("ALTER TABLE tb_client ADD COLUMN i_agent_id INTEGER"))
        print("[MIGRATE] Added tb_client.i_agent_id")

    db.execute(text("CREATE INDEX IF NOT EXISTS ix_tb_client_i_agent_id ON tb_client (i_agent_id)"))

    has_agent_table = db.execute(text("SELECT to_regclass('public.tb_agent')")).scalar() is not None
    if not has_agent_table:
        return

    db.execute(
        text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'fk_tb_client_i_agent_id_tb_agent'
                      AND conrelid = 'tb_client'::regclass
                ) THEN
                    ALTER TABLE tb_client
                    ADD CONSTRAINT fk_tb_client_i_agent_id_tb_agent
                    FOREIGN KEY (i_agent_id) REFERENCES tb_agent(i_agent_id)
                    ON DELETE SET NULL;
                END IF;
            END $$;
            """
        )
    )


def _next_client_code_value(db: Session) -> str:
    max_seq = db.execute(
        text(
            """
            SELECT COALESCE(
                MAX(CAST(SUBSTRING(c_client_code FROM '([0-9]+)$') AS INTEGER)),
                :seed_start - 1
            ) AS current_max
            FROM tb_client
            WHERE c_client_code ~ '^CLT_[0-9]+$'
            """
        ),
        {"seed_start": CLIENT_CODE_SEQ_START},
    ).scalar() or (CLIENT_CODE_SEQ_START - 1)
    return f"{CLIENT_CODE_PREFIX}{int(max_seq) + 1:05d}"


def init_platform_db() -> None:
    """Create platform tables in PostgreSQL and seed baseline data."""
    from backend.models_admin import (
        account,
        business_type,
        store,
        platform_user,
        session,
        role,
        license,
        device,
        device_log,
        agent,
        agent_type,
        billing,
        payment_method,
        subscription,
        provision_log,
        store_sync_status,
    )
    from backend.models_admin.account import Client
    from backend.models_admin.business_type import BusinessType
    from backend.models_admin.role import Role
    from backend.models_admin.agent_type import AgentType
    from backend.models_admin.store import Store
    from backend.models_admin.platform_user import PlatformUser

    # Align existing schema non-destructively before seed to avoid startup failures
    # when older DBs miss newly added nullable columns.
    safe_schema_migrate(PlatformBase, platform_engine, backup_dir="./db_backups/platform")

    db = PlatformSessionLocal()
    try:
        # Enforce platform-user-only auth policy by removing legacy tb_user dependencies.
        _drop_legacy_tb_user_dependencies(db)
        _drop_legacy_tb_user_table(db)
        _drop_legacy_session_user_column(db)
        _drop_legacy_store_type_dependencies(db)
        _ensure_client_code_schema_and_backfill(db)
        _ensure_client_primary_agent_schema(db)
        db.commit()

        # Client seed
        if not db.query(Client).filter_by(c_client_name="SystemClient").first():
            db.add(
                Client(
                    c_client_code=_next_client_code_value(db),
                    c_client_name="SystemClient",
                    c_first_name="System",
                    c_last_name="Client",
                    c_email="client@system.local",
                    c_status="active",
                )
            )

        business_types = [
            {"c_business_code": "PLATFORM", "c_name": "Platform", "c_business_name_kr": "플랫폼", "c_description": "Platform"},
            {"c_business_code": "TUXEDO_RENTAL", "c_name": "Tuxedo Rental", "c_business_name_kr": "턱시도 렌탈", "c_description": "Tuxedo Rental"},
            {"c_business_code": "RESTAURANT", "c_name": "Restaurant", "c_business_name_kr": "레스토랑", "c_description": "Restaurant"},
            {"c_business_code": "LIQUOR_STORE", "c_name": "Liquor Store", "c_business_name_kr": "리커스토어", "c_description": "Liquor Store"},
            {"c_business_code": "SUPERMARKET", "c_name": "Supermarket", "c_business_name_kr": "슈퍼마켓", "c_description": "Supermarket"},
            {"c_business_code": "DELI", "c_name": "Deli", "c_business_name_kr": "델리", "c_description": "Deli"},
        ]
        for bt in business_types:
            existing = db.query(BusinessType).filter_by(c_business_code=bt["c_business_code"]).first()
            if not existing:
                db.add(BusinessType(**bt))

        default_roles = [
            {"c_name": "SuperAdmin", "c_description": "System Administrator"},
            {"c_name": "Admin", "c_description": "Store Administrator"},
            {"c_name": "CustomerAdmin", "c_description": "Customer Store Administrator"},
            {"c_name": "Manager", "c_description": "Store Manager"},
            {"c_name": "Staff", "c_description": "Store Staff"},
            {"c_name": "Customer", "c_description": "End Customer"},
        ]
        for role_item in default_roles:
            if not db.query(Role).filter_by(c_name=role_item["c_name"]).first():
                db.add(Role(**role_item))

        default_agent_types = [
            {"c_agent_type_code": "DEALER", "c_agent_type_name": "Dealer", "c_status": "active"},
            {"c_agent_type_code": "INSTALLER", "c_agent_type_name": "Installer", "c_status": "active"},
        ]
        for type_item in default_agent_types:
            existing = db.query(AgentType).filter_by(c_agent_type_code=type_item["c_agent_type_code"]).first()
            if not existing:
                db.add(AgentType(**type_item))

        db.commit()

        platform_business_type = db.query(BusinessType).filter_by(c_business_code="PLATFORM").first()
        admin_store_pw = bcrypt.hashpw("AdminStorePw!".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        admin_store = db.query(Store).filter_by(i_store_id=ADMIN_STORE_ID).first()
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
                c_status="active",
            )
            db.add(admin_store)
        else:
            admin_store.c_store_name = "Admin"
            admin_store.c_store_code = "Admin"
            admin_store.i_business_type = platform_business_type.i_business_type_id if platform_business_type else None
            admin_store.c_operation_type = "Platform Admin"
            admin_store.c_channel_type = "Admin"
            admin_store.c_device_type = "Server"

        default_admin_email = "is2ceo@gmail.com"
        default_admin_password = "Awsum123!"
        default_admin_hash = bcrypt.hashpw(default_admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        superadmin_role = db.query(Role).filter_by(c_name="SuperAdmin").first()

        # Platform admin auth source of truth (tb_platform_user)
        existing_platform_admin = (
            db.query(PlatformUser)
            .filter_by(c_email=default_admin_email, i_store_id=ADMIN_STORE_ID)
            .first()
        )
        if existing_platform_admin:
            existing_platform_admin.c_status = "active"
            existing_platform_admin.i_store_id = ADMIN_STORE_ID
            existing_platform_admin.i_must_change_password = 0
            try:
                if not bcrypt.checkpw(default_admin_password.encode("utf-8"), existing_platform_admin.c_password.encode("utf-8")):
                    existing_platform_admin.c_password = default_admin_hash
            except Exception:
                existing_platform_admin.c_password = default_admin_hash
            if not existing_platform_admin.c_first_name:
                existing_platform_admin.c_first_name = "Platform"
            if not existing_platform_admin.c_last_name:
                existing_platform_admin.c_last_name = "Admin"
        else:
            db.add(
                PlatformUser(
                    i_store_id=ADMIN_STORE_ID,
                    c_email=default_admin_email,
                    c_password=default_admin_hash,
                    c_first_name="Platform",
                    c_last_name="Admin",
                    c_status="active",
                    i_must_change_password=0,
                )
            )

        # Fixed test client store (undeletable by API)
        tuxedo_business_type = db.query(BusinessType).filter_by(c_business_code="TUXEDO_RENTAL").first()
        test_store_pw = bcrypt.hashpw("Cust00001".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        target_test_store_code = "CLT_00001"
        legacy_test_store_code = "CUST_00001"

        test_store = db.query(Store).filter_by(c_store_code=target_test_store_code).first()
        if not test_store:
            test_store = db.query(Store).filter_by(c_store_code=legacy_test_store_code).first()
            if test_store:
                test_store.c_store_code = target_test_store_code
        # Fallback: find by c_is_test flag in case the code was renamed externally
        if not test_store:
            test_store = (
                db.query(Store)
                .filter(Store.c_is_test == 1, Store.i_store_id != ADMIN_STORE_ID)
                .first()
            )
            if test_store:
                test_store.c_store_code = target_test_store_code

        if not test_store:
            test_store = Store(
                c_store_code=target_test_store_code,
                i_store_seq=1,
                c_store_name="Test Customer Store",
                c_store_pw=test_store_pw,
                c_status="active",
                c_is_test=1,
                c_operation_type="Information POS",
                c_channel_type="POS",
                c_device_type="Windows POS",
            )
            if tuxedo_business_type:
                test_store.i_business_type = tuxedo_business_type.i_business_type_id
            db.add(test_store)
            db.flush()
        else:
            test_store.c_store_name = "Test Customer Store"
            test_store.c_is_test = 1
            test_store.i_store_seq = 1
            if tuxedo_business_type:
                test_store.i_business_type = tuxedo_business_type.i_business_type_id
            test_store.c_operation_type = "Information POS"
            test_store.c_channel_type = "POS"
            test_store.c_device_type = "Windows POS"
            try:
                if not bcrypt.checkpw("Cust00001".encode("utf-8"), (test_store.c_store_pw or "").encode("utf-8")):
                    test_store.c_store_pw = test_store_pw
            except Exception:
                test_store.c_store_pw = test_store_pw

        db.commit()

        # ensure admin store id sequence doesn't collide with manual ADMIN_STORE_ID insert
        db.execute(text("SELECT setval(pg_get_serial_sequence('tb_store','i_store_id'), GREATEST((SELECT COALESCE(MAX(i_store_id),1) FROM tb_store), 1100000))"))
        db.commit()

        print("[OK] awsum_platform initialized with default seed data")
    finally:
        db.close()
