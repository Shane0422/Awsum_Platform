"""One configuration shared with the browser image processor."""
import json

from backend.core.data_paths import PROJECT_ROOT


IMAGE_CONFIG = json.loads((PROJECT_ROOT / "config" / "image.json").read_text(encoding="utf-8"))
