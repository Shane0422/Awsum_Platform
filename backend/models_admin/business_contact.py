from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, text

from backend.database.pg_platform import PlatformBase


class BusinessContact(PlatformBase):
    """Contacts shared by Agency / Merchant / Store, keyed by owner_type + owner_id."""

    __tablename__ = "tb_business_contact"

    i_contact_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    c_owner_type = Column(String(20), nullable=False)  # AGENCY / MERCHANT / STORE
    i_owner_id = Column(Integer, nullable=False)
    c_contact_role = Column(String(20), nullable=False, default="OTHER")  # PRIMARY / OPERATIONS / BILLING / TECHNICAL / OTHER
    c_title = Column(String(120), nullable=True)
    c_first_name = Column(String(120), nullable=True)
    c_last_name = Column(String(120), nullable=True)
    c_email = Column(String(255), nullable=True)
    c_phone = Column(String(50), nullable=True)
    b_is_primary = Column(Boolean, nullable=False, default=False)
    i_display_order = Column(Integer, nullable=False, default=0)
    c_status = Column(String(20), nullable=False, default="active")
    dt_created = Column(DateTime, nullable=False, default=datetime.now)
    dt_updated = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("ix_business_contact_owner", "c_owner_type", "i_owner_id"),
        # At most one primary contact per owner.
        Index("ux_business_contact_primary", "c_owner_type", "i_owner_id", unique=True,
              postgresql_where=text("b_is_primary")),
    )
