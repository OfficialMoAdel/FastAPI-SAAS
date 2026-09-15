"""Request-guard IP rules seam.

The middleware layer (attack detection's ban mode) must not reach across
into the security *feature* module (``modules/security/service.py``) — an
ARCHITECTURE.md layering violation with cycle risk. This module owns the
ONE interface for IP allow/deny writes; the Redis-backed implementation
delegates to the feature service internally, so the direction of the
dependency flips: middleware → guard seam → feature, never middleware →
feature directly.

Ordering + exempt paths stay where they are: ``main.py`` owns the stack
order (pinned by ``tests/unit/test_middleware_order.py``) and
``core/middleware_exempt.py`` owns the exempt sets. This seam covers only
the IP-rules side effect.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class IpRules(Protocol):
    """ONE interface for IP allow/deny reads + writes from the guard layer."""

    async def check_ip_status(self, ip_address: str) -> tuple[bool, bool]:
        """Return (is_blacklisted, is_whitelisted) for an IP."""
        ...

    async def ban_ip(self, ip_address: str, reason: str, expires_at: datetime | None = None) -> bool:
        """Ban an IP; returns True on success."""
        ...


class RedisIpRules:
    """Redis-backed IpRules delegating to the security feature service."""

    async def check_ip_status(self, ip_address: str) -> tuple[bool, bool]:
        from src.app.modules.security.service import IPFilterService

        return await IPFilterService().check_ip_status(ip_address)

    async def ban_ip(self, ip_address: str, reason: str, expires_at: datetime | None = None) -> bool:
        from src.app.modules.security.service import IPFilterService

        return await IPFilterService().add_to_blacklist(
            ip_address=ip_address, reason=reason, expires_at=expires_at
        )


__all__ = ["IpRules", "RedisIpRules"]
