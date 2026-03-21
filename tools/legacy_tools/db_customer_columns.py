import json
import os

import psycopg

url = os.getenv(
    "AWSUM_PLATFORM_DATABASE_URL",
    "postgresql://postgres:Awsum123!@127.0.0.1:5432/awsum_platform",
)
if url.startswith("postgresql+psycopg://"):
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)

out = {}
with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        for tbl in ("tb_customer", "tb_customer_auth", "tb_account", "tb_store"):
            cur.execute(
                """
                select column_name
                from information_schema.columns
                where table_schema='public' and table_name=%s
                order by ordinal_position
                """,
                (tbl,),
            )
            out[tbl] = [r[0] for r in cur.fetchall()]

print(json.dumps(out, ensure_ascii=True, indent=2))
