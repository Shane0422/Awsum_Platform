"""Store Override of the Merchant-common MD (SO-1): per-Store Detail price and MD-level POS visibility.

The Merchant keeps one MD Master / MD Detail; a Store never gets its own copy. A row exists only when the Store
overrides the Merchant value; no row = the Merchant value applies.

tb_store_md_price  Store x MD Detail price. NULL is never stored: no row = the Merchant Detail price (inherit).
                   $0.00 is a valid override. A row is removed only by "revert to the Merchant price".
tb_store_md        Store x MD: POS visibility (b_pos_visible). Hidden only removes the item from POS buttons / lists;
                   it is not a sales block (selling stop is out of scope: future Product Policy > Block Sales).

Merchant isolation is enforced by the database as well as the service: i_owner_key is at the same time the Store's
Merchant (composite FK -> tb_store (i_store_id, i_merchant_id)) and the MD's owner (composite FK -> tb_md_master
(i_md_id, i_owner_key)), and the price row's Detail belongs to its MD (composite FK -> tb_md_detail
(i_md_detail_id, i_md_id)). Deleting the MD, the Detail or the Store removes its override rows (CASCADE); an MD owner
or Store Merchant change is refused while override rows exist (NO ACTION), like the Employee Store links.

i_row_version: optimistic concurrency token (1 on insert, +1 on each update); a save that read another version is
refused by the service. Audit columns come from the common AuditMixin (server-side login user only).
"""
from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKeyConstraint, Index, Integer, Numeric, UniqueConstraint, text

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin


def _owner_fks(table: str) -> tuple:
    return (
        ForeignKeyConstraint(["i_store_id", "i_owner_key"], ["tb_store.i_store_id", "tb_store.i_merchant_id"],
                             name=f"fk_{table}_store_owner", ondelete="CASCADE"),
        ForeignKeyConstraint(["i_md_id", "i_owner_key"], ["tb_md_master.i_md_id", "tb_md_master.i_owner_key"],
                             name=f"fk_{table}_md_owner", ondelete="CASCADE"),
    )


class StoreMdPrice(PlatformBase, AuditMixin):
    __tablename__ = "tb_store_md_price"
    __table_args__ = (
        *_owner_fks("tb_store_md_price"),
        ForeignKeyConstraint(["i_md_detail_id", "i_md_id"], ["tb_md_detail.i_md_detail_id", "tb_md_detail.i_md_id"],
                             name="fk_tb_store_md_price_detail_md", ondelete="CASCADE"),
        UniqueConstraint("i_store_id", "i_md_detail_id", name="uq_tb_store_md_price_store_detail"),
        CheckConstraint("n_price >= 0", name="ck_tb_store_md_price_price"),
        CheckConstraint("i_row_version >= 1", name="ck_tb_store_md_price_row_version"),
        Index("ix_tb_store_md_price_md", "i_md_id"),  # the Store Settings grid of one MD; MD cascade
        Index("ix_tb_store_md_price_detail", "i_md_detail_id"),  # Detail cascade
    )

    i_store_md_price_id = Column(Integer, primary_key=True, autoincrement=True)
    i_store_id = Column(Integer, nullable=False)
    i_md_id = Column(Integer, nullable=False)
    i_md_detail_id = Column(Integer, nullable=False)
    i_owner_key = Column(Integer, nullable=False)  # = the Store's Merchant = the MD's owner (composite FKs)
    n_price = Column(Numeric(12, 2), nullable=False)
    i_row_version = Column(Integer, nullable=False, default=1, server_default=text("1"))


class StoreMd(PlatformBase, AuditMixin):
    __tablename__ = "tb_store_md"
    __table_args__ = (
        *_owner_fks("tb_store_md"),
        UniqueConstraint("i_store_id", "i_md_id", name="uq_tb_store_md_store_md"),
        CheckConstraint("i_row_version >= 1", name="ck_tb_store_md_row_version"),
        Index("ix_tb_store_md_md", "i_md_id"),
    )

    i_store_md_id = Column(Integer, primary_key=True, autoincrement=True)
    i_store_id = Column(Integer, nullable=False)
    i_md_id = Column(Integer, nullable=False)
    i_owner_key = Column(Integer, nullable=False)
    b_pos_visible = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    i_row_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
