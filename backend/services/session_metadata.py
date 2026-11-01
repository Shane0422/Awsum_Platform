"""Global, audit-only network and client metadata collected once at web login."""
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from fastapi import Request
from user_agents import parse

from backend.services.ip_geolocation import IpGeolocationProvider, locate_ip


logger = logging.getLogger(__name__)


def _text(value: str | None, limit: int) -> str | None:
    return "".join(c for c in (value or "") if c.isprintable()).strip()[:limit] or None


def _ip(value: str | None) -> IPv4Address | IPv6Address | None:
    try:
        if not value or "%" in value:
            return None
        address = ip_address(value.strip())
        return address.ipv4_mapped if isinstance(address, IPv6Address) and address.ipv4_mapped else address
    except ValueError:
        return None


@lru_cache(maxsize=8)
def _proxy_networks(config: str):
    # Invalid trusted-proxy configuration must be visible, not silently trusted.
    networks = tuple(ip_network(value.strip(), strict=False) for value in config.split(",") if value.strip())
    if any(network.prefixlen == 0 for network in networks):
        raise ValueError("AWSUM_TRUSTED_PROXY_CIDRS must not trust the entire Internet.")
    return networks


def trusted_proxy_networks():
    return _proxy_networks(os.environ.get("AWSUM_TRUSTED_PROXY_CIDRS", ""))


@dataclass(frozen=True)
class ClientNetwork:
    client_public_ip: str | None
    transport_peer_ip: str | None
    source: str


def resolve_client_network(request: Request) -> ClientNetwork:
    """Requires an unmodified socket peer (run Uvicorn with --no-proxy-headers)."""
    peer = _ip(request.client.host if request.client else None)
    if peer is None:
        logger.warning("Session transport client IP unavailable.")
        return ClientNetwork(None, None, "UNAVAILABLE")
    networks = trusted_proxy_networks()

    def trusted(address):
        return any(address in network for network in networks)

    if not trusted(peer):
        return ClientNetwork(str(peer), str(peer), "DIRECT")
    forwarded = request.headers.getlist("x-forwarded-for")
    if forwarded:
        chain = ",".join(forwarded)
        parts = chain.split(",")
        if len(chain) > 4096 or len(parts) > 32:
            logger.warning("Ignoring oversized session proxy IP chain.")
            return ClientNetwork(None, str(peer), "UNAVAILABLE")
        addresses = []
        for part in parts:
            address = _ip(part)
            if address is None:
                logger.warning("Ignoring malformed session proxy IP chain.")
                return ClientNetwork(None, str(peer), "UNAVAILABLE")
            addresses.append(address)
        current = peer
        for address in reversed(addresses):
            if not trusted(current):
                break
            current = address
        if not trusted(current):
            return ClientNetwork(str(current), str(peer), "FORWARDED")
        logger.warning("Session forwarding chain contains only trusted proxies; original client is unavailable.")
        return ClientNetwork(None, str(peer), "UNAVAILABLE")
    real_headers = request.headers.getlist("x-real-ip")
    if real_headers:
        address = _ip(real_headers[0]) if len(real_headers) == 1 else None
        if address is not None and not trusted(address):
            return ClientNetwork(str(address), str(peer), "FORWARDED")
        logger.warning("Ignoring malformed session X-Real-IP.")
    logger.warning("Original session client IP unavailable behind trusted proxy; proxy will not be geolocated.")
    return ClientNetwork(None, str(peer), "UNAVAILABLE")


def client_ip(request: Request) -> str | None:
    return resolve_client_network(request).client_public_ip


def session_client_network(session) -> dict[str, str | None]:
    """Canonical API semantics, also used for historical sessions without source data."""
    address = _ip(session.c_client_public_ip)
    source = session.c_client_ip_source or "LEGACY"
    city = region = country = None
    if address is None:
        status, label = "UNAVAILABLE", "Location unavailable"
    elif address.is_loopback and source in {"DIRECT", "LEGACY"}:
        status, label = "LOCAL_DEVELOPMENT", "Local Development"
    elif not address.is_global:
        status, label = "NON_PUBLIC", "Location unavailable (non-public client IP)"
    else:
        city, region, country = session.c_client_city, session.c_client_region, session.c_client_country_code
        label = ", ".join(value for value in (city, region, country) if value)
        status = "APPROXIMATE" if label else "UNAVAILABLE"
        label = label or "Location unavailable"
    return {
        "client_public_ip": str(address) if address else None,
        "client_local_ip": session.c_local_ip,
        "client_country_code": country, "client_region": region, "client_city": city,
        "client_ip_source": source, "transport_peer_ip": session.c_transport_peer_ip,
        "client_location_status": status, "client_location": label,
        "connection_type": session.c_connection_type or "Unknown",
    }


@dataclass(frozen=True)
class TrustedClientMetadata:
    """Only for server-verified desktop clients, never from ordinary web form/header data.

    A future native login handler must verify the client before constructing this
    value. These claims are audit metadata, not device license or authorization.
    """
    local_ip: str | None = None
    device_uuid: str | None = None
    device_name: str | None = None
    app_version: str | None = None
    os_name: str | None = None
    os_version: str | None = None


def parse_user_agent(raw: str | None) -> dict[str, str | None]:
    agent = parse(raw or "")
    return {
        "c_browser_name": None if agent.browser.family == "Other" else _text(agent.browser.family, 80),
        "c_browser_version": _text(agent.browser.version_string, 64),
        "c_os_name": None if agent.os.family == "Other" else _text(agent.os.family, 80),
        "c_os_version": _text(agent.os.version_string, 64),
    }


def collect_session_metadata(
    request: Request,
    *,
    trusted_client: TrustedClientMetadata | None = None,
    geolocation_provider: IpGeolocationProvider | None = None,
) -> dict[str, str | None]:
    network = resolve_client_network(request)
    public_ip = network.client_public_ip
    location = locate_ip(public_ip, geolocation_provider)
    raw_agent = (request.headers.get("user-agent") or "")[:1024] or None
    metadata = {
        "c_ip_address": public_ip,  # Existing column reused exclusively for resolved client IP.
        "c_transport_peer_ip": network.transport_peer_ip,
        "c_client_ip_source": network.source,
        "c_connection_type": "Desktop" if trusted_client is not None else "Web",
        "c_user_agent": raw_agent,
        "c_country_code": _text(location.country_code, 2),
        "c_region": _text(location.region, 128),
        "c_city": _text(location.city, 128),
        "c_local_ip": None, "c_device_uuid": None, "c_device_name": None, "c_app_version": None,
        **parse_user_agent(raw_agent),
    }
    if trusted_client is not None:
        local_ip = _ip(trusted_client.local_ip)
        if local_ip and local_ip.is_private and not (
            local_ip.is_loopback or local_ip.is_unspecified or local_ip.is_multicast
        ):
            metadata["c_local_ip"] = str(local_ip)
        elif trusted_client.local_ip:
            logger.warning("Ignoring invalid trusted-client local IP metadata.")
        metadata.update(
            c_device_uuid=_text(trusted_client.device_uuid, 128),
            c_device_name=_text(trusted_client.device_name, 128),
            c_app_version=_text(trusted_client.app_version, 64),
        )
        if _text(trusted_client.os_name, 80):
            metadata["c_os_name"] = _text(trusted_client.os_name, 80)
            metadata["c_os_version"] = _text(trusted_client.os_version, 64)
    return metadata
