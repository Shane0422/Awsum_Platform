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
            cur.execute(f"select count(*) from {tbl}")
            out[tbl] = cur.fetchone()[0]
        cur.execute("select count(*) from tb_store where i_customer_id is not null")
        out["tb_store_i_customer_id_not_null"] = cur.fetchone()[0]
        cur.execute("select count(*) from tb_store where i_account_id is not null")
        out["tb_store_i_account_id_not_null"] = cur.fetchone()[0]

print(json.dumps(out, ensure_ascii=True, indent=2))
