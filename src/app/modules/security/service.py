"""Security IP filter service.

Provides Redis-based storage and management for IP whitelist and blacklist.
"""

import json
from datetime import UTC, datetime
from typing import Any

from src.app.core.config import settings
from src.app.core.utils import cache


class IPFilterService:
    """Service for managing IP whitelist and blacklist.

    Uses Redis for storage to ensure fast lookups by middleware
    and consistency across application instances.

    Redis key structure:
    - security:ip:whitelist:{ip} - Whitelist entry
    - security:ip:blacklist:{ip} - Blacklist entry
    """

    # Redis key prefixes
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

        Args:
            ip_address: IPv4 or IPv6 address
            reason: Optional reason for addition
            expires_at: Optional expiration time
            added_by: User ID who added this entry

        Returns:
            True if successful
        """
        key = f"{self.WHITELIST_PREFIX}{ip_address}"
        data = {
            "ip_address": ip_address,
            "added_at": datetime.now(UTC).isoformat(),
            "reason": reason,
            "added_by": added_by,
        }

        if expires_at:
            ttl_seconds = int((expires_at - datetime.now(UTC)).total_seconds())
            if ttl_seconds > 0:
                await cache.client.setex(key, ttl_seconds, json.dumps(data))
            else:
                # Expiration is in the past, don't add
                return False
        else:
            await cache.client.set(key, json.dumps(data))

        return True

    async def remove_from_whitelist(self, ip_address: str) -> bool:
        """Remove an IP address from the whitelist.

        Args:
            ip_address: IPv4 or IPv6 address

        Returns:
            True if entry existed and was removed
        """
        key = f"{self.WHITELIST_PREFIX}{ip_address}"
        result = await cache.client.delete(key)
        return result > 0

    async def add_to_blacklist(
        self,
        ip_address: str,
        reason: str | None = None,
        expires_at: datetime | None = None,
        added_by: int | None = None,
    ) -> bool:
        """Add an IP address to the blacklist.

        Args:
            ip_address: IPv4 or IPv6 address
            reason: Optional reason for addition
            expires_at: Optional expiration time
            added_by: User ID who added this entry

        Returns:
            True if successful
        """
        key = f"{self.BLACKLIST_PREFIX}{ip_address}"
        data = {
            "ip_address": ip_address,
            "added_at": datetime.now(UTC).isoformat(),
            "reason": reason,
            "added_by": added_by,
        }

        if expires_at:
            ttl_seconds = int((expires_at - datetime.now(UTC)).total_seconds())
            if ttl_seconds > 0:
                await cache.client.setex(key, ttl_seconds, json.dumps(data))
            else:
                # Expiration is in the past, don't add
                return False
        else:
            await cache.client.set(key, json.dumps(data))

        return True

    async def remove_from_blacklist(self, ip_address: str) -> bool:
        """Remove an IP address from the blacklist.

        Args:
            ip_address: IPv4 or IPv6 address

        Returns:
            True if entry existed and was removed
        """
        key = f"{self.BLACKLIST_PREFIX}{ip_address}"
        result = await cache.client.delete(key)
        return result > 0

    async def is_whitelisted(self, ip_address: str) -> bool:
        """Check if an IP address is in the whitelist.

        Args:
            ip_address: IPv4 or IPv6 address

        Returns:
            True if IP is whitelisted
        """
        if not settings.IP_WHITELIST_ENABLED:
            return False

        key = f"{self.WHITELIST_PREFIX}{ip_address}"
        exists = await cache.client.exists(key)
        return exists > 0

    async def is_blacklisted(self, ip_address: str) -> bool:
        """Check if an IP address is in the blacklist.

        Args:
            ip_address: IPv4 or IPv6 address

        Returns:
            True if IP is blacklisted
        """
        if not settings.IP_BLACKLIST_ENABLED:
            return False

        key = f"{self.BLACKLIST_PREFIX}{ip_address}"
        exists = await cache.client.exists(key)
        return exists > 0

    async def get_whitelist(self) -> list[dict[str, Any]]:
        """Get all whitelisted IP addresses.

        Returns:
            List of whitelist entries with metadata
        """
        pattern = f"{self.WHITELIST_PREFIX}*"
        keys = []
        cursor = 0

        while True:
            cursor, batch = await cache.client.scan(cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break

        entries = []
        for key in keys:
            data = await cache.client.get(key)
            if data:
                entry = json.loads(data)
                # Get TTL for expiration info
                ttl = await cache.client.ttl(key)
                if ttl > 0:
                    entry["expires_at"] = datetime.now(UTC).timestamp() + ttl
                entries.append(entry)

        return entries

    async def get_blacklist(self) -> list[dict[str, Any]]:
        """Get all blacklisted IP addresses.

        Returns:
            List of blacklist entries with metadata
        """
        pattern = f"{self.BLACKLIST_PREFIX}*"
        keys = []
        cursor = 0

        while True:
            cursor, batch = await cache.client.scan(cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break

        entries = []
        for key in keys:
            data = await cache.client.get(key)
            if data:
                entry = json.loads(data)
                # Get TTL for expiration info
                ttl = await cache.client.ttl(key)
                if ttl > 0:
                    entry["expires_at"] = datetime.now(UTC).timestamp() + ttl
                entries.append(entry)

        return entries

    async def clear_whitelist(self) -> int:
        """Clear all entries from the whitelist.

        Returns:
            Number of entries removed
        """
        pattern = f"{self.WHITELIST_PREFIX}*"
        keys = []
        cursor = 0

        while True:
            cursor, batch = await cache.client.scan(cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break

        if keys:
            return await cache.client.delete(*keys)
        return 0

    async def clear_blacklist(self) -> int:
        """Clear all entries from the blacklist.

        Returns:
            Number of entries removed
        """
        pattern = f"{self.BLACKLIST_PREFIX}*"
        keys = []
        cursor = 0

        while True:
            cursor, batch = await cache.client.scan(cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break

        if keys:
            return await cache.client.delete(*keys)
        return 0


# Global instance
ip_filter_service = IPFilterService()