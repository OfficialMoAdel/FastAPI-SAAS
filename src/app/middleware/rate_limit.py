"""Rate limit middleware with tier-aware limits.

Provides different rate limits based on user's tier:
- Unauthenticated: IP-based with default limits
- Authenticated: User-based with tier-specific limits

Supports two algorithms:
- fixed_window: Classic fixed window counter (default)
- gcra: Generic Cell Rate Algorithm (smoother, no boundary bursts)
"""

import ipaddress
from collections.abc import Callable
from functools import lru_cache

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security.jwt_utils import extract_jwt_claims_to_state, get_cached_jwt_payload
from src.app.core.security.token_blacklist_cache import is_token_blacklisted
from src.app.core.utils.gcra_rate_limit import get_gcra_rate_limiter
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

# Trusted proxy configuration (module-level for LRU cache hashability)
TRUSTED_PROXY_IPS: set[str] = {"127.0.0.1", "::1"}
TRUSTED_PROXY_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


# OPT P0-3: LRU cache for IP address parsing and network check
# ipaddress.ip_address() is a pure function - same input always gives same output
# This eliminates ~5.8us parsing overhead per request for cached IPs
@lru_cache(maxsize=2048)
def _is_ip_in_trusted_networks(ip_str: str) -> bool:
    """Check if IP is within any trusted network (cached).

    Pure function with LRU caching for performance.
    Expected hit rate: >95% in production (most requests from same IPs).

    Args:
        ip_str: IP address string to check

    Returns:
        True if IP is in a trusted network, False otherwise
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in network for network in TRUSTED_PROXY_NETWORKS)
    except ValueError:
        return False


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

        # Check rate limit using configured algorithm
        use_gcra = getattr(settings, "RATE_LIMIT_ALGORITHM", "fixed_window").lower() == "gcra"

        if use_gcra:
            gcra = get_gcra_rate_limiter()
            if gcra:
                result = await gcra.check_rate_limit(key, limit, period)
                is_limited = result.is_limited
                remaining = result.remaining
                retry_after = result.retry_after
            else:
                # GCRA not initialized, fall back to fixed window
                is_limited, current_count = await rate_limiter.check_rate_limit(key, limit, period)
                remaining = max(0, limit - current_count)
                retry_after = 0.0
        else:
            # Fixed window algorithm
            is_limited, current_count = await rate_limiter.check_rate_limit(key, limit, period)
            remaining = max(0, limit - current_count)
            retry_after = 0.0

        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Period": str(period),
        }

        if is_limited:
            if retry_after > 0:
                headers["Retry-After"] = str(int(retry_after) + 1)
            logger.warning("Rate limit exceeded | key=%s | limit=%s | period=%s", key, limit, period)
            return Response("Too Many Requests", status_code=429, headers=headers)

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

        if user:
            user_id = getattr(user, "id", None) or user.get("id") if isinstance(user, dict) else None
            tier_name = await self._get_user_tier_name(user)

        # 2. If no user in state, use jwt_utils to extract/cache JWT claims
        # This eliminates double JWT decode when auth dependency runs later
        token = None
        if not user_id:
            # Extract token from Authorization header for blacklist check
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]  # Remove "Bearer " prefix

            # Use centralized JWT utilities for extraction and caching
            cached_payload = get_cached_jwt_payload(request)
            if cached_payload is None:
                # Not cached yet - extract and cache for later use
                cached_payload = await extract_jwt_claims_to_state(request)

            if cached_payload:
                user_id = cached_payload.get("sub")
                tier_name = cached_payload.get("tier_name")

        # 3. Security: Check if token is blacklisted (SECURITY-GAP-1 fix)
        # If token is blacklisted, treat user as anonymous to prevent premium rate limit abuse
        if user_id and token:
            is_blacklisted = await is_token_blacklisted(token)
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
            limit, period = TIER_RATE_LIMITS.get(
                tier_name.lower() if tier_name else "basic", DEFAULT_AUTHENTICATED_LIMIT
            )
            key = f"user:{user_id}"
        else:
            # Unauthenticated - use global IP-based limits
            limit = settings.DEFAULT_RATE_LIMIT_LIMIT
            period = settings.DEFAULT_RATE_LIMIT_PERIOD
            key = f"ip:{self._get_client_ip(request)}"

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

    def _is_trusted_proxy(self, ip_str: str) -> bool:
        """Check if IP is a trusted proxy (exact match or within trusted network).

        Uses module-level cached function for network checks (OPT P0-3).
        """
        if ip_str in TRUSTED_PROXY_IPS:
            return True
        return _is_ip_in_trusted_networks(ip_str)

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP with trusted proxy validation.

        Only trusts X-Forwarded-For header if request comes from a trusted proxy.
        This prevents attackers from spoofing their IP to bypass rate limits.
        """
        client_ip = request.client.host if request.client else "unknown"

        # Only trust X-Forwarded-For if request is from a trusted proxy
        if self._is_trusted_proxy(client_ip):
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                # Return the first (original client) IP from the chain
                return forwarded.split(",")[0].strip()

        return client_ip
