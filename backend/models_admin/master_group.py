"""Master Group: product base data (Tax, Category I/II/III, MD Master).

MD Master stores per-item management rules only. Individual serial numbers and
real license keys never live here: serials belong to delivered/installed units
(see tb_device.c_serial_no) and keys to tb_license.
"""
from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class Tax(PlatformBase, AuditMixin):
    __tablename__ = "tb_tax"

    i_tax_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id"), nullable=True, index=True)
    c_tax_code = Column(String(20), nullable=False, unique=True, index=True)
    c_tax_name = Column(String(100), nullable=False)
    n_tax_rate = Column(Numeric(7, 4), nullable=False, default=0)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)


class ItemKind(PlatformBase, AuditMixin):
    """Platform-only System Master. c_item_kind_code is a program logic key and never changes."""
    __tablename__ = "tb_item_kind"

    i_item_kind_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_item_kind_code = Column(String(30), nullable=False, unique=True, index=True)
    c_item_kind_name = Column(String(100), nullable=False)
    i_display_order = Column(Integer, nullable=False, default=100)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)


class Category1(PlatformBase, AuditMixin):
    __tablename__ = "tb_category1"

    i_category1_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id"), nullable=True, index=True)
    c_category_code = Column(String(20), nullable=False, unique=True, index=True)
    c_category_name = Column(String(100), nullable=False)
    i_display_order = Column(Integer, nullable=False, default=100)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)


class Category2(PlatformBase, AuditMixin):
    __tablename__ = "tb_category2"

    i_category2_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id"), nullable=True, index=True)
    i_category1_id = Column(Integer, ForeignKey("tb_category1.i_category1_id"), nullable=False, index=True)
    c_category_code = Column(String(20), nullable=False, unique=True, index=True)
    c_category_name = Column(String(100), nullable=False)
    i_display_order = Column(Integer, nullable=False, default=100)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)


class Category3(PlatformBase, AuditMixin):
    __tablename__ = "tb_category3"

    i_category3_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id"), nullable=True, index=True)
    i_category1_id = Column(Integer, ForeignKey("tb_category1.i_category1_id"), nullable=False, index=True)
    i_category2_id = Column(Integer, ForeignKey("tb_category2.i_category2_id"), nullable=False, index=True)
    i_tax1_id = Column(Integer, ForeignKey("tb_tax.i_tax_id"), nullable=True, index=True)
    i_tax2_id = Column(Integer, ForeignKey("tb_tax.i_tax_id"), nullable=True, index=True)
    i_tax3_id = Column(Integer, ForeignKey("tb_tax.i_tax_id"), nullable=True, index=True)
    # Inventory defaults for MD items in this Category III (services.inventory). NULL = not set.
    i_inventory_type = Column(Integer, nullable=True)
    c_keep = Column(String(20), nullable=True)
    c_category_code = Column(String(20), nullable=False, unique=True, index=True)
    c_category_name = Column(String(100), nullable=False)
    i_display_order = Column(Integer, nullable=False, default=100)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)


class MdMaster(PlatformBase, AuditMixin):
    __tablename__ = "tb_md_master"

    i_md_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_item_code = Column(String(20), nullable=False, unique=True, index=True)
    c_item_name = Column(String(200), nullable=False)
    c_second_name = Column(String(200), nullable=True)
    c_description = Column(Text, nullable=True)
    c_image_path = Column(String(1000), nullable=True)
    c_item_kind = Column(String(20), nullable=False, index=True)  # tb_item_kind.c_item_kind_code (default ITEM)
    i_category1_id = Column(Integer, ForeignKey("tb_category1.i_category1_id"), nullable=False, index=True)
    i_category2_id = Column(Integer, ForeignKey("tb_category2.i_category2_id"), nullable=True, index=True)
    i_category3_id = Column(Integer, ForeignKey("tb_category3.i_category3_id"), nullable=True, index=True)
    c_unit = Column(String(30), nullable=False, default="EA")
    n_default_unit_price = Column(Numeric(12, 2), nullable=False, default=0)  # Retail Price (pre-tax, USD)
    n_cost = Column(Numeric(12, 2), nullable=True)  # purchase cost; NULL = not set. Restricted by permission.
    # Agency supply price levels. Mode NULL = level not set (never treated as $0).
    # FIXED: n_pN_price is the entered amount. DISCOUNT: n_pN_price is derived from retail and n_pN_discount_pct.
    c_p1_mode = Column(String(10), nullable=True)
    n_p1_discount_pct = Column(Numeric(5, 2), nullable=True)
    n_p1_price = Column(Numeric(12, 2), nullable=True)
    c_p2_mode = Column(String(10), nullable=True)
    n_p2_discount_pct = Column(Numeric(5, 2), nullable=True)
    n_p2_price = Column(Numeric(12, 2), nullable=True)
    c_p3_mode = Column(String(10), nullable=True)
    n_p3_discount_pct = Column(Numeric(5, 2), nullable=True)
    n_p3_price = Column(Numeric(12, 2), nullable=True)
    i_default_tax_id = Column(Integer, ForeignKey("tb_tax.i_tax_id"), nullable=True, index=True)
    c_billing_cycle = Column(String(20), nullable=False, default="ONE_TIME")  # ONE_TIME / MONTHLY / ANNUAL
    # Item override of the Category III inventory defaults; NULL = inherit from Category III.
    # Inventory Type number: 1 None / 2 Stock / 3 Recipe / 4 Consume on Receipt / 5 Consume on Issue.
    # Logic must read services.inventory.resolve_inventory() / inventory_behavior(), never the raw number.
    i_inventory_type = Column(Integer, nullable=True)
    c_keep = Column(String(20), nullable=True)  # DRY / COLD / FROZEN
    b_license_required = Column(Boolean, nullable=False, default=False)
    c_license_type = Column(String(30), nullable=True)  # reuses tb_license.c_license_type values
    i_licenses_per_unit = Column(Integer, nullable=True)
    b_serial_required = Column(Boolean, nullable=False, default=False)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)


class MdDetail(PlatformBase, AuditMixin):
    __tablename__ = "tb_md_detail"
    __table_args__ = (
        UniqueConstraint("i_md_id", "i_detail_no", name="uq_tb_md_detail_md_detail_no"),
        Index(
            "uq_tb_md_detail_default",
            "i_md_id",
            unique=True,
            postgresql_where=text("b_is_default = true"),
            sqlite_where=text("b_is_default = 1"),
        ),
    )

    i_md_detail_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_md_id = Column(Integer, ForeignKey("tb_md_master.i_md_id", ondelete="CASCADE"), nullable=False, index=True)
    i_detail_no = Column(Integer, nullable=False)
    b_is_default = Column(Boolean, nullable=False, default=False)
    c_barcode = Column(String(100), nullable=True, index=True)
    c_unit = Column(String(30), nullable=False, default="EA")
    c_pk_size = Column(String(100), nullable=True)
    c_size = Column(String(100), nullable=True)
    n_cost = Column(Numeric(12, 2), nullable=True)
    n_price = Column(Numeric(12, 2), nullable=False, default=0)
    c_modifier = Column(Text, nullable=True)


# System-wide barcode uniqueness (services.barcode): non-empty trimmed barcodes are unique across all MD Details.
# Existing databases get it from database/md_barcode_unique_migration once their data has no duplicates.
Index(
    "uq_tb_md_detail_barcode",
    func.trim(MdDetail.c_barcode),
    unique=True,
    postgresql_where=text("c_barcode IS NOT NULL AND trim(c_barcode) <> ''"),
    sqlite_where=text("c_barcode IS NOT NULL AND trim(c_barcode) <> ''"),
)
