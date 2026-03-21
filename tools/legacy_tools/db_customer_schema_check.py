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
        cur.execute(
            """
            select table_name
            from information_schema.tables
            where table_schema = 'public'
              and table_name ilike '%customer%'
            order by table_name
            """
        )
        out["customer_tables"] = [r[0] for r in cur.fetchall()]

        cur.execute(
            """
            select table_name, column_name
            from information_schema.columns
            where table_schema = 'public'
              and (column_name ilike '%customer%' or column_name = 'i_customer_id')
            order by table_name, column_name
            """
        )
        out["customer_columns"] = [
            {"table": t, "column": c} for t, c in cur.fetchall()
        ]

print(json.dumps(out, ensure_ascii=True, indent=2))
