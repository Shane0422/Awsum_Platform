"""Common AWSUM Barcode rules: one barcode belongs to exactly one MD Detail across the whole system.

MD Master has no barcode column of its own; its Barcode is the Default Detail's, so checking tb_md_detail
covers MD Master and every MD Detail. Empty barcodes are allowed and never compared. The DB guarantee is the
partial UNIQUE index uq_tb_md_detail_barcode on trim(c_barcode) (see md_barcode_unique_migration).
"""
from __future__ import annotations

from sqlalchemy import func, text
from sqlalchemy.orm import Session

UNIQUE_INDEX = "uq_tb_md_detail_barcode"
# Same predicate for the index, the duplicate report and the lookups, so all three agree.
INDEX_WHERE = "c_barcode IS NOT NULL AND trim(c_barcode) <> ''"


def normalize_barcode(value) -> str | None:
    """Trim surrounding whitespace; blank means no barcode."""
    if value is None:
        return None
    code = str(value).strip()
    return code or None


def find_barcode_owner(db: Session, barcode: str | None, *, exclude_detail_id: int | None = None):
    """(item_code, detail_no) of the MD Detail already using this barcode, or None."""
    from backend.models_admin.master_group import MdDetail, MdMaster

    code = normalize_barcode(barcode)
    if code is None:
        return None
    query = (db.query(MdMaster.c_item_code, MdDetail.i_detail_no)
             .join(MdMaster, MdMaster.i_md_id == MdDetail.i_md_id)
             .filter(func.trim(MdDetail.c_barcode) == code))
    if exclude_detail_id is not None:
        query = query.filter(MdDetail.i_md_detail_id != exclude_detail_id)
    return query.order_by(MdMaster.c_item_code, MdDetail.i_detail_no).first()


def barcode_conflict(db: Session, barcode, *, exclude_detail_id: int | None = None,
                     current_barcode=None) -> str | None:
    """Message when the barcode is taken by another MD Detail, else None.

    An unchanged value is not re-checked, so records that were already duplicated before this rule
    (reported by duplicate_barcodes) stay editable; they can only be changed to a free barcode.
    """
    code = normalize_barcode(barcode)
    if code is None or code == normalize_barcode(current_barcode):
        return None
    owner = find_barcode_owner(db, code, exclude_detail_id=exclude_detail_id)
    if owner is None:
        return None
    item_code, detail_no = owner
    return f"Barcode {code} is already used by {item_code} (Detail {detail_no})."


def duplicate_barcodes(conn) -> list[tuple[str, str]]:
    """Existing duplicates as (barcode, "MD00001#1, MD00002#1"); read-only, used for reporting."""
    rows = conn.execute(text("""
        SELECT trim(d.c_barcode) AS barcode, m.c_item_code, d.i_detail_no
        FROM tb_md_detail AS d JOIN tb_md_master AS m ON m.i_md_id = d.i_md_id
        WHERE d.c_barcode IS NOT NULL AND trim(d.c_barcode) <> ''
        ORDER BY 1, 2, 3
    """)).all()
    grouped: dict[str, list[str]] = {}
    for barcode, item_code, detail_no in rows:
        grouped.setdefault(barcode, []).append(f"{item_code}#{detail_no}")
    return [(barcode, ", ".join(owners)) for barcode, owners in grouped.items() if len(owners) > 1]
