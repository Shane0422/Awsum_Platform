# backend 폴더 기준 실행

$folders = @(
    "core",
    "database",
    "models_admin",
    "models_store",
    "routers",
    "schemas",
    "utils",
    "db_backups"
)

$files = @(
    "main.py",

    "core/config.py",
    "core/logging_config.py",

    "database/db_base.py",
    "database/admin_db.py",
    "database/store_db.py",

    "models_admin/role.py",
    "models_admin/store.py",
    "models_admin/user.py",

    "models_store/product.py",
    "models_store/order.py",
    "models_store/rental.py",

    "routers/auth.py",
    "routers/admin_store.py",
    "routers/admin_user.py",
    "routers/store_product.py",

    "schemas/auth.py",
    "schemas/user.py",
    "schemas/store.py",
    "schemas/product.py",

    "utils/passwords.py",
    "utils/tokens.py",
    "utils/seed_minimal.py",
    "utils/store_provision.py",
    "utils/db_migration.py",
    "utils/safe_schema_migrate.py"
)

# 폴더 생성
foreach ($folder in $folders) {
    if (-Not (Test-Path $folder)) {
        New-Item -ItemType Directory -Path $folder | Out-Null
        Write-Host "Created folder: $folder"
    }
}

# 파일 생성
foreach ($file in $files) {
    if (-Not (Test-Path $file)) {
        New-Item -ItemType File -Path $file | Out-Null
        Write-Host "Created file: $file"
    }
}
