"""Common Maintenance: shared code groups (COLOR, UNIT, JOB_GRADE, JOB_TITLE) and their codes.

tb_common_group defines a group: how its codes are issued (AUTO prefix + number, or MANUAL upper case) and
its optional typed Value (TEXT / NUMBER / CURRENCY / COLOR, NULL = no Value).
tb_common_code holds the codes. A Value lives in exactly one typed column: c_value_text (TEXT, COLOR
"#RRGGBB") or n_value NUMERIC(18,6) (NUMBER, CURRENCY; exact, never FLOAT). NULL means "no value", 0 is a value.

i_merchant_id NULL = Platform code; set = that Merchant's own code (copied once from Platform, see tb_merchant_common_init).
SIZE and ITEM_KIND are not Common Maintenance groups (Size stays free text, Item Kind is its own master).
"""
from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, Integer, Numeric, String, Text, text

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin

COMMON_VALUE_TYPES = ("TEXT", "NUMBER", "CURRENCY", "COLOR")
COMMON_CODE_MODES = ("AUTO", "MANUAL")


def _in(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class CommonGroup(PlatformBase, AuditMixin):
    __tablename__ = "tb_common_group"
    __table_args__ = (
        CheckConstraint(_in("c_code_mode", COMMON_CODE_MODES), name="ck_tb_common_group_code_mode"),
        CheckConstraint(f"c_value_type IS NULL OR {_in('c_value_type', COMMON_VALUE_TYPES)}",
                        name="ck_tb_common_group_value_type"),
    )

    i_common_group_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_group_code = Column(String(30), nullable=False, unique=True, index=True)  # COLOR / UNIT / JOB_GRADE / JOB_TITLE
    c_group_name = Column(String(100), nullable=False)
    c_description = Column(String(200), nullable=True)
    c_code_mode = Column(String(10), nullable=False, default="MANUAL")  # AUTO (prefix + number) / MANUAL
    c_code_prefix = Column(String(10), nullable=True)  # AUTO only, e.g. CLR
    i_code_max_length = Column(Integer, nullable=False, default=30)
    c_value_type = Column(String(10), nullable=True)  # TEXT / NUMBER / CURRENCY / COLOR; NULL = no Value
    c_value_label = Column(String(50), nullable=True)  # e.g. "Swatch HEX"
    b_value_required = Column(Boolean, nullable=False, default=False)
    i_value_scale = Column(Integer, nullable=True)  # NUMBER decimal places (CURRENCY is always 2)
    n_value_min = Column(Numeric(18, 6), nullable=True)
    n_value_max = Column(Numeric(18, 6), nullable=True)
    i_text_max_length = Column(Integer, nullable=True)  # TEXT (<= 200)
    b_merchant_extendable = Column(Boolean, nullable=False, default=False)  # always False for now
    i_display_order = Column(Integer, nullable=False, default=100)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)


class CommonCode(PlatformBase, AuditMixin):
    __tablename__ = "tb_common_code"
    __table_args__ = (
        # Platform codes: unique per group. Merchant codes (later): unique per group and merchant.
        Index("ux_tb_common_code_platform_code", "i_common_group_id", "c_code", unique=True,
              postgresql_where=text("i_merchant_id IS NULL"), sqlite_where=text("i_merchant_id IS NULL")),
        Index("ux_tb_common_code_merchant_code", "i_common_group_id", "i_merchant_id", "c_code", unique=True,
              postgresql_where=text("i_merchant_id IS NOT NULL"), sqlite_where=text("i_merchant_id IS NOT NULL")),
        CheckConstraint("c_value_text IS NULL OR n_value IS NULL", name="ck_tb_common_code_single_value"),
    )

    i_common_code_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_common_group_id = Column(Integer, ForeignKey("tb_common_group.i_common_group_id"), nullable=False, index=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id"), nullable=True, index=True)
    c_code = Column(String(30), nullable=False)
    c_name = Column(String(100), nullable=False)
    c_description = Column(String(200), nullable=True)
    c_value_text = Column(String(200), nullable=True)  # TEXT / COLOR (#RRGGBB)
    n_value = Column(Numeric(18, 6), nullable=True)  # NUMBER / CURRENCY
    i_display_order = Column(Integer, nullable=False, default=100)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)


class MerchantCommonInit(PlatformBase, AuditMixin):
    """One row per Merchant and Common Group once its Platform defaults were copied (services.master_group.
    initialize_merchant_common_codes). The row, never a code count, decides "initialized": codes a Merchant
    deletes later are never copied again, and Platform changes never reach an initialized Merchant.
    The primary key also stops two concurrent first visits from copying twice."""
    __tablename__ = "tb_merchant_common_init"

    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id"), primary_key=True, autoincrement=False)
    c_group_code = Column(String(30), primary_key=True)  # COLOR / UNIT / JOB_GRADE / JOB_TITLE
    i_copied_count = Column(Integer, nullable=False, default=0)
    i_skipped_count = Column(Integer, nullable=False, default=0)  # codes the Merchant already had (kept as they are)
