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

STEP 1A-2 (login IDs, every uniqueness rule in the Merchant scope; the same value may exist in another Merchant):
- Address: the common AddressMixin fields, optional, saved with the Employee (independent of Merchant / Store).
- POS Login: enabled flag + POS Login ID (4-10 digits, unique per Merchant, shared by all its Stores). The screen
  suggests the phone's last 4 digits; a saved ID never follows a phone change. Duplicates are refused (never
  overwritten or renumbered); the database unique index is the final guard for concurrent saves.
- POS PIN: set / reset only (digits, bcrypt hash in c_pin_hash; interim 4-8 digits until the STEP 2 policy); never
  returned. Each change bumps i_pin_version for the Store Server sync.
- Portal Login: a tb_platform_user account of this Merchant (role "Employee", linked by i_platform_user_id). Its email
  is the Portal Login ID (unique per Merchant with every user of the Merchant, the Merchant Admin included:
  uq_platform_user_merchant_email); the password is set / reset here (bcrypt, must change at next login) and kept
  only on the account. Disabling, an email change or a reset ends the account's sessions. Portal sign-in rights
  for the Employee role come with the Portal Role step (routers.auth admits organization admin roles only).
- Termination also disables POS Login and the Portal account; Reactivate restores neither.

STEP 1A-3 Job Grade / Job Title: optional codes of this Merchant's Common Maintenance JOB_GRADE / JOB_TITLE groups
(tb_common_code ids). The same rule as the MD Color: the code must be of that group and this Merchant; an inactive code
is accepted only when it is the Employee's saved code (it keeps showing by name); blank clears it; key absent keeps it.
Portal only (list subtitle shows the Title, the export both names); never POS / receipts.
STEP 1A-3 Personal Information: optional Birthday (common date rule, never in the future) and Gender (a code of
models_admin.employee.EMPLOYEE_GENDERS, the fixed list in code; blank = not entered). Merchant scope only like every
Employee field; shown only in the edit form (never the list display, search, export, POS or receipts).

Audit (common AuditMixin columns, unchanged): the response adds created_by / updated_by, the names of this
Merchant's users only (never an ID, email or another owner's user; otherwise None, shown as an em dash). Last Updated
moves only on a real change: a Save that changes no value and no photo, or a Portal Access save that changes nothing,
issues no UPDATE (no row_version bump, no stamp, so the dt_updated onupdate does not fire). PIN, Terminate and
Reactivate always change the row. Store assignment changes keep their own audit on tb_employee_store.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import exists, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.database.pg_platform import ADMIN_STORE_ID
from backend.models_admin.employee import EMPLOYEE_GENDERS, Employee, EmployeeStore
from backend.models_admin.merchant import Merchant
from backend.models_admin.platform_user import EMPLOYEE_ROLE_NAME, PlatformUser
from backend.models_admin.role import Role
from backend.models_admin.store import Store
from backend.services.app_navigation import STORE_INACTIVE_READ_ONLY
from backend.services.mail_common import validate_email_address
from backend.models_admin.common_code import CommonCode
from backend.services.master_group import (Conflict, FieldErrors, NotFound, _id, _stamp, _text, common_code_query)
from backend.services.organization_users import organization_user_query
from backend.services.pos_roles import POS_ROLE_LABELS, normalize_pos_role
from backend.services.web_sessions import terminate_user_sessions
from backend.utils.passwords import MAX_BCRYPT_PASSWORD_BYTES, hash_secret, secret_too_long

CODE_PREFIX = "EMP"
CODE_DIGITS = 5
POS_NUMBER_START = 1001
OPEN = ("active", "inactive")  # assignment statuses that are not history
# Address (common AddressMixin columns): payload key -> (attribute, max length).
ADDRESS_FIELDS = {"address_line1": ("c_address_line1", 200), "address_line2": ("c_address_line2", 200),
                  "city": ("c_city", 100), "state": ("c_state", 50), "zip": ("c_zip", 20),
                  "country": ("c_country", 100)}
DEFAULT_COUNTRY = "United States"  # the common address default (AddressMixin)
# Job Grade / Job Title: payload key -> (attribute, Common Maintenance group, label).
JOB_FIELDS = {"job_grade_code_id": ("i_job_grade_code_id", "JOB_GRADE", "Job Grade"),
              "job_title_code_id": ("i_job_title_code_id", "JOB_TITLE", "Job Title")}
GENDER_LABELS = dict(EMPLOYEE_GENDERS)
EDITABLE = frozenset({"first_name", "last_name", "display_name", "phone", "email", "hire_date", "status",
                      "pos_login_enabled", "pos_login_id", *ADDRESS_FIELDS, *JOB_FIELDS, "birth_date", "gender"})
POS_LOGIN_ID = re.compile(r"^\d{4,10}$")
POS_PIN = re.compile(r"^\d{4,8}$")  # interim until the STEP 2 PIN policy
PORTAL_FIELDS = frozenset({"enabled", "email", "password", "confirm_password"})
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


def _bump_keep_audit(row: Employee) -> None:
    """Version bump for a change that has its own audit (a Store assignment): the saved dt_updated is written back
    explicitly, so the AuditMixin onupdate does not move the Employee's Last Updated."""
    _bump(row)
    flag_modified(row, "dt_updated")


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


def _portal_users(db: Session, rows: list[Employee]) -> dict[int, PlatformUser]:
    ids = [row.i_platform_user_id for row in rows if row.i_platform_user_id]
    if not ids:
        return {}
    return {user.i_platform_user_id: user
            for user in db.query(PlatformUser).filter(PlatformUser.i_platform_user_id.in_(ids))}


def _actor_names(db: Session, merchant_id: int, rows: list[Employee]) -> dict[int, str]:
    """Created By / Last Updated By: names of this Merchant's own users only (no email, ID or other owners)."""
    ids = {uid for row in rows for uid in (row.i_created_by, row.i_updated_by) if uid}
    if not ids:
        return {}
    users = organization_user_query(db, "MERCHANT", merchant_id).filter(PlatformUser.i_platform_user_id.in_(ids))
    names = {}
    for user in users:
        name = " ".join(part.strip() for part in (user.c_first_name, user.c_last_name) if part and part.strip())
        if name:
            names[user.i_platform_user_id] = name
    return names


def _portal_view(user: PlatformUser | None) -> dict:
    """Portal Login state: never the password or its hash, only whether one is registered (password_set)."""
    if user is None:
        return {"enabled": False, "email": None, "status": "not_created", "must_change_password": False,
                "password_set": False}
    active = (user.c_status or "").lower() == "active"
    return {"enabled": active, "email": user.c_email, "status": "active" if active else "inactive",
            "must_change_password": active and user.i_must_change_password == 1,
            "password_set": bool(user.c_password)}


def _pos_login_status(row: Employee) -> str:
    if not row.b_pos_login_enabled or row.c_status == "terminated":
        return "disabled"
    if not row.c_pin_hash:
        return "pin_not_set"
    if row.dt_pin_locked_until and row.dt_pin_locked_until > datetime.now():
        return "locked"
    return "active"


def gender_options() -> list[dict]:
    """Gender selector (Employee Master, Merchant login): the fixed list in code."""
    return [{"code": code, "name": label} for code, label in EMPLOYEE_GENDERS]


def _job_codes(db: Session, rows: list[Employee]) -> dict[int, dict]:
    """Job Grade / Job Title codes referenced by these Employees: id -> {id, code, name, active} (inactive included)."""
    ids = {getattr(row, attr) for row in rows for attr, _group, _label in JOB_FIELDS.values()} - {None}
    if not ids:
        return {}
    return {r.i_common_code_id: {"id": r.i_common_code_id, "code": r.c_code, "name": r.c_name,
                                 "active": bool(r.b_active)}
            for r in db.query(CommonCode).filter(CommonCode.i_common_code_id.in_(ids))}


def serialize(row: Employee, summary: dict | None = None, portal_user: PlatformUser | None = None,
              actors: dict[int, str] | None = None, jobs: dict[int, dict] | None = None) -> dict:
    from backend.services.images import image_url
    actors = actors or {}
    jobs = jobs or {}
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
        "job_grade_code_id": row.i_job_grade_code_id, "job_grade": jobs.get(row.i_job_grade_code_id),
        "job_title_code_id": row.i_job_title_code_id, "job_title": jobs.get(row.i_job_title_code_id),
        "birth_date": _iso(row.d_birth_date), "gender": row.c_gender, "gender_name": GENDER_LABELS.get(row.c_gender or ""),
        "image_path": row.c_image_path, "image_url": image_url(row.c_image_path) if row.c_image_path else None,
        "primary_store": summary["primary_store"], "store_count": summary["store_count"],
        "stores": summary["stores"], "pos_access": summary["pos_access"],
        **{key: getattr(row, attr) for key, (attr, _limit) in ADDRESS_FIELDS.items()},
        "pos_login_enabled": bool(row.b_pos_login_enabled), "pos_login_id": row.c_pos_login_id,
        "pos_pin_set": bool(row.c_pin_hash), "pos_login_status": _pos_login_status(row),
        "pin_changed_at": _iso(row.dt_pin_changed),
        "portal_login": _portal_view(portal_user),
        "read_only": terminated, "read_only_message": TERMINATED_MESSAGE if terminated else None,
        "row_version": row.i_row_version,
        "created_at": _iso(row.dt_created), "updated_at": _iso(row.dt_updated),
        "created_by": actors.get(row.i_created_by), "updated_by": actors.get(row.i_updated_by),
    }


def _serialized(db: Session, row: Employee) -> dict:
    return serialize(row, _store_summaries(db, [row.i_employee_id])[row.i_employee_id],
                     _portal_users(db, [row]).get(row.i_platform_user_id), _actor_names(db, row.i_merchant_id, [row]),
                     _job_codes(db, [row]))


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
            conditions.append(Employee.c_pos_login_id == term)
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
    users = _portal_users(db, rows)
    actors = _actor_names(db, merchant_id, rows)
    jobs = _job_codes(db, rows)
    return {"items": [serialize(row, summaries[row.i_employee_id], users.get(row.i_platform_user_id), actors, jobs)
                      for row in rows], "total": total, "page": page,
            "pages": pages, "size": size, "sort": key, "direction": "desc" if direction == "desc" else "asc"}


def export_employees(db: Session, merchant_id: int, *, q="", status="", store_id=None, pos_role=None, sort="",
                     direction="asc") -> tuple[list[str], list[list[str]]]:
    """Every matching Employee (no PIN or login data)."""
    query, _key = _ordered(_query(db, merchant_id, q=q, status=status, store_id=store_id, pos_role=pos_role),
                           sort, direction)
    rows = query.all()
    summaries = _store_summaries(db, [row.i_employee_id for row in rows])
    jobs = _job_codes(db, rows)
    headers = ["Employee Code", "POS #", "Last Name", "First Name", "Display Name", "Phone", "Email", "Hire Date",
               "Job Grade", "Job Title", "Termination Date", "Status", "Primary Store", "Stores", "POS Access",
               "Address Line 1", "Address Line 2", "City", "State", "ZIP Code", "Country"]
    data = []
    for row in rows:
        info = summaries[row.i_employee_id]
        data.append([row.c_employee_code, str(row.i_pos_number), row.c_last_name or "", row.c_first_name or "",
                     row.c_display_name or "", row.c_phone or "", row.c_email or "", _us(row.d_hire_date),
                     *[(jobs.get(getattr(row, attr)) or {}).get("name", "") for attr, _g, _l in JOB_FIELDS.values()],
                     _us(row.d_termination_date), row.c_status.title(),
                     info["primary_store"]["name"] if info["primary_store"] else "", ", ".join(info["stores"]),
                     "Yes" if info["pos_access"] else "No",
                     *[getattr(row, attr) or "" for attr, _limit in ADDRESS_FIELDS.values()]])
    return headers, data


# ---------- Employee save ----------
def _pos_login_taken(db: Session, merchant_id: int, login_id: str, employee_id: int | None) -> bool:
    query = db.query(Employee.i_employee_id).filter(Employee.i_merchant_id == merchant_id,
                                                    Employee.c_pos_login_id == login_id)
    if employee_id:
        query = query.filter(Employee.i_employee_id != employee_id)
    return query.first() is not None


POS_LOGIN_DUPLICATE = "POS Login ID {} is already used by another employee of this Merchant. Enter a different ID."


def _validated(db: Session, payload: dict, current: Employee | None, merchant_id: int) -> dict:
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
    for key, (attr, limit) in ADDRESS_FIELDS.items():
        if key in payload or current is None:
            values[attr] = _text(payload, key, limit, errors)
    if values.get("c_state"):
        values["c_state"] = values["c_state"].upper()
    if current is None and not values.get("c_country"):
        values["c_country"] = DEFAULT_COUNTRY
    if "pos_login_id" in payload or current is None:
        login_id = str(payload.get("pos_login_id") or "").strip()
        if login_id and not POS_LOGIN_ID.match(login_id):
            errors["pos_login_id"] = "POS Login ID must be 4 to 10 digits."
        values["c_pos_login_id"] = login_id or None
    if "pos_login_enabled" in payload or current is None:
        values["b_pos_login_enabled"] = _flag(payload.get("pos_login_enabled"))
    login_id = values.get("c_pos_login_id", current.c_pos_login_id if current else None)
    enabled = values.get("b_pos_login_enabled", current.b_pos_login_enabled if current else False)
    if enabled and not login_id and "pos_login_id" not in errors:
        errors["pos_login_id"] = "Enter a POS Login ID to enable POS Login."
    if (login_id and "pos_login_id" not in errors and (current is None or login_id != current.c_pos_login_id)
            and _pos_login_taken(db, merchant_id, login_id, current.i_employee_id if current else None)):
        errors["pos_login_id"] = POS_LOGIN_DUPLICATE.format(login_id)
    if "hire_date" in payload or current is None:
        values["d_hire_date"] = _date(payload, "hire_date", errors, label="Hire Date")
    if "birth_date" in payload or current is None:
        birth = _date(payload, "birth_date", errors, label="Birthday")
        if birth and birth > date.today():
            errors["birth_date"] = "Birthday cannot be in the future."
        values["d_birth_date"] = birth
    if "gender" in payload or current is None:
        gender = str(payload.get("gender") or "").strip().upper() or None
        if gender is not None and gender not in GENDER_LABELS:
            errors["gender"] = "Select a valid Gender."
        values["c_gender"] = gender
    for key, (attr, group, label) in JOB_FIELDS.items():
        if key in payload or current is None:
            values[attr] = _job_code(db, payload, key, group, label, getattr(current, attr) if current else None,
                                     merchant_id, errors)
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


def _job_code(db: Session, payload: dict, key: str, group: str, label: str, saved: int | None, merchant_id: int,
              errors: dict) -> int | None:
    """A Job Grade / Job Title code id: this Merchant's code of that group; active, or the Employee's saved one."""
    code_id = _id(payload, key, errors, required=False, label=label)
    if key in errors or code_id is None:
        return saved if key in errors else None
    code = common_code_query(db, group, merchant_id).filter(CommonCode.i_common_code_id == code_id).first()
    if code is None:
        errors[key] = f"{label} does not exist."
    elif not code.b_active and code_id != saved:
        errors[key] = f"{label} is inactive."
    return code_id


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
    values = _validated(db, payload, None, merchant_id)
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
            login_id = values.get("c_pos_login_id")
            if login_id and _pos_login_taken(db, merchant_id, login_id, None):  # saved concurrently: refuse
                raise FieldErrors({"pos_login_id": POS_LOGIN_DUPLICATE.format(login_id)}) from None
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
    values = _validated(db, payload, row, merchant_id)
    changed = {attr: value for attr, value in values.items() if getattr(row, attr) != value}
    photo_changed = image_data is not None or (remove_image and bool(row.c_image_path))
    for attr, value in changed.items():
        setattr(row, attr, value)
    if changed or photo_changed:  # a Save that changes nothing issues no UPDATE (no version bump, no stamp)
        _bump(row)
        _stamp(row, user_id, created=False)
    try:
        db.flush()
    except IntegrityError:  # the POS Login ID was saved concurrently by another employee of this Merchant
        db.rollback()
        raise FieldErrors({"pos_login_id": POS_LOGIN_DUPLICATE.format(values.get("c_pos_login_id") or "")}) \
            from None
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
    row.b_pos_login_enabled = False  # the POS Login ID stays reserved for this employee (history)
    _disable_portal(db, row, user_id)
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
    _bump_keep_audit(employee)  # the assignment has its own audit; the Employee's Last Updated stays
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


# ---------- POS PIN / Portal Login (STEP 1A-2) ----------
def set_pos_pin(db: Session, merchant_id: int, employee_id: int, payload: dict, user_id: int) -> dict:
    """Set or reset the POS PIN: hash only, never returned; bumps the PIN version and clears a lockout."""
    _reject_unexpected(payload, {"pin", "confirm_pin"})
    row = _employee(db, merchant_id, employee_id, lock=True)
    if row.c_status == "terminated":
        raise Conflict(TERMINATED_MESSAGE)
    pin = str(payload.get("pin") or "").strip()
    errors: dict[str, str] = {}
    if not row.c_pos_login_id:
        errors["pin"] = "Save a POS Login ID before setting the PIN."
    elif not POS_PIN.match(pin):
        errors["pin"] = "PIN must be 4 to 8 digits."
    elif pin != str(payload.get("confirm_pin") or "").strip():
        errors["confirm_pin"] = "PINs do not match."
    if errors:
        raise FieldErrors(errors)
    row.c_pin_hash = hash_secret(pin)
    row.i_pin_version = (row.i_pin_version or 0) + 1
    row.dt_pin_changed, row.i_pin_changed_by = datetime.now(), user_id
    row.i_pin_failed_count, row.dt_pin_locked_until = 0, None
    _bump(row)
    _stamp(row, user_id, created=False)
    db.commit()
    db.refresh(row)
    return _serialized(db, row)


def _portal_user(db: Session, row: Employee) -> PlatformUser | None:
    if not row.i_platform_user_id:
        return None
    return db.query(PlatformUser).filter(PlatformUser.i_platform_user_id == row.i_platform_user_id,
                                         PlatformUser.i_merchant_id == row.i_merchant_id).with_for_update().first()


def _disable_portal(db: Session, row: Employee, user_id: int) -> None:
    user = _portal_user(db, row)
    if user is None:
        return
    if (user.c_status or "").lower() != "inactive":
        user.c_status, user.dt_updated = "inactive", datetime.now()
    terminate_user_sessions(db, user.i_platform_user_id, user_id)


EMAIL_TAKEN = "This email is already a login of this Merchant. Enter a different email."


def _portal_email(db: Session, merchant_id: int, payload: dict, user: PlatformUser | None, errors: dict) -> str | None:
    try:
        email = validate_email_address(payload.get("email")).lower()  # stored normalized (trim + lowercase)
    except ValueError as exc:
        errors["email"] = str(exc)
        return None
    query = organization_user_query(db, "MERCHANT", merchant_id).filter(
        func.lower(func.trim(PlatformUser.c_email)) == email)
    if user is not None:
        query = query.filter(PlatformUser.i_platform_user_id != user.i_platform_user_id)
    if query.first() is not None:
        errors["email"] = EMAIL_TAKEN
    return email


def set_portal_access(db: Session, merchant_id: int, employee_id: int, payload: dict, user_id: int) -> dict:
    """Enable (create / update the account, set or reset its password) or disable the Employee's Portal Login."""
    _reject_unexpected(payload, PORTAL_FIELDS)
    row = _employee(db, merchant_id, employee_id, lock=True)
    if row.c_status == "terminated":
        raise Conflict(TERMINATED_MESSAGE)
    user = _portal_user(db, row)
    if not _flag(payload.get("enabled")):
        changed = user is not None and (user.c_status or "").lower() != "inactive"
        _disable_portal(db, row, user_id)
    else:
        errors: dict[str, str] = {}
        email = _portal_email(db, merchant_id, payload, user, errors)
        password = str(payload.get("password") or "")
        if user is None and not password:
            errors["password"] = "Set a password to create the Portal Login."
        elif password and secret_too_long(password):
            errors["password"] = f"Password is too long (max {MAX_BCRYPT_PASSWORD_BYTES} bytes)."
        elif password and password != str(payload.get("confirm_password") or ""):
            errors["confirm_password"] = "Passwords do not match."
        if errors:
            raise FieldErrors(errors)
        role = db.query(Role).filter(Role.c_name == EMPLOYEE_ROLE_NAME).first()
        if role is None or (role.c_status or "").lower() != "active":
            raise Conflict("An active Employee Role is required. Apply the Employee Management migration.")
        now = datetime.now()
        changed = user is None or bool(password) or user.c_email != email or (user.c_status or "").lower() != "active"
        if user is None:
            user = PlatformUser(i_merchant_id=merchant_id, i_store_id=ADMIN_STORE_ID, i_role_id=role.i_role_id,
                                c_email=email, c_first_name=row.c_first_name, c_last_name=row.c_last_name,
                                c_phone=row.c_phone, c_password=hash_secret(password), c_status="active",
                                i_must_change_password=1, dt_created=now, dt_updated=now)
            db.add(user)
        else:
            ends_sessions = changed
            if changed:  # the same email of an active account without a password changes nothing
                user.c_email, user.c_status, user.dt_updated = email, "active", now
            if password:
                user.c_password, user.i_must_change_password = hash_secret(password), 1
            if ends_sessions:
                terminate_user_sessions(db, user.i_platform_user_id, user_id)
        try:
            db.flush()
        except IntegrityError:  # the same email was saved concurrently in this Merchant
            db.rollback()
            raise FieldErrors({"email": EMAIL_TAKEN}) from None
        if row.i_platform_user_id != user.i_platform_user_id:
            row.i_platform_user_id, changed = user.i_platform_user_id, True
    if changed:  # Portal account created / changed / reset / disabled: the Employee's Last Updated
        _bump(row)
        _stamp(row, user_id, created=False)
    db.commit()
    db.refresh(row)
    return _serialized(db, row)
