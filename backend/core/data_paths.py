import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_data_root() -> Path:
    from backend.services.system_settings import get_storage_root

    path, _ = get_storage_root()
    return path
