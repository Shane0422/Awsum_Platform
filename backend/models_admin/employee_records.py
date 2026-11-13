"""Employee Management STEP 1A-4: HISTORY (manager records) and BACKGROUND (education, work experience,
certifications & licenses, job skills) of an Employee.

Every row belongs to one Employee of one Merchant: the composite FK (i_employee_id, i_merchant_id) ->
tb_employee (uq_tb_employee_owner) keeps a record and its Employee in the same Merchant, exactly like
tb_employee_store. Common AuditMixin (created / updated by + at) and i_row_version like the other Employee tables.
Dates are DATE (API ISO; screens MM-DD-YYYY through the Common Date standard).

- tb_employee_history: a record written by a manager (not the automatic audit). No delete; only its author edits it.
- tb_employee_education / _experience / _certification / _skill: many per Employee; added, edited and deleted while
  the Employee is not terminated. A Skill is a JOB_SKILL Common Maintenance code of the Merchant (FK RESTRICT).
The BACKGROUND overall remark is tb_employee.c_background_remark (one per Employee).
"""
from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text

from backend.database.pg_platform import PlatformBase
from backend.models_admin import common_code  # noqa: F401  (registers tb_common_code for the Skill FK)
from backend.models_admin import employee  # noqa: F401  (registers tb_employee for the owner FK)
from backend.models_common.audit_mixin import AuditMixin

# Job Skill proficiency (code, label); fixed list in code like the Gender / POS Role lists.
SKILL_PROFICIENCIES = (("BEGINNER", "Beginner"), ("INTERMEDIATE", "Intermediate"), ("ADVANCED", "Advanced"),
                       ("EXPERT", "Expert"))
PROFICIENCY_CHECK = "c_proficiency IS NULL OR c_proficiency IN ({})".format(
    ", ".join(f"'{code}'" for code, _label in SKILL_PROFICIENCIES))


def _owner(table: str):
    """The Employee owner FK of a record table (same Merchant guaranteed by the database)."""
    return ForeignKeyConstraint(["i_employee_id", "i_merchant_id"], ["tb_employee.i_employee_id", "tb_employee.i_merchant_id"],
                                name=f"fk_{table}_employee", ondelete="RESTRICT")


class _EmployeeRecord(AuditMixin):
    i_employee_id = Column(Integer, nullable=False)
    i_merchant_id = Column(Integer, nullable=False)
    i_row_version = Column(Integer, nullable=False, default=1, server_default="1")


class EmployeeHistory(PlatformBase, _EmployeeRecord):
    __tablename__ = "tb_employee_history"
    __table_args__ = (_owner("tb_employee_history"),
                      Index("ix_tb_employee_history_employee", "i_employee_id", "d_record_date"))

    i_history_id = Column(Integer, primary_key=True, autoincrement=True)
    d_record_date = Column(Date, nullable=False)
    c_subject = Column(String(200), nullable=False)
    c_description = Column(Text, nullable=False)


class EmployeeEducation(PlatformBase, _EmployeeRecord):
    __tablename__ = "tb_employee_education"
    __table_args__ = (_owner("tb_employee_education"),
                      CheckConstraint("i_graduation_year IS NULL OR i_graduation_year BETWEEN 1900 AND 2100",
                                      name="ck_tb_employee_education_year"),
                      Index("ix_tb_employee_education_employee", "i_employee_id"))

    i_education_id = Column(Integer, primary_key=True, autoincrement=True)
    c_school = Column(String(200), nullable=False)
    c_degree = Column(String(100), nullable=True)
    c_major = Column(String(100), nullable=True)
    i_graduation_year = Column(Integer, nullable=True)
    c_remark = Column(Text, nullable=True)


class EmployeeExperience(PlatformBase, _EmployeeRecord):
    __tablename__ = "tb_employee_experience"
    __table_args__ = (_owner("tb_employee_experience"),
                      CheckConstraint("d_end IS NULL OR d_start IS NULL OR d_end >= d_start",
                                      name="ck_tb_employee_experience_dates"),
                      Index("ix_tb_employee_experience_employee", "i_employee_id"))

    i_experience_id = Column(Integer, primary_key=True, autoincrement=True)
    c_company = Column(String(200), nullable=False)
    c_position = Column(String(100), nullable=True)
    d_start = Column(Date, nullable=True)
    d_end = Column(Date, nullable=True)  # empty = currently working there
    c_description = Column(Text, nullable=True)
    c_remark = Column(Text, nullable=True)


class EmployeeCertification(PlatformBase, _EmployeeRecord):
    __tablename__ = "tb_employee_certification"
    __table_args__ = (_owner("tb_employee_certification"),
                      CheckConstraint("d_expires IS NULL OR d_issued IS NULL OR d_expires >= d_issued",
                                      name="ck_tb_employee_certification_dates"),
                      Index("ix_tb_employee_certification_employee", "i_employee_id"))

    i_certification_id = Column(Integer, primary_key=True, autoincrement=True)
    c_name = Column(String(200), nullable=False)
    c_issuer = Column(String(200), nullable=True)
    d_issued = Column(Date, nullable=True)
    d_expires = Column(Date, nullable=True)  # empty = no expiration
    c_remark = Column(Text, nullable=True)


class EmployeeSkill(PlatformBase, _EmployeeRecord):
    __tablename__ = "tb_employee_skill"
    __table_args__ = (_owner("tb_employee_skill"),
                      CheckConstraint(PROFICIENCY_CHECK, name="ck_tb_employee_skill_proficiency"),
                      Index("ix_tb_employee_skill_employee", "i_employee_id"))

    i_skill_id = Column(Integer, primary_key=True, autoincrement=True)
    i_skill_code_id = Column(Integer, ForeignKey("tb_common_code.i_common_code_id", ondelete="RESTRICT",
                                                 name="fk_tb_employee_skill_code"), nullable=False, index=True)
    c_proficiency = Column(String(20), nullable=True)
    c_remark = Column(Text, nullable=True)


RECORD_MODELS = (EmployeeHistory, EmployeeEducation, EmployeeExperience, EmployeeCertification, EmployeeSkill)
RECORD_TABLES = tuple(model.__tablename__ for model in RECORD_MODELS)
