"""Common Map configuration for static/js/awsum-map.js (every address form, every scope).

The Maps Embed key is a browser key by design (it is part of the map iframe URL), so it is protected by its Google
Cloud restrictions (HTTP referrers + Maps Embed API only), not by secrecy. It comes from the environment / the
Git-ignored .env (backend.core.local_env, loaded at startup) and is never written in code, logs or the database.
A future server-side Routes / Distance key is a different key and is never returned here.
"""
import os

EMBED_KEY_ENV = "AWSUM_GOOGLE_MAPS_EMBED_KEY"


def map_client_config() -> dict:
    """What the browser needs to show a map: enabled + the Embed key, nothing else."""
    key = os.getenv(EMBED_KEY_ENV, "").strip()
    return {"enabled": bool(key), "embed_key": key}
