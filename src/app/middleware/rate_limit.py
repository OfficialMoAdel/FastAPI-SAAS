"""Rate limit middleware with tier-aware limits.

Provides different rate limits based on user's tier:
- Unauthenticated: IP-based with default limits
- Authenticated: User-based with tier-specific limits
"""

import logging
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.utils.rate_limit import rate_limiter

logger = logging.getLogger(__name__)

# Tier-based rate limits configuration
# Format: {tier_name: (limit, period_seconds)}
TIER_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "free": (10, 3600),       # 10 requests per hour
    "basic": (100, 3600),     # 100 requests per hour
    "pro": (1000, 3600),      # 1000 requests per hour
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
        """Process request with tier-aware rate limiting."""
        # Determine rate limit key and limits
        limit, period, key = await self._get_rate_limit_config(request)

        # Check rate limit
        is_limited, current_count = await rate_limiter.check_rate_limit(key, limit, period)
        remaining = max(0, limit - current_count)

        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Period": str(period),
        }

        if is_limited:
            logger.warning(
                "Rate limit exceeded | key=%s | limit=%s | period=%s",
                key, limit, period
            )
            return Response("Too Many Requests", status_code=429, headers=headers)

        response = await call_next(request)

        # Add rate limit headers to response
        for name, value in headers.items():
            response.headers[name] = value

        return response

    async def _get_rate_limit_config(self, request: Request) -> tuple[int, int, str]:
        """Determine rate limit configuration based on request context.
        
        Returns:
            Tuple of (limit, period, key)
        """
        # Try to get user tier from request state (set by auth dependency)
        user = getattr(request.state, "user", None)
        
        if user:
            # Authenticated user - use tier-based limits
            tier_name = await self._get_user_tier_name(user)
            limit, period = TIER_RATE_LIMITS.get(
                tier_name.lower() if tier_name else "",
                DEFAULT_AUTHENTICATED_LIMIT
            )
            # Key based on user ID for authenticated requests
            user_id = getattr(user, "id", None) or user.get("id") if isinstance(user, dict) else None
            key = f"user:{user_id}" if user_id else f"ip:{self._get_client_ip(request)}"
        else:
            # Unauthenticated - use global IP-based limits
            limit = settings.DEFAULT_RATE_LIMIT_LIMIT
            period = settings.DEFAULT_RATE_LIMIT_PERIOD
            key = f"ip:{self._get_client_ip(request)}"

        return limit, period, key

    async def _get_user_tier_name(self, user) -> str | None:
        """Get tier name for user, with Redis caching.
        
        Args:
            user: User object or dict
            
        Returns:
            Tier name or None
        """
        # Try to get tier from user object/dict directly
        if isinstance(user, dict):
            tier = user.get("tier")
            if tier:
                return tier.get("name") if isinstance(tier, dict) else tier
            tier_id = user.get("tier_id")
        else:
            tier = getattr(user, "tier", None)
            if tier:
                return getattr(tier, "name", None)
            tier_id = getattr(user, "tier_id", None)

        if not tier_id:
            return None

        # Look up tier name from cache or DB
        from src.app.core.utils.cache import client as redis_client
        
        if redis_client:
            try:
                cache_key = f"tier:name:{tier_id}"
                cached = await redis_client.get(cache_key)
                if cached:
                    return cached.decode()
                
                # Cache miss - query DB
                from src.app.core.db.database import local_session
                from src.app.modules.tiers.models import Tier
                from sqlalchemy import select
                
                async with local_session() as session:
                    result = await session.execute(
                        select(Tier.name).where(Tier.id == tier_id)
                    )
                    tier_name = result.scalar_one_or_none()
                    
                    if tier_name:
                        await redis_client.setex(cache_key, 3600, tier_name)
                    
                    return tier_name
            except Exception as e:
                logger.warning("Tier lookup failed: %s", e)
        
        return None

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP, respecting X-Forwarded-For header."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
