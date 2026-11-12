"""Common AWSUM Barcode rules: within one owner a barcode belongs to exactly one MD Detail.

Owner = the MD's i_owner_key: 0 for Platform, the Merchant ID for a Merchant (shared by all its Stores). Platform and
each Merchant are separate: the same barcode may exist in different owners, never twice in one owner.
MD Master has no barcode column of its own; its Barcode is the Default Detail's, so checking tb_md_detail
covers MD Master and every MD Detail. Empty barcodes are allowed and never compared. The DB guarantee is the
partial UNIQUE index uq_tb_md_detail_owner_barcode on (i_owner_key, trim(c_barcode)) (md_owner_scope_migration).
"""
from __future__ import annotations

from sqlalchemy import func, text
from sqlalchemy.orm import Session

UNIQUE_INDEX = "uq_tb_md_detail_owner_barcode"
LEGACY_UNIQUE_INDEX = "uq_tb_md_detail_barcode"  # the earlier system-wide index (replaced, STEP 2)
# Same predicate for the index, the duplicate report and the lookups, so all three agree.
INDEX_WHERE = "c_barcode IS NOT NULL AND trim(c_barcode) <> ''"


def normalize_barcode(value) -> str | None:
    """Trim surrounding whitespace; blank means no barcode."""
    if value is None:
        return None
    code = str(value).strip()
    return code or None


def find_barcode_owner(db: Session, barcode: str | None, *, owner_key: int, exclude_detail_id: int | None = None):
    """(item_code, detail_no) of the owner's MD Detail already using this barcode, or None."""
    from backend.models_admin.master_group import MdDetail, MdMaster

    code = normalize_barcode(barcode)
    if code is None:
        return None
    query = (db.query(MdMaster.c_item_code, MdDetail.i_detail_no)
             .join(MdMaster, MdMaster.i_md_id == MdDetail.i_md_id)
             .filter(MdDetail.i_owner_key == owner_key, func.trim(MdDetail.c_barcode) == code))
    if exclude_detail_id is not None:
        query = query.filter(MdDetail.i_md_detail_id != exclude_detail_id)
    return query.order_by(MdMaster.c_item_code, MdDetail.i_detail_no).first()


def barcode_conflict(db: Session, barcode, *, owner_key: int, exclude_detail_id: int | None = None,
                     current_barcode=None) -> str | None:
    """Message when the barcode is taken by another MD Detail of the same owner, else None (other owners never
    conflict, and the message only ever names the caller's own item).

    An unchanged value is not re-checked, so records that were already duplicated before this rule
    (reported by duplicate_barcodes) stay editable; they can only be changed to a free barcode.
    """
    code = normalize_barcode(barcode)
    if code is None or code == normalize_barcode(current_barcode):
        return None
    owner = find_barcode_owner(db, code, owner_key=owner_key, exclude_detail_id=exclude_detail_id)
    if owner is None:
        return None
    item_code, detail_no = owner
    return f"Barcode {code} is already used by {item_code} (Detail {detail_no})."


def duplicate_barcodes(conn) -> list[tuple[str, str]]:
    """Existing duplicates within one owner as (barcode, "MD00001#1, MD00002#1"); read-only, used for reporting.
    Item codes are unique system-wide, so the owners list identifies the rows."""
    rows = conn.execute(text("""
        SELECT d.i_owner_key, trim(d.c_barcode) AS barcode, m.c_item_code, d.i_detail_no
        FROM tb_md_detail AS d JOIN tb_md_master AS m ON m.i_md_id = d.i_md_id
        WHERE d.c_barcode IS NOT NULL AND trim(d.c_barcode) <> ''
        ORDER BY 1, 2, 3, 4
    """)).all()
    grouped: dict[tuple, list[str]] = {}
    for owner_key, barcode, item_code, detail_no in rows:
        grouped.setdefault((owner_key, barcode), []).append(f"{item_code}#{detail_no}")
    return [(barcode, ", ".join(owners)) for (_owner, barcode), owners in grouped.items() if len(owners) > 1]
