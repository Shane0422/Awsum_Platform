"""Access rules shared by the unauthenticated device APIs (license activation, device activation, device validation).

- require_operational_store(db, store): the Store must be active and belong to an existing, active Merchant (a Store
  without a Merchant is refused: Store creation / update require a Merchant). Otherwise 403; nothing is changed or
  removed (links and device data stay, so the device works again when the Store / Merchant is active again).
- enforce_rate_limits(request, endpoint, key): per server-confirmed client IP (services.session_metadata, trusted
  proxy policy) and per request key (License Key / Activation Code hash, or the device code). The request key is only
  a counting key, never an authenticated identity. Over a limit: 429 with Retry-After. When the client IP cannot be
  determined, only the IP limit is skipped (logged without request data); the key limit still applies.
Limits come from the environment ("<limit>/<seconds>"), defaults below (services.rate_limit).
"""
import logging

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from backend.models_admin.merchant import Merchant
from backend.services import rate_limit
from backend.services.rate_limit import Policy
from backend.services.session_metadata import resolve_client_network

logger = logging.getLogger(__name__)

STORE_NOT_ACTIVE = "Store or Merchant is not active."

# endpoint -> (IP policy env / default, key policy env / default, key kind)
LIMITS = {
    "license_activate": (("AWSUM_RL_LICENSE_ACTIVATE_IP", Policy(20, 600)),
                         ("AWSUM_RL_LICENSE_ACTIVATE_KEY", Policy(10, 600)), "license key"),
    "device_activate": (("AWSUM_RL_DEVICE_ACTIVATE_IP", Policy(20, 600)),
                        ("AWSUM_RL_DEVICE_ACTIVATE_CODE", Policy(10, 600)), "activation code"),
    "device_validate": (("AWSUM_RL_DEVICE_VALIDATE_IP", Policy(600, 60)),
                        ("AWSUM_RL_DEVICE_VALIDATE_DEVICE", Policy(30, 60)), "device"),
}


def require_operational_store(db: Session, store) -> None:
    if store is None or (store.c_status or "").strip().lower() != "active":
        raise HTTPException(status_code=403, detail=STORE_NOT_ACTIVE)
    merchant = db.get(Merchant, store.i_merchant_id) if store.i_merchant_id is not None else None
    if merchant is None or (merchant.c_status or "").strip().lower() != "active":  # no Merchant: refused too
        raise HTTPException(status_code=403, detail=STORE_NOT_ACTIVE)


def _client_ip(request: Request) -> str | None:
    try:
        network = resolve_client_network(request)
    except ValueError:  # invalid AWSUM_TRUSTED_PROXY_CIDRS: visible in the log, never trusted
        logger.warning("Device rate limit: trusted proxy configuration is invalid; IP limit skipped.")
        return None
    if network.client_public_ip is None:
        logger.info("Device rate limit: client IP unavailable (source=%s); IP limit skipped.", network.source)
    return network.client_public_ip


def _refuse(endpoint: str, scope: str, retry_after: int):
    logger.warning("Device rate limit exceeded: endpoint=%s scope=%s retry_after=%ss.", endpoint, scope, retry_after)
    raise HTTPException(status_code=429, detail=f"Too many requests. Retry after {retry_after} seconds.",
                        headers={"Retry-After": str(retry_after)})


def enforce_rate_limits(request: Request, endpoint: str, key: str) -> None:
    (ip_env, ip_default), (key_env, key_default), kind = LIMITS[endpoint]
    ip = _client_ip(request)
    if ip is not None:
        allowed, retry_after = rate_limit.hit(f"{endpoint}:ip", rate_limit.key_hash(ip),
                                              rate_limit.policy(ip_env, ip_default))
        if not allowed:
            _refuse(endpoint, "ip", retry_after)
    allowed, retry_after = rate_limit.hit(f"{endpoint}:key", rate_limit.key_hash(key),
                                          rate_limit.policy(key_env, key_default))
    if not allowed:
        _refuse(endpoint, kind, retry_after)
