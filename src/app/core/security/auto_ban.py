"""Auto-ban service for rate limit violators.

Tracks repeated rate limit violations and automatically bans offenders
to prevent abuse. Supports escalation for repeat offenders.

Redis Schema:
    - security:violations:{ip} (STRING with TTL) - Violation counter
    - security:ban:{ip} (STRING with TTL) - Ban record with reason
    - security:ban_count:{ip} (STRING no TTL) - Historical ban count for escalation
"""

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Any, Final

from cachetools import TTLCache
from redis.exceptions import RedisError

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

# R1-01: Negative cache for ban lookups in `get_ban_info` (the sole unconditional per-request
# Redis GET in the rate-limit middleware, as tier policies and token blacklists already have L1 caches).
# Caching "not banned" states for a short TTL eliminates this overhead for repeat clean-IP traffic.
#
# Tradeoffs: Similar to `token_blacklist_cache`, newly banned IPs may bypass checks for up to
# `_BAN_NEGATIVE_TTL` seconds, and cross-worker ban propagation is delayed until TTL expiry.
# `ban_ip` and `unban_ip` explicitly evict local process entries for immediate same-process effect.
# This bounded staleness is an acceptable design tradeoff for a defense-in-depth rate-limiting layer.
_BAN_NEGATIVE_TTL = 5  # P1-6: was already 5; now also fenced by pub/sub invalidation channel
_ban_negative_cache: TTLCache[str, bool] = TTLCache(maxsize=10000, ttl=_BAN_NEGATIVE_TTL)
# P1-6: cross-worker L1 invalidation. ban_ip/unban_ip publish here so every web
# process drops its in-process TTLCache entry immediately instead of serving
# stale "not banned" for up to _BAN_NEGATIVE_TTL (5s).
_BAN_L1_INVALIDATION_CHANNEL = "ban:l1_invalidate"
_BAN_KEY_PREFIX = "ban:"

# ADR-0015 idempotency guard (§8.3). One nonce per REQUEST, written by the
# merged script before it moves any budget:
#
#   * if the merged EVALSHA times out AFTER the server executed it, the nonce
#     is present, and the fallback must NOT consume again -- it fails closed
#     (429), the ADR's sanctioned direction;
#   * if the nonce is absent, the script provably did not run (the SETNX is
#     the first thing it does), so the sequential fallback is safe and costs
#     exactly one advance.
#
# The nonce lives in Redis because the ambiguity ("did the server execute
# it?") is a property of the server. TTL is bounded well below a request
# timeout's relevance: it only has to outlive the fallback's own round-trips.
_NONCE_TTL_MS = 30_000

#: The Lua idempotency guard returns the Redis bulk string ``'duplicate'``.
#: ``lua_manager.execute`` hands the raw redis-py reply back unchanged, so the
#: sentinel arrives as ``str`` on the production client (``decode_responses=
#: True``, ``redis_provider``) and as ``bytes`` only where a test mocks the
#: undecoded shape. Accepting one and not the other makes the replay
#: fast-path dead in production and routes every duplicate through the
#: 9-element unpack below -- which raises ``ValueError``, is caught, and pays
#: for a best-effort ``EXISTS`` probe to recover the same verdict.
_DUPLICATE_SENTINELS: Final[frozenset[str | bytes]] = frozenset(("duplicate", b"duplicate"))


def _new_request_nonce() -> str:
    """A fresh, unguessable nonce for one admission attempt."""
    return secrets.token_hex(16)


def _nonce_key(nonce: str) -> str:
    """Redis key for a request nonce (namespaced away from user data)."""
    return f"ratelimit:nonce:{nonce}"


async def _nonce_was_claimed(redis_client: Any, nonce: str) -> bool:
    """True when the merged script already ran for this request.

    Best effort by design: if the check itself fails we cannot prove the
    script ran, and refusing every request on a Redis blip would turn a
    timeout into an outage. The caller then takes the sequential path, whose
    own failure policy (fail-open/fail-closed) already applies.

    The result is accepted only when it is a GENUINE int. `bool()` is wrong
    here because a mock (or any object with `__bool__`/`__int__`) is truthy by
    default, which would make every script error look like "already executed"
    and fail closed on a fault that never ran anything. A non-int answer is
    not evidence, and the sequential path is then taken.
    """
    try:
        result = await redis_client.exists(_nonce_key(nonce))
    except (RedisError, OSError) as exc:  # the claim probe, not the request path
        logger.debug("nonce_probe_failed", error=str(exc))
        return False
    return type(result) is int and result == 1


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


async def _duplicate_replay_result(
    redis_client: Any, client_ip: str, ban_key: str
) -> tuple[BanInfo, tuple[bool, int, int, bool], None]:
    """The result for a replayed request: allowed, nothing consumed again.

    A replay means the first execution already spent the budget and returned a
    verdict the client never received. Reporting "not limited" here is the
    only answer that cannot double-charge; the ban leg is re-read so a ban
    that landed in between is still honoured.
    """
    reason = None
    expires_at = None
    try:
        raw = await redis_client.get(ban_key)
        if raw:
            reason = raw.decode() if isinstance(raw, bytes) else str(raw)
            ttl = int(await redis_client.ttl(ban_key))
            expires_at = time.time() + ttl if ttl > 0 else None
    except (RedisError, OSError) as exc:  # the replay verdict stands without the ban detail
        logger.debug("duplicate_replay_ban_read_failed", error=str(exc))
    ban_info = BanInfo(is_banned=bool(reason), reason=reason, expires_at=expires_at)
    return ban_info, (False, 0, 0, False), None


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
    *,
    aggregate_limit: int = 0,
    aggregate_period: int = 0,
) -> tuple[BanInfo, tuple[bool, int, int, bool], tuple[bool, int, int] | None]:
    """H-3 / F-10: ban check + per-path GCRA + optional aggregate in ONE RTT.

    Replaces the middleware's sequential ``get_ban_info`` +
    ``check_rate_limit_algorithm`` pair (2 RTTs) with a single
    ``ban_and_gcra_check`` script (1 RTT), extending the composite-Lua
    pattern from auth_prefetch/lua_manager.

    F-10 folds the anonymous per-IP aggregate ceiling into the SAME script as a
    second GCRA bucket, so anonymous traffic makes 1 RTT instead of 2. The
    consumption order is the documented one -- the aggregate is consumed only
    when the per-path leg allowed the request (ADR-0015's stated invariant),
    and not at all when the ceiling is disabled.

    Args:
        client_ip: Client IP (ban key namespace).
        rate_limit_key: GCRA key (already tenant/user/path-scoped).
        limit: Max requests per period.
        period: Period in seconds.
        aggregate_limit: Anonymous aggregate ceiling; 0 disables the fold.
        aggregate_period: Aggregate period in seconds.

    Returns:
        ``(BanInfo, (is_limited, remaining, retry_after, degraded), aggregate)``
        -- the rate-limit tuple has identical semantics to calling the two
        functions separately, plus the degradation flag: True when the limit
        was NOT enforced by the configured algorithm (the GCRA path failed and
        the fixed window took over), so the caller can mark the verdict
        degraded. ``aggregate`` is ``(is_limited, remaining, retry_after)``
        when the fold ran, else ``None`` (the caller then owns the ceiling).
    """
    from src.app.core.security.rate_policy import build_anonymous_aggregate_key
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
            return BanInfo(is_banned=False), (False, limit, 0, False), None
        return BanInfo(is_banned=False), (True, limit, 0, False), None

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
    ban_cached = bool(_ban_negative_cache.get(cache_key))
    want_aggregate = aggregate_limit > 0 and aggregate_period > 0
    if ban_cached and not want_aggregate:
        gcra = get_gcra_rate_limiter()
        if gcra is not None:
            from src.app.core.utils.rate_limit import rate_limiter as _rl

            is_limited, remaining, retry_after, degraded = await _rl.check_rate_limit_algorithm_with_status(
                rate_limit_key, limit, period
            )
            return BanInfo(is_banned=False), (is_limited, remaining, retry_after, degraded), None

    # P1-1 FIX: limit <= 0 means "block this path for this tier" (e.g. free
    # tier cannot call AI endpoints). The combined Lua script has no such
    # guard — emission_interval_ms=0 makes its TAT math allow every request
    # (fail-open). Mirror the standalone limiter's explicit block
    # (gcra_rate_limit.py:124-130) instead of invoking the script.
    if limit <= 0:
        ban_info = await get_ban_info(client_ip)
        return ban_info, (True, 0, period, False), None

    period_ms = period * 1000
    # `limit <= 0` returned above, so the division is safe and the old
    # `else 0` arm was unreachable.
    emission_interval_ms = max(1, round(period_ms / limit))
    burst_tolerance_ms = emission_interval_ms * limit

    ban_key = BAN_KEY_PREFIX.format(ip=client_ip)
    gcra_key = f"gcra:{rate_limit_key}"

    # F-10: the aggregate bucket's own emission/burst parameters. When the
    # ceiling is disabled the placeholders keep the ARGV positions stable and
    # ARGV[7]='0' tells the script not to read KEYS[3] at all.
    aggregate_key = ""
    aggregate_args = ["0", "0", "0", "0", "0"]
    if want_aggregate:
        aggregate_period_ms = aggregate_period * 1000
        aggregate_emission_interval_ms = max(1, round(aggregate_period_ms / aggregate_limit))
        aggregate_burst_tolerance_ms = aggregate_emission_interval_ms * aggregate_limit
        aggregate_key = f"gcra:{build_anonymous_aggregate_key(client_ip)}"
        aggregate_args = [
            "1",
            str(float(aggregate_emission_interval_ms)),
            str(int(aggregate_limit)),
            str(int(aggregate_burst_tolerance_ms)),
            str(float(aggregate_period_ms)),
        ]

    # ADR-0015 idempotency guard: a per-REQUEST nonce, written by the script
    # BEFORE it moves any budget. A timeout that fired AFTER the server
    # executed the script leaves the nonce behind, which is the only way the
    # fallback can tell "never ran" from "ran and the reply was lost".
    nonce = _new_request_nonce()

    try:
        result = await get_lua_manager().execute(
            redis_client,
            "ban_and_gcra_check",
            keys=[gcra_key, ban_key, aggregate_key, _nonce_key(nonce)],
            args=[
                # F-11: no now_ms -- the script reads Redis's clock.
                str(float(emission_interval_ms)),
                str(int(limit)),
                str(int(burst_tolerance_ms)),
                str(float(period_ms)),
                "1",
                *aggregate_args,
                # F-10: with a warm ban negative cache the ban GET is skipped
                # INSIDE this round-trip instead of being paid as a second
                # call — the cached "not banned" verdict is the one the script
                # would read, within the documented 5 s window.
                "0" if ban_cached else "1",
                # ADR-0015: the nonce's own TTL (ms). Long enough to cover the
                # fallback's own round-trips, short enough that the key cannot
                # outlive its request.
                str(_NONCE_TTL_MS),
            ],
        )
        if result and result[0] in _DUPLICATE_SENTINELS:
            # The script has already run for this request; it consumed the
            # budget then. Do NOT consume again -- return the merged verdict
            # the first execution produced by reading the ban leg only.
            return await _duplicate_replay_result(redis_client, client_ip, ban_key)
        (
            is_allowed,
            remaining,
            retry_after_ms,
            is_banned,
            ban_reason,
            ban_ttl,
            agg_limited,
            agg_remaining,
            agg_retry_after_ms,
        ) = result
    except Exception as e:
        logger.warning("Combined ban+GCRA check failed, falling back to sequential", error=str(e))
        # ADR-0015: Enforce "consume at most once" per request. If the nonce is present,
        # the server already executed the merged script prior to the error, meaning the budget was spent.
        # Re-running sequential helpers would double-charge legitimate callers, which ADR-0015 forbids.
        # In this ambiguous state, the system fails closed with a 429 status code rather
        # than risking a duplicate charge.
        if await _nonce_was_claimed(redis_client, nonce):
            logger.warning(
                "merged_gcra_timed_out_after_execution_failing_closed",
                client_ip=client_ip,
                rate_limit_key=rate_limit_key,
            )
            return BanInfo(is_banned=False), (True, 0, period, True), None
        ban_info = await get_ban_info(client_ip)
        from src.app.core.utils.rate_limit import rate_limiter as _rl

        is_limited, remaining, retry_after, degraded = await _rl.check_rate_limit_algorithm_with_status(
            rate_limit_key, limit, period
        )
        return ban_info, (is_limited, remaining, retry_after, degraded), None

    is_b = int(is_banned) == 1
    ban_info = BanInfo(
        is_banned=is_b,
        reason=(ban_reason.decode() if isinstance(ban_reason, bytes) else str(ban_reason or "")) if is_b else None,
        expires_at=(time.time() + int(ban_ttl)) if is_b and int(ban_ttl) > 0 else None,
    )
    if not is_b and not ban_cached:
        _ban_negative_cache[cache_key] = True
    is_limited = int(is_allowed) == 0
    import math

    aggregate: tuple[bool, int, int] | None = None
    if want_aggregate:
        aggregate = (
            int(agg_limited) == 1,
            int(float(agg_remaining)),
            int(math.ceil(float(agg_retry_after_ms) / 1000.0)) if int(agg_limited) == 1 else 0,
        )

    return (
        ban_info,
        (
            is_limited,
            int(float(remaining)),
            int(math.ceil(float(retry_after_ms) / 1000.0)) if is_limited else 0,
            False,  # the merged Lua enforces GCRA — the configured engine
        ),
        aggregate,
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
) -> tuple[bool | None, int, int, str, BanInfo, tuple[bool, int, int, bool]]:
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
        (is_limited, remaining, retry_after, degraded)) for the SELECTED
        candidate. is_blacklisted is None only when Redis is unavailable.
        ``degraded`` is True when the limit was NOT enforced by the configured
        algorithm — the merged Lua always enforces GCRA, so only the
        sequential fallback can report it.
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
            return verdict, sel_limit, sel_period, anon_key, BanInfo(is_banned=False), (False, sel_limit, 0, False)
        return verdict, sel_limit, sel_period, anon_key, BanInfo(is_banned=False), (True, sel_limit, 0, False)

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
        ban_info, rl, _aggregate = await check_ban_and_rate_limit(client_ip, sel_key, sel_limit, sel_period)
        return seq_verdict, sel_limit, sel_period, sel_key, ban_info, rl

    def _gcra_params(limit: int, period: int) -> tuple[int, int, int, int]:
        # Both limits are > 0 here: the `limit <= 0` block-path above returns
        # before this is reached, so the old `else 0` arm was unreachable
        # (E9).
        period_ms = period * 1000
        emission_interval_ms = max(1, round(period_ms / limit))
        return emission_interval_ms, limit, emission_interval_ms * limit, period_ms

    now_sec = int(time.time())
    neg_ttl = getattr(settings.r1_unified, "TOKEN_NEGATIVE_CACHE_TTL", 60)
    negative_key = f"{NEGATIVE_CACHE_KEY}{_token_cache_key(token, jti)}"
    member = _extract_blacklist_member(token, jti)
    ban_key = BAN_KEY_PREFIX.format(ip=client_ip)
    a_emit, a_lim, a_burst, a_per = _gcra_params(authed_limit, authed_period)
    n_emit, n_lim, n_burst, n_per = _gcra_params(anon_limit, anon_period)

    # ADR-0015 idempotency guard (see the sibling `ban_and_gcra_check`).
    nonce = _new_request_nonce()

    try:
        result = await get_lua_manager().execute(
            redis_client,
            "ban_blacklist_gcra_check",
            keys=[
                negative_key,
                BLACKLIST_KEY,
                f"gcra:{authed_key}",
                ban_key,
                f"gcra:{anon_key}",
                _nonce_key(nonce),
            ],
            args=[
                member,
                str(now_sec),
                str(int(neg_ttl)),
                # F-11: no now_ms -- the script reads Redis's clock.
                str(float(a_emit)),
                str(int(a_lim)),
                str(int(a_burst)),
                str(float(a_per)),
                str(float(n_emit)),
                str(int(n_lim)),
                str(int(n_burst)),
                str(float(n_per)),
                str(_NONCE_TTL_MS),  # ADR-0015 nonce TTL
            ],
        )
        if result and result[0] in _DUPLICATE_SENTINELS:
            # ADR-0015: this request's budget was already spent by the first
            # execution. Do not consume again; report the replay verdict.
            seq_verdict = await is_token_blacklisted(token, jti=jti)
            sel_key, sel_limit, sel_period = _selected(seq_verdict)
            replay_ban, replay_rl, _agg = await _duplicate_replay_result(redis_client, client_ip, ban_key)
            return seq_verdict, sel_limit, sel_period, sel_key, replay_ban, replay_rl
        raw_bl, is_allowed, remaining, retry_after_ms, is_banned, ban_reason, ban_ttl = result
    except Exception as e:
        logger.warning("Combined ban+blacklist+GCRA check failed, falling back to sequential", error=str(e))
        # ADR-0015: consume at most ONCE per request (see the sibling
        # `ban_and_gcra_check` handler for the full reasoning). A claimed
        # nonce means the server executed the merged script, so re-running the
        # sequential helpers would charge the caller twice -> fail closed.
        if await _nonce_was_claimed(redis_client, nonce):
            logger.warning(
                "merged_blacklist_gcra_timed_out_after_execution_failing_closed",
                client_ip=client_ip,
            )
            sel_key, sel_limit, sel_period = _selected(None)
            return (
                None,
                sel_limit,
                sel_period,
                sel_key,
                BanInfo(is_banned=False),
                (True, 0, sel_period, True),
            )
        seq_verdict = await is_token_blacklisted(token, jti=jti)
        sel_key, sel_limit, sel_period = _selected(seq_verdict)
        ban_info = await get_ban_info(client_ip)
        from src.app.core.utils.rate_limit import rate_limiter as _rl

        is_limited, remaining, retry_after, degraded = await _rl.check_rate_limit_algorithm_with_status(
            sel_key, sel_limit, sel_period
        )
        return seq_verdict, sel_limit, sel_period, sel_key, ban_info, (is_limited, remaining, retry_after, degraded)

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
        False,  # the merged Lua enforces GCRA — the configured engine
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
        # P1-6 / F9: Cross-worker fan-out for ban/unban operations is best-effort.
        # A pub/sub publish failure must not flip the result to `False` (which callers interpret as "not banned"),
        # because the underlying Lua state change already succeeded. Fan-out errors are guarded
        # to preserve operation success.
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

            if fresh:
                # F-12: ONE MGET for the page instead of per-key GETs. The keys
                # are independent reads and the F19 dedupe above is unchanged,
                # so the sum is identical; the result is still approximate only
                # by the same documented mid-scan races. (get_all_banned_ips
                # already multiplexes its reads this way.)
                for count in await redis_client.mget(*fresh):
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
