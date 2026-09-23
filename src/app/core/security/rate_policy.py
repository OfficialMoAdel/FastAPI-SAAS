"""Shared rate-limit verdicts, key construction, tier policies, and endpoint budgets."""

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.middleware_state import get_client_ip_state
from src.app.core.security.rate_limit_cache import get_tier_policy_limits
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.rate_limit import rate_limiter as default_rate_limiter

logger = get_logger(__name__)


@dataclass(frozen=True)
class RateLimitVerdict:
    """The complete verdict and HTTP headers returned by the rate policy engine."""

    is_limited: bool
    limit: int
    period: int
    remaining: int
    retry_after: int
    key: str
    headers: dict[str, str]
    bypassed: bool = False


# A bypassed caller never reaches a counter, so limit/period are meaningless.
# One pair of named constants beats a bare 999999 at every call site — the
# sentinel is a fact about Bypass, not about the caller.
BYPASS_LIMIT = 999999
BYPASS_PERIOD = 60


def build_verdict(
    *,
    key: str,
    limit: int,
    period: int,
    is_limited: bool,
    remaining: int,
    retry_after: int | float = 0,
    bypassed: bool = False,
    degraded: bool = False,
    extra: dict[str, str] | None = None,
) -> RateLimitVerdict:
    """Assemble the canonical rate-limit verdict.

    The ONE place a counter result becomes a Verdict. Header grammar and the
    ``retry_after == 0`` fallback live here and nowhere else — both the
    per-request admission path and the endpoint Security budgets cross this
    seam, so they cannot drift apart.

    Args:
        key: Bucket key the counter was consumed from.
        limit: Configured limit for the bucket.
        period: Window in seconds.
        is_limited: Whether the caller is over budget.
        remaining: Tokens left, as reported by the engine.
        retry_after: Seconds until the caller may retry (0 = unknown).
        bypassed: Operator-declared whitelist skipped the counter. A bypassed
            verdict is never limited — the ban boundary is enforced elsewhere.
        degraded: The configured engine failed and the fixed-window engine
            served the check. Surfaced as a response header so the swap is
            observable instead of silent.
        extra: Additional headers for a specific caller (e.g. the whitelist
            marker). Caller-specific, never part of the core grammar.
    """
    resolved_retry_after = int(retry_after)
    if is_limited and resolved_retry_after == 0:
        # Both engines can report "limited" with no retry hint when the window
        # is sub-second. Truncating to 0 told clients to retry immediately;
        # fall back to the remainder of the current period.
        resolved_retry_after = period - (int(time.time()) % period) if period else 0

    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Period": str(period),
    }
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


def uses_gcra() -> bool:
    """Whether the operator configured the GCRA engine.

    The ONE place that answers "which engine". The merged ban/blacklist Lua
    composites hard-code GCRA arithmetic (that is what buys their single
    round-trip), so callers must consult this before taking a composite path
    — otherwise ``RATE_LIMIT_ALGORITHM=fixed_window`` silently gets GCRA on
    the authenticated hot path and fixed window everywhere else.
    """
    return settings.rate_limit.RATE_LIMIT_ALGORITHM.lower() == "gcra"


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
        if gcra is not None:
            try:
                result = await gcra.check_rate_limit(key, limit, period, fail_closed=fail_closed)
                # R6 FIX: round UP so sub-second retry windows are not
                # truncated to 0 (0 triggered the fixed-window formula
                # upstream, reporting e.g. 400ms as a full period).
                import math

                retry_after = int(math.ceil(result.retry_after)) if result.is_limited else 0
                return result.is_limited, result.remaining, retry_after, False
            except Exception as exc:
                # A silent swap to a different algorithm is worse than the
                # outage it hides: log at error level and mark the verdict so
                # the degradation is visible in the response.
                logger.error(
                    "GCRA rate limit check failed; degrading to fixed window | key=%s | error=%s",
                    key,
                    exc,
                )
                degraded = True

    is_limited, current_count = await default_rate_limiter.check_rate_limit(
        key, limit, period, fail_closed=fail_closed
    )
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
            from fastcrud import FastCRUD

            from src.app.modules.rate_limits.models import RateLimit

            crud: Any = FastCRUD(RateLimit)
            tier_id_val = int(tier_id)
            all_rows = await crud.get_multi(db=db, tier_id=tier_id_val, return_total_count=False)
            raw_policies = all_rows.get("data") or [] if isinstance(all_rows, dict) else []
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
            logger.debug("DB rate-limit query fallback failed: %s", e)

    # 3. Settings per-tier fallback
    if limit is None or period is None:
        resolved_tier = str(tier_name).lower() if tier_name else "anonymous"
        limit_config = settings.rate_limit.TIER_RATE_LIMITS.get(resolved_tier)
        if limit_config is None:
            default_limit = (
                settings.rate_limit.DEFAULT_AUTHENTICATED_LIMIT
                if user_id
                else settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT
            )
            limit_config = default_limit

        limit, period = limit_config

    return limit, period


async def resolve_anonymous_user_id(request: Request) -> str:  # noqa: C901
    """Resolve an anonymous user identifier using email-body hash or client IP.

    .. deprecated::
        No production callers (endpoint budgets inline their own email-hash
        identity, e.g. magic_link_send; the middleware resolves anonymous
        identity inline from ``request.state``/JWT). Kept for tests and
        ad-hoc tooling; do not wire new production callers here — extend
        ``check_security_budget`` instead.
    """
    # Check if request has an email in JSON body for per-email rate limiting
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict) and "email" in body and body["email"]:
                email_str = str(body["email"]).lower().strip()
                email_hash = hashlib.sha256(email_str.encode()).hexdigest()[:16]
                return f"email:{email_hash}"
        except Exception:
            pass

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

    from src.app.core.utils.cache import build_tenant_key

    base_key = f"rate_limit:user:{user_id}:{path}"
    return build_tenant_key(base_key, tenant_id)


async def check_security_budget(
    budget: str,
    identifier: str,
    *,
    tenant_id: Any = None,
    limiter: Any = None,
) -> RateLimitVerdict:
    """Check an identity-keyed security budget (login/MFA/passkey/AI defenses).

    The single seam for endpoint-level brute-force budgets, replacing the
    per-module limiter helpers that each hand-rolled keys, hardcoded limits,
    and called the fixed-window engine directly (bypassing the configured
    algorithm and fail-open policy). Consumers pass an identity (client IP
    for anonymous surfaces, user id for authenticated ones) and a budget
    name; limit/period come from ``settings.rate_limit.SECURITY_BUDGETS``.

    Keys are namespaced under the canonical ``build_rate_limit_key`` scheme
    so every rate-limit consumer in the app shares one key format. No path
    is involved — a budget is endpoint semantics, not path policy.

    Args:
        budget: Budget name; must exist in SECURITY_BUDGETS.
        identifier: Identity to key on (client IP or user id).
        tenant_id: Optional tenant scoping for authenticated budgets.
        limiter: Optional limiter override (tests).

    Returns:
        RateLimitVerdict with is_limited and Retry-After headers.

    Raises:
        KeyError: If the budget name is not configured (fail-loud, not a
            silent default — an unknown budget means a caller typo).
    """
    limit, period = settings.rate_limit.SECURITY_BUDGETS[budget]

    # Budget keys get their own namespace inside the canonical scheme; a
    # bare identifier would collide with path-shaped keys from
    # build_rate_limit_key's "rate_limit:user:{id}:{path}" format.
    key = build_rate_limit_key(f"budget:{budget}", user_id=identifier, tenant_id=tenant_id)

    # C2: fail-closed budgets block on Redis outage even when the general
    # RATE_LIMIT_FAIL_OPEN=True. The explicit flag threads through the
    # limiter (fixed-window + GCRA paths); general path policy stays
    # operator-controlled.
    fail_closed = budget in settings.rate_limit.SECURITY_BUDGETS_FAIL_CLOSED
    is_limited, remaining, retry_after, degraded = await consume(
        key, limit, period, fail_closed=fail_closed, limiter=limiter
    )

    return build_verdict(
        key=key,
        limit=limit,
        period=period,
        is_limited=is_limited,
        remaining=remaining,
        retry_after=retry_after,
        degraded=degraded,
    )


__all__ = [
    "BYPASS_LIMIT",
    "BYPASS_PERIOD",
    "RateLimitVerdict",
    "build_rate_limit_key",
    "build_verdict",
    "check_security_budget",
    "consume",
    "resolve_anonymous_user_id",
    "resolve_rate_limits",
    "uses_gcra",
]
