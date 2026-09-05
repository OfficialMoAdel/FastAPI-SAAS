"""Auto-ban service for rate limit violators.

Tracks repeated rate limit violations and automatically bans offenders
to prevent abuse. Supports escalation for repeat offenders.

Redis Schema:
    - security:violations:{ip} (STRING with TTL) - Violation counter
    - security:ban:{ip} (STRING with TTL) - Ban record with reason
    - security:ban_count:{ip} (STRING no TTL) - Historical ban count for escalation
"""

import asyncio
import time
from dataclasses import dataclass

from cachetools import TTLCache

from src.app.core.cache.invalidation_channel import InvalidationChannel
from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.security.logging import SecurityEventType, create_security_event, log_security_event

logger = get_logger(__name__)

# Redis key prefixes
VIOLATIONS_KEY_PREFIX = "security:violations:{ip}"
BAN_KEY_PREFIX = "security:ban:{ip}"
BAN_COUNT_KEY_PREFIX = "security:ban_count:{ip}"

# R1-01: negative cache for ban lookups. get_ban_info is the one unconditional
# per-request Redis GET in the rate-limit middleware (tier policy and token
# blacklist already have their own L1/negative caches). Caching "not banned"
# for a short TTL removes that GET for repeat requests from clean IPs.
#
# Tradeoff (same as token_blacklist_cache): a freshly-banned IP can slip through
# for up to _BAN_NEGATIVE_TTL seconds, and the cache is per-process so a ban set
# by another worker is not seen until expiry. ban_ip/unban_ip evict the local
# entry so same-process bans/unbans take effect immediately. Bounded and
# acceptable for a rate-limiting defense (not a primary security boundary).
_BAN_NEGATIVE_TTL = 5  # P1-6: was already 5; now also fenced by pub/sub invalidation channel
_ban_negative_cache: TTLCache[str, bool] = TTLCache(maxsize=10000, ttl=_BAN_NEGATIVE_TTL)
# P1-6: cross-worker L1 invalidation channel. ban_ip/unban_ip publish here
# so every web process drops its in-process TTLCache entry immediately
# instead of serving stale "not banned" for up to _BAN_NEGATIVE_TTL (5s).
# Mirrors the tenant L1 pattern at middleware/tenant.py:45-72.
_BAN_L1_INVALIDATION_CHANNEL = "ban:l1_invalidate"


def invalidate_ban_negative_cache(client_ip: str | None = None) -> None:
    """Drop one (or all) entries from this process's ban negative cache."""
    if client_ip is None or client_ip == "*":
        _ban_negative_cache.clear()
    else:
        _ban_negative_cache.pop(_ban_cache_key(client_ip), None)



_ban_invalidation_channel = InvalidationChannel(
    channel=_BAN_L1_INVALIDATION_CHANNEL,
    on_message=lambda ip: invalidate_ban_negative_cache(ip if ip != "*" else None),
    logger_name="ban_l1",
)

_publish_ban_l1_invalidation = _ban_invalidation_channel.publish
_start_ban_l1_invalidation_listener = _ban_invalidation_channel.start


async def stop_ban_l1_invalidation_listener() -> None:
    """Cancel the background subscriber (idempotent, awaitable)."""
    await _ban_invalidation_channel.stop()

def _ban_cache_key(client_ip: str) -> str:
    return f"ban:{client_ip}"


# TTL for violations counter - configurable via settings
# Uses AUTO_BAN_VIOLATION_WINDOW (default 24 hours, configurable 1 hour to 7 days)
def get_violations_ttl() -> int:
    """Get the TTL for violation counters from settings (clamped 1h..7d)."""
    raw = getattr(settings, "AUTO_BAN_VIOLATION_WINDOW", 86400)
    try:
        v = int(raw)
    except Exception:
        v = 86400
    return max(3600, min(v, 604800))


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
    redis_client = get_redis()
    if not redis_client:
        logger.warning("Redis unavailable - cannot track violations for %s", client_ip)
        return 0

    key = VIOLATIONS_KEY_PREFIX.format(ip=client_ip)

    try:
        ttl = get_violations_ttl()
        # Atomic fixed-window increment: INCR + conditional EXPIRE only if first hit.
        # Use Lua to avoid race between INCR and EXPIRE and to keep window from sliding.
        try:
            # Prefer EVAL for true atomicity
            result = await redis_client.eval(  # type: ignore[misc]
                "local c=redis.call('INCR',KEYS[1]); if c==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return c",
                1,
                key,
                str(ttl),
            )
            return int(result)  # type: ignore[arg-type]
        except Exception:
            # Fallback for FakeRedis / test mocks that don't support EVAL but support pipeline
            async with redis_client.pipeline() as pipe:
                pipe.incr(key)
                results = await pipe.execute()
            count = int(results[0])
            if count == 1:
                await redis_client.expire(key, ttl)
            return count
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
    redis_client = get_redis()
    if not redis_client:
        return False

    key = VIOLATIONS_KEY_PREFIX.format(ip=client_ip)

    try:
        await redis_client.delete(key)
        return True
    except Exception as e:
        logger.error("Failed to reset violations for %s: %s", client_ip, e)
        return False


async def get_ban_info(client_ip: str, include_count: bool = False) -> BanInfo:
    """Get ban information for an IP.

    Args:
        client_ip: Client IP address
        include_count: P2 FIX — the per-request hot path only needs ban
            status; skip the count/TTL reads when False (1 Redis command
            instead of 3). ``ban_ip`` passes True because escalation needs
            the previous-ban count.

    Returns:
        BanInfo with ban status and details
    """
    redis_client = get_redis()
    if not redis_client:
        return BanInfo(is_banned=False)

    # R1-01: short-TTL negative cache for the per-request hot path. Only the
    # status-only lookup (include_count=False) is cached; ban_ip's
    # include_count=True path always reads fresh for correct escalation.
    cache_key = _ban_cache_key(client_ip)
    if not include_count and _ban_negative_cache.get(cache_key):
        return BanInfo(is_banned=False)

    ban_key = BAN_KEY_PREFIX.format(ip=client_ip)
    count_key = BAN_COUNT_KEY_PREFIX.format(ip=client_ip)

    try:
        ban_data = await redis_client.get(ban_key)

        if not ban_data and not include_count:
            _ban_negative_cache[cache_key] = True
            return BanInfo(is_banned=False)

        ttl, ban_count_str = await asyncio.gather(
            redis_client.ttl(ban_key),
            redis_client.get(count_key),
        )
        expires_at = time.time() + ttl if (ban_data and ttl > 0) else None
        ban_count = int(ban_count_str) if ban_count_str else 0

        if ban_data:
            return BanInfo(
                is_banned=True,
                reason=ban_data.decode() if isinstance(ban_data, bytes) else ban_data,
                expires_at=expires_at,
                # A live ban implies at least one prior offense even if the
                # count key expired first.
                ban_count=max(ban_count, 1),
            )
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
        duration: Ban duration in seconds (uses settings.security.AUTO_BAN_DURATION if None)
        reason: Ban reason for logging

    Returns:
        True if ban was successful
    """
    redis_client = get_redis()
    if not redis_client:
        logger.warning("Redis unavailable - cannot ban IP %s", client_ip)
        return False

    if not settings.security.AUTO_BAN_ENABLED:
        logger.debug("Auto-ban disabled - skipping ban for %s", client_ip)
        return False

    # Get ban info for escalation — needs the previous-ban COUNT.
    ban_info = await get_ban_info(client_ip, include_count=True)

    # Calculate duration with escalation if enabled
    base_duration = duration or settings.security.AUTO_BAN_DURATION
    if settings.security.AUTO_BAN_ESCALATION and ban_info.ban_count > 0:
        # Double duration for each previous ban, safely capped to prevent Redis TTL overflow
        # and Python memory issues from massive exponents. Max cap 1 year (31536000 seconds).
        capped_ban_count = min(ban_info.ban_count, 30)
        actual_duration = min(base_duration * (2**capped_ban_count), 31536000)
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
    violations_key = VIOLATIONS_KEY_PREFIX.format(ip=client_ip)

    try:
        from src.app.core.utils.lua_manager import get_lua_manager

        # P3-1: Atomic Lua ban (SETNX + INCR + EXPIRE + DEL in 1 RTT)
        res = await get_lua_manager().execute(
            redis_client,
            "auto_ban_ip",
            keys=[ban_key, count_key, violations_key],
            args=[reason, str(actual_duration), "2592000"],
        )
        is_new_ban = bool(int(res[0])) if isinstance(res, list | tuple) and len(res) >= 2 else False
        new_ban_count = int(res[1]) if isinstance(res, list | tuple) and len(res) >= 2 else (ban_info.ban_count + 1)

        if not is_new_ban:
            logger.debug("IP %s already banned by concurrent request", client_ip)
            return False

        # R1-01: evict the negative cache so the unban is reflected on the
        # next lookup instead of waiting out the 5s TTL.
        _ban_negative_cache.pop(_ban_cache_key(client_ip), None)
        # P1-6: cross-worker fan-out (best-effort — never fail the unban
        # on a publish error).
        await _publish_ban_l1_invalidation(client_ip)

        # Log security event
        event = create_security_event(
            event_type=SecurityEventType.IP_BLOCKED,
            client_ip=client_ip,
            request_path="auto-ban",
            reason=reason,
            extra={
                "ban_duration": actual_duration,
                "ban_count": new_ban_count,
                "escalated": settings.security.AUTO_BAN_ESCALATION and ban_info.ban_count > 0,
            },
        )
        log_security_event(event)

        logger.warning(
            "IP banned | ip=%s | duration=%ds | reason=%s | ban_count=%d",
            client_ip,
            actual_duration,
            reason,
            new_ban_count,
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
    redis_client = get_redis()
    if not redis_client:
        return False

    ban_key = BAN_KEY_PREFIX.format(ip=client_ip)

    try:
        result = await redis_client.delete(ban_key)

        # Evict the local in-process cache and broadcast invalidation to all workers
        _ban_negative_cache.pop(_ban_cache_key(client_ip), None)
        await _publish_ban_l1_invalidation(client_ip)
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
    if not settings.security.AUTO_BAN_ENABLED:
        return False, 0

    # Increment violations
    violations = await increment_violations(client_ip)

    # Check if threshold exceeded
    if violations >= settings.security.AUTO_BAN_THRESHOLD:
        ban_reason = (
            f"Auto-ban: {violations} rate limit violations exceeded threshold of {settings.security.AUTO_BAN_THRESHOLD}"
        )
        banned = await ban_ip(client_ip, reason=ban_reason)
        return banned, violations

    return False, violations


async def get_all_banned_ips() -> list[dict]:
    """Get list of all currently banned IPs.

    Returns:
        List of ban records with IP, reason, and TTL
    """
    redis_client = get_redis()
    if not redis_client:
        return []

    try:
        # Scan for all ban keys
        banned = []
        cursor = 0

        while True:
            cursor, keys = await redis_client.scan(cursor, match="security:ban:*", count=100)

            if keys:
                # Filter out ban_count keys (SCAN pattern "security:ban:*" also matches "security:ban_count:*")
                ban_keys = [
                    k for k in keys if not (k.decode() if isinstance(k, bytes) else k).startswith("security:ban_count:")
                ]

                if ban_keys:
                    ips = []
                    for key in ban_keys:
                        key_str = key.decode() if isinstance(key, bytes) else key
                        ip = key_str.replace("security:ban:", "")
                        ips.append(ip)

                    # Fetch all ban data concurrently
                    # asyncio.gather achieves the same multiplexing network efficiency
                    # as pipeline(transaction=False) without the fragile execute() order bugs
                    tasks = []
                    for ip in ips:
                        ban_key = BAN_KEY_PREFIX.format(ip=ip)
                        ban_count_key = BAN_COUNT_KEY_PREFIX.format(ip=ip)
                        tasks.extend(
                            [redis_client.get(ban_key), redis_client.ttl(ban_key), redis_client.get(ban_count_key)]
                        )

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for i, ip in enumerate(ips):
                        ban_data = results[i * 3]
                        ttl = results[i * 3 + 1]
                        ban_count_str = results[i * 3 + 2]

                        # Handle exceptions gracefully
                        if isinstance(ban_data, BaseException) or ban_data is None:
                            continue

                        if isinstance(ttl, BaseException):
                            ttl_val = 0
                        else:
                            ttl_val = int(ttl) if int(ttl) > 0 else 0

                        if isinstance(ban_count_str, BaseException):
                            ban_count = 1
                        else:
                            try:
                                ban_count = int(ban_count_str) if ban_count_str else 1
                            except (ValueError, TypeError):
                                ban_count = 1

                        banned.append(
                            {
                                "ip": ip,
                                "reason": ban_data.decode() if isinstance(ban_data, bytes) else ban_data,
                                "ttl_seconds": ttl_val,
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
    redis_client = get_redis()
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
