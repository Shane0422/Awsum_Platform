"""Location Management (Store-level data; never stored on the Platform-common MD Master / MD Detail).

tb_store_location            Location Master: one Store's Sales Floor and Warehouse Location Codes (a flat list
                             per Area; the code itself carries any structure, e.g. A03-B02-C01, W01-B02-C01).
tb_store_location_init       One row per Store once its default Locations were handled (created, or skipped because
                             the Store already had Locations). The Store id is the primary key, so a Store is
                             initialized at most once, even with concurrent first visits or after every Location is deleted.
tb_store_md_location         DEPRECATED, unused (earlier MD Detail links; kept until its removal is approved).
tb_store_category3_location  DEPRECATED, unused (kept until its removal is approved; no reads or writes).

Store and area isolation are enforced by the database as well as the service: every reference carries
(location, store[, area]) through composite foreign keys, so it can never point at another Store's location
or at a location of the other area.
"""
from sqlalchemy import (Boolean, CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Index, Integer, String,
                        Text, UniqueConstraint, func, text)

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin

# Area codes (logic keys) -> display names.
LOCATION_AREAS = {"SALES_FLOOR": "Sales Floor", "WAREHOUSE": "Warehouse"}
DEFAULT_LOCATION_AREA = "SALES_FLOOR"
# The earlier Location Type (c_location_type) and parent tree (i_parent_location_id) were removed
# (backend.database.store_location_migration.drop_location_hierarchy).


class StoreLocation(PlatformBase, AuditMixin):
    __tablename__ = "tb_store_location"
    __table_args__ = (
        # Target of the (location, store, area) foreign keys: a reference keeps its Store and Area.
        UniqueConstraint("i_location_id", "i_store_id", "c_location_area", name="uq_tb_store_location_id_store_area"),
        # Location Code is unique per Store, ignoring case (the service also stores it upper-cased).
        Index("uq_tb_store_location_store_code", "i_store_id", func.upper(text("c_location_code")), unique=True),
    )

    i_location_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    c_location_code = Column(String(30), nullable=False)
    c_location_name = Column(String(100), nullable=False)
    c_location_area = Column(String(20), nullable=False, default=DEFAULT_LOCATION_AREA,
                             server_default=text(f"'{DEFAULT_LOCATION_AREA}'"))
    # Earlier free-text position fields; not shown on the screen. Kept (never dropped).
    c_zone = Column(String(50), nullable=True)
    c_aisle = Column(String(50), nullable=True)
    c_bay = Column(String(50), nullable=True)
    c_level = Column(String(50), nullable=True)
    i_display_order = Column(Integer, nullable=False, default=100)
    b_active = Column(Boolean, nullable=False, default=True, index=True)
    c_notes = Column(Text, nullable=True)


class StoreLocationInit(PlatformBase, AuditMixin):
    """Default Location initialization record of one Store (never deleted with its Locations)."""
    __tablename__ = "tb_store_location_init"

    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), primary_key=True, autoincrement=False)
    i_created_count = Column(Integer, nullable=False, default=0)  # 0 = the Store already had Locations (skipped)


class StoreMdLocation(PlatformBase, AuditMixin):
    """DEPRECATED - no new links. MD positions are the common c_sales_location / c_warehouse_location on
    tb_md_master (inheriting tb_category3). Kept (not dropped) until its removal is approved."""
    __tablename__ = "tb_store_md_location"
    __table_args__ = (
        # Same Store and the location's own area (c_location_area is copied from the location).
        ForeignKeyConstraint(["i_location_id", "i_store_id", "c_location_area"],
                             ["tb_store_location.i_location_id", "tb_store_location.i_store_id",
                              "tb_store_location.c_location_area"],
                             name="fk_tb_store_md_location_location_area"),
        UniqueConstraint("i_md_detail_id", "i_location_id", name="uq_tb_store_md_location_detail_location"),
        # At most one Primary per Store + MD Detail + Area (none is allowed).
        Index("uq_tb_store_md_location_area_primary", "i_store_id", "i_md_detail_id", "c_location_area", unique=True,
              postgresql_where=text("b_is_primary = true"), sqlite_where=text("b_is_primary = 1")),
    )

    i_store_md_location_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    # Deleting an MD Detail removes only its links; the Location Master rows stay.
    i_md_detail_id = Column(Integer, ForeignKey("tb_md_detail.i_md_detail_id", ondelete="CASCADE"), nullable=False, index=True)
    i_location_id = Column(Integer, nullable=False, index=True)
    c_location_area = Column(String(20), nullable=False, default=DEFAULT_LOCATION_AREA,
                             server_default=text(f"'{DEFAULT_LOCATION_AREA}'"))
    b_is_primary = Column(Boolean, nullable=False, default=False)


class StoreCategory3Location(PlatformBase, AuditMixin):
    """DEPRECATED - not used. Earlier per Store Category III default locations (MD inheritance was removed).

    Kept only so every database keeps the same schema until removing the table is approved. No program may
    read or write it (it holds no rows). Do not build on it.
    """
    __tablename__ = "tb_store_category3_location"
    __table_args__ = (
        UniqueConstraint("i_store_id", "i_category3_id", name="uq_tb_store_category3_location_store_category"),
        # The constant area columns let the composite FKs force "Sales Floor default is a Sales Floor location" etc.
        CheckConstraint("c_sales_area = 'SALES_FLOOR'", name="ck_tb_store_category3_location_sales_area"),
        CheckConstraint("c_warehouse_area = 'WAREHOUSE'", name="ck_tb_store_category3_location_warehouse_area"),
        ForeignKeyConstraint(["i_sales_location_id", "i_store_id", "c_sales_area"],
                             ["tb_store_location.i_location_id", "tb_store_location.i_store_id",
                              "tb_store_location.c_location_area"],
                             name="fk_tb_store_category3_location_sales"),
        ForeignKeyConstraint(["i_warehouse_location_id", "i_store_id", "c_warehouse_area"],
                             ["tb_store_location.i_location_id", "tb_store_location.i_store_id",
                              "tb_store_location.c_location_area"],
                             name="fk_tb_store_category3_location_warehouse"),
    )

    i_store_category3_location_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    i_store_id = Column(Integer, ForeignKey("tb_store.i_store_id"), nullable=False, index=True)
    # Deleting a Category III removes its defaults; the Location Master rows stay.
    i_category3_id = Column(Integer, ForeignKey("tb_category3.i_category3_id", ondelete="CASCADE"), nullable=False, index=True)
    i_sales_location_id = Column(Integer, nullable=True, index=True)
    c_sales_area = Column(String(20), nullable=False, default="SALES_FLOOR", server_default=text("'SALES_FLOOR'"))
    i_warehouse_location_id = Column(Integer, nullable=True, index=True)
    c_warehouse_area = Column(String(20), nullable=False, default="WAREHOUSE", server_default=text("'WAREHOUSE'"))
