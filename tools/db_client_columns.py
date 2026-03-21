import json
import os

import psycopg

DB_URL = os.getenv(
    "AWSUM_PLATFORM_DATABASE_URL",
    "postgresql://postgres:Awsum123!@127.0.0.1:5432/awsum_platform",
)
if DB_URL.startswith("postgresql+psycopg://"):
    DB_URL = DB_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> int:
    out = {
        "scope": "B2B client schema inspection",
        "tables": {},
    }

    target_tables = ("tb_client", "tb_store", "tb_user", "tb_session")

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            for table in target_tables:
                cur.execute(
                    """
                    select column_name
                    from information_schema.columns
                    where table_schema='public' and table_name=%s
                    order by ordinal_position
                    """,
                    (table,),
                )
                out["tables"][table] = [row[0] for row in cur.fetchall()]

    print(json.dumps(out, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
