"""Final cutover marker and strict validation of persisted runtime references."""
import json

from sqlalchemy import inspect, text

from backend.core.data_paths import get_data_root
from backend.services.builtin_assets import builtin_asset_file
from backend.services.paths import AwsumPathResolver, resource_storage_path


def layout_marker():
    return resource_storage_path(AwsumPathResolver("PLATFORM").get_data_path("security", "storage-layout.json"))


def final_layout_enabled() -> bool:
    marker = layout_marker()
    if not marker.exists():
        return False
    value = json.loads(marker.read_text(encoding="utf-8"))
    if value.get("version") != 2 or value.get("storage_root") != str(get_data_root().resolve()):
        raise RuntimeError("Storage cutover marker does not match the configured root; reconcile migration first.")
    return True


def validate_runtime_references(engine) -> int:
    count = 0
    with engine.connect() as conn:
        inspector = inspect(conn)
        for table, field in (
            ("tb_system_settings", "c_logo_url"), ("tb_agency", "c_logo_url"),
            ("tb_merchant", "c_logo_url"), ("tb_store", "c_logo_url"),
            ("tb_store", "c_db_path"), ("tb_contract", "c_contract_pdf_path"),
            ("tb_md_master", "c_image_path"), ("tb_platform_user", "c_image_path"),
        ):
            if not inspector.has_table(table):
                continue
            for (reference,) in conn.execute(text(f"SELECT {field} FROM {table} WHERE {field} IS NOT NULL")):
                if not reference:
                    continue
                # PostgreSQL tenant database names are not filesystem references.
                if field == "c_db_path" and not any(separator in reference for separator in ("/", "\\", ".")):
                    continue
                file = (builtin_asset_file(reference)
                        if field == "c_logo_url" and reference.startswith("/images/platform/")
                        else resource_storage_path(reference))
                if not file.is_file():
                    raise RuntimeError(f"Referenced runtime file is missing: {table}.{field}: {reference}")
                count += 1
    return count
