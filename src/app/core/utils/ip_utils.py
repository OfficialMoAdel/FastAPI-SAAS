"""IP utility functions for extracting true client IP securely.

Provides common methods for middlewares to extract client IP addresses
without being vulnerable to X-Forwarded-For spoofing attacks.
"""

import ipaddress
from functools import lru_cache

from fastapi import Request

from src.app.core.config import settings
from src.app.core.logger import get_logger

# Parse trusted proxy networks once at module load.
# NOTE (restart requirement): these are snapshots of settings at import time,
# not live reads. Changing TRUSTED_PROXY_IPS / TRUSTED_PROXY_NETWORKS at
# runtime (or via a settings reload) has NO effect until the process restarts.
# A reload-aware accessor was deliberately not added: the hot path relies on
# the module-level snapshot + lru_cache, and live re-reads would silently
# widen the spoofing surface mid-process.
logger = get_logger(__name__)

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


@lru_cache(maxsize=2048)
def _is_trusted_proxy_cached(ip_str: str) -> bool:
    """Static half of is_trusted_proxy (cached).

    R1-OPT: hoists the ipaddress parse + set/loopback checks out of the
    per-request path. Pure function of import-time snapshots
    (TRUSTED_PROXY_IPS / TRUSTED_PROXY_NETWORKS are restart-only) — the
    live-settings "testclient" branch stays OUTSIDE in is_trusted_proxy.
    Test fixtures that monkeypatch TRUSTED_PROXY_* must clear this cache
    alongside _is_ip_in_trusted_networks.
    """
    if ip_str in TRUSTED_PROXY_IPS:
        return True

    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_loopback:
            return True
    except ValueError:
        return False

    return _is_ip_in_trusted_networks(ip_str)


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

    return _is_trusted_proxy_cached(ip_str)


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
            # Every hop in the chain is a trusted proxy, so the chain carries
            # no untrusted hop to identify the client. Returning ips[0] here
            # would hand back the LEFTMOST entry, which is the one an
            # attacker controls whenever the edge appends rather than
            # overwrites — and this value becomes the ban key
            # (ip_rules.ban_ip) and the rate-limit identity. Fail closed to
            # the peer address instead: the only value here we actually
            # observed at the socket.
            return client_ip

        # X-Real-IP is deliberately NOT consulted (P1-3).
        #
        # The XFF path above is the only header-derived identity: it walks
        # right-to-left, skips trusted hops, and fails closed to the peer.
        # X-Real-IP is a single header any client can send, trustworthy only
        # if the edge OVERWRITES it — and when an edge forwards instead, a
        # spoofed value would reach every identity sink built on this
        # function: the login and refresh budget keys, the auto-ban key, the
        # audit-trail IP, and the ip_filter blacklist decision itself. That
        # last one makes it a ban-evasion and lockout-evasion primitive, so
        # the previous fallback (already guarded against trusted-proxy
        # values) is gone entirely rather than hardened again.
        #
        # Deployments whose edge sends only X-Real-IP must send
        # X-Forwarded-For instead; the warning below makes that visible the
        # moment it starts happening. Reintroducing this path would need a
        # default-OFF operator setting — the same treatment the project
        # gives other overridable identity knobs — not an implicit fallback.
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            logger.warning(
                "client_ip_x_real_ip_ignored",
                peer=client_ip,
                x_real_ip=real_ip[:64],
                resolved_from="peer",
                remediation="configure the edge to send X-Forwarded-For",
            )

    return client_ip
