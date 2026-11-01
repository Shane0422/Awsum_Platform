"""Shared US ZIP lookup used by every address form (Merchant, Store, later Agency).

The external provider is hidden behind ZipProvider so it can be swapped without
touching routers or templates. Lookup never raises: failures return a result with
found=False so saving a record is never blocked by an outage.
"""
import json
import re
import time
import urllib.error
import urllib.request
from typing import Optional, Protocol

ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")

# Default time zone per state (a few states span zones; users can edit the value).
STATE_TIMEZONE = {
    "AL": "America/Chicago", "AK": "America/Anchorage", "AZ": "America/Phoenix", "AR": "America/Chicago",
    "CA": "America/Los_Angeles", "CO": "America/Denver", "CT": "America/New_York", "DE": "America/New_York",
    "FL": "America/New_York", "GA": "America/New_York", "HI": "Pacific/Honolulu", "ID": "America/Denver",
    "IL": "America/Chicago", "IN": "America/Indiana/Indianapolis", "IA": "America/Chicago", "KS": "America/Chicago",
    "KY": "America/New_York", "LA": "America/Chicago", "ME": "America/New_York", "MD": "America/New_York",
    "MA": "America/New_York", "MI": "America/Detroit", "MN": "America/Chicago", "MS": "America/Chicago",
    "MO": "America/Chicago", "MT": "America/Denver", "NE": "America/Chicago", "NV": "America/Los_Angeles",
    "NH": "America/New_York", "NJ": "America/New_York", "NM": "America/Denver", "NY": "America/New_York",
    "NC": "America/New_York", "ND": "America/Chicago", "OH": "America/New_York", "OK": "America/Chicago",
    "OR": "America/Los_Angeles", "PA": "America/New_York", "RI": "America/New_York", "SC": "America/New_York",
    "SD": "America/Chicago", "TN": "America/Chicago", "TX": "America/Chicago", "UT": "America/Denver",
    "VT": "America/New_York", "VA": "America/New_York", "WA": "America/Los_Angeles", "WV": "America/New_York",
    "WI": "America/Chicago", "WY": "America/Denver", "DC": "America/New_York",
}


class ZipProvider(Protocol):
    def lookup(self, zip5: str) -> Optional[dict]:
        """Return {"city", "state"} or None when the ZIP does not exist. May raise on outage."""


class ZippopotamProvider:
    """api.zippopotam.us: free, no API key."""

    def lookup(self, zip5: str) -> Optional[dict]:
        req = urllib.request.Request(
            f"https://api.zippopotam.us/us/{zip5}", headers={"User-Agent": "awsum-platform"}
        )
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        places = data.get("places") or []
        if not places:
            return None
        return {"city": places[0].get("place name") or "", "state": places[0].get("state abbreviation") or ""}


_provider: ZipProvider = ZippopotamProvider()
_cache: dict = {}
_CACHE_TTL = 24 * 3600


def set_provider(provider: ZipProvider) -> None:
    global _provider
    _provider = provider
    _cache.clear()


def lookup_zip(raw_zip: str) -> dict:
    zip_text = str(raw_zip or "").strip()
    if not ZIP_RE.match(zip_text):
        return {"found": False, "reason": "invalid"}
    zip5 = zip_text[:5]

    hit = _cache.get(zip5)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]

    try:
        place = _provider.lookup(zip5)
    except Exception:
        return {"found": False, "reason": "unavailable"}
    if not place:
        result = {"found": False, "reason": "not_found"}
    else:
        state = (place["state"] or "").upper()
        result = {
            "found": True,
            "zip": zip5,
            "city": place["city"],
            "state": state,
            "country": "United States",
            "timezone": STATE_TIMEZONE.get(state, ""),
        }
    _cache[zip5] = (time.time(), result)
    return result
