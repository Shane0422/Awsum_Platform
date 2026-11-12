"""Employee Master and Store assignments (STEP 1A, Merchant Portal).

Plugged into the common Master Group entity flow (routers.master_group, entity "employee"): same list / form /
export / image responses and errors (FieldErrors 422, Conflict 409, NotFound 404) as every other master; only the
Employee rules live here.

Rules:
- The Merchant comes from the login scope (never the payload). Employee Code (EMP00001) and POS Number (1001...) are
  issued per Merchant under a Merchant row lock, with the database unique constraints and a retry as the final guard.
- Field policy: EDITABLE fields only; PROTECTED fields may be sent unchanged (403 when changed); any other key 422.
- Status changes through PUT are active / inactive only; termination and rehire have their own commands.
- Termination ends every open Store assignment (Inactive Stores included), clears POS access and the PIN (bumping the
  sync versions) and keeps every row. Rehire does not restore assignments.
- Assignments: Store of the same Merchant (also enforced by the composite FKs); an Inactive Store is view only (403);
  one open assignment per Store; one active primary Store; start date on or after the Hire Date; the Termination Date
  must be on or after every open assignment start.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import exists, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.models_admin.employee import Employee, EmployeeStore
from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.services.app_navigation import STORE_INACTIVE_READ_ONLY
from backend.services.master_group import Conflict, FieldErrors, NotFound, _stamp, _text
from backend.services.pos_roles import POS_ROLE_LABELS, normalize_pos_role

CODE_PREFIX = "EMP"
CODE_DIGITS = 5
POS_NUMBER_START = 1001
OPEN = ("active", "inactive")  # assignment statuses that are not history
EDITABLE = frozenset({"first_name", "last_name", "display_name", "phone", "email", "hire_date", "status"})
# payload key -> saved attribute; may be sent unchanged (the common form sends the whole record), 403 when changed.
PROTECTED = {"id": "i_employee_id", "code": "c_employee_code", "pos_number": "i_pos_number",
             "merchant_id": "i_merchant_id", "termination_date": "d_termination_date",
             "termination_reason": "c_termination_reason"}
ASSIGNMENT_FIELDS = frozenset({"store_id", "pos_access", "pos_role", "primary", "status", "start_date"})
SORTS = {"code": (Employee.c_employee_code,), "pos_number": (Employee.i_pos_number,),
         "name": (Employee.c_last_name, Employee.c_first_name), "status": (Employee.c_status,),
         "hire_date": (Employee.d_hire_date,)}
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
TERMINATED_MESSAGE = "Terminated employee: view only. Reactivate to make changes."


# ---------- helpers ----------
def _date(payload: dict, key: str, errors: dict, *, label: str, required: bool = False) -> date | None:
    raw = str(payload.get(key) or "").strip()
    if not raw:
        if required:
            errors[key] = f"{label} is required."
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    errors[key] = f"Enter {label} as MM/DD/YYYY."
    return None


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "on", "yes", "y"}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _us(value) -> str:
    return value.strftime("%m/%d/%Y") if value else ""


def _reject_unexpected(payload: dict, allowed) -> None:
    if not isinstance(payload, dict):
        raise FieldErrors({}, "Invalid request.")
    unexpected = sorted(set(payload) - set(allowed))
    if unexpected:
        raise FieldErrors({key: "This field cannot be saved here." for key in unexpected},
                          f"Unexpected fields: {', '.join(unexpected)}.")


def _same(saved, sent) -> bool:
    if isinstance(saved, date):
        saved = saved.isoformat()
    left = "" if saved is None else str(saved).strip()
    right = "" if sent is None else str(sent).strip()
    if isinstance(saved, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", left) and re.fullmatch(r"\d{2}/\d{2}/\d{4}", right):
        right = datetime.strptime(right, "%m/%d/%Y").date().isoformat()
    return left.upper() == right.upper()


def _employee(db: Session, merchant_id: int, employee_id: int, *, lock: bool = False) -> Employee:
    query = db.query(Employee).filter(Employee.i_employee_id == employee_id, Employee.i_merchant_id == merchant_id)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise NotFound("Employee not found.")
    return row


def _assignments(db: Session, employee_id: int, *, open_only: bool = False, lock: bool = False):
    query = db.query(EmployeeStore).filter(EmployeeStore.i_employee_id == employee_id)
    if open_only:
        query = query.filter(EmployeeStore.c_status.in_(OPEN))
    return (query.with_for_update() if lock else query).all()


def _store_of(db: Session, merchant_id: int, store_id) -> Store:
    try:
        store = db.get(Store, int(store_id))
    except (TypeError, ValueError):
        store = None
    if store is None or store.i_store_id == ADMIN_STORE_ID or store.i_merchant_id != merchant_id:
        raise FieldErrors({"store_id": "Select a Store of this Merchant."})
    return store


def _writable_store(store: Store) -> None:
    """Inactive Store: view only for the Merchant (common Store Status rule)."""
    if (store.c_status or "").lower() != "active":
        raise HTTPException(403, STORE_INACTIVE_READ_ONLY)


def _bump(row) -> None:
    row.i_row_version = (row.i_row_version or 0) + 1


def _commit_image(db: Session, row: Employee, *, image_data: bytes | None = None, remove_image: bool = False):
    """Employee photo through the common image service (Merchant-wide path, file name = Employee Code). Commits."""
    from backend.services.image_service import commit_entity_image, image_operation_lock
    from backend.services.resource_storage import AwsumStorageService
    if image_data is None and not remove_image:
        db.commit()
        return None
    owner = AwsumStorageService(db).resolve_owner("MERCHANT", row.i_merchant_id)
    with image_operation_lock(owner, "employee"):
        return commit_entity_image(db, row, owner, row.c_employee_code, category="employee",
                                   image_data=image_data, remove_image=remove_image, reference_field="c_image_path")


# ---------- serialization ----------
def _store_summaries(db: Session, employee_ids: list[int]) -> dict[int, dict]:
    summary = {eid: {"primary_store": None, "store_count": 0, "pos_access": False, "stores": []} for eid in employee_ids}
    if not employee_ids:
        return summary
    rows = (db.query(EmployeeStore, Store.c_store_name, Store.c_store_code)
            .join(Store, Store.i_store_id == EmployeeStore.i_store_id)
            .filter(EmployeeStore.i_employee_id.in_(employee_ids), EmployeeStore.c_status.in_(OPEN))
            .order_by(Store.c_store_name).all())
    for assignment, name, code in rows:
        item = summary[assignment.i_employee_id]
        item["store_count"] += 1
        item["stores"].append(name)
        if assignment.c_status == "active":
            item["pos_access"] = item["pos_access"] or bool(assignment.b_pos_access)
            if assignment.b_primary:
                item["primary_store"] = {"id": assignment.i_store_id, "name": name, "code": code}
    return summary


def serialize(row: Employee, summary: dict | None = None) -> dict:
    from backend.services.images import image_url
    summary = summary or {"primary_store": None, "store_count": 0, "pos_access": False, "stores": []}
    names = [part for part in (row.c_last_name, row.c_first_name) if part]
    terminated = row.c_status == "terminated"
    return {
        "id": row.i_employee_id, "code": row.c_employee_code, "pos_number": row.i_pos_number,
        "first_name": row.c_first_name, "last_name": row.c_last_name, "display_name": row.c_display_name,
        "name": ", ".join(names), "phone": row.c_phone, "email": row.c_email,
        "hire_date": _iso(row.d_hire_date), "termination_date": _iso(row.d_termination_date),
        "termination_reason": row.c_termination_reason, "status": row.c_status,
        "active": row.c_status == "active",
        "image_path": row.c_image_path, "image_url": image_url(row.c_image_path) if row.c_image_path else None,
        "primary_store": summary["primary_store"], "store_count": summary["store_count"],
        "stores": summary["stores"], "pos_access": summary["pos_access"],
        "read_only": terminated, "read_only_message": TERMINATED_MESSAGE if terminated else None,
        "row_version": row.i_row_version,
        "created_at": _iso(row.dt_created), "updated_at": _iso(row.dt_updated),
    }


def _serialized(db: Session, row: Employee) -> dict:
    return serialize(row, _store_summaries(db, [row.i_employee_id])[row.i_employee_id])


def serialize_assignment(assignment: EmployeeStore, store: Store, employee: Employee) -> dict:
    store_active = (store.c_status or "").lower() == "active"
    read_only = assignment.c_status == "ended" or not store_active or employee.c_status == "terminated"
    return {
        "id": assignment.i_employee_store_id, "store_id": store.i_store_id, "store_code": store.c_store_code,
        "store_name": store.c_store_name, "store_active": store_active,
        "pos_access": bool(assignment.b_pos_access), "pos_role": assignment.c_pos_role,
        "pos_role_name": POS_ROLE_LABELS.get(assignment.c_pos_role or ""),
        "primary": bool(assignment.b_primary), "status": assignment.c_status,
        "start_date": _iso(assignment.d_start), "end_date": _iso(assignment.d_end),
        "read_only": read_only,
        "read_only_message": (STORE_INACTIVE_READ_ONLY if not store_active and assignment.c_status != "ended"
                              else None),
        "row_version": assignment.i_row_version,
    }


# ---------- list / export ----------
def _query(db: Session, merchant_id: int, *, q="", status="", store_id=None, pos_role=None):
    query = db.query(Employee).filter(Employee.i_merchant_id == merchant_id)
    term = str(q or "").strip()
    if term:
        like = f"%{term.replace('%', '').replace('_', ' ')}%"
        conditions = [Employee.c_employee_code.ilike(like), Employee.c_first_name.ilike(like),
                      Employee.c_last_name.ilike(like), Employee.c_display_name.ilike(like),
                      Employee.c_phone.ilike(like)]
        if term.isdigit():
            conditions.append(Employee.i_pos_number == int(term))
        query = query.filter(or_(*conditions))
    if status in ("active", "inactive", "terminated"):
        query = query.filter(Employee.c_status == status)
    if store_id not in (None, ""):
        try:
            sid = int(store_id)
        except (TypeError, ValueError):
            raise FieldErrors({"store_id": "Select a Store of this Merchant."})
        query = query.filter(exists().where(
            (EmployeeStore.i_employee_id == Employee.i_employee_id) & (EmployeeStore.i_store_id == sid)
            & EmployeeStore.c_status.in_(OPEN)))
    if pos_role not in (None, ""):
        try:
            code = normalize_pos_role(pos_role)
        except ValueError as exc:
            raise FieldErrors({"pos_role": str(exc)})
        query = query.filter(exists().where(
            (EmployeeStore.i_employee_id == Employee.i_employee_id) & (EmployeeStore.c_status == "active")
            & EmployeeStore.b_pos_access.is_(True) & (EmployeeStore.c_pos_role == code)))
    return query


def _ordered(query, sort: str, direction: str):
    key = sort if sort in SORTS else "code"
    columns = [column.desc() if direction == "desc" else column.asc() for column in SORTS[key]]
    return query.order_by(*columns, Employee.i_employee_id.asc()), key


def list_employees(db: Session, merchant_id: int, *, q="", status="", store_id=None, pos_role=None, sort="",
                   direction="asc", page=1, size=20) -> dict:
    query, key = _ordered(_query(db, merchant_id, q=q, status=status, store_id=store_id, pos_role=pos_role),
                          sort, direction)
    size = min(max(int(size or 20), 1), 100)
    total = query.order_by(None).count()
    pages = max((total + size - 1) // size, 1)
    page = min(max(int(page or 1), 1), pages)
    rows = query.offset((page - 1) * size).limit(size).all()
    summaries = _store_summaries(db, [row.i_employee_id for row in rows])
    return {"items": [serialize(row, summaries[row.i_employee_id]) for row in rows], "total": total, "page": page,
            "pages": pages, "size": size, "sort": key, "direction": "desc" if direction == "desc" else "asc"}


def export_employees(db: Session, merchant_id: int, *, q="", status="", store_id=None, pos_role=None, sort="",
                     direction="asc") -> tuple[list[str], list[list[str]]]:
    """Every matching Employee (no PIN or login data)."""
    query, _key = _ordered(_query(db, merchant_id, q=q, status=status, store_id=store_id, pos_role=pos_role),
                           sort, direction)
    rows = query.all()
    summaries = _store_summaries(db, [row.i_employee_id for row in rows])
    headers = ["Employee Code", "POS #", "Last Name", "First Name", "Display Name", "Phone", "Email", "Hire Date",
               "Termination Date", "Status", "Primary Store", "Stores", "POS Access"]
    data = []
    for row in rows:
        info = summaries[row.i_employee_id]
        data.append([row.c_employee_code, str(row.i_pos_number), row.c_last_name or "", row.c_first_name or "",
                     row.c_display_name or "", row.c_phone or "", row.c_email or "", _us(row.d_hire_date),
                     _us(row.d_termination_date), row.c_status.title(),
                     info["primary_store"]["name"] if info["primary_store"] else "", ", ".join(info["stores"]),
                     "Yes" if info["pos_access"] else "No"])
    return headers, data


# ---------- Employee save ----------
def _validated(db: Session, payload: dict, current: Employee | None) -> dict:
    _reject_unexpected(payload, EDITABLE | set(PROTECTED))
    changed = sorted(key for key, attr in PROTECTED.items()
                     if key in payload and (current is None or not _same(getattr(current, attr), payload[key])))
    if changed and (current is not None or any(str(payload[k] or "").strip() for k in changed)):
        raise HTTPException(403, f"Assigned by the system or changed by Terminate / Reactivate only: {', '.join(changed)}.")
    errors: dict[str, str] = {}
    values: dict = {}
    for key, attr, limit in (("first_name", "c_first_name", 100), ("last_name", "c_last_name", 100),
                             ("display_name", "c_display_name", 40), ("phone", "c_phone", 40),
                             ("email", "c_email", 254)):
        if key in payload or current is None:
            values[attr] = _text(payload, key, limit, errors)
    if values.get("c_email") and not EMAIL.match(values["c_email"]):
        errors["email"] = "Please enter a valid email address."
    if "hire_date" in payload or current is None:
        values["d_hire_date"] = _date(payload, "hire_date", errors, label="Hire Date")
    if "status" in payload or current is None:
        status = str(payload.get("status") or "active").strip().lower()
        if status == "terminated":
            errors["status"] = "Use Terminate to end employment."
        elif status not in ("active", "inactive"):
            errors["status"] = "Select Active or Inactive."
        else:
            values["c_status"] = status
    first = values.get("c_first_name", current.c_first_name if current else None)
    last = values.get("c_last_name", current.c_last_name if current else None)
    if not first and not last and "first_name" not in errors and "last_name" not in errors:
        errors["first_name"] = "Enter a First Name or Last Name."
    hire = values.get("d_hire_date", current.d_hire_date if current else None)
    if current is not None and hire and "hire_date" not in errors:
        starts = [a.d_start for a in _assignments(db, current.i_employee_id, open_only=True)]
        if starts and min(starts) < hire:
            errors["hire_date"] = (f"Hire Date must be on or before the earliest Store assignment start "
                                   f"({_us(min(starts))}).")
    if errors:
        raise FieldErrors(errors)
    return values


def _next_numbers(db: Session, merchant_id: int) -> tuple[str, int]:
    pattern = re.compile(rf"^{CODE_PREFIX}(\d+)$")
    top_code, top_pos = 0, POS_NUMBER_START - 1
    for code, pos in db.query(Employee.c_employee_code, Employee.i_pos_number).filter(
            Employee.i_merchant_id == merchant_id):
        match = pattern.match(code or "")
        if match:
            top_code = max(top_code, int(match.group(1)))
        top_pos = max(top_pos, pos or 0)
    return f"{CODE_PREFIX}{top_code + 1:0{CODE_DIGITS}d}", top_pos + 1


def create_employee(db: Session, merchant_id: int, payload: dict, user_id: int, *,
                    image_data: bytes | None = None) -> dict:
    values = _validated(db, payload, None)
    for _attempt in range(5):
        # One Merchant at a time issues numbers (row lock); the unique constraints + retry are the final guard.
        db.query(Merchant).filter(Merchant.i_merchant_id == merchant_id).with_for_update().one()
        code, pos_number = _next_numbers(db, merchant_id)
        row = Employee(i_merchant_id=merchant_id, c_employee_code=code, i_pos_number=pos_number, **values)
        _stamp(row, user_id, created=True)
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            continue
        warning = _commit_image(db, row, image_data=image_data)
        db.refresh(row)
        item = _serialized(db, row)
        if warning:
            item["image_warning"] = warning
        return item
    raise Conflict("Could not issue a unique Employee Code / POS Number. Please try again.")


def update_employee(db: Session, merchant_id: int, employee_id: int, payload: dict, user_id: int, *,
                    image_data: bytes | None = None, remove_image: bool = False) -> dict:
    row = _employee(db, merchant_id, employee_id, lock=True)
    if row.c_status == "terminated":
        raise Conflict(TERMINATED_MESSAGE)
    values = _validated(db, payload, row)
    for attr, value in values.items():
        setattr(row, attr, value)
    _bump(row)
    _stamp(row, user_id, created=False)
    warning = _commit_image(db, row, image_data=image_data, remove_image=remove_image)
    db.refresh(row)
    item = _serialized(db, row)
    if warning:
        item["image_warning"] = warning
    return item


def terminate_employee(db: Session, merchant_id: int, employee_id: int, payload: dict, user_id: int) -> dict:
    _reject_unexpected(payload, {"termination_date", "termination_reason"})
    row = _employee(db, merchant_id, employee_id, lock=True)
    if row.c_status == "terminated":
        raise Conflict("This employee is already terminated.")
    errors: dict[str, str] = {}
    end = _date(payload, "termination_date", errors, label="Termination Date", required=True)
    reason = _text(payload, "termination_reason", 200, errors)
    open_rows = _assignments(db, employee_id, open_only=True, lock=True)
    if end and row.d_hire_date and end < row.d_hire_date:
        errors["termination_date"] = f"Termination Date must be on or after the Hire Date ({_us(row.d_hire_date)})."
    late = [a for a in open_rows if end and a.d_start > end]
    if late and "termination_date" not in errors:
        store = db.get(Store, late[0].i_store_id)
        errors["termination_date"] = (f"Termination Date must be on or after every Store assignment start "
                                      f"({store.c_store_name if store else late[0].i_store_id}: {_us(late[0].d_start)}).")
    if errors:
        raise FieldErrors(errors)
    for assignment in open_rows:  # every open assignment ends, Inactive Stores included (history kept)
        assignment.c_status, assignment.d_end = "ended", end
        assignment.b_primary = False
        assignment.b_pos_access = False
        _bump(assignment)
        _stamp(assignment, user_id, created=False)
    row.c_status, row.d_termination_date, row.c_termination_reason = "terminated", end, reason
    row.c_pin_hash, row.i_pin_failed_count, row.dt_pin_locked_until = None, 0, None
    row.i_pin_version = (row.i_pin_version or 0) + 1  # Store Servers drop the PIN on their next sync (STEP 2)
    _bump(row)
    _stamp(row, user_id, created=False)
    db.commit()
    db.refresh(row)
    return _serialized(db, row)


def reactivate_employee(db: Session, merchant_id: int, employee_id: int, payload: dict, user_id: int) -> dict:
    _reject_unexpected(payload, {"hire_date"})
    row = _employee(db, merchant_id, employee_id, lock=True)
    if row.c_status != "terminated":
        raise Conflict("Only a terminated employee can be reactivated.")
    errors: dict[str, str] = {}
    hire = _date(payload, "hire_date", errors, label="Hire Date")
    if errors:
        raise FieldErrors(errors)
    row.c_status, row.d_termination_date, row.c_termination_reason = "active", None, None
    if hire:
        row.d_hire_date = hire
    _bump(row)
    _stamp(row, user_id, created=False)
    db.commit()  # earlier Store assignments stay ended (not restored)
    db.refresh(row)
    return _serialized(db, row)


# ---------- Store assignments ----------
def list_assignments(db: Session, merchant_id: int, employee_id: int) -> dict:
    employee = _employee(db, merchant_id, employee_id)
    rows = (db.query(EmployeeStore, Store).join(Store, Store.i_store_id == EmployeeStore.i_store_id)
            .filter(EmployeeStore.i_employee_id == employee_id).all())
    order = {"active": 0, "inactive": 1, "ended": 2}
    rows.sort(key=lambda pair: (order.get(pair[0].c_status, 3), pair[1].c_store_name or "",
                                -(pair[0].d_start.toordinal() if pair[0].d_start else 0)))
    return {"items": [serialize_assignment(a, s, employee) for a, s in rows]}


def _assignment_values(payload: dict, current: EmployeeStore | None, employee: Employee) -> dict:
    errors: dict[str, str] = {}
    values: dict = {}
    if "status" in payload or current is None:
        status = str(payload.get("status") or "active").strip().lower()
        if status not in ("active", "inactive"):
            errors["status"] = "Select Active or Inactive (use End to close an assignment)."
        values["c_status"] = status
    if "start_date" in payload or current is None:
        start = _date(payload, "start_date", errors, label="Start Date") or date.today()
        if employee.d_hire_date and start < employee.d_hire_date:
            errors["start_date"] = f"Start Date must be on or after the Hire Date ({_us(employee.d_hire_date)})."
        values["d_start"] = start
    if "pos_access" in payload or current is None:
        values["b_pos_access"] = _flag(payload.get("pos_access"))
    if "pos_role" in payload:
        try:
            values["c_pos_role"] = normalize_pos_role(payload.get("pos_role"))
        except ValueError as exc:
            errors["pos_role"] = str(exc)
    pos_access = values.get("b_pos_access", current.b_pos_access if current else False)
    pos_role = values.get("c_pos_role", current.c_pos_role if current else None)
    if pos_access and not pos_role and "pos_role" not in errors:
        errors["pos_role"] = "Select a POS Role for POS access."
    if "primary" in payload or current is None:
        values["b_primary"] = _flag(payload.get("primary"))
    if values.get("c_status", current.c_status if current else "active") != "active":
        values["b_primary"] = False  # only an active assignment can be the primary Store
    if errors:
        raise FieldErrors(errors)
    return values


def _clear_other_primary(db: Session, employee_id: int, keep_id: int | None) -> None:
    for other in db.query(EmployeeStore).filter(
            EmployeeStore.i_employee_id == employee_id, EmployeeStore.b_primary.is_(True),
            EmployeeStore.i_employee_store_id != (keep_id or 0)).with_for_update():
        other.b_primary = False
        _bump(other)
    db.flush()


def create_assignment(db: Session, merchant_id: int, employee_id: int, payload: dict, user_id: int) -> dict:
    _reject_unexpected(payload, ASSIGNMENT_FIELDS)
    employee = _employee(db, merchant_id, employee_id, lock=True)
    if employee.c_status == "terminated":
        raise Conflict("A terminated employee cannot be assigned. Reactivate first.")
    store = _store_of(db, merchant_id, payload.get("store_id"))
    _writable_store(store)
    if any(a.i_store_id == store.i_store_id for a in _assignments(db, employee_id, open_only=True)):
        raise Conflict("This employee is already assigned to this Store.")
    values = _assignment_values(payload, None, employee)
    if values.get("b_primary"):
        _clear_other_primary(db, employee_id, None)
    assignment = EmployeeStore(i_employee_id=employee_id, i_store_id=store.i_store_id, i_merchant_id=merchant_id,
                               **values)
    _stamp(assignment, user_id, created=True)
    db.add(assignment)
    _bump(employee)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("This assignment conflicts with another change. Reload and try again.") from None
    db.refresh(assignment)
    return serialize_assignment(assignment, store, employee)


def _assignment(db: Session, employee_id: int, assignment_id: int) -> EmployeeStore:
    row = db.query(EmployeeStore).filter(EmployeeStore.i_employee_store_id == assignment_id,
                                         EmployeeStore.i_employee_id == employee_id).with_for_update().first()
    if row is None:
        raise NotFound("Store assignment not found.")
    return row


def update_assignment(db: Session, merchant_id: int, employee_id: int, assignment_id: int, payload: dict,
                      user_id: int) -> dict:
    _reject_unexpected(payload, ASSIGNMENT_FIELDS - {"store_id"})
    employee = _employee(db, merchant_id, employee_id, lock=True)
    if employee.c_status == "terminated":
        raise Conflict(TERMINATED_MESSAGE)
    assignment = _assignment(db, employee_id, assignment_id)
    if assignment.c_status == "ended":
        raise Conflict("An ended assignment is history and cannot be changed.")
    store = db.get(Store, assignment.i_store_id)
    _writable_store(store)
    values = _assignment_values(payload, assignment, employee)
    if values.get("b_primary"):
        _clear_other_primary(db, employee_id, assignment.i_employee_store_id)
    for attr, value in values.items():
        setattr(assignment, attr, value)
    _bump(assignment)
    _stamp(assignment, user_id, created=False)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("This assignment conflicts with another change. Reload and try again.") from None
    db.refresh(assignment)
    return serialize_assignment(assignment, store, employee)


def end_assignment(db: Session, merchant_id: int, employee_id: int, assignment_id: int, payload: dict,
                   user_id: int) -> dict:
    _reject_unexpected(payload, {"end_date"})
    employee = _employee(db, merchant_id, employee_id, lock=True)
    assignment = _assignment(db, employee_id, assignment_id)
    if assignment.c_status == "ended":
        raise Conflict("This assignment has already ended.")
    store = db.get(Store, assignment.i_store_id)
    _writable_store(store)
    errors: dict[str, str] = {}
    end = _date(payload, "end_date", errors, label="End Date") or date.today()
    if end < assignment.d_start:
        errors["end_date"] = f"End Date must be on or after the Start Date ({_us(assignment.d_start)})."
    if errors:
        raise FieldErrors(errors)
    assignment.c_status, assignment.d_end = "ended", end
    assignment.b_primary = False
    assignment.b_pos_access = False
    _bump(assignment)
    _stamp(assignment, user_id, created=False)
    db.commit()
    db.refresh(assignment)
    return serialize_assignment(assignment, store, employee)
