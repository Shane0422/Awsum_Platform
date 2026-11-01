"""Replaceable approximate IP geolocation; the default provider never sends IPs off-server."""
import logging
import os
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Protocol

import maxminddb


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApproximateLocation:
    country_code: str | None = None
    region: str | None = None
    city: str | None = None


class IpGeolocationProvider(Protocol):
    def lookup(self, public_ip: str) -> ApproximateLocation: ...


class LocalGeoIpProvider:
    """Reads an operator-supplied GeoLite2/GeoIP2 City MMDB, without network I/O."""

    def __init__(self, path: str):
        self.path = path

    def lookup(self, public_ip: str) -> ApproximateLocation:
        with maxminddb.open_database(self.path) as reader:
            record = reader.get(public_ip)
        if not record:
            return ApproximateLocation()
        subdivisions = record.get("subdivisions") or []
        region = subdivisions[-1].get("names", {}).get("en") if subdivisions else None
        return ApproximateLocation(
            country_code=record.get("country", {}).get("iso_code"),
            region=region,
            city=record.get("city", {}).get("names", {}).get("en"),
        )


def locate_ip(public_ip: str | None, provider: IpGeolocationProvider | None = None) -> ApproximateLocation:
    if not public_ip or not ip_address(public_ip).is_global:
        return ApproximateLocation()
    if provider is None:
        path = os.environ.get("AWSUM_GEOIP_DATABASE_PATH", "").strip()
        if not path:
            return ApproximateLocation()
        provider = LocalGeoIpProvider(path)
    # Geolocation is optional enrichment, never an authentication dependency.
    # Provider errors are observable without logging the client's IP or payload.
    try:
        return provider.lookup(public_ip)
    except Exception as exc:
        logger.warning("Session geolocation unavailable (%s); continuing without location.", type(exc).__name__)
        return ApproximateLocation()
