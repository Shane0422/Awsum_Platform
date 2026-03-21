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
        "scope": "B2B client schema keyword scan",
        "client_tables": [],
        "legacy_customer_tables": [],
        "legacy_account_tables": [],
        "client_columns": [],
        "legacy_customer_columns": [],
        "legacy_account_columns": [],
    }

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select table_name
                from information_schema.tables
                where table_schema='public'
                order by table_name
                """
            )
            tables = [row[0] for row in cur.fetchall()]

            out["client_tables"] = [t for t in tables if "client" in t.lower()]
            out["legacy_customer_tables"] = [t for t in tables if "customer" in t.lower()]
            out["legacy_account_tables"] = [t for t in tables if "account" in t.lower()]

            cur.execute(
                """
                select table_name, column_name
                from information_schema.columns
                where table_schema='public'
                order by table_name, ordinal_position
                """
            )
            for table_name, column_name in cur.fetchall():
                item = {"table": table_name, "column": column_name}
                lowered = column_name.lower()
                if "client" in lowered:
                    out["client_columns"].append(item)
                if "customer" in lowered or column_name == "i_customer_id":
                    out["legacy_customer_columns"].append(item)
                if "account" in lowered or column_name == "i_account_id":
                    out["legacy_account_columns"].append(item)

    print(json.dumps(out, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
