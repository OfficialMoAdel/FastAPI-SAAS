"""Auto-ban service for rate limit violators.

Tracks repeated rate limit violations and automatically bans offenders
to prevent abuse. Supports escalation for repeat offenders.

Redis Schema:
    - security:violations:{ip} (STRING with TTL) - Violation counter
    - security:ban:{ip} (STRING with TTL) - Ban record with reason
    - security:ban_count:{ip} (STRING no TTL) - Historical ban count for escalation
"""

import time
from dataclasses import dataclass

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security.logging import SecurityEventType, create_security_event, log_security_event
from src.app.core.utils.cache import client as redis_client

logger = get_logger(__name__)

# Redis key prefixes
VIOLATIONS_KEY_PREFIX = "security:violations:{ip}"
BAN_KEY_PREFIX = "security:ban:{ip}"
BAN_COUNT_KEY_PREFIX = "security:ban_count:{ip}"

# Default TTL for violations counter (should be longer than ban duration)
VIOLATIONS_TTL = 86400  # 24 hours


@dataclass
class BanInfo:
    """Information about an active ban."""

    is_banned: bool
    reason: str | None = None
    expires_at: float | None = None
    ban_count: int = 0


async def increment_violations(client_ip: str) -> int:
    """Increment violation counter for an IP.

    Args:
        client_ip: Client IP address

    Returns:
        Current violation count
    """
    if not redis_client:
        logger.warning("Redis unavailable - cannot track violations for %s", client_ip)
        return 0

    key = VIOLATIONS_KEY_PREFIX.format(ip=client_ip)

    try:
        # Increment counter and set/update TTL
        count = await redis_client.incr(key)
        await redis_client.expire(key, VIOLATIONS_TTL)
        return int(count)
    except Exception as e:
        logger.error("Failed to increment violations for %s: %s", client_ip, e)
        return 0


async def reset_violations(client_ip: str) -> bool:
    """Reset violation counter for an IP (e.g., after successful ban).

    Args:
        client_ip: Client IP address

    Returns:
        True if successful
    """
    if not redis_client:
        return False

    key = VIOLATIONS_KEY_PREFIX.format(ip=client_ip)

    try:
        await redis_client.delete(key)
        return True
    except Exception as e:
        logger.error("Failed to reset violations for %s: %s", client_ip, e)
        return False


async def get_ban_info(client_ip: str) -> BanInfo:
    """Get ban information for an IP.

    Args:
        client_ip: Client IP address

    Returns:
        BanInfo with ban status and details
    """
    if not redis_client:
        return BanInfo(is_banned=False)

    ban_key = BAN_KEY_PREFIX.format(ip=client_ip)
    count_key = BAN_COUNT_KEY_PREFIX.format(ip=client_ip)

    try:
        # Check if currently banned
        ban_data = await redis_client.get(ban_key)

        if ban_data:
            ttl = await redis_client.ttl(ban_key)
            expires_at = time.time() + ttl if ttl > 0 else None

            # Get historical ban count for escalation
            ban_count_str = await redis_client.get(count_key)
            ban_count = int(ban_count_str) if ban_count_str else 1

            return BanInfo(
                is_banned=True,
                reason=ban_data.decode() if isinstance(ban_data, bytes) else ban_data,
                expires_at=expires_at,
                ban_count=ban_count,
            )

        # Not banned - get historical count for potential escalation
        ban_count_str = await redis_client.get(count_key)
        ban_count = int(ban_count_str) if ban_count_str else 0

        return BanInfo(is_banned=False, ban_count=ban_count)

    except Exception as e:
        logger.error("Failed to get ban info for %s: %s", client_ip, e)
        return BanInfo(is_banned=False)


async def ban_ip(
    client_ip: str,
    duration: int | None = None,
    reason: str = "Auto-ban: rate limit violations exceeded",
) -> bool:
    """Ban an IP address.

    Args:
        client_ip: Client IP address to ban
        duration: Ban duration in seconds (uses settings.AUTO_BAN_DURATION if None)
        reason: Ban reason for logging

    Returns:
        True if ban was successful
    """
    if not redis_client:
        logger.warning("Redis unavailable - cannot ban IP %s", client_ip)
        return False

    if not settings.AUTO_BAN_ENABLED:
        logger.debug("Auto-ban disabled - skipping ban for %s", client_ip)
        return False

    # Get ban info for escalation
    ban_info = await get_ban_info(client_ip)

    # Calculate duration with escalation if enabled
    base_duration = duration or settings.AUTO_BAN_DURATION
    if settings.AUTO_BAN_ESCALATION and ban_info.ban_count > 0:
        # Double duration for each previous ban
        actual_duration = base_duration * (2**ban_info.ban_count)
        logger.info(
            "Escalating ban for %s: previous_bans=%d, base_duration=%d, actual_duration=%d",
            client_ip,
            ban_info.ban_count,
            base_duration,
            actual_duration,
        )
    else:
        actual_duration = base_duration

    ban_key = BAN_KEY_PREFIX.format(ip=client_ip)
    count_key = BAN_COUNT_KEY_PREFIX.format(ip=client_ip)

    try:
        # Set ban with TTL
        await redis_client.setex(ban_key, actual_duration, reason)

        # Increment historical ban count (no TTL - persistent)
        await redis_client.incr(count_key)

        # Reset violations counter
        await reset_violations(client_ip)

        # Log security event
        event = create_security_event(
            event_type=SecurityEventType.IP_BLOCKED,
            client_ip=client_ip,
            request_path="auto-ban",
            reason=reason,
            extra={
                "ban_duration": actual_duration,
                "ban_count": ban_info.ban_count + 1,
                "escalated": settings.AUTO_BAN_ESCALATION and ban_info.ban_count > 0,
            },
        )
        log_security_event(event)

        logger.warning(
            "IP banned | ip=%s | duration=%ds | reason=%s | ban_count=%d",
            client_ip,
            actual_duration,
            reason,
            ban_info.ban_count + 1,
        )

        return True

    except Exception as e:
        logger.error("Failed to ban IP %s: %s", client_ip, e)
        return False


async def unban_ip(client_ip: str) -> bool:
    """Remove ban for an IP address.

    Args:
        client_ip: Client IP address to unban

    Returns:
        True if unban was successful (or IP wasn't banned)
    """
    if not redis_client:
        return False

    ban_key = BAN_KEY_PREFIX.format(ip=client_ip)

    try:
        result = await redis_client.delete(ban_key)

        if result > 0:
            logger.info("IP unbanned | ip=%s", client_ip)

            # Log security event
            event = create_security_event(
                event_type=SecurityEventType.IP_WHITELISTED,
                client_ip=client_ip,
                request_path="admin/unban",
                reason="Manual unban via admin API",
            )
            log_security_event(event)

        return True

    except Exception as e:
        logger.error("Failed to unban IP %s: %s", client_ip, e)
        return False


async def is_ip_banned(client_ip: str) -> bool:
    """Quick check if IP is currently banned.

    Args:
        client_ip: Client IP address

    Returns:
        True if IP is banned
    """
    ban_info = await get_ban_info(client_ip)
    return ban_info.is_banned


async def check_and_auto_ban(client_ip: str) -> tuple[bool, int]:
    """Check violations and auto-ban if threshold exceeded.

    This is the main entry point for auto-ban integration.
    Should be called when rate limit is exceeded.

    Args:
        client_ip: Client IP address

    Returns:
        Tuple of (was_banned, violation_count)
    """
    if not settings.AUTO_BAN_ENABLED:
        return False, 0

    # Increment violations
    violations = await increment_violations(client_ip)

    # Check if threshold exceeded
    if violations >= settings.AUTO_BAN_THRESHOLD:
        banned = await ban_ip(
            client_ip,
            reason=f"Auto-ban: {violations} rate limit violations exceeded threshold of {settings.AUTO_BAN_THRESHOLD}",
        )
        return banned, violations

    return False, violations


async def get_all_banned_ips() -> list[dict]:
    """Get list of all currently banned IPs.

    Returns:
        List of ban records with IP, reason, and TTL
    """
    if not redis_client:
        return []

    try:
        # Scan for all ban keys
        banned = []
        cursor = 0

        while True:
            cursor, keys = await redis_client.scan(cursor, match="security:ban:*", count=100)

            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                ip = key_str.replace("security:ban:", "")

                ban_data = await redis_client.get(key)
                ttl = await redis_client.ttl(key)

                ban_count_key = BAN_COUNT_KEY_PREFIX.format(ip=ip)
                ban_count_str = await redis_client.get(ban_count_key)
                ban_count = int(ban_count_str) if ban_count_str else 1

                banned.append(
                    {
                        "ip": ip,
                        "reason": ban_data.decode() if isinstance(ban_data, bytes) else ban_data,
                        "ttl_seconds": ttl if ttl > 0 else 0,
                        "ban_count": ban_count,
                    }
                )

            if cursor == 0:
                break

        return banned

    except Exception as e:
        logger.error("Failed to get banned IPs: %s", e)
        return []


async def get_violation_stats(client_ip: str | None = None) -> dict:
    """Get violation statistics.

    Args:
        client_ip: Specific IP to check, or None for all stats

    Returns:
        Dictionary with violation statistics
    """
    if not redis_client:
        return {"total_ips": 0, "total_violations": 0}

    try:
        if client_ip:
            # Get specific IP stats
            key = VIOLATIONS_KEY_PREFIX.format(ip=client_ip)
            count = await redis_client.get(key)
            return {
                "ip": client_ip,
                "violations": int(count) if count else 0,
            }

        # Get all violation stats
        total_ips = 0
        total_violations = 0
        cursor = 0

        while True:
            cursor, keys = await redis_client.scan(cursor, match="security:violations:*", count=100)
            total_ips += len(keys)

            for key in keys:
                count = await redis_client.get(key)
                if count:
                    total_violations += int(count)

            if cursor == 0:
                break

        return {
            "total_ips": total_ips,
            "total_violations": total_violations,
        }

    except Exception as e:
        logger.error("Failed to get violation stats: %s", e)
        return {"total_ips": 0, "total_violations": 0}
