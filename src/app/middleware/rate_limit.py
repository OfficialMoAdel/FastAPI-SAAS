"""Rate limit middleware with tier-aware limits.

Provides different rate limits based on user's tier:
- Unauthenticated: IP-based with default limits
- Authenticated: User-based with tier-specific limits

Supports two algorithms:
- fixed_window: Classic fixed window counter (default)
- gcra: Generic Cell Rate Algorithm (smoother, no boundary bursts)
"""

import time
from collections.abc import Callable

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security.auto_ban import check_and_auto_ban, get_ban_info, is_ip_banned
from src.app.core.security.jwt_utils import extract_jwt_claims_to_state, get_cached_jwt_payload
from src.app.core.security.logging import SecurityEvent, SecurityEventType, log_security_event
from src.app.core.security.token_blacklist_cache import is_token_blacklisted
from src.app.core.utils.gcra_rate_limit import get_gcra_rate_limiter
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.rate_limit import rate_limiter

logger = get_logger(__name__)

# Tier-based rate limits configuration
# Format: {tier_name: (limit, period_seconds)}
TIER_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "free": (10, 3600),  # 10 requests per hour
    "basic": (100, 3600),  # 100 requests per hour
    "pro": (1000, 3600),  # 1000 requests per hour
    "enterprise": (10000, 3600),  # 10000 requests per hour
}

# Default for users without tier or unknown tier
DEFAULT_AUTHENTICATED_LIMIT = (50, 3600)  # 50 requests per hour


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for tier-aware rate limiting.

    Features:
    - Unauthenticated requests: IP-based limits
    - Authenticated requests: User + tier-based limits
    - Configurable limits per tier
    - X-RateLimit-* response headers
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with tier-aware rate limiting and auto-ban."""
        client_ip = get_client_ip(request)

        # SECURITY: Check if IP is banned before processing
        if settings.AUTO_BAN_ENABLED:
            if await is_ip_banned(client_ip):
                ban_info = await get_ban_info(client_ip)
                logger.warning(
                    "Request from banned IP blocked | ip=%s | reason=%s",
                    client_ip,
                    ban_info.reason,
                )

                # Log security event
                security_event = SecurityEvent(
                    event_type=SecurityEventType.ATTACK_DETECTED,
                    client_ip=client_ip,
                    request_path=str(request.url.path),
                    reason="Request from banned IP",
                    extra={"ban_reason": ban_info.reason, "expires_at": ban_info.expires_at},
                )
                log_security_event(security_event)

                headers = {
                    "X-Banned": "true",
                    "X-Ban-Reason": ban_info.reason or "Auto-ban",
                }
                if ban_info.expires_at:
                    retry_after = int(ban_info.expires_at - time.time()) + 1
                    if retry_after > 0:
                        headers["Retry-After"] = str(retry_after)

                return Response(
                    content="Forbidden: IP address banned due to abuse",
                    status_code=status.HTTP_403_FORBIDDEN,
                    headers=headers,
                )

        # Check if IP is whitelisted and should bypass rate limiting
        if settings.IP_WHITELIST_BYPASS_RATE_LIMIT:
            if hasattr(request.state, "ip_whitelisted"):
                is_whitelisted = request.state.ip_whitelisted
            else:
                logger.warning(
                    "RateLimitMiddleware: ip_whitelisted attribute not set. "
                    "Ensure IPFilterMiddleware is enabled and ordered before RateLimitMiddleware."
                )
                is_whitelisted = False

            if is_whitelisted:
                # Skip rate limiting for whitelisted IPs
                whitelisted_response: Response = await call_next(request)
                # Add headers indicating unlimited rate for whitelisted IPs
                whitelisted_response.headers["X-RateLimit-Limit"] = "unlimited"
                whitelisted_response.headers["X-RateLimit-Remaining"] = "unlimited"
                whitelisted_response.headers["X-RateLimit-Whitelisted"] = "true"
                return whitelisted_response

        # Determine rate limit key and limits
        limit, period, key = await self._get_rate_limit_config(request)

        # Check rate limit using configured algorithm
        use_gcra = getattr(settings, "RATE_LIMIT_ALGORITHM", "fixed_window").lower() == "gcra"

        if use_gcra:
            gcra = get_gcra_rate_limiter()
            if gcra:
                try:
                    result = await gcra.check_rate_limit(key, limit, period)
                    is_limited = result.is_limited
                    remaining = result.remaining
                    retry_after = int(result.retry_after)
                except Exception as e:
                    logger.warning("GCRA rate limit check failed: %s", e)
                    # Fallback to fixed window
                    try:
                        is_limited, current_count = await rate_limiter.check_rate_limit(key, limit, period)
                        remaining = max(0, limit - current_count)
                        retry_after = 0
                    except Exception as fallback_e:
                        logger.error("Rate limit fallback check failed: %s", fallback_e)
                        is_limited = not getattr(settings, "RATE_LIMIT_FAIL_OPEN", False)
                        remaining = 0 if is_limited else limit
                        retry_after = 0
            else:
                # GCRA not initialized, fall back to fixed window
                is_limited, current_count = await rate_limiter.check_rate_limit(key, limit, period)
                remaining = max(0, limit - current_count)
                retry_after = 0
        else:
            # Fixed window algorithm
            is_limited, current_count = await rate_limiter.check_rate_limit(key, limit, period)
            remaining = max(0, limit - current_count)
            retry_after = 0

        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Period": str(period),
        }

        if is_limited:
            if retry_after > 0:
                headers["Retry-After"] = str(int(retry_after) + 1)
            logger.warning("Rate limit exceeded | key=%s | limit=%s | period=%s", key, limit, period)

            # AUTO-BAN: Track violations and ban repeat offenders
            if settings.AUTO_BAN_ENABLED and client_ip:
                try:
                    was_banned, violation_count = await check_and_auto_ban(client_ip)
                except Exception as auto_ban_err:
                    # BUG FIX: If auto-ban (Redis) fails, log warning but still return 429.
                    # Without this, a Redis outage would cause an unhandled exception here,
                    # turning a rate-limit response into a 500 for all rate-limited clients.
                    logger.warning(
                        "Auto-ban check failed, skipping | ip=%s | error=%s",
                        client_ip,
                        auto_ban_err,
                    )
                    was_banned, violation_count = False, 0
                if was_banned:
                    logger.warning(
                        "Auto-ban triggered | ip=%s | violations=%d | threshold=%d",
                        client_ip,
                        violation_count,
                        settings.AUTO_BAN_THRESHOLD,
                    )
                    # Return 403 Forbidden instead of 429 to indicate ban
                    headers["X-Banned"] = "true"
                    headers["X-Ban-Reason"] = f"Auto-ban after {violation_count} violations"
                    from fastapi.responses import JSONResponse

                    return JSONResponse(
                        content={"detail": f"Forbidden: IP banned after {violation_count} rate limit violations"},
                        status_code=status.HTTP_403_FORBIDDEN,
                        headers=headers,
                    )

            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"detail": "Rate limit exceeded"}, status_code=status.HTTP_429_TOO_MANY_REQUESTS, headers=headers
            )

        response: Response = await call_next(request)

        # Add rate limit headers to response
        for name, value in headers.items():
            response.headers[name] = value

        return response

    async def _get_rate_limit_config(self, request: Request) -> tuple[int, int, str]:
        """Determine rate limit configuration based on request context.

        Security: Checks token blacklist to prevent revoked tokens from keeping premium limits.

        Returns
        -------
            Tuple of (limit, period, key)
        """
        # 1. Try to get user from request state (if auth middleware ran first)
        user = getattr(request.state, "user", None)
        user_id = None
        tier_name = None
        tenant_id = None

        if user:
            # Handle both ORM objects and dicts safely
            if isinstance(user, dict):
                user_id = user.get("id")
                tenant_id = str(user["tenant_id"]) if user.get("tenant_id") else None
                tier_name = await self._get_user_tier_name(user)
            else:
                user_id = getattr(user, "id", None)
                tenant_id = str(getattr(user, "tenant_id", None) or "") or None
                tier_name = await self._get_user_tier_name(user)

        # 2. If no user in state, use jwt_utils to extract/cache JWT claims
        # This eliminates double JWT decode when auth dependency runs later

        # Always extract token from Authorization header for blacklist check
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else None

        cached_payload = get_cached_jwt_payload(request)
        if cached_payload is None and token:
            # Not cached yet - extract and cache for later use
            cached_payload = await extract_jwt_claims_to_state(request)

        if not user_id:
            if cached_payload:
                user_id = cached_payload.get("sub")
                tier_name = cached_payload.get("tier_name")

        # Always try to get tenant_id from JWT payload if not already set
        if tenant_id is None and cached_payload:
            tenant_id = cached_payload.get("tenant_id")

        # 3. Security: Check if token is blacklisted (SECURITY-GAP-1 fix)
        # If token is blacklisted, treat user as anonymous to prevent premium rate limit abuse
        if user_id and token:
            is_blacklisted = await is_token_blacklisted(token)
            request.state.is_token_blacklisted = is_blacklisted
            if is_blacklisted:
                # Token is blacklisted - downgrade to anonymous limits
                logger.info(
                    "Blacklisted token detected in rate limiter | user_id=%s | treating as anonymous",
                    user_id,
                )
                user_id = None
                tier_name = None

        if user_id:
            # Authenticated user - use tier-based limits
            # If we successfully identified the user (via state or JWT), we use their specific key
            if tier_name:
                limit, period = TIER_RATE_LIMITS.get(tier_name.lower(), DEFAULT_AUTHENTICATED_LIMIT)
            else:
                limit, period = DEFAULT_AUTHENTICATED_LIMIT
            # SECURITY FIX: Include tenant_id in key so tenants never share rate limit counters.
            # Without this, user IDs that happen to collide across tenants share the same bucket.
            if tenant_id:
                key = f"tenant:{tenant_id}:user:{user_id}"
            else:
                key = f"user:{user_id}"
        else:
            # Unauthenticated - use IP + tenant so different tenants on same IP aren't pooled.
            limit = settings.rate_limit.DEFAULT_RATE_LIMIT_LIMIT
            period = settings.rate_limit.DEFAULT_RATE_LIMIT_PERIOD
            if tenant_id:
                key = f"tenant:{tenant_id}:ip:{get_client_ip(request)}"
            else:
                key = f"ip:{get_client_ip(request)}"

        return limit, period, key

    async def _get_user_tier_name(self, user) -> str | None:
        """Get tier name for user from eager-loaded tier object.

        The tier is now eager-loaded in get_current_user via joinedload,
        eliminating the need for DB queries in middleware.

        Args:
            user: User object or dict with tier included

        Returns:
            Tier name or None
        """
        # Try to get tier from user object/dict directly (eager-loaded)
        if isinstance(user, dict):
            tier = user.get("tier")
            if tier:
                return tier.get("name") if isinstance(tier, dict) else tier
        else:
            tier = getattr(user, "tier", None)
            if tier:
                return getattr(tier, "name", None)

        # No tier loaded - return None (will use default rate limit)
        return None
