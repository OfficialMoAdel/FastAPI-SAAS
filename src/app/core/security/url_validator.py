"""URL validation utilities for SSRF protection.

This module provides utilities to validate URLs before making outbound requests,
preventing Server-Side Request Forgery (SSRF) attacks.
"""

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
]

# Specific blocked hostnames (cloud metadata endpoints)
BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.goog",
    "metadata",
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
        for network in BLOCKED_NETWORKS:
            if ip_obj in network:
                return True
        return False
    except ValueError:
        # Invalid IP address format
        return True


def validate_webhook_url(url: str) -> None:
    """Validate a URL is safe for outbound webhook requests.

    Performs the following checks:
    1. URL must use HTTPS scheme
    2. Hostname must not be a blocked hostname
    3. Resolved IP must not be in blocked ranges

    Args:
        url: The URL to validate

    Raises
    ------
        SSRFProtectionError: If the URL fails validation
    """
    parsed = urlparse(url)

    # Scheme validation
    if parsed.scheme.lower() != "https":
        raise SSRFProtectionError(f"URL must use HTTPS scheme, got: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFProtectionError("URL must have a valid hostname")

    # Check blocked hostnames
    hostname_lower = hostname.lower()
    if hostname_lower in BLOCKED_HOSTNAMES:
        logger.warning(
            "ssrf_blocked_hostname",
            url=url,
            hostname=hostname,
        )
        raise SSRFProtectionError(f"Hostname '{hostname}' is not allowed")

    # Resolve hostname and check IP
    try:
        # Get all IP addresses for the hostname
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        resolved_ips = {info[4][0] for info in addr_info}
    except socket.gaierror as e:
        raise SSRFProtectionError(f"Could not resolve hostname '{hostname}': {e}")

    # Check all resolved IPs
    for ip in resolved_ips:
        if is_ip_blocked(ip):
            logger.warning(
                "ssrf_blocked_ip",
                url=url,
                hostname=hostname,
                blocked_ip=ip,
            )
            raise SSRFProtectionError(f"URL resolves to blocked IP address: {ip}")

    logger.debug(
        "ssrf_url_validated",
        url=url,
        hostname=hostname,
        resolved_ips=list(resolved_ips),
    )
