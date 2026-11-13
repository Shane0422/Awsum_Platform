"""Employee Management (STEP 1A): the Merchant-wide Employee Master and its Store assignments.

Employees are not Application Users (docs/AWSUM_GLOBAL_PATH_STANDARD.md): most use only POS.
STEP 1A-2 (login IDs, Merchant scope):
- Portal Login: an optional tb_platform_user account of this Merchant (role "Employee"), linked by i_platform_user_id.
  Its email is the Portal Login ID (unique per Merchant: tb_platform_user uq_platform_user_merchant_email) and its
  bcrypt password lives only there. Portal Roles / login permissions come in a later step.
- POS Login: c_pos_login_id (digits, unique per Merchant: uq_tb_employee_pos_login), shared by every Store of the
  Merchant, and the PIN as a bcrypt hash only (c_pin_hash). PIN length / lockout / offline rules come in STEP 2.
- Address: the common AddressMixin columns (independent of the Merchant / Store address).
STEP 1A-3 (Job Grade / Job Title): one optional JOB_GRADE and one JOB_TITLE code of the Employee's Merchant
(tb_common_code, Common Maintenance), the same plain FK as MdDetail.i_color_code_id; the service checks the group and
the Merchant. Portal only (never POS / receipts).
STEP 1A-3 Personal Information: optional Birthday (d_birth_date) and Gender (c_gender, a code of EMPLOYEE_GENDERS, a
fixed list in code like the POS Role Registry; not a Common Maintenance group). Portal only: never in the Employee list
display, the export, POS or receipts.

Rows are never deleted (FKs RESTRICT): termination ends every Store assignment and keeps all history.
Merchant ownership is enforced by the database: an assignment's Employee and Store must belong to the same Merchant
(composite FKs on (id, i_merchant_id)), and Portal Roles are referenced with their scope (MERCHANT / STORE).
"""
from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index,
                        Integer, String, Text, UniqueConstraint, text)

from backend.database.pg_platform import PlatformBase
from backend.models_admin import common_code  # noqa: F401  (registers tb_common_code for the Job Grade / Title FKs)
from backend.models_common.address_mixin import AddressMixin
from backend.models_common.audit_mixin import AuditMixin

EMPLOYEE_STATUSES = ("active", "inactive", "terminated")
ASSIGNMENT_STATUSES = ("active", "inactive", "ended")
# Gender (code, label); NULL = not selected. The stored code is checked by ck_tb_employee_gender.
EMPLOYEE_GENDERS = (("MALE", "Male"), ("FEMALE", "Female"))
GENDER_CHECK = "c_gender IS NULL OR c_gender IN ({})".format(", ".join(f"'{code}'" for code, _label in EMPLOYEE_GENDERS))


class Employee(PlatformBase, AddressMixin, AuditMixin):
    __tablename__ = "tb_employee"
    __table_args__ = (
        UniqueConstraint("i_merchant_id", "c_employee_code", name="uq_tb_employee_code"),
        UniqueConstraint("i_merchant_id", "i_pos_number", name="uq_tb_employee_pos_number"),
        UniqueConstraint("i_employee_id", "i_merchant_id", name="uq_tb_employee_owner"),
        UniqueConstraint("i_platform_user_id", name="uq_tb_employee_user"),
        # POS Login ID: unique per Merchant (NULL = not set; one ID for every Store of the Merchant).
        Index("uq_tb_employee_pos_login", "i_merchant_id", "c_pos_login_id", unique=True),
        CheckConstraint("c_pos_login_id IS NULL OR length(c_pos_login_id) BETWEEN 4 AND 10",
                        name="ck_tb_employee_pos_login_id"),
        CheckConstraint("NOT b_pos_login_enabled OR c_pos_login_id IS NOT NULL", name="ck_tb_employee_pos_login"),
        ForeignKeyConstraint(["i_merchant_portal_role_id", "i_merchant_id", "c_merchant_portal_scope"],
                             ["tb_portal_role.i_portal_role_id", "tb_portal_role.i_merchant_id",
                              "tb_portal_role.c_scope"], name="fk_tb_employee_merchant_portal_role"),
        CheckConstraint("c_status IN ('active', 'inactive', 'terminated')", name="ck_tb_employee_status"),
        CheckConstraint("c_merchant_portal_scope = 'MERCHANT'", name="ck_tb_employee_portal_scope"),
        CheckConstraint("i_pos_number BETWEEN 1 AND 999999", name="ck_tb_employee_pos_number"),
        CheckConstraint("coalesce(trim(c_first_name), '') <> '' OR coalesce(trim(c_last_name), '') <> ''",
                        name="ck_tb_employee_name"),
        CheckConstraint("c_status <> 'terminated' OR d_termination_date IS NOT NULL", name="ck_tb_employee_terminated"),
        CheckConstraint("d_termination_date IS NULL OR d_hire_date IS NULL OR d_termination_date >= d_hire_date",
                        name="ck_tb_employee_dates"),
        CheckConstraint(GENDER_CHECK, name="ck_tb_employee_gender"),
    )

    i_employee_id = Column(Integer, primary_key=True, autoincrement=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id", ondelete="RESTRICT"), nullable=False,
                           index=True)
    c_employee_code = Column(String(32), nullable=False)   # EMP00001 per Merchant, immutable (photo file name)
    i_pos_number = Column(Integer, nullable=False)          # internal number per Merchant (1001...), immutable
    c_first_name = Column(String(100), nullable=True)
    c_last_name = Column(String(100), nullable=True)
    c_display_name = Column(String(40), nullable=True)      # POS / receipt name
    c_phone = Column(String(40), nullable=True)
    c_email = Column(String(254), nullable=True)            # contact only (the Portal Login ID is the account email)
    c_image_path = Column(String(1000), nullable=True)      # /merchant/{M}/images/employee/{code}.webp
    d_hire_date = Column(Date, nullable=True)
    d_birth_date = Column(Date, nullable=True)              # Personal Information (optional, never in the future)
    c_gender = Column(String(20), nullable=True)            # EMPLOYEE_GENDERS code (optional)
    c_background_remark = Column(Text, nullable=True)       # BACKGROUND overall remark (STEP 1A-4, one per Employee)
    i_job_grade_code_id = Column(Integer, ForeignKey("tb_common_code.i_common_code_id", ondelete="RESTRICT",
                                                     name="fk_tb_employee_job_grade_code"), nullable=True, index=True)
    i_job_title_code_id = Column(Integer, ForeignKey("tb_common_code.i_common_code_id", ondelete="RESTRICT",
                                                     name="fk_tb_employee_job_title_code"), nullable=True, index=True)
    d_termination_date = Column(Date, nullable=True)
    c_termination_reason = Column(String(200), nullable=True)
    c_status = Column(String(12), nullable=False, default="active")
    # STEP 1A-2: POS Login (ID per Merchant, editable; suggested from the phone's last 4 digits on screen).
    b_pos_login_enabled = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    c_pos_login_id = Column(String(10), nullable=True)
    # STEP 1A-2: Portal Login account (at most one per Employee; tb_platform_user, role "Employee").
    i_platform_user_id = Column(Integer, ForeignKey("tb_platform_user.i_platform_user_id", ondelete="RESTRICT"),
                                nullable=True)
    # STEP 1B: Merchant Portal access (a MERCHANT Portal Role of this Merchant; the scope column pins it).
    i_merchant_portal_role_id = Column(Integer, nullable=True)
    c_merchant_portal_scope = Column(String(10), nullable=False, default="MERCHANT", server_default="MERCHANT")
    # POS PIN (bcrypt hash only; set / reset in STEP 1A-2, policy in STEP 2) and the Store Server sync versions.
    c_pin_hash = Column(String(100), nullable=True)
    i_pin_version = Column(Integer, nullable=False, default=0, server_default="0")
    dt_pin_changed = Column(DateTime, nullable=True)
    i_pin_changed_by = Column(Integer, nullable=True)
    i_pin_failed_count = Column(Integer, nullable=False, default=0, server_default="0")
    dt_pin_locked_until = Column(DateTime, nullable=True)
    i_row_version = Column(Integer, nullable=False, default=1, server_default="1")


class EmployeeStore(PlatformBase, AuditMixin):
    """One Store assignment: Store Portal access (STEP 1B) and POS access + POS Role (code from services.pos_roles)."""
    __tablename__ = "tb_employee_store"
    __table_args__ = (
        ForeignKeyConstraint(["i_employee_id", "i_merchant_id"], ["tb_employee.i_employee_id", "tb_employee.i_merchant_id"],
                             name="fk_tb_employee_store_employee", ondelete="RESTRICT"),
        ForeignKeyConstraint(["i_store_id", "i_merchant_id"], ["tb_store.i_store_id", "tb_store.i_merchant_id"],
                             name="fk_tb_employee_store_store", ondelete="RESTRICT"),
        ForeignKeyConstraint(["i_store_portal_role_id", "i_merchant_id", "c_store_portal_scope"],
                             ["tb_portal_role.i_portal_role_id", "tb_portal_role.i_merchant_id",
                              "tb_portal_role.c_scope"], name="fk_tb_employee_store_portal_role"),
        CheckConstraint("c_status IN ('active', 'inactive', 'ended')", name="ck_tb_employee_store_status"),
        CheckConstraint("c_store_portal_scope = 'STORE'", name="ck_tb_employee_store_portal_scope"),
        CheckConstraint("NOT b_pos_access OR c_pos_role IS NOT NULL", name="ck_tb_employee_store_pos_role"),
        CheckConstraint("d_end IS NULL OR d_end >= d_start", name="ck_tb_employee_store_dates"),
        CheckConstraint("c_status <> 'ended' OR d_end IS NOT NULL", name="ck_tb_employee_store_ended"),
        # One open (not ended) assignment per Employee and Store; ended periods stay as history rows.
        Index("uq_tb_employee_store_open", "i_employee_id", "i_store_id", unique=True,
              postgresql_where=text("c_status <> 'ended'"), sqlite_where=text("c_status <> 'ended'")),
        # At most one active primary Store per Employee.
        Index("uq_tb_employee_store_primary", "i_employee_id", unique=True,
              postgresql_where=text("b_primary AND c_status = 'active'"),
              sqlite_where=text("b_primary AND c_status = 'active'")),
        Index("ix_tb_employee_store_store", "i_store_id", "c_status"),
    )

    i_employee_store_id = Column(Integer, primary_key=True, autoincrement=True)
    i_employee_id = Column(Integer, nullable=False)
    i_store_id = Column(Integer, nullable=False)
    i_merchant_id = Column(Integer, nullable=False)
    i_store_portal_role_id = Column(Integer, nullable=True)  # STEP 1B
    c_store_portal_scope = Column(String(10), nullable=False, default="STORE", server_default="STORE")
    b_pos_access = Column(Boolean, nullable=False, default=False)
    c_pos_role = Column(String(30), nullable=True)
    b_primary = Column(Boolean, nullable=False, default=False)
    c_status = Column(String(10), nullable=False, default="active")
    d_start = Column(Date, nullable=False)
    d_end = Column(Date, nullable=True)
    i_row_version = Column(Integer, nullable=False, default=1, server_default="1")


class EmployeePolicy(PlatformBase, AuditMixin):
    """Store Admin capabilities (STEP 1D; all OFF by default). i_store_id NULL = the Merchant default; a Store row
    overrides it for that Store. Only the Merchant changes these; the code ceiling still applies."""
    __tablename__ = "tb_employee_policy"
    __table_args__ = (
        ForeignKeyConstraint(["i_store_id", "i_merchant_id"], ["tb_store.i_store_id", "tb_store.i_merchant_id"],
                             name="fk_tb_employee_policy_store"),
        Index("uq_tb_employee_policy_merchant", "i_merchant_id", unique=True,
              postgresql_where=text("i_store_id IS NULL"), sqlite_where=text("i_store_id IS NULL")),
        Index("uq_tb_employee_policy_store", "i_store_id", unique=True,
              postgresql_where=text("i_store_id IS NOT NULL"), sqlite_where=text("i_store_id IS NOT NULL")),
    )

    i_policy_id = Column(Integer, primary_key=True, autoincrement=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id", ondelete="RESTRICT"), nullable=False)
    i_store_id = Column(Integer, nullable=True)
    b_sa_pos_role_change = Column(Boolean, nullable=False, default=False)
    b_sa_pin_reset = Column(Boolean, nullable=False, default=False)
    b_sa_pos_access = Column(Boolean, nullable=False, default=False)
    b_sa_portal_grant = Column(Boolean, nullable=False, default=False)
