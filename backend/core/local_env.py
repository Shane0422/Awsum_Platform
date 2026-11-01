"""Server-only configuration loader for the Git-ignored ``.env`` file.

Values already present in the process environment always win; the file only
fills in names that are missing.
"""
import os
from pathlib import Path

from backend.core.data_paths import PROJECT_ROOT


LOCAL_ENV_PATH = PROJECT_ROOT / ".env"


def read_local_env(path: Path | None = None) -> dict[str, str]:
    target = path or LOCAL_ENV_PATH
    values: dict[str, str] = {}
    try:
        lines = target.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if name:
            values[name] = value
    return values


def load_local_env(names: set[str] | None = None) -> None:
    for name, value in read_local_env().items():
        if names is not None and name not in names:
            continue
        if value and not os.environ.get(name):
            os.environ[name] = value
