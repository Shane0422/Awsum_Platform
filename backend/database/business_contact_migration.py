"""Create tb_business_contact and copy legacy Agency / Merchant / Store contact columns once.

Legacy contact columns are kept untouched. The copy runs only in the transaction that
creates the table, so it is safe to re-run on every startup.
"""
from sqlalchemy import inspect, text

from backend.database.pg_platform import ADMIN_STORE_ID

# "John A Smith" -> first "John A", last "Smith"; a single word becomes the first name.
_FIRST = "NULLIF(trim(regexp_replace(trim(COALESCE({col}, '')), '\\s+\\S+$', '')), '')"
_LAST = ("CASE WHEN trim(COALESCE({col}, '')) ~ '\\s' "
         "THEN substring(trim({col}) FROM '\\S+$') END")

_INSERT = """
    INSERT INTO tb_business_contact
        (c_owner_type, i_owner_id, c_contact_role, c_first_name, c_last_name, c_email, c_phone,
         b_is_primary, i_display_order, c_status, dt_created, dt_updated)
    SELECT :owner_type, src.owner_id, 'PRIMARY', src.first_name, src.last_name,
           NULLIF(trim(src.email), ''), NULLIF(trim(src.phone), ''), TRUE, 1, 'active', now(), now()
    FROM ({source}) src
    WHERE COALESCE(src.first_name, src.last_name, NULLIF(trim(src.email), ''), NULLIF(trim(src.phone), '')) IS NOT NULL
"""


def _sources() -> dict:
    agency_first, agency_last = _FIRST.format(col="c_contact_name"), _LAST.format(col="c_contact_name")
    return {
        "AGENCY": ("tb_agency", f"""
            SELECT i_agency_id AS owner_id, {agency_first} AS first_name, {agency_last} AS last_name,
                   c_email AS email, c_phone AS phone
            FROM tb_agency"""),
        "MERCHANT": ("tb_client", """
            SELECT i_client_id AS owner_id, NULLIF(trim(c_first_name), '') AS first_name,
                   NULLIF(trim(c_last_name), '') AS last_name, c_email AS email, c_phone AS phone
            FROM tb_client"""),
        "STORE": ("tb_store", f"""
            SELECT i_store_id AS owner_id,
                   CASE WHEN NULLIF(trim(c_contact_name), '') IS NOT NULL THEN {agency_first}
                        ELSE NULLIF(trim(c_first_name), '') END AS first_name,
                   CASE WHEN NULLIF(trim(c_contact_name), '') IS NOT NULL THEN {agency_last}
                        ELSE NULLIF(trim(c_last_name), '') END AS last_name,
                   c_email AS email, c_phone AS phone
            FROM tb_store WHERE i_store_id <> {int(ADMIN_STORE_ID)}"""),
    }


def migrate_business_contacts(engine) -> dict:
    from backend.models_admin.business_contact import BusinessContact

    counts = {}
    with engine.begin() as conn:
        if inspect(conn).has_table("tb_business_contact"):
            return counts
        BusinessContact.__table__.create(conn)
        for owner_type, (table, source) in _sources().items():
            if inspect(conn).has_table(table):
                counts[owner_type] = conn.execute(
                    text(_INSERT.format(source=source)), {"owner_type": owner_type}
                ).rowcount
    print(f"[MIGRATION] tb_business_contact created; legacy contacts copied: {counts}")
    return counts
