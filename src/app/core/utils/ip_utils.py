"""IP utility functions for extracting true client IP securely.

Provides common methods for middlewares to extract client IP addresses
without being vulnerable to X-Forwarded-For spoofing attacks.
"""

import ipaddress
from functools import lru_cache

from fastapi import Request

from src.app.core.config import settings

# Parse trusted proxy networks once at module load.
# NOTE (restart requirement): these are snapshots of settings at import time,
# not live reads. Changing TRUSTED_PROXY_IPS / TRUSTED_PROXY_NETWORKS at
# runtime (or via a settings reload) has NO effect until the process restarts.
# A reload-aware accessor was deliberately not added: the hot path relies on
# the module-level snapshot + lru_cache, and live re-reads would silently
# widen the spoofing surface mid-process.
TRUSTED_PROXY_IPS: set[str] = set(settings.security.TRUSTED_PROXY_IPS or [])
try:
    TRUSTED_PROXY_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
        ipaddress.ip_network(n) for n in (settings.security.TRUSTED_PROXY_NETWORKS or [])
    )
except ValueError:
    # S-3: on unparseable operator input, trust NOTHING network-level rather
    # than silently falling back to all-RFC1918 (XFF spoofing surface).
    try:
        from src.app.core.logger import get_logger

        get_logger(__name__).error("TRUSTED_PROXY_NETWORKS parse failed; treating as empty (fail-closed)")
    except Exception:  # noqa: BLE001 - import-time safety
        pass
    TRUSTED_PROXY_NETWORKS = ()


@lru_cache(maxsize=2048)
def _is_ip_in_trusted_networks(ip_str: str) -> bool:
    """Check if an IP is within any trusted network (network-only check, cached).

    This is the low-level cached network check. It does NOT check TRUSTED_PROXY_IPS
    or loopback addresses — use `is_trusted_proxy` for the full check.

    Pure function with LRU caching for performance.
    Expected hit rate: >95% in production (most requests from same IPs).

    Args:
        ip_str: IP address string to check.

    Returns:
        True if IP is within a trusted network, False otherwise.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in network for network in TRUSTED_PROXY_NETWORKS)
    except ValueError:
        return False


def is_trusted_proxy(ip_str: str) -> bool:
    """Check if an IP is a trusted proxy (full check including loopback and exact IPs).

    Checks against:
    1. Exact match in TRUSTED_PROXY_IPS (e.g. 127.0.0.1)
    2. Loopback addresses
    3. Trusted network ranges via _is_ip_in_trusted_networks (cached)

    Args:
        ip_str: IP address string to check.

    Returns:
        True if the IP is a trusted proxy, False otherwise.
    """
    if not ip_str:
        return False

    if ip_str == "testclient":
        from src.app.core.config.environment import EnvironmentOption

        return bool(settings.env.ENVIRONMENT != EnvironmentOption.PRODUCTION)

    if ip_str in TRUSTED_PROXY_IPS:
        return True

    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_loopback:
            return True
    except ValueError:
        return False

    return _is_ip_in_trusted_networks(ip_str)


def get_client_ip(request: Request) -> str:
    """Get the true client IP with secure X-Forwarded-For parsing.

    SECURITY FIX: Prevents IP spoofing attacks by parsing X-Forwarded-For
    from right to left. We skip any IPs that belong to trusted proxy networks
    and return the first untrusted IP.

    Args:
        request: FastAPI Request object

    Returns:
        The evaluated true client IP, or 'unknown'.
    """
    client_ip = request.client.host if request.client else "unknown"

    if is_trusted_proxy(client_ip):
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # IPs are appended left-to-right: client, proxy1, proxy2.
            # Thus, the real client IP is the rightmost IP that is not a trusted proxy.
            ips = [ip.strip() for ip in forwarded_for.split(",")]
            for ip in reversed(ips):
                if not is_trusted_proxy(ip):
                    return ip
            # If all IPs in X-Forwarded-For are trusted proxies, the true client is the first one.
            if ips:
                return ips[0]

        # Fallback to X-Real-IP ONLY when the direct peer is a trusted proxy
        # AND the value parses as a real IP literal. X-Real-IP is a single
        # header any client can send — it is trustworthy only if the edge
        # proxy is configured to OVERWRITE it (never append/forward). If your
        # edge forwards client-supplied X-Real-IP, spoofed values reach this
        # fallback whenever the peer is trusted, so harden the edge and treat
        # this as defense-in-depth, not identity. Unparseable values (lists,
        # garbage, "unknown") are stripped and ignored.
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            candidate = real_ip.strip().split(",")[0].strip()
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                pass
            else:
                if candidate and not is_trusted_proxy(candidate):
                    return candidate
                # A trusted-proxy-valued X-Real-IP carries no client identity;
                # fall through to the peer address instead of echoing it.

    return client_ip
