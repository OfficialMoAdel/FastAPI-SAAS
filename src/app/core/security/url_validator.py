"""URL validation utilities for SSRF protection.

This module provides utilities to validate URLs before making outbound requests,
preventing Server-Side Request Forgery (SSRF) attacks.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from structlog import get_logger

logger = get_logger(__name__)

# Blocked IP ranges (RFC 1918, RFC 6890, cloud metadata)
BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # Loopback
    ipaddress.ip_network("10.0.0.0/8"),  # Private Class A
    ipaddress.ip_network("172.16.0.0/12"),  # Private Class B
    ipaddress.ip_network("192.168.0.0/16"),  # Private Class C
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local (AWS/Azure/GCP metadata)
    ipaddress.ip_network("0.0.0.0/8"),  # Current network
    ipaddress.ip_network("100.64.0.0/10"),  # Carrier-grade NAT
    ipaddress.ip_network("192.0.0.0/24"),  # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),  # Multicast
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved for future use
    ipaddress.ip_network("255.255.255.255/32"),  # Broadcast
    # IPv6 equivalents
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IPv6 metadata
    # Container / Orchestration networks (Phase 1 Fix)
    ipaddress.ip_network("10.244.0.0/14"),  # Kubernetes pods (Flannel)
    ipaddress.ip_network("172.17.0.0/16"),  # Docker default bridge
    ipaddress.ip_network("192.168.99.0/24"),  # Minikube
]

# Cloud metadata (IMDS) addresses — enforced even when ``allow_internal`` is
# True, so the internal-URLs kill switch can never expose a cloud metadata
# service through a webhook target.
METADATA_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local (AWS/Azure/GCP metadata)
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IPv6 metadata
]

# Specific blocked hostnames (cloud metadata endpoints)
BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.goog",
    "metadata",
    "169.254.169.254",
    "metadata.azure.com",
    "metadata.nova.openstack.org",
}


class SSRFProtectionError(Exception):
    """Raised when a URL fails SSRF validation."""

    pass


def is_ip_blocked(ip: str) -> bool:
    """Check if an IP address is in a blocked range.

    Args:
        ip: IP address string (IPv4 or IPv6)

    Returns
    -------
        True if the IP is blocked, False otherwise
    """
    try:
        ip_obj = ipaddress.ip_address(ip)
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
            ip_obj = ip_obj.ipv4_mapped
        for network in BLOCKED_NETWORKS:
            if ip_obj in network:
                return True
        return False
    except ValueError:
        # Invalid IP address format
        return True


def is_metadata_ip(ip: str) -> bool:
    """Check if an IP address is a cloud metadata (IMDS) address.

    Unlike :func:`is_ip_blocked`, only the metadata ranges are consulted —
    RFC1918/loopback/link-local-private targets stay reachable when
    ``allow_internal`` is enabled. Invalid input fails safe (blocked).

    Args:
        ip: IP address string (IPv4 or IPv6)

    Returns
    -------
        True if the IP is a cloud metadata address (or unparsable), else False
    """
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        ip_obj = ip_obj.ipv4_mapped
    return any(ip_obj in network for network in METADATA_NETWORKS)


async def validate_webhook_url(url: str, allow_internal: bool = False) -> str | None:
    """Validate a URL is safe for outbound webhook requests and return its safe resolved IP address.

    Performs the following checks:
    1. URL must use HTTPS scheme (ALWAYS enforced, even when ``allow_internal`` is True)
    2. Hostname must not be a blocked hostname (skipped when ``allow_internal`` is True)
    3. Resolved IP must not be in blocked ranges; when ``allow_internal`` is
       True the private-range check is replaced by a cloud-metadata-only check
       so DNS pinning still happens for internal targets

    Args:
        url: The URL to validate
        allow_internal: When True, skip the private-network/hostname checks for
            internal webhook delivery (e.g., self-hosted consumers). The HTTPS
            scheme check and the cloud-metadata IP check are never skipped, and
            the hostname is still resolved so the caller receives a pin-able IP.

    Returns
    -------
        str | None: The safe resolved IP address to use for connection,
        or None when internal URLs are allowed and no pinning was performed.

    Raises
    ------
        SSRFProtectionError: If the URL fails validation
    """
    parsed = urlparse(url)

    # Scheme validation — always enforced regardless of allow_internal,
    # so the internal-URLs kill switch can never disable it entirely.
    if parsed.scheme.lower() != "https":
        raise SSRFProtectionError(f"URL must use HTTPS scheme, got: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFProtectionError("URL must have a valid hostname")

    # When explicitly allowed, skip only the hostname/private-network checks —
    # the hostname is still resolved below so the caller gets a pin-able IP
    # (DNS rebinding protection must not be disabled by the kill switch).
    if not allow_internal:
        hostname_lower = hostname.lower()
        if hostname_lower in BLOCKED_HOSTNAMES:
            logger.warning(
                "ssrf_blocked_hostname",
                url=url,
                hostname=hostname,
            )
            raise SSRFProtectionError(f"Hostname '{hostname}' is not allowed")

    # Resolve hostname and check IP
    # OPT P0-1: Use asyncio.to_thread to avoid blocking the event loop during DNS resolution
    try:
        # Get all IP addresses for the hostname (non-blocking via thread pool)
        addr_info = await asyncio.to_thread(socket.getaddrinfo, hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        resolved_ips = {info[4][0] for info in addr_info}
    except socket.gaierror as e:
        raise SSRFProtectionError(f"Could not resolve hostname '{hostname}': {e}")

    # Full blocked-range check normally; metadata-only check when internal
    # URLs are allowed (private ranges stay reachable, IMDS never is).
    ip_is_blocked = is_metadata_ip if allow_internal else is_ip_blocked

    # Check all resolved IPs and return the first valid one
    safe_ip = None
    for ip in resolved_ips:
        if ip_is_blocked(str(ip)):
            logger.warning(
                "ssrf_blocked_metadata_ip" if allow_internal else "ssrf_blocked_ip",
                url=url,
                hostname=hostname,
                blocked_ip=ip,
            )
            raise SSRFProtectionError(f"URL resolves to blocked IP address: {ip}")
        # Note: We keep checking all IPs to ensure none of them are blocked
        # (DNS rebinding can sometimes return multiple IPs where one is safe and one is an internal target).
        # We'll use the first one that passes validation.
        if safe_ip is None:
            safe_ip = str(ip)

    if not safe_ip:
        raise SSRFProtectionError(f"Could not resolve safe IP for '{hostname}'")

    logger.debug(
        "ssrf_url_validated",
        url=url,
        hostname=hostname,
        safe_ip=safe_ip,
        resolved_ips=list(resolved_ips),
        allow_internal=allow_internal,
    )

    return safe_ip
