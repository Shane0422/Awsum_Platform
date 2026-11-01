from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, Numeric, String

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


class Invoice(PlatformBase, AuditMixin):
    __tablename__ = "tb_invoice"

    i_invoice_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_subscription_id = Column(Integer, ForeignKey("tb_subscription.i_subscription_id"), nullable=False, index=True)
    i_account_id = Column(Integer, ForeignKey("tb_client.i_client_id"), nullable=False, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)

    c_invoice_no = Column(String(40), nullable=False, unique=True, index=True)
    dt_invoice_date = Column(Date, nullable=False, index=True)
    dt_due_date = Column(Date, nullable=True, index=True)

    c_currency = Column(String(10), nullable=False, default="USD")
    n_subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    n_tax = Column(Numeric(12, 2), nullable=False, default=0)
    n_total = Column(Numeric(12, 2), nullable=False, default=0)

    c_status = Column(String(30), nullable=False, default="issued", index=True)
    c_memo = Column(String, nullable=True)


class InvoiceLine(PlatformBase, AuditMixin):
    __tablename__ = "tb_invoice_line"

    i_invoice_line_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_invoice_id = Column(Integer, ForeignKey("tb_invoice.i_invoice_id"), nullable=False, index=True)

    c_line_type = Column(String(50), nullable=False, index=True)
    c_description = Column(String(255), nullable=False)
    n_quantity = Column(Numeric(12, 4), nullable=False, default=1)
    n_unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    n_amount = Column(Numeric(12, 2), nullable=False, default=0)

    c_currency = Column(String(10), nullable=False, default="USD")

    # MD Master reference plus the values in force when the line was issued.
    # Later Master edits must not change issued invoices; nothing writes these yet.
    i_md_id = Column(Integer, ForeignKey("tb_md_master.i_md_id", name="fk_invoice_line_md_master"), nullable=True, index=True)
    c_item_code_snapshot = Column(String(20), nullable=True)
    c_item_name_snapshot = Column(String(200), nullable=True)
    c_price_level_snapshot = Column(String(10), nullable=True)
    n_retail_price_snapshot = Column(Numeric(12, 2), nullable=True)
    c_price_mode_snapshot = Column(String(10), nullable=True)
    n_discount_pct_snapshot = Column(Numeric(5, 2), nullable=True)
    c_item_kind_snapshot = Column(String(20), nullable=True)
    c_unit_snapshot = Column(String(30), nullable=True)
    c_billing_cycle_snapshot = Column(String(20), nullable=True)
    c_tax_name_snapshot = Column(String(100), nullable=True)
    n_tax_rate_snapshot = Column(Numeric(7, 4), nullable=True)
    b_license_required_snapshot = Column(Boolean, nullable=True)
    c_license_type_snapshot = Column(String(30), nullable=True)
    i_licenses_per_unit_snapshot = Column(Integer, nullable=True)
    b_serial_required_snapshot = Column(Boolean, nullable=True)
