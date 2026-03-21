import json
import os

import psycopg

DB_URL = os.getenv(
    "AWSUM_PLATFORM_DATABASE_URL",
    "postgresql://postgres:Awsum123!@127.0.0.1:5432/awsum_platform",
)
if DB_URL.startswith("postgresql+psycopg://"):
    DB_URL = DB_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def column_exists(cur: psycopg.Cursor, table_name: str, column_name: str) -> bool:
    cur.execute(
        """
        select 1
        from information_schema.columns
        where table_schema='public' and table_name=%s and column_name=%s
        limit 1
        """,
        (table_name, column_name),
    )
    return cur.fetchone() is not None


def main() -> int:
    out = {
        "scope": "B2B client count check",
        "counts": {},
        "legacy_markers": {},
    }

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            for table in ("tb_client", "tb_store", "tb_user", "tb_session"):
                cur.execute(f"select count(*) from {table}")
                out["counts"][table] = cur.fetchone()[0]

            if column_exists(cur, "tb_store", "i_account_id"):
                cur.execute("select count(*) from tb_store where i_account_id is not null")
                out["legacy_markers"]["tb_store_i_account_id_not_null"] = cur.fetchone()[0]
            else:
                out["legacy_markers"]["tb_store_i_account_id_not_null"] = None

            if column_exists(cur, "tb_store", "i_customer_id"):
                cur.execute("select count(*) from tb_store where i_customer_id is not null")
                out["legacy_markers"]["tb_store_i_customer_id_not_null"] = cur.fetchone()[0]
            else:
                out["legacy_markers"]["tb_store_i_customer_id_not_null"] = None

    print(json.dumps(out, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
