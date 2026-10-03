"""Request-guard IP rules and the Redis-backed implementation.

The middleware layer depends on the small :class:`IpRules` seam. The
implementation remains reusable by the security administration module, but
its dependency direction is now core-owned: core no longer imports the
feature module to provide IP filtering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from cachetools import TTLCache

from src.app.core.config import settings
from src.app.core.exceptions.cache_exceptions import MissingClientError
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.utils import cache

logger = get_logger(__name__)


class IpRules(Protocol):
    """ONE interface for IP allow/deny reads and writes from the guard layer."""

    async def check_ip_status(self, ip_address: str) -> tuple[bool, bool]:
        """Return ``(is_blacklisted, is_whitelisted)`` for an IP."""
        ...

    async def ban_ip(self, ip_address: str, reason: str, expires_at: datetime | None = None) -> bool:
        """Ban an IP; return ``True`` on success."""
        ...


@asynccontextmanager
async def get_redis_client() -> AsyncIterator[Any]:
    """Yield the configured Redis client without owning its lifecycle.

    Raises :class:`MissingClientError` when the shared client is not
    initialized — it is never substituted with ``None``. Callers that want a
    soft answer (``return False``) must catch it explicitly; the guard
    middleware translates it to a 503 (see ``middleware/ip_filter.py``).

    The ``if client:`` guards that remain at some call sites below are
    therefore always-true, and are left in place rather than churned here.
    """
    async for redis_client in cache.async_get_redis():
        yield redis_client
        break


_IP_STATUS_NEGATIVE_TTL = 5
_ip_status_negative_cache: TTLCache[str, tuple[bool, bool]] = TTLCache(
    maxsize=10000,
    ttl=_IP_STATUS_NEGATIVE_TTL,
)


def invalidate_ip_status_cache(ip_address: str | None = None) -> None:
    """Drop one or all clean-status negative-cache entries."""
    if ip_address is None:
        _ip_status_negative_cache.clear()
    else:
        _ip_status_negative_cache.pop(ip_address, None)


class IPFilterService:
    """Redis-backed IP whitelist and blacklist implementation."""

    WHITELIST_PREFIX = "security:ip:whitelist:"
    BLACKLIST_PREFIX = "security:ip:blacklist:"

    async def add_to_whitelist(
        self,
        ip_address: str,
        reason: str | None = None,
        expires_at: datetime | None = None,
        added_by: int | None = None,
    ) -> bool:
        """Add an IP address to the whitelist.

        S-39: the negative-status cache is invalidated AFTER the write, not
        before. Invalidating first let a concurrent `check_ip_status` repopulate
        the stale `(False, False)` entry after the rule landed, so the new
        whitelist/blacklist entry took up to _IP_STATUS_NEGATIVE_TTL to become
        visible.
        """
        key = f"{self.WHITELIST_PREFIX}{ip_address}"
        data = {
            "ip_address": ip_address,
            "added_at": datetime.now(UTC).isoformat(),
            "reason": reason,
            "added_by": added_by,
        }
        # Normalise a naive expiry to UTC before subtracting. Subtracting a
        # naive datetime from an aware one raises TypeError, and only
        # MissingClientError was caught here, so a caller passing a naive
        # datetime got an unhandled 500 instead of the documented False.
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        try:
            if expires_at:
                ttl_seconds = int((expires_at - datetime.now(UTC)).total_seconds())
                if ttl_seconds <= 0:
                    invalidate_ip_status_cache(ip_address)
                    return False
                async with get_redis_client() as client:
                    await client.setex(key, ttl_seconds, json_dumps(data, as_str=True))
            else:
                async with get_redis_client() as client:
                    await client.set(key, json_dumps(data, as_str=True))
        except MissingClientError:
            logger.warning("add_to_whitelist failed: Redis unavailable", ip_address=ip_address)
            return False
        # S-39: invalidate only now that the entry is durable.
        invalidate_ip_status_cache(ip_address)
        return True

    async def remove_from_whitelist(self, ip_address: str) -> bool:
        """Remove an IP address from the whitelist. (S-39: invalidate after.)"""
        try:
            async with get_redis_client() as client:
                removed = bool(await client.delete(f"{self.WHITELIST_PREFIX}{ip_address}") > 0)
        except MissingClientError:
            logger.warning("remove_from_whitelist failed: Redis unavailable", ip_address=ip_address)
            return False
        invalidate_ip_status_cache(ip_address)
        return removed

    async def add_to_blacklist(
        self,
        ip_address: str,
        reason: str | None = None,
        expires_at: datetime | None = None,
        added_by: int | None = None,
    ) -> bool:
        """Add an IP address to the blacklist.

        S-39: see add_to_whitelist — the cache drop moves after the write so a
        concurrent reader cannot re-arm the stale "not banned" entry.
        """
        key = f"{self.BLACKLIST_PREFIX}{ip_address}"
        data = {
            "ip_address": ip_address,
            "added_at": datetime.now(UTC).isoformat(),
            "reason": reason,
            "added_by": added_by,
        }
        # Normalise a naive expiry to UTC before subtracting. Subtracting a
        # naive datetime from an aware one raises TypeError, and only
        # MissingClientError was caught here, so a caller passing a naive
        # datetime got an unhandled 500 instead of the documented False.
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        try:
            if expires_at:
                ttl_seconds = int((expires_at - datetime.now(UTC)).total_seconds())
                if ttl_seconds <= 0:
                    invalidate_ip_status_cache(ip_address)
                    return False
                async with get_redis_client() as client:
                    await client.setex(key, ttl_seconds, json_dumps(data, as_str=True))
            else:
                async with get_redis_client() as client:
                    await client.set(key, json_dumps(data, as_str=True))
        except MissingClientError:
            logger.warning("add_to_blacklist failed: Redis unavailable", ip_address=ip_address)
            return False
        # S-39: invalidate only now that the ban is durable.
        invalidate_ip_status_cache(ip_address)
        return True

    async def remove_from_blacklist(self, ip_address: str) -> bool:
        """Remove an IP address from the blacklist. (S-39: invalidate after.)"""
        try:
            async with get_redis_client() as client:
                removed = bool(await client.delete(f"{self.BLACKLIST_PREFIX}{ip_address}") > 0)
        except MissingClientError:
            logger.warning("remove_from_blacklist failed: Redis unavailable", ip_address=ip_address)
            return False
        invalidate_ip_status_cache(ip_address)
        return removed

    async def is_whitelisted(self, ip_address: str) -> bool:
        """Check whether an IP is whitelisted."""
        if not settings.security.IP_WHITELIST_ENABLED:
            return False
        async with get_redis_client() as client:
            if client:
                return bool(await client.exists(f"{self.WHITELIST_PREFIX}{ip_address}") > 0)
        return False

    async def is_blacklisted(self, ip_address: str) -> bool:
        """Check whether an IP is blacklisted."""
        if not settings.security.IP_BLACKLIST_ENABLED:
            return False
        async with get_redis_client() as client:
            if client:
                return bool(await client.exists(f"{self.BLACKLIST_PREFIX}{ip_address}") > 0)
        return False

    async def check_ip_status(self, ip_address: str) -> tuple[bool, bool]:
        """Check blacklist and whitelist status in one Redis pipeline."""
        if not settings.security.IP_BLACKLIST_ENABLED and not settings.security.IP_WHITELIST_ENABLED:
            return False, False

        cached = _ip_status_negative_cache.get(ip_address)
        if cached is not None:
            return cached

        black_key = f"{self.BLACKLIST_PREFIX}{ip_address}"
        white_key = f"{self.WHITELIST_PREFIX}{ip_address}"
        async with get_redis_client() as client:
            if client:
                async with client.pipeline() as pipe:
                    if settings.security.IP_BLACKLIST_ENABLED:
                        pipe.exists(black_key)
                    if settings.security.IP_WHITELIST_ENABLED:
                        pipe.exists(white_key)
                    results = await pipe.execute()

                idx = 0
                is_blacklisted = False
                is_whitelisted = False
                if settings.security.IP_BLACKLIST_ENABLED:
                    if results and idx < len(results):
                        is_blacklisted = bool(results[idx] > 0)
                    idx += 1
                if settings.security.IP_WHITELIST_ENABLED and results and idx < len(results):
                    is_whitelisted = bool(results[idx] > 0)
                if not is_blacklisted and not is_whitelisted:
                    _ip_status_negative_cache[ip_address] = (False, False)
                return is_blacklisted, is_whitelisted
        return False, False

    async def get_whitelist(self) -> list[dict[str, Any]]:
        """Return all whitelist entries with expiration metadata."""
        return await self._get_list(self.WHITELIST_PREFIX)

    async def get_blacklist(self) -> list[dict[str, Any]]:
        """Return all blacklist entries with expiration metadata."""
        return await self._get_list(self.BLACKLIST_PREFIX)

    async def _get_list(self, prefix: str) -> list[dict[str, Any]]:
        pattern = f"{prefix}*"
        keys: list[str | bytes] = []
        async with get_redis_client() as client:
            if not client:
                return []
            cursor = 0
            while True:
                cursor, batch = await client.scan(cursor, match=pattern, count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
            if not keys:
                return []
            async with client.pipeline() as pipe:
                for key in keys:
                    pipe.get(key)
                    pipe.ttl(key)
                results = await pipe.execute()

        entries: list[dict[str, Any]] = []
        for index in range(0, len(results), 2):
            data = results[index]
            ttl = results[index + 1]
            if data:
                entry = json_loads(data)
                if ttl > 0:
                    entry["expires_at"] = datetime.now(UTC).timestamp() + ttl
                entries.append(entry)
        return entries

    async def clear_whitelist(self) -> int:
        """Delete all whitelist keys and return the number removed."""
        return await self._clear_list(self.WHITELIST_PREFIX)

    async def clear_blacklist(self) -> int:
        """Delete all blacklist keys and return the number removed."""
        return await self._clear_list(self.BLACKLIST_PREFIX)

    async def _clear_list(self, prefix: str) -> int:
        pattern = f"{prefix}*"
        keys: list[str | bytes] = []
        try:
            async with get_redis_client() as client:
                cursor = 0
                while True:
                    cursor, batch = await client.scan(cursor, match=pattern, count=100)
                    keys.extend(batch)
                    if cursor == 0:
                        break
                return int(await client.delete(*keys)) if keys else 0
        except MissingClientError:
            return 0


class RedisIpRules:
    """Production :class:`IpRules` adapter backed by :class:`IPFilterService`."""

    def __init__(self, service: IPFilterService | None = None) -> None:
        self._service = service or IPFilterService()

    async def check_ip_status(self, ip_address: str) -> tuple[bool, bool]:
        return await self._service.check_ip_status(ip_address)

    async def ban_ip(self, ip_address: str, reason: str, expires_at: datetime | None = None) -> bool:
        return await self._service.add_to_blacklist(
            ip_address=ip_address,
            reason=reason,
            expires_at=expires_at,
        )


ip_filter_service = IPFilterService()


__all__ = [
    "IPFilterService",
    "IpRules",
    "RedisIpRules",
    "get_redis_client",
    "invalidate_ip_status_cache",
    "ip_filter_service",
]
