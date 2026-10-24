"""Shared rate-limit verdicts, key construction, tier policies, and endpoint budgets."""

import hashlib
import math
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.middleware_state import get_client_ip_state
from src.app.core.security.policies import require_rate_policy_fetcher
from src.app.core.security.rate_limit_cache import get_tier_policy_limits
from src.app.core.utils.cache import build_tenant_key
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.rate_limit import rate_limiter as default_rate_limiter

logger = get_logger(__name__)


@dataclass(frozen=True)
class RateLimitVerdict:
    """The complete verdict and HTTP headers returned by the rate policy engine."""

    is_limited: bool
    limit: int | None
    period: int | None
    remaining: int | None
    retry_after: int
    key: str
    headers: dict[str, str]
    bypassed: bool = False

    def retry_after_seconds(self) -> int:
        """Return a numeric retry delay for a limited, counter-backed verdict."""
        if self.retry_after > 0:
            return self.retry_after
        if self.period is None:
            raise ValueError("A limited rate-limit verdict requires a period")
        return self.period


def build_verdict(
    *,
    key: str,
    limit: int | None,
    period: int | None,
    is_limited: bool,
    remaining: int | None,
    retry_after: int | float = 0,
    bypassed: bool = False,
    degraded: bool = False,
    extra: dict[str, str] | None = None,
) -> RateLimitVerdict:
    """Assemble the canonical rate-limit verdict.

    Numeric quota fields are optional because a bypassed caller never reaches
    a counter. ``None`` means no quota was evaluated and is never serialized
    as a fabricated number.
    """
    resolved_retry_after = int(retry_after)
    if is_limited and resolved_retry_after == 0:
        resolved_retry_after = period - (int(time.time()) % period) if period else 0

    headers: dict[str, str] = {}
    if limit is not None:
        headers["X-RateLimit-Limit"] = str(limit)
    if remaining is not None:
        headers["X-RateLimit-Remaining"] = str(remaining)
    if period is not None:
        headers["X-RateLimit-Period"] = str(period)
    if is_limited and resolved_retry_after > 0:
        headers["Retry-After"] = str(resolved_retry_after)
    if degraded:
        headers["X-RateLimit-Algorithm"] = "fixed-window (degraded)"
    if bypassed:
        headers["X-RateLimit-Bypassed"] = "true"
    if extra:
        headers.update(extra)

    return RateLimitVerdict(
        is_limited=is_limited,
        limit=limit,
        period=period,
        remaining=remaining,
        retry_after=resolved_retry_after,
        key=key,
        headers=headers,
        bypassed=bypassed,
    )


def window_reset_seconds(period: int | None) -> int:
    """Seconds until the current rate-limit window rolls over.

    The reset a client should wait for: the remainder of the window the
    counter lives in. ``build_verdict`` uses the same formula for the
    ``Retry-After`` of a limited verdict.

    Args:
        period: The window length in seconds, or None when no window applies.

    Returns:
        The seconds left in the window (0 when there is no window).
    """
    if not period or period <= 0:
        return 0
    return period - (int(time.time()) % period)


#: Engine decision cache, keyed on the CONFIGURED VALUE (§6).
#:
#: Not frozen at import: `RATE_LIMIT_ALGORITHM` is read at call time because
#: the engine fence is a live contract -- tests flip it to prove a
#: fixed-window operator is not routed through the GCRA composite, and an
#: operator-facing knob that silently required a restart would be a trap.
#: Keying the cache on the value keeps the per-call cost at one dict lookup
#: (no `.lower()` allocation) while preserving that behaviour.
_GCRA_DECISION_CACHE: dict[str, bool] = {}


def uses_gcra() -> bool:
    """Whether the operator configured the GCRA engine.

    The ONE place that answers "which engine". The merged ban/blacklist Lua
    composites hard-code GCRA arithmetic (that is what buys their single
    round-trip), so callers must consult this before taking a composite path
    — otherwise ``RATE_LIMIT_ALGORITHM=fixed_window`` silently gets GCRA on
    the authenticated hot path and fixed window everywhere else.
    """
    # §6: the `.lower()` on a constant string was per-call waste. Memoized on
    # the value, so a runtime change is still honoured (see the cache note).
    algorithm = settings.rate_limit.RATE_LIMIT_ALGORITHM
    decision = _GCRA_DECISION_CACHE.get(algorithm)
    if decision is None:
        decision = algorithm.lower() == "gcra"
        _GCRA_DECISION_CACHE[algorithm] = decision
    return decision


async def consume(
    key: str,
    limit: int,
    period: int,
    *,
    fail_closed: bool = False,
    limiter: Any = None,
) -> tuple[bool, int, int, bool]:
    """Consume one token from a rate-limit bucket.

    The ONE place that chooses an engine, and the ONE place a GCRA failure can
    degrade to the fixed-window engine. Every rate-limit consumer in the app
    crosses this seam, so algorithm policy cannot leak to call sites.

    Args:
        key: Bucket key.
        limit: Configured limit.
        period: Window in seconds.
        fail_closed: Override the operator fail-open default (brute-force
            budgets pass True so a Redis outage maps to limited).
        limiter: Injected engine. The test seam — an injected engine replaces
            dispatch entirely and must expose ``check_rate_limit_algorithm``.

    Returns:
        ``(is_limited, remaining, retry_after, degraded)``.
    """
    if limiter is not None:
        is_limited, remaining, retry_after = await limiter.check_rate_limit_algorithm(
            key, limit, period, fail_closed=fail_closed
        )
        return is_limited, remaining, retry_after, False

    degraded = False
    if uses_gcra():
        from src.app.core.utils.gcra_rate_limit import get_gcra_rate_limiter

        gcra = get_gcra_rate_limiter()
        if gcra is None:
            # GCRA is the CONFIGURED engine but the limiter was never
            # initialised — Redis was absent at boot, so
            # `init_gcra_rate_limiter` never ran (composition.py). The
            # fixed window below will enforce the limit instead: the same
            # degradation as a failed GCRA call, including the 2x boundary
            # burst GCRA exists to remove. Counted and reported, not silent.
            from src.app.core.monitoring.r1_unified_metrics import record_fail_open

            record_fail_open("rate_limit:gcra_uninitialized")
            logger.error("gcra_limiter_uninitialized_degrading_to_fixed_window", key=key)
            degraded = True
        else:
            try:
                result = await gcra.check_rate_limit(key, limit, period, fail_closed=fail_closed)
                # R6 FIX: round UP so sub-second retry windows are not
                # truncated to 0 (0 triggered the fixed-window formula
                # upstream, reporting e.g. 400ms as a full period).
                # §6: `math` is imported at module scope (a function-local
                # import re-hits sys.modules on every call).
                retry_after = int(math.ceil(result.retry_after)) if result.is_limited else 0
                return result.is_limited, result.remaining, retry_after, False
            except Exception as exc:
                # A silent swap to a different algorithm is worse than the
                # outage it hides: log at error level, mark the verdict so the
                # degradation is visible in the response, and COUNT it. The
                # fixed-window path below already calls record_fail_open on
                # its own failure (core/utils/rate_limit.py:75,79); without a
                # counter here, operators saw nothing while every auth/MFA
                # budget silently changed engine.
                from src.app.core.monitoring.r1_unified_metrics import record_fail_open

                record_fail_open("rate_limit:gcra_degraded")
                logger.error(
                    "gcra_check_failed_degrading_to_fixed_window",
                    key=key,
                    error=str(exc),
                )
                degraded = True

    is_limited, current_count = await default_rate_limiter.check_rate_limit(key, limit, period, fail_closed=fail_closed)
    return is_limited, max(0, limit - current_count), 0, degraded


async def resolve_rate_limits(
    path: str,
    tier_id: int | str | None = None,
    tier_name: str | None = None,
    user_id: str | None = None,
    db: AsyncSession | None = None,
) -> tuple[int, int]:
    """Resolve (limit, period) for a given path and tier.

    Order of precedence:
    1. Tier-policy cache (L1 in-memory + Redis index via get_tier_policy)
    2. Direct DB query (crud_rate_limits.get) if db is provided and cache missed
    3. Settings per-tier configuration (settings.rate_limit.TIER_RATE_LIMITS)
    4. Settings default limit (authenticated vs anonymous)
    """
    limit: int | None = None
    period: int | None = None

    # 1. Tier-policy cache lookup (R5-MICRO: compiled per-path map instead
    # of a linear scan over the policy list on every request)
    if tier_id is not None:
        try:
            tier_id_val = int(tier_id)
            limit, period = await get_tier_policy_limits(tier_id_val, path)
        except (ValueError, TypeError):
            pass

    # 2. DB fallback if db session provided and cache missed. H-9: on hit,
    # write the full tier policy list back to the policy cache so subsequent
    # requests resolve from L1/Redis instead of repeating this DB query.
    # P2: single get_multi (was: per-path get + full get_multi = 2 queries).
    # The path match resolves in Python over the same row set, so the
    # resolved (limit, period) and the write-back payload are identical.
    if (limit is None or period is None) and db is not None and tier_id is not None:
        try:
            # Rows come from the application seam; see core/security/policies.py.
            # Which row applies to a path stays core's decision — only the
            # fetch is supplied from outside.
            fetch_tier_policies = require_rate_policy_fetcher()
            tier_id_val = int(tier_id)
            raw_policies = await fetch_tier_policies(db, tier_id_val)
            matched = False
            for rec in raw_policies:
                rec_path = rec.get("path") if isinstance(rec, dict) else getattr(rec, "path", None)
                if rec_path != path:
                    continue
                limit = rec.get("limit") if isinstance(rec, dict) else getattr(rec, "limit", None)
                period = rec.get("period") if isinstance(rec, dict) else getattr(rec, "period", None)
                matched = True
                break
            if matched:
                # H-9 write-back: refresh this tier's whole policy list in
                # Redis + L1 so the next request is a zero-DB resolution.
                try:
                    from src.app.core.security.rate_limit_cache import warm_policy_cache

                    policies = [dict(r) if not isinstance(r, dict) else r for r in raw_policies]
                    if policies:
                        await warm_policy_cache({tier_id_val: policies})
                except Exception as wb_err:
                    logger.debug("policy cache write-back failed", error=str(wb_err))
        except Exception as e:
            logger.debug("rate_limit_db_fallback_failed", error=str(e))

    # 3. Settings per-tier fallback
    if limit is None or period is None:
        resolved_tier = str(tier_name).lower() if tier_name else "anonymous"
        limit_config = settings.rate_limit.TIER_RATE_LIMITS.get(resolved_tier)
        if limit_config is None:
            if tier_name:
                # The tier was NAMED but is not configured: the caller would
                # silently get the generic default instead of its tier's
                # budget. Loud, because it is a configuration mistake — the
                # anonymous fallback (no tier name) is NOT a mistake and must
                # not spam this line.
                logger.warning(
                    "rate_limit_tier_not_configured",
                    tier_name=resolved_tier,
                    user_id=user_id,
                )
            default_limit = (
                settings.rate_limit.DEFAULT_AUTHENTICATED_LIMIT
                if user_id
                else settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT
            )
            limit_config = default_limit

        limit, period = limit_config

    # C9: a period of 0 makes the 429 carry no Retry-After (the reset
    # formula needs the period). The DB rows are protected by a
    # `period > 0` check constraint, so this is the operator-configured
    # path; the floor keeps the window non-degenerate instead of serving a
    # 429 with no retry hint.
    if period <= 0:
        logger.warning("rate_limit_period_invalid_floored", period=period, limit=limit)
        period = 1

    return limit, period


async def resolve_anonymous_user_id(request: Request) -> str:  # noqa: C901
    """Resolve an anonymous user identifier using email-body hash or client IP.

    .. deprecated::
        No production callers (endpoint budgets inline their own email-hash
        identity, e.g. magic_link_send; the middleware resolves anonymous
        identity inline from ``request.state``/JWT). Kept for tests and
        ad-hoc tooling; do not wire new production callers here — extend
        the security-budget seam instead.
    """
    # Check if request has an email in JSON body for per-email rate limiting
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict) and "email" in body and body["email"]:
                email_str = str(body["email"]).lower().strip()
                email_hash = hashlib.sha256(email_str.encode()).hexdigest()[:16]
                return f"email:{email_hash}"
        except Exception as exc:
            # H2: log-and-fall-through — an unreadable body just means no
            # per-email identity; the IP fallback below still applies.
            logger.debug("anonymous identity body parse failed", error=str(exc))

    client_ip: str | None = None
    if request is not None:
        raw_ip = get_client_ip_state(request.state)
        if isinstance(raw_ip, str) and raw_ip:
            client_ip = raw_ip
        else:
            client_ip = get_client_ip(request)
    return f"unknown:{client_ip or 'unknown'}"


def build_rate_limit_key(
    path: str,
    user_id: str | None = None,
    tenant_id: Any = None,
    anonymous_ip: str | None = None,
) -> str:
    """Build canonical rate limit key."""
    if not user_id:
        # P2: a missing IP and the literal "unknown" sentinel share one
        # explicit bucket (never a per-request fragment, never colliding
        # with a real IP — "unknown" is not a valid IP literal).
        # NOTE: tenant scoping for anonymous keys was deliberately NOT added:
        # the pinned key contract (exact-match assertions in
        # test_merged_blacklist_ratelimit.py) and existing Redis buckets use
        # the unprefixed form; scoping needs a coordinated key migration.
        ip_str = anonymous_ip if anonymous_ip and anonymous_ip != "unknown" else "unknown"
        return f"rate_limit:anonymous:ip:{ip_str}:{path}"

    # §6: imported at module scope. `utils.cache` does not import this module,
    # so the hoist cannot create a cycle (verified by the import-linter
    # contract in CI).
    base_key = f"rate_limit:user:{user_id}:{path}"
    return build_tenant_key(base_key, tenant_id)


# Run-3 N-1 (middleware.rate_limit.anonymous_key_path_scoped): constant
# discriminator for the aggregate anonymous bucket. Because it is a fixed token
# rather than the request path, rotating path segments cannot mint a fresh
# aggregate bucket - which was the original bypass.
AGGREGATE_PATH_TOKEN = "__ip_aggregate__"


def build_anonymous_aggregate_key(anonymous_ip: str | None) -> str:
    """Per-IP aggregate bucket for anonymous traffic.

    Keyed by client IP with a CONSTANT path component, so it is immune to path
    rotation. Per-path buckets roll up into this ceiling, so an anonymous caller
    cannot buy unlimited requests by rotating a string path segment.
    """
    ip_str = anonymous_ip if anonymous_ip and anonymous_ip != "unknown" else "unknown"
    return f"rate_limit:anonymous:ip:{ip_str}:{AGGREGATE_PATH_TOKEN}"


def build_api_key_rate_limit_key(path: str, api_key_ip: str | None) -> str:
    """Per-IP bucket for a caller presenting ``X-API-Key`` (bead g4my).

    Its own namespace, not the anonymous one, for the same reason the budget is
    its own (see ``settings.rate_limit.API_KEY_LIMIT``): sharing
    ``rate_limit:anonymous:ip:*`` would make a keyed integration share - and
    exhaust - the browser budget, and would pull it into the anonymous
    aggregate ceiling, which exists to bound UNAUTHENTICATED traffic.

    Keyed by source IP rather than by key: admission runs before the key is
    validated, so a bucket per presented key would let a brute-forcer mint an
    unlimited number of buckets by guessing.

    No tenant scoping, matching ``build_rate_limit_key``'s anonymous form and
    for the same reason - scoping either anonymous-style key needs a coordinated
    migration of the buckets already in Redis.
    """
    ip_str = api_key_ip if api_key_ip and api_key_ip != "unknown" else "unknown"
    return f"rate_limit:api_key:ip:{ip_str}:{path}"


__all__ = [
    "AGGREGATE_PATH_TOKEN",
    "RateLimitVerdict",
    "build_anonymous_aggregate_key",
    "build_api_key_rate_limit_key",
    "build_rate_limit_key",
    "build_verdict",
    "consume",
    "resolve_anonymous_user_id",
    "resolve_rate_limits",
    "uses_gcra",
    "window_reset_seconds",
]
