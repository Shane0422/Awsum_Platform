"""
migrate_store_codes_to_str.py
---
Force-rename all CLT_XXXXX store codes to STR_XXXXX format.
- Admin store (1000001) and non-CLT_ codes are left untouched.
- Renumbers sequentially from 20001 ordered by i_store_id.
- Also resets i_store_seq to the new numeric value.

Run: python tools/migrate_store_codes_to_str.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault(
    "AWSUM_PLATFORM_DATABASE_URL",
    "postgresql+psycopg://postgres:Awsum123!@127.0.0.1:5432/awsum_platform",
)

from sqlalchemy import create_engine, text

DB_URL = os.environ["AWSUM_PLATFORM_DATABASE_URL"]
ADMIN_STORE_ID = 1000001
NEW_PREFIX = "STR_"
SEQ_START = 20001

engine = create_engine(DB_URL)

with engine.begin() as conn:
    # Fetch all CLT_ stores (excluding admin), ordered by store id
    rows = conn.execute(text(
        """
        SELECT i_store_id, c_store_code
        FROM tb_store
        WHERE c_store_code ~ '^CLT_[0-9]+$'
          AND i_store_id != :admin_id
          AND (c_is_test IS NULL OR c_is_test = 0)
        ORDER BY i_store_id
        """
    ), {"admin_id": ADMIN_STORE_ID}).fetchall()

    if not rows:
        print("[INFO] No CLT_ store codes found. Nothing to migrate.")
        sys.exit(0)

    print(f"[INFO] Found {len(rows)} stores to rename.\n")
    print(f"  {'OLD CODE':<18}  {'NEW CODE':<18}  i_store_id")
    print("  " + "-" * 58)

    for idx, (store_id, old_code) in enumerate(rows):
        new_seq = SEQ_START + idx
        new_code = f"{NEW_PREFIX}{new_seq:05d}"
        conn.execute(text(
            """
            UPDATE tb_store
            SET c_store_code = :new_code, i_store_seq = :new_seq
            WHERE i_store_id = :sid
            """
        ), {"new_code": new_code, "new_seq": new_seq, "sid": store_id})
        print(f"  {old_code:<18} → {new_code:<18}  id={store_id}")

    print(f"\n[OK] Renamed {len(rows)} store codes. Committed.")
