"""STEP 2-1: MD ownership (Platform / Merchant) and barcode uniqueness per owner, in ONE transaction.

Runs before safe_schema_migrate (that generic step only adds nullable columns, without constraints). A new database
gets everything from the models (create_all). On an existing PostgreSQL database it:

1. adds CHECK tb_merchant.i_merchant_id > 0, so the Platform owner key 0 can never be a Merchant;
2. tb_md_master: i_merchant_id (FK tb_merchant, NULL = Platform) and i_owner_key NOT NULL DEFAULT 0 with
   CHECK (i_owner_key = COALESCE(i_merchant_id, 0)) and UNIQUE (i_md_id, i_owner_key) for the Detail FK;
3. tb_md_detail: i_owner_key NOT NULL (no default) = its MD's key, enforced by the composite FK
   (i_md_id, i_owner_key) -> tb_md_master ON UPDATE / DELETE CASCADE;
4. creates uq_tb_md_detail_owner_barcode (owner, trimmed barcode), proves it refuses a same-owner duplicate, then
   drops the global uq_tb_md_detail_barcode.

Columns that already exist (e.g. added nullable by an earlier generic startup step) are kept and completed, never
recreated. Only the new owner columns are written (0 for every existing row, all Platform). The existing columns of
tb_md_master / tb_md_detail are compared before and after: any difference, or a failed check, raises and the whole
transaction rolls back. Re-runs are no-ops.
"""
import logging

from sqlalchemy import inspect, text

from backend.services.barcode import INDEX_WHERE, LEGACY_UNIQUE_INDEX, UNIQUE_INDEX, duplicate_barcodes

log = logging.getLogger(__name__)

MERCHANT_ID_CHECK = "ck_tb_merchant_id_positive"
MD_MERCHANT_FK = "fk_tb_md_master_merchant"
MD_OWNER_CHECK = "ck_tb_md_master_owner_key"
MD_OWNER_UNIQUE = "uq_tb_md_master_id_owner"
DETAIL_OWNER_FK = "fk_tb_md_detail_md_owner"
NEW_COLUMNS = {"tb_md_master": ("i_merchant_id", "i_owner_key"), "tb_md_detail": ("i_owner_key",)}


class OwnerScopeMigrationError(RuntimeError):
    """A check failed: nothing of this migration is kept (the transaction is rolled back)."""


def _columns(conn, table: str) -> dict:
    return {c["name"]: c for c in inspect(conn).get_columns(table)}


def _constraint_exists(conn, table: str, name: str) -> bool:
    return conn.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :name AND conrelid = CAST(:table AS regclass)"),
                        {"name": name, "table": table}).first() is not None


def index_exists(conn, name: str) -> bool:
    if conn.dialect.name == "postgresql":
        sql = "SELECT 1 FROM pg_indexes WHERE tablename = 'tb_md_detail' AND indexname = :name"
    else:
        sql = "SELECT 1 FROM sqlite_master WHERE type = 'index' AND tbl_name = 'tb_md_detail' AND name = :name"
    return conn.execute(text(sql), {"name": name}).first() is not None


def _snapshot(conn) -> dict:
    """Existing columns only (the new owner columns are expected to change from NULL to their key)."""
    result = {}
    for table, key in (("tb_md_master", "i_md_id"), ("tb_md_detail", "i_md_detail_id")):
        names = [n for n in _columns(conn, table) if n not in NEW_COLUMNS[table]]
        quoted = ", ".join(conn.dialect.identifier_preparer.quote(n) for n in names)
        result[table] = [tuple(r) for r in conn.execute(text(f"SELECT {quoted} FROM {table} ORDER BY {key}"))]
    return result


def _probe_owner_index(conn) -> None:
    """Prove the new index refuses a same-owner duplicate (inside a savepoint that is always rolled back)."""
    sample = conn.execute(text(
        f"SELECT i_md_detail_id FROM tb_md_detail WHERE {INDEX_WHERE} ORDER BY i_md_detail_id LIMIT 1")).first()
    if sample is None:
        return  # no barcode yet: nothing to probe with
    probe = conn.begin_nested()
    try:
        conn.execute(text("""
            INSERT INTO tb_md_detail (i_md_id, i_owner_key, i_detail_no, b_is_default, c_barcode, c_unit, n_price)
            SELECT i_md_id, i_owner_key, i_detail_no + 100000, FALSE, ' ' || c_barcode || ' ', c_unit, 0
            FROM tb_md_detail WHERE i_md_detail_id = :id
        """), {"id": sample[0]})
    except Exception:  # expected: the unique index refused the trimmed duplicate
        probe.rollback()
        return
    probe.rollback()
    raise OwnerScopeMigrationError(f"{UNIQUE_INDEX} did not refuse a duplicate barcode of the same owner.")


def migrate_md_owner_scope(engine) -> dict:
    report = {"steps": [], "duplicates": []}
    with engine.begin() as conn:
        tables = inspect(conn)
        if not all(tables.has_table(t) for t in ("tb_merchant", "tb_md_master", "tb_md_detail")):
            return report  # new database: create_all builds the columns, constraints and index from the models
        postgres = conn.dialect.name == "postgresql"
        if postgres:
            conn.execute(text("LOCK TABLE tb_merchant, tb_md_master, tb_md_detail IN SHARE ROW EXCLUSIVE MODE"))
        before = _snapshot(conn)
        steps = report["steps"]

        def run(label: str, sql: str, params=None):
            result = conn.execute(text(sql), params or {})
            if result.rowcount in (None, -1):
                steps.append(label)
            elif result.rowcount:  # an UPDATE that found nothing to do is not a step
                steps.append(f"{label} ({result.rowcount} rows)")

        # 1. Merchant IDs are positive (0 is the Platform owner key).
        if postgres and not _constraint_exists(conn, "tb_merchant", MERCHANT_ID_CHECK):
            run("tb_merchant CHECK i_merchant_id > 0",
                f"ALTER TABLE tb_merchant ADD CONSTRAINT {MERCHANT_ID_CHECK} CHECK (i_merchant_id > 0)")

        # 2. tb_md_master owner
        md = _columns(conn, "tb_md_master")
        if "i_merchant_id" not in md:
            reference = f"CONSTRAINT {MD_MERCHANT_FK} " if postgres else ""
            run("tb_md_master ADD i_merchant_id",
                f"ALTER TABLE tb_md_master ADD COLUMN i_merchant_id INTEGER {reference}REFERENCES tb_merchant (i_merchant_id)")
        elif postgres and not any(fk["constrained_columns"] == ["i_merchant_id"]
                                  for fk in inspect(conn).get_foreign_keys("tb_md_master")):
            run("tb_md_master FK i_merchant_id", f"ALTER TABLE tb_md_master ADD CONSTRAINT {MD_MERCHANT_FK} "
                "FOREIGN KEY (i_merchant_id) REFERENCES tb_merchant (i_merchant_id)")
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tb_md_master_i_merchant_id ON tb_md_master (i_merchant_id)"))
        if "i_owner_key" not in md:
            run("tb_md_master ADD i_owner_key", "ALTER TABLE tb_md_master ADD COLUMN i_owner_key INTEGER DEFAULT 0")
        run("tb_md_master SET i_owner_key", "UPDATE tb_md_master SET i_owner_key = COALESCE(i_merchant_id, 0) "
            "WHERE i_owner_key IS NULL OR i_owner_key <> COALESCE(i_merchant_id, 0)")
        if postgres:
            conn.execute(text("ALTER TABLE tb_md_master ALTER COLUMN i_owner_key SET DEFAULT 0"))
            conn.execute(text("ALTER TABLE tb_md_master ALTER COLUMN i_owner_key SET NOT NULL"))
            if not _constraint_exists(conn, "tb_md_master", MD_OWNER_CHECK):
                run("tb_md_master CHECK owner key", f"ALTER TABLE tb_md_master ADD CONSTRAINT {MD_OWNER_CHECK} "
                    "CHECK (i_owner_key = COALESCE(i_merchant_id, 0))")
            if not _constraint_exists(conn, "tb_md_master", MD_OWNER_UNIQUE):
                run("tb_md_master UNIQUE (i_md_id, i_owner_key)",
                    f"ALTER TABLE tb_md_master ADD CONSTRAINT {MD_OWNER_UNIQUE} UNIQUE (i_md_id, i_owner_key)")

        # 3. tb_md_detail owner = its MD's owner
        if "i_owner_key" not in _columns(conn, "tb_md_detail"):
            run("tb_md_detail ADD i_owner_key", "ALTER TABLE tb_md_detail ADD COLUMN i_owner_key INTEGER")
        run("tb_md_detail SET i_owner_key", """
            UPDATE tb_md_detail SET i_owner_key = (SELECT m.i_owner_key FROM tb_md_master m WHERE m.i_md_id = tb_md_detail.i_md_id)
            WHERE i_owner_key IS NULL
               OR i_owner_key <> (SELECT m.i_owner_key FROM tb_md_master m WHERE m.i_md_id = tb_md_detail.i_md_id)""")
        if postgres:
            conn.execute(text("ALTER TABLE tb_md_detail ALTER COLUMN i_owner_key DROP DEFAULT"))
            conn.execute(text("ALTER TABLE tb_md_detail ALTER COLUMN i_owner_key SET NOT NULL"))
            if not _constraint_exists(conn, "tb_md_detail", DETAIL_OWNER_FK):
                run("tb_md_detail composite FK", f"ALTER TABLE tb_md_detail ADD CONSTRAINT {DETAIL_OWNER_FK} "
                    "FOREIGN KEY (i_md_id, i_owner_key) REFERENCES tb_md_master (i_md_id, i_owner_key) "
                    "ON UPDATE CASCADE ON DELETE CASCADE")

        # 4. Barcode: unique per owner (new index first, proven, then the global one goes)
        if not index_exists(conn, UNIQUE_INDEX):
            duplicates = duplicate_barcodes(conn)
            if duplicates:  # never changed or deleted: the global index (if any) stays until they are fixed
                report["duplicates"] = duplicates
                log.warning("Barcode index %s not created: %d barcode(s) duplicated within one owner: %s", UNIQUE_INDEX,
                            len(duplicates), "; ".join(f"{code} -> {owners}" for code, owners in duplicates))
            else:
                run(f"CREATE {UNIQUE_INDEX}", f"CREATE UNIQUE INDEX {UNIQUE_INDEX} ON tb_md_detail "
                    f"(i_owner_key, (trim(c_barcode))) WHERE {INDEX_WHERE}")
        if index_exists(conn, UNIQUE_INDEX) and index_exists(conn, LEGACY_UNIQUE_INDEX):
            conn.execute(text(f"DROP INDEX {LEGACY_UNIQUE_INDEX}"))
            if postgres:
                _probe_owner_index(conn)  # the per-owner index alone must refuse a duplicate
            steps.append(f"DROP {LEGACY_UNIQUE_INDEX}")

        # Verification: existing data unchanged, every rule in place.
        if _snapshot(conn) != before:
            raise OwnerScopeMigrationError("Existing MD Master / MD Detail values changed; rolled back.")
        if postgres:
            missing = [name for table, name in (("tb_merchant", MERCHANT_ID_CHECK), ("tb_md_master", MD_MERCHANT_FK),
                                                ("tb_md_master", MD_OWNER_CHECK), ("tb_md_master", MD_OWNER_UNIQUE),
                                                ("tb_md_detail", DETAIL_OWNER_FK))
                       if not _constraint_exists(conn, table, name)
                       and not (name == MD_MERCHANT_FK and any(fk["constrained_columns"] == ["i_merchant_id"]
                                                               for fk in inspect(conn).get_foreign_keys("tb_md_master")))]
            nullable = [f"{t}.i_owner_key" for t in ("tb_md_master", "tb_md_detail")
                        if _columns(conn, t)["i_owner_key"]["nullable"]]
            if missing or nullable:
                raise OwnerScopeMigrationError(f"Owner scope incomplete: missing {missing}, nullable {nullable}")
    if report["steps"]:
        print(f"[MD OWNER] {'; '.join(report['steps'])}")
    return report
