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
        result = conn.execute(text(f"SELECT * FROM {table_name}"))
        rows = result.mappings().all()
        if rows:
            writer.writerow(rows[0].keys())  # 헤더
            writer.writerows([list(r.values()) for r in rows])
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
    """테이블 백업 후 재생성 (데이터 이관은 최소 공통 컬럼만)

    DESTRUCTIVE (drops the table). Never called automatically: safe_schema_migrate raises
    SchemaMismatchError instead. Use only inside a separately approved migration, after a verified backup.
    """
    from backend.database.migration_policy import assert_schema_change_allowed

    assert_schema_change_allowed(engine)
    table_name = table_class.__tablename__
    if not _table_exists(inspect(engine), table_name):
        # 없으면 그냥 생성
        table_class.__table__.create(bind=engine, checkfirst=True)
        print(f"[CREATE] {table_name} created (no previous table).")
        return

    print(f"[RECREATE] {table_name}: backup + recreate")
    backup_table_to_csv(engine, table_name, backup_dir)

    with engine.begin() as conn:
        # Clean up any previous intermediate tables/indexes
        conn.execute(text(f"DROP TABLE IF EXISTS {table_name}_old"))
        conn.execute(text(f"ALTER TABLE {table_name} RENAME TO {table_name}_old"))

        # SQLite does not rename indexes when renaming a table; drop old indexes to avoid conflicts
        idx_list = conn.execute(text(f"PRAGMA index_list('{table_name}_old')")).mappings().all()
        for idx in idx_list:
            idx_name = idx.get("name") or idx.get("index") or idx.get(1)
            if idx_name:
                try:
                    conn.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))
                except Exception:
                    # Some indexes (e.g., sqlite_autoindex for PK/UNIQUE) cannot be dropped
                    pass

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


class SchemaMismatchError(RuntimeError):
    """The database differs from the models in a way that only a destructive change could fix."""


def plan_schema_changes(base, bind) -> dict:
    """Read-only comparison of the models with the database (inspection queries only, no DDL).

    Returns {"missing_tables": [...], "missing_columns": {table: [...]}, "extra_columns": {table: [...]}}.
    Extra DB columns are reported only: they are kept (no data loss) and never dropped automatically.
    """
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    plan = {"missing_tables": [], "missing_columns": {}, "extra_columns": {}}
    for table in base.metadata.sorted_tables:
        if table.name not in existing:
            plan["missing_tables"].append(table.name)
            continue
        db_cols = set(_get_db_columns(inspector, table.name))
        model_cols = {c.name for c in table.columns}
        if model_cols - db_cols:
            plan["missing_columns"][table.name] = sorted(model_cols - db_cols)
        if db_cols - model_cols:
            plan["extra_columns"][table.name] = sorted(db_cols - model_cols)
    return plan


def safe_schema_migrate(base, engine, backup_dir=None):
    """
    안전 스키마 마이그레이션 (non-destructive only)
    1) 테이블 없으면 생성
    2) 모델에만 있는 '새 컬럼'은 ALTER TABLE ADD COLUMN으로 추가 (비파괴, NULL 허용)
    3) 그래도 불일치가 남으면 테이블을 재생성하지 않고 SchemaMismatchError로 중단한다.
       Table DROP / recreate is a destructive change: only a separately approved migration may do it.
    backup_dir is kept for callers' compatibility (no automatic recreate backup is taken any more).
    """
    from backend.database.migration_policy import assert_schema_change_allowed

    assert_schema_change_allowed(engine)
    # 먼저 모델 기준 테이블 생성(없으면) - checkfirst, never drops anything
    base.metadata.create_all(engine)

    plan = plan_schema_changes(base, engine)
    model_tables = {t.name: t for t in base.metadata.sorted_tables}
    for table_name, col_names in plan["missing_columns"].items():
        with engine.begin() as conn:
            for col_name in col_names:
                ddl = _compile_coltype(model_tables[table_name].columns[col_name], engine)
                sql = f'ALTER TABLE {table_name} ADD COLUMN {ddl}'
                print(f"[ADD] {table_name}.{col_name} -> {sql}")
                conn.execute(text(sql))

    remaining = plan_schema_changes(base, engine)
    if remaining["missing_tables"] or remaining["missing_columns"]:
        raise SchemaMismatchError(
            "Schema still differs from the models after non-destructive changes "
            f"(missing tables: {remaining['missing_tables']}, missing columns: {remaining['missing_columns']}). "
            "Tables are never dropped or recreated automatically; prepare an approved migration."
        )
    for table_name, extra in remaining["extra_columns"].items():
        print(f"[OK] {table_name} schema aligned (extra columns kept in DB: {extra}).")
