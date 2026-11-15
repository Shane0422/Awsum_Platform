"""Common in-memory rate limiter (fixed window), shared by any API that needs request limits.

    allowed, retry_after = rate_limit.hit("bucket", key, rate_limit.Policy(limit=20, window=600))

- Keys are opaque strings; callers pass a hash (rate_limit.key_hash) for secrets such as License Keys or codes, so no
  raw secret is kept in memory or written to a log.
- policy(env_name, default) reads "<limit>/<seconds>" from the environment; an invalid value logs a warning and the
  default is used.
- The counters live in this process only: valid for development and a single process. With several workers or servers
  each process counts on its own (the effective limit grows with the number of processes) and a restart clears them;
  such deployments need a shared store (for example Redis or the database). See docs/AWSUM_INSTALLATION_GUIDE.md.
"""
import hashlib
import logging
import math
import os
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_counters: dict[tuple[str, str], list] = {}  # (bucket, key) -> [window start, count, window seconds]
_clock = time.monotonic  # replaced in tests
_PRUNE_EVERY = 60.0
_last_prune = [0.0]


@dataclass(frozen=True)
class Policy:
    limit: int
    window: int  # seconds


def policy(env_name: str, default: Policy) -> Policy:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        limit_text, window_text = raw.split("/", 1)
        limit, window = int(limit_text), int(window_text)
        if limit <= 0 or window <= 0:
            raise ValueError
    except ValueError:
        logger.warning("Invalid %s=%r (expected <limit>/<seconds>); using the default %s/%s.",
                       env_name, raw, default.limit, default.window)
        return default
    return Policy(limit, window)


def key_hash(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32]


def _prune(now: float) -> None:
    if now - _last_prune[0] < _PRUNE_EVERY:
        return
    _last_prune[0] = now
    for key in [key for key, (start, _count, window) in _counters.items() if now - start >= window]:
        del _counters[key]


def hit(bucket: str, key: str, rule: Policy) -> tuple[bool, int]:
    """Count one request; returns (allowed, retry_after_seconds). A refused request is not counted."""
    now = _clock()
    with _lock:
        _prune(now)
        entry = _counters.get((bucket, key))
        if entry is None or now - entry[0] >= entry[2]:
            entry = [now, 0, rule.window]
            _counters[(bucket, key)] = entry
        if entry[1] >= rule.limit:
            return False, max(1, math.ceil(entry[0] + entry[2] - now))
        entry[1] += 1
        return True, 0


def reset() -> None:
    """Clear every counter (tests, or an operator action)."""
    with _lock:
        _counters.clear()
        _last_prune[0] = 0.0
