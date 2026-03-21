import csv
import json
import os
from datetime import datetime

import psycopg

DB_URL = os.getenv(
    "AWSUM_PLATFORM_DATABASE_URL",
    "postgresql://postgres:Awsum123!@127.0.0.1:5432/awsum_platform",
)
if DB_URL.startswith("postgresql+psycopg://"):
    DB_URL = DB_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = os.path.join("db_backups", "platform", f"snapshot_{ts}")
    os.makedirs(root, exist_ok=True)

    metadata = {
        "timestamp": ts,
        "db_url_masked": DB_URL.replace(DB_URL.split("@")[0], "postgresql://***") if "@" in DB_URL else "***",
        "tables": {},
    }

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select table_name
                from information_schema.tables
                where table_schema='public' and table_type='BASE TABLE'
                order by table_name
                """
            )
            tables = [r[0] for r in cur.fetchall()]

            for table in tables:
                cur.execute(
                    """
                    select column_name, data_type, is_nullable
                    from information_schema.columns
                    where table_schema='public' and table_name=%s
                    order by ordinal_position
                    """,
                    (table,),
                )
                cols = cur.fetchall()
                col_names = [c[0] for c in cols]

                csv_path = os.path.join(root, f"{table}.csv")
                with conn.cursor() as data_cur, open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(col_names)
                    data_cur.execute(f'SELECT * FROM "{table}"')
                    row_count = 0
                    for row in data_cur.fetchall():
                        writer.writerow(row)
                        row_count += 1

                metadata["tables"][table] = {
                    "columns": [
                        {"name": c[0], "data_type": c[1], "is_nullable": c[2]} for c in cols
                    ],
                    "row_count": row_count,
                    "csv": f"{table}.csv",
                }

    meta_path = os.path.join(root, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=True, indent=2)

    print(json.dumps({"backup_dir": root, "table_count": len(metadata["tables"])}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
