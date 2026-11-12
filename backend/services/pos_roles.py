"""POS Role Registry (code; not tb_role). STEP 1A: the role list stored on a Store assignment (tb_employee_store.c_pos_role).

tb_role stays the web login role list (Admin detection, organization login, Platform user roles); a POS Role never
grants web access, and a Portal Role never grants POS rights. POS permissions per role and their resolver
(role AND Employee / assignment active AND Store active AND feature AND Device Role) come in STEP 2, in this module.
Merchant-defined POS roles (a later step) will keep these codes as the built-in set.
"""
POS_ROLES = (
    ("MANAGER", "Manager"),
    ("CASHIER", "Cashier"),
    ("SERVER", "Server"),
    ("KITCHEN", "Kitchen"),
)
POS_ROLE_CODES = frozenset(code for code, _label in POS_ROLES)
POS_ROLE_LABELS = dict(POS_ROLES)


def pos_role_options() -> list[dict]:
    return [{"code": code, "name": label} for code, label in POS_ROLES]


def normalize_pos_role(value) -> str | None:
    """The registry code for a submitted value; None when empty. Raises ValueError for an unknown code."""
    code = str(value or "").strip().upper()
    if not code:
        return None
    if code not in POS_ROLE_CODES:
        raise ValueError("Select a valid POS Role.")
    return code
