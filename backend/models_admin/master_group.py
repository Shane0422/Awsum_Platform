"""Master Group: product base data (Tax, Category I/II/III, MD Master).

MD Master stores per-item management rules only. Individual serial numbers and
real license keys never live here: serials belong to delivered/installed units
(see tb_device.c_serial_no) and keys to tb_license.
"""
from sqlalchemy import Boolean, Column, ForeignKey, Integer, Numeric, String, Text

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
    c_description = Column(Text, nullable=True)
    c_image_path = Column(String(1000), nullable=True)
    c_item_kind = Column(String(20), nullable=False, index=True)  # HARDWARE / SOFTWARE / SERVICE
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
    b_license_required = Column(Boolean, nullable=False, default=False)
    c_license_type = Column(String(30), nullable=True)  # reuses tb_license.c_license_type values
    i_licenses_per_unit = Column(Integer, nullable=True)
    b_serial_required = Column(Boolean, nullable=False, default=False)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)
