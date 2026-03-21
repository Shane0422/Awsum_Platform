from sqlalchemy import create_engine, text
from backend.database.pg_platform import get_platform_db_url

engine = create_engine(get_platform_db_url())
with engine.connect() as conn:
    rows = conn.execute(text("""
        SELECT i_client_id, c_client_name, c_client_code
        FROM tb_client
        ORDER BY i_client_id
    """)).fetchall()
    print('rows=', len(rows))
    for r in rows:
        print(r[0], r[1], r[2])

    dup = conn.execute(text("""
        SELECT c_client_code, COUNT(*)
        FROM tb_client
        GROUP BY c_client_code
        HAVING COUNT(*) > 1
    """)).fetchall()
    print('dup_codes=', dup)

    mx = conn.execute(text("""
        SELECT MAX(CAST(SUBSTRING(c_client_code FROM '([0-9]+)$') AS INTEGER))
        FROM tb_client
        WHERE c_client_code ~ '^CLT_[0-9]+$'
    """)).scalar()
    print('max_seq=', mx)
