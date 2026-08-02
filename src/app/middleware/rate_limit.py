"""Rate limit middleware with tier-aware limits.

Provides different rate limits based on user's tier:
- Unauthenticated: IP-based with default limits
- Authenticated: User-based with tier-specific limits

Supports two algorithms:
- fixed_window: Classic fixed window counter (default)
- gcra: Generic Cell Rate Algorithm (smoother, no boundary bursts)

Implemented as a native ASGI middleware (not BaseHTTPMiddleware) so that
streaming responses (SSE, large downloads) are not buffered into memory.
"""

import time

from fastapi import Request, status
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.app.core.config import settings
from src.app.core.errors import ErrorCode, create_error_response
from src.app.core.logger import get_logger
from src.app.core.security.auto_ban import check_and_auto_ban, get_ban_info, is_ip_banned
from src.app.core.security.jwt_utils import extract_jwt_claims_to_state, get_cached_jwt_payload
from src.app.core.security.logging import SecurityEvent, SecurityEventType, log_security_event
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.rate_limit import rate_limiter

logger = get_logger(__name__)

# Tier-based rate limits configuration (from settings)
TIER_RATE_LIMITS = settings.TIER_RATE_LIMITS
DEFAULT_AUTHENTICATED_LIMIT = settings.DEFAULT_AUTHENTICATED_LIMIT
DEFAULT_ANONYMOUS_LIMIT = settings.DEFAULT_ANONYMOUS_LIMIT


class RateLimitMiddleware:
    """Middleware for tier-aware rate limiting.

    Features:
    - Unauthenticated requests: IP-based limits (proxy-aware via get_client_ip)
    - Authenticated requests: User + tier-based limits
    - Configurable limits per tier
    - X-RateLimit-* response headers
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process request with tier-aware rate limiting and auto-ban."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        client_ip = getattr(request.state, "client_ip", None) or get_client_ip(request)

        # SECURITY: Check if IP is banned before processing
        if settings.AUTO_BAN_ENABLED and await is_ip_banned(client_ip):
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

            response = create_error_response(
                code=ErrorCode.FORBIDDEN,
                message="Forbidden: IP address banned due to abuse",
                status_code=status.HTTP_403_FORBIDDEN,
                headers=headers,
            )
            await response(scope, receive, send)
            return

        # R5-5: Skip middleware rate limiting for refresh endpoint as it has a strict dependency-level rate limit
        if request.url.path in ("/api/v1/auth/refresh", "/api/v1/account/refresh"):
            await self.app(scope, receive, send)
            return

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
                # Skip rate limiting for whitelisted IPs; inject unlimited headers.
                async def whitelisted_send(message: Message) -> None:
                    if message["type"] == "http.response.start":
                        headers = MutableHeaders(scope=message)
                        headers["X-RateLimit-Limit"] = "unlimited"
                        headers["X-RateLimit-Remaining"] = "unlimited"
                        headers["X-RateLimit-Whitelisted"] = "true"
                    await send(message)

                await self.app(scope, receive, whitelisted_send)
                return

        # Determine rate limit key and limits
        limit, period, key = await self._get_rate_limit_config(request)

        # Check rate limit using configured algorithm
        is_limited, remaining, retry_after = await rate_limiter.check_rate_limit_algorithm(key, limit, period)

        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Period": str(period),
        }

        if is_limited:
            if retry_after == 0:
                retry_after = period - (int(time.time()) % period)
            if retry_after > 0:
                headers["Retry-After"] = str(int(retry_after))
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
                    # Return 403 Forbidden instead of 429 to indicate ban.
                    headers["X-Banned"] = "true"
                    headers["X-Ban-Reason"] = f"Auto-ban after {violation_count} violations"
                    # P3.24: include Retry-After so clients know when to retry.
                    try:
                        fresh_ban_info = await get_ban_info(client_ip)
                        if fresh_ban_info.expires_at:
                            ban_retry_after = int(fresh_ban_info.expires_at - time.time()) + 1
                            if ban_retry_after > 0:
                                headers["Retry-After"] = str(ban_retry_after)
                    except Exception:
                        pass

                    response = create_error_response(
                        code=ErrorCode.FORBIDDEN,
                        message=f"Forbidden: IP banned after {violation_count} rate limit violations",
                        status_code=status.HTTP_403_FORBIDDEN,
                        headers=headers,
                    )
                    await response(scope, receive, send)
                    return

            response = create_error_response(
                code=ErrorCode.RATE_LIMITED,
                message="Rate limit exceeded",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers=headers,
            )
            await response(scope, receive, send)
            return

        # Success: inject rate-limit headers into the response.
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                resp_headers = MutableHeaders(scope=message)
                for name, value in headers.items():
                    resp_headers[name] = value
            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _get_rate_limit_config(self, request: Request) -> tuple[int, int, str]:
        """Determine rate limit configuration based on request context.

        Security: Checks token blacklist to prevent revoked tokens from keeping premium limits.

        Returns
        -------
            Tuple of (limit, period, key)
        """
        # 1. Check if user_id/tier_name/tenant_id are already in request state (e.g. from tests)
        user_id = getattr(request.state, "user_id", None)
        tier_name = getattr(request.state, "tier_name", None)
        tenant_id = getattr(request.state, "tenant_id", None)
        tier_id_raw = getattr(request.state, "tier_id", None)

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

        # Always try to get tenant_id and tier_id from JWT payload if not already set
        if tenant_id is None and cached_payload:
            tenant_id = cached_payload.get("tenant_id")
        if tier_id_raw is None and cached_payload:
            tier_id_raw = cached_payload.get("tier_id")

        # 3. Security: Check if token is blacklisted (SECURITY-GAP-1 fix)
        # If token is blacklisted OR its status is unknown (fail-open during a
        # Redis outage), treat the user as anonymous to prevent premium rate-limit
        # abuse with a revoked/unknown token.
        if user_id and token:
            from src.app.core.security.token_blacklist_cache import is_token_blacklisted

            # Use JTI if available to avoid re-parsing token
            jti = cached_payload.get("jti") if cached_payload else None
            is_blacklisted = await is_token_blacklisted(token, jti=jti)

            # Store in state so `auth.py` (deps) can reuse the result (R5-3)
            request.state.is_token_blacklisted = is_blacklisted is True

            if is_blacklisted is not False:
                logger.info(
                    "Blacklisted/unknown token detected in rate limiter | user_id=%s | treating as anonymous",
                    user_id,
                )
                user_id = None
                tier_name = None

        # 4. Resolve limits
        from src.app.core.security.rate_limit_cache import get_tier_policy
        from src.app.core.utils.path_utils import sanitize_path

        path = sanitize_path(request.url.path)
        limit = None
        period = None

        if tier_name and user_id:
            if tier_id_raw is not None:
                try:
                    tier_id_val = int(tier_id_raw)
                    if tier_id_val:
                        tier_policies = await get_tier_policy(tier_id_val)
                        if tier_policies:
                            for policy in tier_policies:
                                if policy.get("path") == path:
                                    limit = policy.get("limit")
                                    period = policy.get("period")
                                    break
                except (ValueError, TypeError):
                    pass

        if limit is None or period is None:
            # Fallback to settings tier config
            resolved_tier = tier_name.lower() if tier_name else "anonymous"
            limit_config = settings.TIER_RATE_LIMITS.get(resolved_tier)
            if limit_config is None:
                limit_config = settings.DEFAULT_AUTHENTICATED_LIMIT if user_id else settings.DEFAULT_ANONYMOUS_LIMIT

            limit, period = limit_config

        if not user_id:
            # For anonymous users, use the proxy-aware client IP (not the raw peer,
            # which is the load balancer/reverse proxy behind trusted proxies).
            anonymous_ip = getattr(request.state, "client_ip", None) or get_client_ip(request)
            key = f"rate_limit:anonymous:ip:{anonymous_ip}:{path}"
        else:
            # For authenticated users, scope by tenant if available
            from src.app.core.utils.cache import build_tenant_key

            base_key = f"rate_limit:user:{user_id}:{path}"
            key = build_tenant_key(base_key, tenant_id)

        return limit, period, key
