# backend/utils/safe_schema_migrate.py
import os
import csv
from datetime import datetime
from sqlalchemy import inspect, text

def backup_table_to_csv(engine, table_name, backup_dir):
    """테이블 전체를 CSV로 백업"""
    os.makedirs(backup_dir, exist_ok=True)
    file_path = os.path.join(
        backup_dir,
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{table_name}.csv"
    )
    with engine.connect() as conn, open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        rows = conn.execute(text(f"SELECT * FROM {table_name}")).fetchall()
        if rows:
            writer.writerow(rows[0].keys())  # 헤더
            writer.writerows(rows)
    return file_path


def _compile_coltype(col, engine):
    """SQLAlchemy Column -> SQLite DDL 타입 문자열"""
    # 기본적으로 타입만 컴파일
    coltype = col.type.compile(engine.dialect)
    ddl = f"{col.name} {coltype}"
    # NOT NULL / DEFAULT 등은 상황에 따라 위험할 수 있으니 최소만 반영
    # (새 컬럼은 NULL 허용으로 추가하고, 앱단에서 채우는 전략 권장)
    return ddl


def _get_db_columns(inspector, table_name):
    try:
        return {c["name"]: c for c in inspector.get_columns(table_name)}
    except Exception:
        return {}


def _table_exists(inspector, table_name):
    try:
        return table_name in inspector.get_table_names()
    except Exception:
        return False


def recreate_table(table_class, engine, backup_dir):
    """테이블 백업 후 재생성 (데이터 이관은 최소 공통 컬럼만)"""
    table_name = table_class.__tablename__
    if not _table_exists(inspect(engine), table_name):
        # 없으면 그냥 생성
        table_class.__table__.create(bind=engine, checkfirst=True)
        print(f"[CREATE] {table_name} created (no previous table).")
        return

    print(f"[RECREATE] {table_name}: backup + recreate")
    backup_table_to_csv(engine, table_name, backup_dir)

    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table_name} RENAME TO {table_name}_old"))

    # 새 구조로 테이블 생성
    table_class.__table__.create(bind=engine, checkfirst=True)

    # 공통 컬럼만 이관
    inspector = inspect(engine)
    db_old_cols = set(_get_db_columns(inspector, f"{table_name}_old").keys())
    new_cols = set([c.name for c in table_class.__table__.columns])
    common_cols = list(db_old_cols & new_cols)

    if common_cols:
        col_list = ", ".join(common_cols)
        with engine.begin() as conn:
            conn.execute(text(
                f"INSERT INTO {table_name} ({col_list}) "
                f"SELECT {col_list} FROM {table_name}_old"
            ))
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE {table_name}_old"))

    print(f"[MIGRATE] {table_name} recreated and common data migrated ({len(common_cols)} columns).")


def safe_schema_migrate(base, engine, backup_dir="./db_backups"):
    """
    안전 스키마 마이그레이션
    1) 테이블 없으면 생성
    2) 모델에만 있는 '새 컬럼'은 ALTER TABLE ADD COLUMN으로 추가 (비파괴)
    3) 그 외(컬럼 삭제/PK 변경/중대한 불일치)만 재생성
    """
    # 먼저 모델 기준 테이블 생성(없으면)
    base.metadata.create_all(engine)

    inspector = inspect(engine)

    for mapper in base.registry.mappers:
        table_class = mapper.class_
        table_name = table_class.__tablename__

        # DB/모델 컬럼셋 수집
        db_cols = _get_db_columns(inspector, table_name)
        model_cols = {c.name: c for c in table_class.__table__.columns}

        if not _table_exists(inspector, table_name):
            # 존재하지 않으면 create_all에서 이미 생성됨
            print(f"[CREATE] {table_name} created.")
            continue

        db_col_names = set(db_cols.keys())
        model_col_names = set(model_cols.keys())

        missing_in_db = list(model_col_names - db_col_names)        # 새로 추가 필요한 컬럼
        extra_in_db   = list(db_col_names - model_col_names)        # 모델엔 없는데 DB에만 있는 컬럼

        # 2) 새 컬럼은 ADD COLUMN으로 처리
        if missing_in_db:
            with engine.begin() as conn:
                for col_name in missing_in_db:
                    col = model_cols[col_name]
                    ddl = _compile_coltype(col, engine)
                    sql = f'ALTER TABLE {table_name} ADD COLUMN {ddl}'
                    print(f"[ADD] {table_name}.{col_name} -> {sql}")
                    conn.execute(text(sql))
            # inspector 캐시 갱신
            inspector = inspect(engine)

        # 3) 여전히 중대한 차이가 있으면 재생성(선택)
        #   - 컬럼 삭제 필요, PK 변경 등
        #   - 단순 extra 컬럼은 당장은 유지(데이터 손실 방지)
        db_cols_after = set(_get_db_columns(inspector, table_name).keys())
        if (model_col_names - db_cols_after):
            print(f"[WARN] {table_name} still mismatched -> recreate fallback")
            recreate_table(table_class, engine, backup_dir)
        else:
            print(f"[OK] {table_name} schema aligned (extra columns in DB: {extra_in_db}).")
