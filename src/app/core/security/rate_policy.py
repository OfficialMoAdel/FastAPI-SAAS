"""Unified Rate-Policy Engine (Architecture Candidate 10).

Single seam answering "what is this user/tenant allowed and are they limited?"
across:
- RateLimitMiddleware (ASGI middleware path)
- rate_limiter_dependency (FastAPI Depends path)

Centralizes:
- Tier resolution (cached L1/Redis policies via get_tier_policy -> DB fallback -> settings)
- Path sanitization
- Anonymous vs Authenticated identity & key generation (including email-hash fallback)
- Blacklist / revocation downgrade
- RateLimitVerdict with computed headers
"""

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security.rate_limit_cache import get_tier_policy_limits
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.path_utils import sanitize_path
from src.app.core.utils.rate_limit import rate_limiter as default_rate_limiter
from src.app.core.utils.user_utils import get_user_attr

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


async def resolve_rate_limit_identity(
    request: Request,
    user: Any = None,
) -> tuple[str | None, int | str | None, str | None, Any]:
    """Extract (user_id, tier_id, tier_name, tenant_id) from request or user.

    Handles token blacklist downgrade to anonymous.
    """
    user_id: str | None = None
    tier_id: int | str | None = None
    tier_name: str | None = None
    tenant_id: Any = None

    if user is not None:
        raw_uid = get_user_attr(user, "id")
        user_id = str(raw_uid) if raw_uid is not None else None
        tier = get_user_attr(user, "tier")
        if tier:
            tier_id = tier["id"] if isinstance(tier, dict) else getattr(tier, "id", None)
            tier_name = tier["name"] if isinstance(tier, dict) else getattr(tier, "name", None)
    else:
        # Check cached JWT payload from request
        from src.app.core.security.jwt_utils import get_cached_jwt_payload

        cached_payload = get_cached_jwt_payload(request)
        if cached_payload:
            user_id = cached_payload.get("sub")
            tier_name = cached_payload.get("tier_name")
            tier_id = cached_payload.get("tier_id")
            tenant_id = cached_payload.get("tenant_id")

    # Blacklist check: if token is blacklisted, downgrade to anonymous
    is_blacklisted = getattr(request.state, "is_token_blacklisted", None) if request is not None else None
    if is_blacklisted is True:
        user_id = None
        tier_name = None
        tier_id = None

    return user_id, tier_id, tier_name, tenant_id


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


async def resolve_anonymous_user_id(request: Request) -> str:
    """Resolve an anonymous user identifier using email-body hash or client IP."""
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
        raw_ip = getattr(request.state, "client_ip", None)
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
        ip_str = anonymous_ip or "unknown"
        return f"rate_limit:anonymous:ip:{ip_str}:{path}"

    from src.app.core.utils.cache import build_tenant_key

    base_key = f"rate_limit:user:{user_id}:{path}"
    return build_tenant_key(base_key, tenant_id)


async def evaluate_rate_policy(
    request: Request,
    *,
    user: Any = None,
    db: AsyncSession | None = None,
    limiter: Any = None,
) -> RateLimitVerdict:
    """Evaluate rate policy for a request and return a comprehensive verdict."""
    # 1. IP Whitelist bypass
    if settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT and getattr(request.state, "ip_whitelisted", False):
        headers = {
            "X-RateLimit-Limit": "999999",
            "X-RateLimit-Remaining": "999999",
            "X-RateLimit-Whitelisted": "true",
        }
        return RateLimitVerdict(
            is_limited=False,
            limit=999999,
            period=60,
            remaining=999999,
            retry_after=0,
            key="whitelisted",
            headers=headers,
            bypassed=True,
        )

    # 2. Path normalization
    path = sanitize_path(request.url.path)

    # 3. Identity extraction
    user_id, tier_id, tier_name, tenant_id = await resolve_rate_limit_identity(request, user)

    # 4. Limit resolution
    limit, period = await resolve_rate_limits(path, tier_id=tier_id, tier_name=tier_name, user_id=user_id, db=db)

    raw_ip = getattr(request.state, "client_ip", None) if request is not None else None
    anonymous_ip = (
        raw_ip if isinstance(raw_ip, str) and raw_ip else (get_client_ip(request) if request is not None else "unknown")
    )
    key = build_rate_limit_key(path, user_id=user_id, tenant_id=tenant_id, anonymous_ip=anonymous_ip)

    # 6. Check algorithm
    effective_limiter = limiter or default_rate_limiter
    is_limited, remaining, retry_after = await effective_limiter.check_rate_limit_algorithm(key, limit, period)

    if is_limited and retry_after == 0:
        retry_after = period - (int(time.time()) % period)

    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Period": str(period),
    }
    if is_limited and retry_after > 0:
        headers["Retry-After"] = str(int(retry_after))

    return RateLimitVerdict(
        is_limited=is_limited,
        limit=limit,
        period=period,
        remaining=remaining,
        retry_after=retry_after,
        key=key,
        headers=headers,
        bypassed=False,
    )


__all__ = [
    "RateLimitVerdict",
    "build_rate_limit_key",
    "evaluate_rate_policy",
    "resolve_anonymous_user_id",
    "resolve_rate_limit_identity",
    "resolve_rate_limits",
]
