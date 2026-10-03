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
from typing import Any

from cachetools import TTLCache

from src.app.core.cache.l1 import L1Cache, PrefixedKeyCodec
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
# P1-6: cross-worker L1 invalidation. ban_ip/unban_ip publish here so every web
# process drops its in-process TTLCache entry immediately instead of serving
# stale "not banned" for up to _BAN_NEGATIVE_TTL (5s).
_BAN_L1_INVALIDATION_CHANNEL = "ban:l1_invalidate"
_BAN_KEY_PREFIX = "ban:"


def _ban_cache_key(client_ip: str) -> str:
    return f"{_BAN_KEY_PREFIX}{client_ip}"


# The seam owns the grammar. This is the one owner whose wire payload is NOT the
# cache key: the channel carries a bare IP while the cache is keyed "ban:{ip}",
# so the transform lives in the codec instead of at each publish site.
_ban_l1_seam = L1Cache(
    name="ban_l1",
    channel=_BAN_L1_INVALIDATION_CHANNEL,
    codec=PrefixedKeyCodec(prefix=_BAN_KEY_PREFIX),
    caches=[_ban_negative_cache],
    logger_name="ban_l1",
)


def invalidate_ban_negative_cache(client_ip: str | None = None) -> None:
    """Drop one (or all) entries from this process's ban negative cache."""
    if client_ip is None or client_ip == "*":
        _ban_l1_seam.clear()
    else:
        _ban_l1_seam.drop(_ban_cache_key(client_ip))


_publish_ban_l1_invalidation = _ban_l1_seam.publish


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
    # F38: None means "no expiry timestamp available" (non-positive TTL or
    # missing ban data) — NOT "already expired". Consumers must treat None
    # as still-banned (request_admission.py guards with `if expires_at:`);
    # never invent `expires_at = now` at a read site.
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
        logger.warning("auto_ban_violation_tracking_unavailable", ip=client_ip)
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
        logger.error("auto_ban_violation_increment_failed", ip=client_ip, error=str(e))
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
        logger.error("auto_ban_violation_reset_failed", ip=client_ip, error=str(e))
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
        logger.error("auto_ban_info_lookup_failed", ip=client_ip, error=str(e))
        return BanInfo(is_banned=False)


async def check_ban_and_rate_limit(
    client_ip: str,
    rate_limit_key: str,
    limit: int,
    period: int,
) -> tuple[BanInfo, tuple[bool, int, int]]:
    """H-3: ban check + GCRA rate limit in ONE Redis Lua round-trip.

    Replaces the middleware's sequential ``get_ban_info`` +
    ``check_rate_limit_algorithm`` pair (2 RTTs) with a single
    ``ban_and_gcra_check`` script (1 RTT), extending the composite-Lua
    pattern from auth_prefetch/lua_manager.

    Args:
        client_ip: Client IP (ban key namespace).
        rate_limit_key: GCRA key (already tenant/user/path-scoped).
        limit: Max requests per period.
        period: Period in seconds.

    Returns:
        (BanInfo, (is_limited, remaining, retry_after)) — identical
        semantics to calling the two functions separately.
    """
    from src.app.core.utils.gcra_rate_limit import get_gcra_rate_limiter
    from src.app.core.utils.lua_manager import get_lua_manager

    redis_client = get_redis()
    if not redis_client:
        # P2-4: honor RATE_LIMIT_FAIL_OPEN. The default (False) fails
        # CLOSED: with no Redis the ban state is unknown, so treat the
        # request as limited (the middleware maps this to 429), matching
        # the standalone check_rate_limit_algorithm policy. Operators who
        # prefer availability over enforcement can opt out via
        # RATE_LIMIT_FAIL_OPEN=True.
        if settings.rate_limit.RATE_LIMIT_FAIL_OPEN:
            # P2-B: this fail-open was previously silent — a dead Redis
            # means bans AND rate limits are unenforced, so log loudly and
            # emit the fail-open metric for alerting.
            from src.app.core.monitoring.r1_unified_metrics import record_fail_open

            logger.warning(
                "ban_and_rate_limit_fail_open_no_redis",
                client_ip=client_ip,
                rate_limit_key=rate_limit_key,
            )
            record_fail_open("ban_check")
            return BanInfo(is_banned=False), (False, limit, 0)
        return BanInfo(is_banned=False), (True, limit, 0)

    # R1-01 parity: honor the negative cache. On a hit, only GCRA remains →
    # delegate to the existing single-RTT limiter to preserve its fail-open
    # policy.
    #
    # S-09 CORRECTION: this comment previously claimed "a cached 'not banned'
    # skips nothing here (the script still checks Redis)". That was FALSE —
    # the `ban_and_gcra_check` EVALSHA below is NOT reached on this path. The
    # ban verdict is nevertheless sound, and it is worth stating precisely why
    # rather than leaving the old (wrong) justification in place:
    #
    #   * `_ban_negative_cache` is populated ONLY in `get_ban_info`, and only
    #     after an authoritative Redis GET returned no ban key. A hit therefore
    #     means "Redis said not-banned within _BAN_NEGATIVE_TTL", not "we
    #     guessed".
    #   * `ban_ip` (:588) and `unban_ip` (:649) both pop this key locally AND
    #     publish on `_BAN_L1_INVALIDATION_CHANNEL`, so a fresh ban/unban drops
    #     the entry in every worker rather than serving it for the full TTL.
    #   * The residual window — a publish lost to a Redis error, handled
    #     best-effort and logged — is the SAME bounded staleness `get_ban_info`
    #     already accepts on its own fast path. It is documented, not new.
    #
    # The `limit <= 0` block-path guard below is likewise NOT bypassed here:
    # `check_rate_limit_algorithm` handles limit <= 0 itself
    # (gcra_rate_limit.py:126-133, returns is_limited=True).
    cache_key = _ban_cache_key(client_ip)
    if _ban_negative_cache.get(cache_key):
        gcra = get_gcra_rate_limiter()
        if gcra is not None:
            from src.app.core.utils.rate_limit import rate_limiter as _rl

            is_limited, remaining, retry_after = await _rl.check_rate_limit_algorithm(rate_limit_key, limit, period)
            return BanInfo(is_banned=False), (is_limited, remaining, retry_after)

    # P1-1 FIX: limit <= 0 means "block this path for this tier" (e.g. free
    # tier cannot call AI endpoints). The combined Lua script has no such
    # guard — emission_interval_ms=0 makes its TAT math allow every request
    # (fail-open). Mirror the standalone limiter's explicit block
    # (gcra_rate_limit.py:124-130) instead of invoking the script.
    if limit <= 0:
        ban_info = await get_ban_info(client_ip)
        return ban_info, (True, 0, period)

    period_ms = period * 1000
    emission_interval_ms = max(1, round(period_ms / limit)) if limit > 0 else 0
    burst_tolerance_ms = emission_interval_ms * limit
    now_ms = int(time.time() * 1000)

    ban_key = BAN_KEY_PREFIX.format(ip=client_ip)
    gcra_key = f"gcra:{rate_limit_key}"

    try:
        result = await get_lua_manager().execute(
            redis_client,
            "ban_and_gcra_check",
            keys=[gcra_key, ban_key],
            args=[
                str(float(now_ms)),
                str(float(emission_interval_ms)),
                str(int(limit)),
                str(int(burst_tolerance_ms)),
                str(float(period_ms)),
                "1",
            ],
        )
        is_allowed, remaining, retry_after_ms, is_banned, ban_reason, ban_ttl = result
    except Exception as e:
        logger.warning("Combined ban+GCRA check failed, falling back to sequential", error=str(e))
        ban_info = await get_ban_info(client_ip)
        from src.app.core.utils.rate_limit import rate_limiter as _rl

        is_limited, remaining, retry_after = await _rl.check_rate_limit_algorithm(rate_limit_key, limit, period)
        return ban_info, (is_limited, remaining, retry_after)

    is_b = int(is_banned) == 1
    ban_info = BanInfo(
        is_banned=is_b,
        reason=(ban_reason.decode() if isinstance(ban_reason, bytes) else str(ban_reason or "")) if is_b else None,
        expires_at=(time.time() + int(ban_ttl)) if is_b and int(ban_ttl) > 0 else None,
    )
    if not is_b:
        _ban_negative_cache[cache_key] = True
    is_limited = int(is_allowed) == 0
    import math

    return ban_info, (
        is_limited,
        int(float(remaining)),
        int(math.ceil(float(retry_after_ms) / 1000.0)) if is_limited else 0,
    )


async def check_ban_blacklist_and_rate_limit(
    *,
    client_ip: str,
    token: str,
    jti: str | None,
    user_id: str | None,
    tier_name: str | None,
    tenant_id: Any,
    tier_id_raw: int | str | None,
    anonymous_ip: str | None,
    path: str,
) -> tuple[bool | None, int, int, str, BanInfo, tuple[bool, int, int]]:
    """P0-2: blacklist verdict + ban check + GCRA rate limit in ONE Redis Lua round-trip.

    Merges the rate-limit middleware's sequential ``is_token_blacklisted``
    probe + ``check_ban_and_rate_limit`` pair (2 RTTs) into a single
    ``ban_blacklist_gcra_check`` script (1 RTT) on the authenticated hot
    path. The GCRA key depends on the in-script blacklist verdict, so both
    candidate (limit, period, key) triples are resolved up front
    (L1/settings-backed, no Redis RTT on the warm path) and passed in; the
    script consumes exactly one of them.

    Semantics are identical to running the two helpers separately:
    a ``is not False`` verdict downgrades to the anonymous candidate
    (the whole tier identity clears — ``user_id``/``tier_name`` AND
    ``tier_id`` — so a revoked premium token cannot keep premium
    per-path policy via the numeric tier cache),
    ``limit <= 0`` blocks without consuming budget, and the ban
    negative-cache is written (not read) on a clean verdict.

    Args:
        client_ip: Client IP (ban key namespace).
        token: Raw JWT (blacklist member source).
        jti: Verified JTI claim, if available.
        user_id: Authenticated user id (pre-downgrade).
        tier_name: Tier name claim (pre-downgrade).
        tenant_id: Tenant id claim (survives downgrade).
        tier_id_raw: Tier id claim (survives downgrade).
        anonymous_ip: IP to scope the anonymous candidate key.
        path: Sanitized request path.

    Returns:
        (is_blacklisted, limit, period, key, BanInfo,
        (is_limited, remaining, retry_after)) for the SELECTED candidate.
        is_blacklisted is None only when Redis is unavailable.
    """
    from src.app.core.security.rate_policy import build_rate_limit_key, resolve_rate_limits
    from src.app.core.security.token_blacklist_cache import (
        BLACKLIST_KEY,
        NEGATIVE_CACHE_KEY,
        _extract_blacklist_member,
        _get_available_redis_client_for_blacklist,
        _token_cache_key,
        is_token_blacklisted,
        token_cache_circuit,
    )
    from src.app.core.utils.lua_manager import get_lua_manager

    redis_client, fail_closed = _get_available_redis_client_for_blacklist()
    if redis_client is None:
        # Breaker open / Redis down: mirror is_token_blacklisted's
        # no-client semantics (True if fail_closed else None → downgrade
        # via `is not False`) and check_ban_and_rate_limit's no-Redis
        # fail-open/fail-closed policy on the anonymous candidate.
        verdict: bool | None = True if fail_closed else None
        anon_key = build_rate_limit_key(path, user_id=None, tenant_id=tenant_id, anonymous_ip=anonymous_ip)
        # P1: a downgraded identity must resolve the anonymous DEFAULT, not
        # premium per-path policy via the numeric tier cache.
        sel_limit, sel_period = await resolve_rate_limits(path, tier_id=None, tier_name=None, user_id=None)
        if settings.rate_limit.RATE_LIMIT_FAIL_OPEN:
            from src.app.core.monitoring.r1_unified_metrics import record_fail_open

            logger.warning(
                "ban_blacklist_and_rate_limit_fail_open_no_redis",
                client_ip=client_ip,
                rate_limit_key=anon_key,
            )
            record_fail_open("ban_check")
            return verdict, sel_limit, sel_period, anon_key, BanInfo(is_banned=False), (False, sel_limit, 0)
        return verdict, sel_limit, sel_period, anon_key, BanInfo(is_banned=False), (True, sel_limit, 0)

    # Resolve BOTH candidates up front (L1/settings-backed, no extra RTT).
    # The anonymous candidate clears the WHOLE tier identity (tier_id
    # included) — a blacklisted token must not keep premium per-path policy
    # through the numeric tier cache.
    authed_limit, authed_period = await resolve_rate_limits(
        path, tier_id=tier_id_raw, tier_name=tier_name, user_id=user_id
    )
    authed_key = build_rate_limit_key(path, user_id=user_id, tenant_id=tenant_id, anonymous_ip=None)
    anon_limit, anon_period = await resolve_rate_limits(path, tier_id=None, tier_name=None, user_id=None)
    anon_key = build_rate_limit_key(path, user_id=None, tenant_id=tenant_id, anonymous_ip=anonymous_ip)

    def _selected(v: bool | None) -> tuple[str, int, int]:
        if v is False:
            return authed_key, authed_limit, authed_period
        return anon_key, anon_limit, anon_period

    if authed_limit <= 0 or anon_limit <= 0:
        # P1-1 block-path: the merged script has no limit<=0 guard, so
        # delegate to the sequential helpers (identical semantics; the
        # get_ban_info leg usually rides the 5s negative cache).
        seq_verdict = await is_token_blacklisted(token, jti=jti)
        sel_key, sel_limit, sel_period = _selected(seq_verdict)
        ban_info, rl = await check_ban_and_rate_limit(client_ip, sel_key, sel_limit, sel_period)
        return seq_verdict, sel_limit, sel_period, sel_key, ban_info, rl

    def _gcra_params(limit: int, period: int) -> tuple[int, int, int, int]:
        period_ms = period * 1000
        emission_interval_ms = max(1, round(period_ms / limit)) if limit > 0 else 0
        return emission_interval_ms, limit, emission_interval_ms * limit, period_ms

    now_s = time.time()
    now_sec = int(now_s)
    now_ms = int(now_s * 1000)
    neg_ttl = getattr(settings.r1_unified, "TOKEN_NEGATIVE_CACHE_TTL", 60)
    negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token, jti)}"
    member = _extract_blacklist_member(token, jti)
    ban_key = BAN_KEY_PREFIX.format(ip=client_ip)
    a_emit, a_lim, a_burst, a_per = _gcra_params(authed_limit, authed_period)
    n_emit, n_lim, n_burst, n_per = _gcra_params(anon_limit, anon_period)

    try:
        result = await get_lua_manager().execute(
            redis_client,
            "ban_blacklist_gcra_check",
            keys=[negative_key, BLACKLIST_KEY, f"gcra:{authed_key}", ban_key, f"gcra:{anon_key}"],
            args=[
                member,
                str(now_sec),
                str(int(neg_ttl)),
                str(float(now_ms)),
                str(float(a_emit)),
                str(int(a_lim)),
                str(int(a_burst)),
                str(float(a_per)),
                str(float(n_emit)),
                str(int(n_lim)),
                str(int(n_burst)),
                str(float(n_per)),
            ],
        )
        raw_bl, is_allowed, remaining, retry_after_ms, is_banned, ban_reason, ban_ttl = result
    except Exception as e:
        logger.warning("Combined ban+blacklist+GCRA check failed, falling back to sequential", error=str(e))
        seq_verdict = await is_token_blacklisted(token, jti=jti)
        sel_key, sel_limit, sel_period = _selected(seq_verdict)
        ban_info = await get_ban_info(client_ip)
        from src.app.core.utils.rate_limit import rate_limiter as _rl

        is_limited, remaining, retry_after = await _rl.check_rate_limit_algorithm(sel_key, sel_limit, sel_period)
        return seq_verdict, sel_limit, sel_period, sel_key, ban_info, (is_limited, remaining, retry_after)

    await token_cache_circuit.record_success()
    merged_verdict = bool(int(raw_bl))
    is_b = int(is_banned) == 1
    ban_info = BanInfo(
        is_banned=is_b,
        reason=(ban_reason.decode() if isinstance(ban_reason, bytes) else str(ban_reason or "")) if is_b else None,
        expires_at=(time.time() + int(ban_ttl)) if is_b and int(ban_ttl) > 0 else None,
    )
    if not is_b:
        _ban_negative_cache[_ban_cache_key(client_ip)] = True
    sel_key, sel_limit, sel_period = _selected(merged_verdict)
    is_limited = int(is_allowed) == 0
    import math

    return merged_verdict, sel_limit, sel_period, sel_key, ban_info, (
        is_limited,
        int(float(remaining)),
        int(math.ceil(float(retry_after_ms) / 1000.0)) if is_limited else 0,
    )


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
        logger.warning("auto_ban_write_unavailable", ip=client_ip)
        return False

    if not settings.security.AUTO_BAN_ENABLED:
        logger.debug("auto_ban_disabled", ip=client_ip)
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
            "auto_ban_escalating",
            ip=client_ip,
            previous_bans=ban_info.ban_count,
            base_duration=base_duration,
            actual_duration=actual_duration,
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
            logger.debug("auto_ban_already_banned", ip=client_ip)
            return False

        # R1-01: evict the negative cache so the unban is reflected on the
        # next lookup instead of waiting out the 5s TTL.
        _ban_negative_cache.pop(_ban_cache_key(client_ip), None)
        # P1-6: cross-worker fan-out (best-effort — never fail the unban
        # on a publish error).
        # F9: the Lua ban above already succeeded — a publish failure must
        # not flip this to `return False` (callers read that as "not
        # banned"). Guard the fan-out so success stays success.
        try:
            await _publish_ban_l1_invalidation(client_ip)
        except Exception as fanout_exc:  # noqa: BLE001
            logger.warning("ban_l1_fanout_failed", client_ip=client_ip, error=str(fanout_exc))

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

        # P1-12: a named event with fields, not a percent-formatted sentence.
        # This is THE ban audit line — under the old form ip/duration/reason/
        # ban_count were opaque positional elements and could not be queried.
        logger.warning(
            "auto_ban_applied",
            ip=client_ip,
            duration=actual_duration,
            reason=reason,
            ban_count=new_ban_count,
        )

        return True
    except Exception as e:
        logger.error("auto_ban_write_failed", ip=client_ip, error=str(e))
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
        # F9: the delete above already succeeded — a publish failure must not
        # flip this to `return False`. Best-effort fan-out (see ban_ip).
        try:
            await _publish_ban_l1_invalidation(client_ip)
        except Exception as fanout_exc:  # noqa: BLE001
            logger.warning("unban_l1_fanout_failed", client_ip=client_ip, error=str(fanout_exc))
        if result > 0:
            logger.info("auto_ban_lifted", ip=client_ip)

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
        logger.error("auto_ban_lift_failed", ip=client_ip, error=str(e))
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
                        # F20: strip the prefix, not every occurrence — an
                        # IPv6 address contains ":" but never the full
                        # "security:ban:" substring, so replace() was only
                        # latently wrong; removeprefix states the intent.
                        ip = key_str.removeprefix("security:ban:")
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
        logger.error("auto_ban_list_failed", error=str(e))
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
        # F19: SCAN may return duplicate keys across iterations — dedupe so
        # the admin stats neither over-count IPs nor re-GET the same key.
        # (Counts remain approximate by nature: keys can appear/vanish
        # mid-scan. This is an observability endpoint, not a billing input.)
        seen: set[str] = set()

        while True:
            cursor, keys = await redis_client.scan(cursor, match="security:violations:*", count=100)
            fresh = [k for k in keys if k not in seen]
            seen.update(keys)
            total_ips += len(fresh)

            for key in fresh:
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
        logger.error("auto_ban_stats_failed", error=str(e))
        return {"total_ips": 0, "total_violations": 0}
