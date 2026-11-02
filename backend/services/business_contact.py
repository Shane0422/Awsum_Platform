"""Common Business Contact service shared by Agency / Merchant / Store (owner_type + owner_id)."""
import re
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models_admin.merchant import Merchant
from backend.models_admin.agency import Agency
from backend.models_admin.business_contact import BusinessContact
from backend.models_admin.store import Store

OWNER_MODELS = {
    "AGENCY": (Agency, "i_agency_id"),
    "MERCHANT": (Merchant, "i_merchant_id"),
    "STORE": (Store, "i_store_id"),
}
CONTACT_ROLES = ("PRIMARY", "OPERATIONS", "BILLING", "TECHNICAL", "OTHER")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
FIELD_LIMITS = {"title": 120, "first_name": 120, "last_name": 120, "email": 255, "phone": 50}


class BusinessContactService:
    def __init__(self, db: Session):
        self.db = db

    def primary_names(self, owner_type: str, owner_ids) -> dict[int, str]:
        """Primary contact 'First Last' per owner, for list/header display."""
        owner_type = self.normalize_owner_type(owner_type)
        ids = [int(i) for i in owner_ids if i is not None]
        if not ids:
            return {}
        rows = self.db.query(BusinessContact).filter(
            BusinessContact.c_owner_type == owner_type,
            BusinessContact.i_owner_id.in_(ids),
            BusinessContact.b_is_primary.is_(True),
        )
        return {
            row.i_owner_id: name
            for row in rows
            if (name := " ".join(p for p in (row.c_first_name, row.c_last_name) if p).strip())
        }

    @staticmethod
    def normalize_owner_type(owner_type: str) -> str:
        value = str(owner_type or "").strip().upper()
        if value not in OWNER_MODELS:
            raise HTTPException(400, "Owner type must be AGENCY, MERCHANT or STORE.")
        return value

    def _require_owner(self, owner_type: str, owner_id: int) -> str:
        owner_type = self.normalize_owner_type(owner_type)
        model, key = OWNER_MODELS[owner_type]
        if not self.db.query(model).filter(getattr(model, key) == owner_id).first():
            raise HTTPException(404, f"{owner_type.title()} not found.")
        return owner_type

    def _require_contact(self, contact_id: int) -> BusinessContact:
        contact = self.db.get(BusinessContact, contact_id)
        if not contact:
            raise HTTPException(404, "Contact not found.")
        return contact

    @staticmethod
    def serialize(contact: BusinessContact) -> dict:
        return {
            "contact_id": contact.i_contact_id,
            "owner_type": contact.c_owner_type,
            "owner_id": contact.i_owner_id,
            "contact_role": contact.c_contact_role,
            "title": contact.c_title or "",
            "first_name": contact.c_first_name or "",
            "last_name": contact.c_last_name or "",
            "email": contact.c_email or "",
            "phone": contact.c_phone or "",
            "is_primary": bool(contact.b_is_primary),
            "display_order": contact.i_display_order or 0,
            "status": contact.c_status or "active",
        }

    @staticmethod
    def _clean(payload: dict) -> dict:
        values = {}
        for name, limit in FIELD_LIMITS.items():
            value = str(payload.get(name) or "").strip()
            if len(value) > limit:
                raise HTTPException(400, f"{name.replace('_', ' ').title()} must be {limit} characters or fewer.")
            values[name] = value or None
        role = str(payload.get("contact_role") or "OTHER").strip().upper()
        if role not in CONTACT_ROLES:
            raise HTTPException(400, "Invalid contact role.")
        values["contact_role"] = role
        if not (values["first_name"] or values["last_name"]):
            raise HTTPException(400, "First Name or Last Name is required.")
        if values["email"] and not EMAIL_PATTERN.match(values["email"]):
            raise HTTPException(400, "Please enter a valid contact email address.")
        return values

    def _clear_primary(self, owner_type: str, owner_id: int, keep_id: int | None = None) -> None:
        query = self.db.query(BusinessContact).filter(
            BusinessContact.c_owner_type == owner_type,
            BusinessContact.i_owner_id == owner_id,
            BusinessContact.b_is_primary.is_(True),
        )
        if keep_id:
            query = query.filter(BusinessContact.i_contact_id != keep_id)
        query.update({BusinessContact.b_is_primary: False, BusinessContact.dt_updated: datetime.now()},
                     synchronize_session="fetch")
        self.db.flush()

    def get_contacts(self, owner_type: str, owner_id: int) -> list[dict]:
        owner_type = self._require_owner(owner_type, owner_id)
        rows = (
            self.db.query(BusinessContact)
            .filter(BusinessContact.c_owner_type == owner_type, BusinessContact.i_owner_id == owner_id)
            .order_by(BusinessContact.i_display_order, BusinessContact.i_contact_id)
            .all()
        )
        return [self.serialize(row) for row in rows]

    def get_primary_contact(self, owner_type: str, owner_id: int) -> BusinessContact | None:
        owner_type = self._require_owner(owner_type, owner_id)
        return self.db.query(BusinessContact).filter(
            BusinessContact.c_owner_type == owner_type,
            BusinessContact.i_owner_id == owner_id,
            BusinessContact.b_is_primary.is_(True),
            BusinessContact.c_status == "active",
        ).order_by(BusinessContact.i_contact_id).first()

    def create_contact(self, owner_type: str, owner_id: int, payload: dict) -> dict:
        owner_type = self._require_owner(owner_type, owner_id)
        values = self._clean(payload)
        is_primary = bool(payload.get("is_primary"))
        if is_primary:
            self._clear_primary(owner_type, owner_id)
        next_order = (
            self.db.query(func.coalesce(func.max(BusinessContact.i_display_order), 0))
            .filter(BusinessContact.c_owner_type == owner_type, BusinessContact.i_owner_id == owner_id)
            .scalar()
        ) + 1
        contact = BusinessContact(
            c_owner_type=owner_type, i_owner_id=owner_id, c_contact_role=values["contact_role"],
            c_title=values["title"], c_first_name=values["first_name"], c_last_name=values["last_name"],
            c_email=values["email"], c_phone=values["phone"], b_is_primary=is_primary,
            i_display_order=next_order, c_status="active",
        )
        self.db.add(contact)
        self.db.commit()
        self.db.refresh(contact)
        return self.serialize(contact)

    def update_contact(self, contact_id: int, payload: dict) -> dict:
        contact = self._require_contact(contact_id)
        values = self._clean(payload)
        if "is_primary" in payload:
            is_primary = bool(payload.get("is_primary"))
            if is_primary:
                self._clear_primary(contact.c_owner_type, contact.i_owner_id, keep_id=contact.i_contact_id)
            contact.b_is_primary = is_primary
        contact.c_contact_role = values["contact_role"]
        contact.c_title = values["title"]
        contact.c_first_name = values["first_name"]
        contact.c_last_name = values["last_name"]
        contact.c_email = values["email"]
        contact.c_phone = values["phone"]
        contact.dt_updated = datetime.now()
        self.db.commit()
        self.db.refresh(contact)
        return self.serialize(contact)

    def delete_contact(self, contact_id: int) -> None:
        self.db.delete(self._require_contact(contact_id))
        self.db.commit()

    def set_primary(self, contact_id: int) -> dict:
        contact = self._require_contact(contact_id)
        self._clear_primary(contact.c_owner_type, contact.i_owner_id, keep_id=contact.i_contact_id)
        contact.b_is_primary = True
        contact.dt_updated = datetime.now()
        self.db.commit()
        self.db.refresh(contact)
        return self.serialize(contact)

    def reorder(self, owner_type: str, owner_id: int, contact_ids: list[int]) -> list[dict]:
        owner_type = self._require_owner(owner_type, owner_id)
        rows = {
            row.i_contact_id: row
            for row in self.db.query(BusinessContact).filter(
                BusinessContact.c_owner_type == owner_type, BusinessContact.i_owner_id == owner_id
            )
        }
        ordered = [int(cid) for cid in contact_ids if int(cid) in rows]
        ordered += [cid for cid in sorted(rows, key=lambda k: (rows[k].i_display_order, k)) if cid not in ordered]
        for position, cid in enumerate(ordered, start=1):
            rows[cid].i_display_order = position
        self.db.commit()
        return self.get_contacts(owner_type, owner_id)
