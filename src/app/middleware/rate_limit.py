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
from typing import Any, NamedTuple
from urllib.parse import urlparse

from fastapi import Request, status
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.app.core.config import settings
from src.app.core.errors import ErrorCode, middleware_error_response
from src.app.core.logger import get_logger
from src.app.core.middleware_exempt import is_path_exempt
from src.app.core.security.auto_ban import (
    BanInfo,
    check_and_auto_ban,
    check_ban_and_rate_limit,
    check_ban_blacklist_and_rate_limit,
    get_ban_info,
)
from src.app.core.security.jwt_utils import extract_jwt_claims_to_state, get_cached_jwt_payload
from src.app.core.security.logging import SecurityEvent, SecurityEventType, log_security_event
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.path_utils import sanitize_path
from src.app.core.utils.rate_limit import rate_limiter

logger = get_logger(__name__)

# Tier-based rate limits configuration (from settings)
TIER_RATE_LIMITS = settings.rate_limit.TIER_RATE_LIMITS
DEFAULT_AUTHENTICATED_LIMIT = settings.rate_limit.DEFAULT_AUTHENTICATED_LIMIT
DEFAULT_ANONYMOUS_LIMIT = settings.rate_limit.DEFAULT_ANONYMOUS_LIMIT


async def _reject_banned_ip(
    client_ip: str,
    request: Request,
    ban_info: BanInfo,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    """Emit the 403 banned-IP response (shared by the refresh-path gate and
    the combined ban+GCRA block below).

    B3: extracted so the refresh-path early-skip can enforce the ban boundary
    without duplicating the response construction.
    """
    logger.warning(
        "Request from banned IP blocked | ip=%s | reason=%s",
        client_ip,
        ban_info.reason,
    )
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
    response = middleware_error_response(
        code=ErrorCode.FORBIDDEN,
        message="Forbidden: IP address banned due to abuse",
        status_code=status.HTTP_403_FORBIDDEN,
        headers=headers,
    )
    await response(scope, receive, send)


class _RateLimitContext(NamedTuple):
    """Identity context for rate-limit resolution (no Redis touched).

    Produced by _get_rate_limit_config (JWT/state only); consumed either by
    the P0-2 merged helper (authed hot path) or _resolve_legacy_config
    (anonymous / auto-ban-disabled paths).
    """

    user_id: Any
    tier_name: Any
    tenant_id: Any
    tier_id_raw: Any
    token: str | None
    jti: str | None
    path: str


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

    async def _maybe_skip_rate_limit(
        self,
        request: Request,
        client_ip: str,
        norm_path: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> bool:
        """Zero-Redis-cost skips: OPTIONS preflight, exempt paths, refresh.

        Returns True when the request was fully handled (passed through or
        ban-rejected) and the caller must return without touching Redis.
        """
        # P1-3: CORS preflight (OPTIONS) never carries credentials and
        # browsers emit one per cross-origin write — rate-limiting it burns
        # anonymous budget and risks 429ing legit traffic. Ban boundary
        # preserved (B3 precedent) via the cheap cached lookup.
        if request.method == "OPTIONS":
            return await self._passthrough_unless_banned(request, client_ip, scope, receive, send)

        # P1-3: health/live/ready + docs + webhook receivers are exempt from
        # rate limiting via the shared helper (single source of truth in
        # core/middleware_exempt.py). Full passthrough with NO ban check:
        # ip_filter deliberately exempts these same paths from blacklist
        # enforcement so orchestrator probes and Stripe deliveries never 403.
        # Side effect: Stripe webhook retries can no longer be 429'd either.
        if is_path_exempt(norm_path):
            await self.app(scope, receive, send)
            return True

        # R5-5: refresh endpoint has its own strict dependency-level rate
        # limit. B3: the skip must not bypass the ban boundary (see helper).
        if norm_path in ("/api/v1/auth/refresh", "/api/v1/account/refresh"):
            return await self._passthrough_unless_banned(request, client_ip, scope, receive, send)

        return False

    async def _passthrough_unless_banned(
        self,
        request: Request,
        client_ip: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> bool:
        """Pass the request through, enforcing the ban boundary first.

        The ban check rides get_ban_info's 5s negative cache, so the common
        clean-IP case costs zero Redis ops. Returns True always (handled).
        """
        if settings.security.AUTO_BAN_ENABLED:
            ban_info = await get_ban_info(client_ip)
            if ban_info.is_banned:
                await _reject_banned_ip(client_ip, request, ban_info, scope, receive, send)
                return True
        await self.app(scope, receive, send)
        return True

    async def _check_authed_merged(
        self,
        request: Request,
        ctx: _RateLimitContext,
        client_ip: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> tuple[int, int, str, bool, int, int] | None:
        """P0-2: blacklist + ban + GCRA for authenticated requests in ONE Lua RTT.

        Returns None when the request was ban-rejected (caller must return);
        otherwise (limit, period, key, is_limited, remaining, retry_after).
        """
        # Invariant: __call__ only routes here when AUTO_BAN is on AND both
        # user_id and token are present.
        assert ctx.token is not None
        (
            is_blacklisted,
            limit,
            period,
            key,
            ban_info,
            (is_limited, remaining, retry_after),
        ) = await check_ban_blacklist_and_rate_limit(
            client_ip=client_ip,
            token=ctx.token,
            jti=ctx.jti,
            user_id=ctx.user_id,
            tier_name=ctx.tier_name,
            tenant_id=ctx.tenant_id,
            tier_id_raw=ctx.tier_id_raw,
            anonymous_ip=client_ip,
            path=ctx.path,
        )
        # Store tri-state in state so `auth.py` (deps) can reuse the result (R5-3):
        # True = blacklisted, False = verified clean, None = unknown (Redis down).
        # A1: deps must run its DB fallback on None; never collapse it to False.
        request.state.is_token_blacklisted = is_blacklisted

        if is_blacklisted is not False:
            logger.info(
                "Blacklisted/unknown token detected in rate limiter | user_id=%s | treating as anonymous",
                ctx.user_id,
            )
        if ban_info.is_banned:
            await _reject_banned_ip(client_ip, request, ban_info, scope, receive, send)
            return None
        return limit, period, key, is_limited, remaining, retry_after

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process request with tier-aware rate limiting and auto-ban."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        client_ip = getattr(request.state, "client_ip", None) or get_client_ip(request)

        # SECURITY + H-3: ban check and GCRA rate limit are merged into ONE
        # Redis Lua round-trip below (was 2 sequential RTTs). All skips must
        # happen BEFORE the combined call so skipped requests cost zero ops.

        # P1-3: normalize path once (strip slash/query/case) so
        # /api/v1/auth/refresh/?foo=1 is not missed (was H5 inline import).
        norm_path = urlparse(str(request.url)).path.rstrip("/").lower() or "/"
        if await self._maybe_skip_rate_limit(request, client_ip, norm_path, scope, receive, send):
            return

        # Check if IP is whitelisted and should bypass rate limiting
        if settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT:
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
                        # R8 FIX: strict int() clients choke on "unlimited";
                        # emit a large numeric sentinel instead. The dedicated
                        # X-RateLimit-Whitelisted header still marks the bypass.
                        headers["X-RateLimit-Limit"] = "999999"
                        headers["X-RateLimit-Remaining"] = "999999"
                        headers["X-RateLimit-Whitelisted"] = "true"
                    await send(message)

                await self.app(scope, receive, whitelisted_send)
                return

        # Determine identity context (JWT/state only — no Redis yet)
        ctx = await self._get_rate_limit_config(request)

        # P0-2: authenticated hot path runs blacklist + ban + GCRA in ONE
        # Lua RTT (was: is_token_blacklisted probe + check_ban_and_rate_limit
        # = 2 sequential RTTs). Anonymous / auto-ban-disabled paths keep the
        # original sequential flow below.
        if settings.security.AUTO_BAN_ENABLED and ctx.user_id and ctx.token:
            merged = await self._check_authed_merged(request, ctx, client_ip, scope, receive, send)
            if merged is None:
                return
            limit, period, key, is_limited, remaining, retry_after = merged
        else:
            limit, period, key = await self._resolve_legacy_config(request, ctx)

            # H-3: ban check + rate limit in ONE Lua RTT (was: get_ban_info +
            # check_rate_limit_algorithm = 2 sequential RTTs). When auto-ban is
            # disabled or Redis is unavailable the combined helper degrades to
            # the original sequential semantics.
            if settings.security.AUTO_BAN_ENABLED:
                ban_info, (is_limited, remaining, retry_after) = await check_ban_and_rate_limit(
                    client_ip, key, limit, period
                )
                if ban_info.is_banned:
                    await _reject_banned_ip(client_ip, request, ban_info, scope, receive, send)
                    return
            else:
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
            if settings.security.AUTO_BAN_ENABLED and client_ip:
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
                        settings.security.AUTO_BAN_THRESHOLD,
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

                    response = middleware_error_response(
                        code=ErrorCode.FORBIDDEN,
                        message=f"Forbidden: IP banned after {violation_count} rate limit violations",
                        status_code=status.HTTP_403_FORBIDDEN,
                        headers=headers,
                    )
                    await response(scope, receive, send)
                    return

            response = middleware_error_response(
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

    async def _get_rate_limit_config(self, request: Request) -> _RateLimitContext:
        """Extract identity context for rate-limit resolution (no Redis).

        Steps 1-2 of the original resolver: request-state/JWT identity plus
        the sanitized path. The blacklist probe + limit/key resolution happen
        downstream — either in the P0-2 merged helper (authed hot path) or
        _resolve_legacy_config (anonymous / auto-ban-disabled paths).
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
        # H5 FIX: case-insensitive Bearer per RFC 6750
        token = auth_header[7:] if auth_header.lower().startswith("bearer ") else None

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

        # Use JTI if available to avoid re-parsing token
        jti = cached_payload.get("jti") if cached_payload else None

        path = sanitize_path(request.url.path)
        return _RateLimitContext(
            user_id=user_id,
            tier_name=tier_name,
            tenant_id=tenant_id,
            tier_id_raw=tier_id_raw,
            token=token,
            jti=jti,
            path=path,
        )

    async def _resolve_legacy_config(
        self, request: Request, ctx: _RateLimitContext
    ) -> tuple[int, int, str]:
        """Original steps 3-4: blacklist probe + downgrade + resolve + key.

        Used by anonymous requests and when auto-ban is disabled. Behavior
        matches the pre-P0-2 flow, including the tri-state state store and
        the downgrade log line.
        """
        from src.app.core.security.rate_policy import build_rate_limit_key, resolve_rate_limits

        user_id = ctx.user_id
        tier_name = ctx.tier_name

        # 3. Security: Check if token is blacklisted (SECURITY-GAP-1 fix)
        # If token is blacklisted OR its status is unknown (fail-open during a
        # Redis outage), treat the user as anonymous to prevent premium rate-limit
        # abuse with a revoked/unknown token.
        if user_id and ctx.token:
            from src.app.core.security.token_blacklist_cache import is_token_blacklisted

            is_blacklisted = await is_token_blacklisted(ctx.token, jti=ctx.jti)

            # Store tri-state in state so `auth.py` (deps) can reuse the result (R5-3):
            # True = blacklisted, False = verified clean, None = unknown (Redis down).
            # A1: deps must run its DB fallback on None; never collapse it to False.
            request.state.is_token_blacklisted = is_blacklisted

            if is_blacklisted is not False:
                logger.info(
                    "Blacklisted/unknown token detected in rate limiter | user_id=%s | treating as anonymous",
                    user_id,
                )
                user_id = None
                tier_name = None

        # 4. Resolve limits
        limit, period = await resolve_rate_limits(
            ctx.path, tier_id=ctx.tier_id_raw, tier_name=tier_name, user_id=user_id
        )

        raw_ip = getattr(request.state, "client_ip", None) if not user_id else None
        anonymous_ip = (
            raw_ip if isinstance(raw_ip, str) and raw_ip else (get_client_ip(request) if not user_id else None)
        )
        key = build_rate_limit_key(ctx.path, user_id=user_id, tenant_id=ctx.tenant_id, anonymous_ip=anonymous_ip)

        return limit, period, key
