"""Employee HISTORY and BACKGROUND records (STEP 1A-4): one CRUD implementation for the five record kinds.

Kinds (KINDS): history, education, experience, certification, skill. Each kind declares its model, fields and rules;
list / create / update / delete below are shared, with the same Employee scope, audit and validation helpers as the
Employee Master (services.employees, services.master_group, services.dates):

- Scope: the Employee must belong to the login's Merchant (NotFound otherwise); the database composite FK keeps every
  record in its Employee's Merchant. The route guard is the Employee Master one (routers.master_group._employee_scope).
- Audit: AuditMixin through _stamp(); names from _actor_names() (this Merchant's users only, never an ID). A Save that
  changes nothing issues no UPDATE (row_version, Updated By / At unchanged). "Edited" = updated after creation
  (row_version > 1).
- HISTORY: a manager record (not the automatic audit). Only its author edits it; no delete. Allowed for a terminated
  Employee too (the one explicit exception to the terminated read-only rule; the Employee row is never changed).
- BACKGROUND (education / experience / certification / skill): add, edit and delete while the Employee is not
  terminated (terminated: view only, like the Employee Master).
Dates: ISO in the API, parsed by services.dates (MM-DD-YYYY / MM/DD/YYYY accepted), DATE in the DB.

STEP 1A-5 automatic employment HISTORY: record_status_event() is the one writer of SYSTEM records, called by the
existing Employee commands (services.employees: create, Active <-> Inactive save, Terminate, Reactivate) inside their
own transaction (it never commits), so a failed status change leaves no record. A SYSTEM record shows "System" as its
author, keeps its dates in columns, and is never edited or deleted; the manual API can neither create nor change one
(record_type / event_type are not accepted fields).
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models_admin.common_code import CommonCode
from backend.models_admin.employee import Employee
from backend.models_admin.employee_records import (HISTORY_EVENTS, SKILL_PROFICIENCIES, EmployeeCertification,
                                                   EmployeeEducation, EmployeeExperience, EmployeeHistory, EmployeeSkill)
from backend.services.dates import format_date
from backend.services.employees import TERMINATED_MESSAGE, _actor_names, _date, _employee, _iso, _reject_unexpected
from backend.services.master_group import Conflict, FieldErrors, NotFound, _id, _stamp, _text, common_code_query

PROFICIENCY_LABELS = dict(SKILL_PROFICIENCIES)
AUTHOR_ONLY_MESSAGE = "Only the author can edit this record. Add a new record for a follow-up."
SYSTEM_RECORD_MESSAGE = "System records are written automatically and cannot be edited or deleted."
EVENT_LABELS = dict(HISTORY_EVENTS)
SYSTEM_AUTHOR = "System"
NO_DELETE_MESSAGE = "History records cannot be deleted. Add a new record for a correction."

# field spec: payload key -> (attribute, kind, options). kinds: text / longtext / date / year / skill / proficiency.
KINDS = {
    "history": {
        "model": EmployeeHistory, "pk": "i_history_id", "label": "History record",
        "order": (EmployeeHistory.d_record_date.desc(), EmployeeHistory.i_history_id.desc()),
        "fields": {"record_date": ("d_record_date", "date", {"label": "Record Date", "required": True}),
                   "subject": ("c_subject", "text", {"label": "Subject", "required": True, "max": 200}),
                   "description": ("c_description", "longtext", {"label": "Description", "required": True})},
        "author_only": True, "deletable": False, "terminated_writable": True,
    },
    "education": {
        "model": EmployeeEducation, "pk": "i_education_id", "label": "Education",
        "order": (EmployeeEducation.i_graduation_year.desc().nulls_first(), EmployeeEducation.i_education_id.desc()),
        "fields": {"school": ("c_school", "text", {"label": "School", "required": True, "max": 200}),
                   "degree": ("c_degree", "text", {"label": "Degree", "max": 100}),
                   "major": ("c_major", "text", {"label": "Major", "max": 100}),
                   "graduation_year": ("i_graduation_year", "year", {"label": "Graduation Year"}),
                   "remark": ("c_remark", "longtext", {"label": "Remark"})},
    },
    "experience": {
        "model": EmployeeExperience, "pk": "i_experience_id", "label": "Work Experience",
        "order": (EmployeeExperience.d_start.desc().nulls_last(), EmployeeExperience.i_experience_id.desc()),
        "fields": {"company": ("c_company", "text", {"label": "Company", "required": True, "max": 200}),
                   "position": ("c_position", "text", {"label": "Position", "max": 100}),
                   "start_date": ("d_start", "date", {"label": "Start Date"}),
                   "end_date": ("d_end", "date", {"label": "End Date"}),
                   "description": ("c_description", "longtext", {"label": "Description"}),
                   "remark": ("c_remark", "longtext", {"label": "Remark"})},
        "range": ("start_date", "end_date", "End Date must be on or after the Start Date ({})."),
    },
    "certification": {
        "model": EmployeeCertification, "pk": "i_certification_id", "label": "Certification",
        "order": (EmployeeCertification.d_issued.desc().nulls_last(), EmployeeCertification.i_certification_id.desc()),
        "fields": {"name": ("c_name", "text", {"label": "Certificate Name", "required": True, "max": 200}),
                   "issuer": ("c_issuer", "text", {"label": "Issuing Organization", "max": 200}),
                   "issued_date": ("d_issued", "date", {"label": "Issued Date"}),
                   "expiration_date": ("d_expires", "date", {"label": "Expiration Date"}),
                   "remark": ("c_remark", "longtext", {"label": "Remark"})},
        "range": ("issued_date", "expiration_date", "Expiration Date must be on or after the Issued Date ({})."),
    },
    "skill": {
        "model": EmployeeSkill, "pk": "i_skill_id", "label": "Job Skill",
        "order": (EmployeeSkill.i_skill_id.asc(),),
        "fields": {"skill_code_id": ("i_skill_code_id", "skill", {"label": "Skill Name", "required": True}),
                   "proficiency": ("c_proficiency", "proficiency", {"label": "Proficiency Level"}),
                   "remark": ("c_remark", "longtext", {"label": "Remark"})},
    },
}
LONGTEXT_MAX = 4000


def kind_config(kind: str) -> dict:
    cfg = KINDS.get(kind)
    if cfg is None:
        raise NotFound("Unknown record type.")
    return cfg


def can_add_history(employee: Employee) -> bool:
    """Whether HISTORY records may be added for this Employee (also for a terminated one): the one rule shared by the
    HISTORY list (can_add) and the Employee read-only message."""
    return bool(KINDS["history"].get("terminated_writable") or employee.c_status != "terminated")


def record_status_event(db: Session, employee: Employee, event: str, user_id, *, record_date, hire_date=None,
                        previous_hire_date=None, termination_date=None, termination_reason=None) -> EmployeeHistory:
    """Add the SYSTEM HISTORY record of an employment status change (no commit: the caller's transaction decides).

    The dates are kept in their own columns; the description repeats them as text (MM-DD-YYYY) for reading.
    """
    if event not in EVENT_LABELS:
        raise ValueError(f"Unknown history event {event!r}.")
    lines = {
        "HIRED": [f"Initial employment. Hire Date: {format_date(hire_date) or 'not entered'}."],
        "TERMINATED": [f"Termination Date: {format_date(termination_date)}.",
                       f"Reason: {termination_reason}." if termination_reason else "Reason: not entered."],
        "REHIRED": [f"Previous Hire Date: {format_date(previous_hire_date) or 'not entered'}",
                    f"Previous Termination Date: {format_date(termination_date) or 'not entered'}",
                    f"Previous Termination Reason: {termination_reason or 'not entered'}",
                    f"New Hire Date: {format_date(hire_date)}"],
        "DEACTIVATED": ["Status changed from Active to Inactive."],
        "REACTIVATED": ["Status changed from Inactive to Active."],
    }[event]
    row = EmployeeHistory(i_employee_id=employee.i_employee_id, i_merchant_id=employee.i_merchant_id,
                          d_record_date=record_date, c_subject=EVENT_LABELS[event], c_description="\n".join(lines),
                          c_record_type="SYSTEM", c_event_type=event, d_hire_date=hire_date,
                          d_previous_hire_date=previous_hire_date, d_termination_date=termination_date,
                          c_termination_reason=termination_reason)
    _stamp(row, user_id, created=True)
    db.add(row)
    return row


def proficiency_options() -> list[dict]:
    return [{"code": code, "name": label} for code, label in SKILL_PROFICIENCIES]


# ---------- serialization ----------
def _skill_codes(db: Session, rows) -> dict[int, dict]:
    ids = {row.i_skill_code_id for row in rows if isinstance(row, EmployeeSkill)}
    if not ids:
        return {}
    return {r.i_common_code_id: {"id": r.i_common_code_id, "code": r.c_code, "name": r.c_name, "active": bool(r.b_active)}
            for r in db.query(CommonCode).filter(CommonCode.i_common_code_id.in_(ids))}


def _serialize(cfg: dict, row, *, employee: Employee, user_id, actors: dict, skills: dict) -> dict:
    item = {"id": getattr(row, cfg["pk"]), "row_version": row.i_row_version}
    for key, (attr, kind, _opts) in cfg["fields"].items():
        value = getattr(row, attr)
        item[key] = _iso(value) if kind == "date" else value
        if kind == "skill":
            item["skill"] = skills.get(value)
        if kind == "proficiency":
            item["proficiency_name"] = PROFICIENCY_LABELS.get(value or "")
    edited = (row.i_row_version or 1) > 1
    terminated = employee.c_status == "terminated"
    writable = cfg.get("terminated_writable") or not terminated
    system = getattr(row, "c_record_type", "MANUAL") == "SYSTEM"
    item.update({
        "created_by": SYSTEM_AUTHOR if system else actors.get(row.i_created_by), "created_at": _iso(row.dt_created),
        "updated_by": actors.get(row.i_updated_by) if edited else None,
        "updated_at": _iso(row.dt_updated) if edited else None, "edited": edited,
        "can_edit": bool(writable and not system and (not cfg.get("author_only") or row.i_created_by == user_id)),
        "can_delete": bool(writable and not system and cfg.get("deletable", True)),
    })
    if isinstance(row, EmployeeHistory):
        item.update({
            "record_type": row.c_record_type or "MANUAL", "event_type": row.c_event_type,
            "event_name": EVENT_LABELS.get(row.c_event_type or ""),
            "event_hire_date": _iso(row.d_hire_date), "event_previous_hire_date": _iso(row.d_previous_hire_date),
            "event_termination_date": _iso(row.d_termination_date), "event_termination_reason": row.c_termination_reason,
            # who caused the status change (the record itself is the system's)
            "recorded_by": actors.get(row.i_created_by) if system else None,
        })
    return item


def _rows(db: Session, cfg: dict, employee: Employee):
    model = cfg["model"]
    return db.query(model).filter(model.i_employee_id == employee.i_employee_id,
                                  model.i_merchant_id == employee.i_merchant_id).order_by(*cfg["order"]).all()


def _listing(db: Session, cfg: dict, employee: Employee, user_id) -> dict:
    rows = _rows(db, cfg, employee)
    actors = _actor_names(db, employee.i_merchant_id, rows)
    skills = _skill_codes(db, rows)
    terminated = employee.c_status == "terminated"
    can_add = can_add_history(employee) if cfg is KINDS["history"] else not terminated
    return {"items": [_serialize(cfg, row, employee=employee, user_id=user_id, actors=actors, skills=skills)
                      for row in rows],
            "can_add": can_add,
            "read_only_message": None if can_add else TERMINATED_MESSAGE}


def list_records(db: Session, merchant_id: int, employee_id: int, kind: str, user_id) -> dict:
    return _listing(db, kind_config(kind), _employee(db, merchant_id, employee_id), user_id)


# ---------- validation ----------
def _validated(db: Session, cfg: dict, payload: dict, current, merchant_id: int) -> dict:
    _reject_unexpected(payload, cfg["fields"])
    errors: dict[str, str] = {}
    values: dict = {}
    for key, (attr, kind, opts) in cfg["fields"].items():
        if current is not None and key not in payload:
            continue  # key absent on edit: the saved value stays
        label, required = opts.get("label", key), opts.get("required", False)
        if kind == "text":
            values[attr] = _text(payload, key, opts["max"], errors, required=required, label=label)
        elif kind == "longtext":
            values[attr] = _text(payload, key, LONGTEXT_MAX, errors, required=required, label=label)
        elif kind == "date":
            values[attr] = _date(payload, key, errors, label=label, required=required)
        elif kind == "year":
            year = _id(payload, key, errors, required=required, label=label)
            if year is not None and not 1900 <= year <= 2100:
                errors[key] = f"{label} must be between 1900 and 2100."
            values[attr] = year
        elif kind == "skill":
            saved = getattr(current, attr) if current is not None else None
            code_id = _id(payload, key, errors, required=required, label=label)
            if code_id is not None and key not in errors:
                code = common_code_query(db, "JOB_SKILL", merchant_id).filter(
                    CommonCode.i_common_code_id == code_id).first()  # this Merchant's JOB_SKILL codes only
                if code is None:
                    errors[key] = f"{label} does not exist."
                elif not code.b_active and code_id != saved:
                    errors[key] = f"{label} is inactive."
            values[attr] = code_id
        elif kind == "proficiency":
            level = str(payload.get(key) or "").strip().upper() or None
            if level is not None and level not in PROFICIENCY_LABELS:
                errors[key] = f"Select a valid {label}."
            values[attr] = level
    if "range" in cfg:
        start_key, end_key, message = cfg["range"]
        start_attr, end_attr = cfg["fields"][start_key][0], cfg["fields"][end_key][0]
        start = values.get(start_attr, getattr(current, start_attr) if current is not None else None)
        end = values.get(end_attr, getattr(current, end_attr) if current is not None else None)
        if start and end and end < start and start_key not in errors and end_key not in errors:
            errors[end_key] = message.format(format_date(start))
    if errors:
        raise FieldErrors(errors)
    return values


def _writable_employee(db: Session, cfg: dict, merchant_id: int, employee_id: int) -> Employee:
    employee = _employee(db, merchant_id, employee_id, lock=True)
    if employee.c_status == "terminated" and not cfg.get("terminated_writable"):
        raise Conflict(TERMINATED_MESSAGE)
    return employee


def _record(db: Session, cfg: dict, employee: Employee, record_id: int):
    model = cfg["model"]
    row = db.query(model).filter(getattr(model, cfg["pk"]) == record_id, model.i_employee_id == employee.i_employee_id,
                                 model.i_merchant_id == employee.i_merchant_id).with_for_update().first()
    if row is None:
        raise NotFound(f"{cfg['label']} not found.")
    return row


# ---------- commands ----------
def create_record(db: Session, merchant_id: int, employee_id: int, kind: str, payload: dict, user_id) -> dict:
    cfg = kind_config(kind)
    employee = _writable_employee(db, cfg, merchant_id, employee_id)
    values = _validated(db, cfg, payload or {}, None, merchant_id)
    row = cfg["model"](i_employee_id=employee.i_employee_id, i_merchant_id=employee.i_merchant_id, **values)
    _stamp(row, user_id, created=True)
    db.add(row)
    db.commit()
    return _result(db, cfg, employee, row, user_id)


def update_record(db: Session, merchant_id: int, employee_id: int, kind: str, record_id: int, payload: dict,
                  user_id) -> dict:
    cfg = kind_config(kind)
    employee = _writable_employee(db, cfg, merchant_id, employee_id)
    row = _record(db, cfg, employee, record_id)
    if getattr(row, "c_record_type", "MANUAL") == "SYSTEM":
        raise HTTPException(403, SYSTEM_RECORD_MESSAGE)
    if cfg.get("author_only") and row.i_created_by != user_id:
        raise HTTPException(403, AUTHOR_ONLY_MESSAGE)
    values = _validated(db, cfg, payload or {}, row, merchant_id)
    changed = {attr: value for attr, value in values.items() if getattr(row, attr) != value}
    if changed:  # a Save that changes nothing issues no UPDATE (Updated By / At and row_version stay)
        for attr, value in changed.items():
            setattr(row, attr, value)
        row.i_row_version = (row.i_row_version or 1) + 1
        _stamp(row, user_id, created=False)
    db.commit()
    return _result(db, cfg, employee, row, user_id)


def delete_record(db: Session, merchant_id: int, employee_id: int, kind: str, record_id: int) -> None:
    cfg = kind_config(kind)
    if not cfg.get("deletable", True):
        raise HTTPException(403, NO_DELETE_MESSAGE)
    employee = _writable_employee(db, cfg, merchant_id, employee_id)
    db.delete(_record(db, cfg, employee, record_id))
    db.commit()


def _result(db: Session, cfg: dict, employee: Employee, row, user_id) -> dict:
    db.refresh(row)
    actors = _actor_names(db, employee.i_merchant_id, [row])
    return _serialize(cfg, row, employee=employee, user_id=user_id, actors=actors, skills=_skill_codes(db, [row]))
