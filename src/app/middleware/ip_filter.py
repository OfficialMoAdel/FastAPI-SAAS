"""IP Filter Middleware for whitelist/blacklist enforcement.

This middleware checks incoming requests against IP whitelist and blacklist
stored in Redis. Whitelisted IPs bypass restrictions, blacklisted IPs are blocked.
"""

from fastapi import Request, status
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.guard.ip_rules import IpRules
from src.app.core.logger import get_logger
from src.app.core.security.logging import SecurityEventType, create_security_event, log_security_event
from src.app.core.utils.ip_utils import get_client_ip

logger = get_logger(__name__)

# P2-7: use the shared exempt-path contract. ip_filter inherits the common
# set (health probes, Stripe webhook receivers) but KEEPS blacklist and
# bad-bot enforcement on /docs, /redoc and /openapi.json (scanner defense —
# see the e2e contract in tests/e2e/core/security/test_ip_filtering.py),
# hence the derived IP_FILTER_EXEMPT_PATHS base instead of COMMON alone.
from src.app.core.middleware_exempt import IP_FILTER_EXEMPT_PATHS, is_path_exempt

EXEMPT_PATHS: frozenset[str] = IP_FILTER_EXEMPT_PATHS  # backward-compat re-export

import re

# Lazy bad bot regex - compiled on first use for testability
_BAD_BOT_REGEX: re.Pattern[str] | None = None
_BAD_BOT_KEY: tuple[str, ...] | None = None


def _get_bad_bot_regex() -> re.Pattern[str] | None:
    """Get compiled bad bot regex, rebuilding only if settings changed.

    P2 micro: memoize on tuple(settings list) — tuple compare is C-speed,
    so the hot path skips the per-request re.escape + join + .pattern
    string compare entirely; recompile happens only on config change.
    """
    global _BAD_BOT_REGEX, _BAD_BOT_KEY
    bots = settings.security.BAD_BOT_USER_AGENTS
    if not bots:
        return None
    key = tuple(bots)
    if _BAD_BOT_REGEX is None or _BAD_BOT_KEY != key:
        _BAD_BOT_REGEX = re.compile("|".join(re.escape(bot) for bot in key), re.IGNORECASE)
        _BAD_BOT_KEY = key
    return _BAD_BOT_REGEX


def _reset_bad_bot_regex() -> None:
    """Reset cached regex. Use in test teardowns for isolation."""
    global _BAD_BOT_REGEX, _BAD_BOT_KEY
    _BAD_BOT_REGEX = None
    _BAD_BOT_KEY = None


class IPFilterMiddleware:
    """Middleware to enforce IP whitelist/blacklist policies.

    Execution order:
    1. Check if path is exempt (health checks, etc.)
    2. Extract client IP with trusted proxy support
    3. If whitelist enabled: check if IP is whitelisted
       - If yes: allow request (optionally skip rate limiting)
       - If no: block with 403
    4. If blacklist enabled: check if IP is blacklisted
       - If yes: block with 403 and log
       - If no: allow request
    """

    def __init__(self, app: "ASGIApp", ip_rules: IpRules | None = None) -> None:
        self.app = app
        from src.app.core.guard.ip_rules import RedisIpRules

        # `_filter_service` is the seam instance (RedisIpRules satisfies IpRules).
        # Kept under the historical attribute name so tests patching
        # `middleware._filter_service.check_ip_status` keep working.
        self._filter_service: IpRules = ip_rules if ip_rules is not None else RedisIpRules()

    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from IP filtering.

        B11: consult the shared exempt-path contract instead of a private
        health-only set. The base is IP_FILTER_EXEMPT_PATHS — the common
        set (health probes, Stripe webhook receivers) minus the docs trio,
        so a blacklisted Stripe source IP never gets its webhooks 403'd,
        while /docs and /openapi.json stay IP-filtered (blacklist +
        bad-bot scanner defense).
        """
        return is_path_exempt(path, base=IP_FILTER_EXEMPT_PATHS)
    async def __call__(self, scope, receive, send):
        """Process request through IP filter."""
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope, receive, send)
        path = request.url.path

        # Skip exempt paths
        if self._is_exempt_path(path):
            # R7 FIX: downstream middleware expects these state attributes on
            # every request; the bare early-return previously set neither,
            # causing fallback warnings and repeated client-IP parsing.
            request.state.client_ip = get_client_ip(request)
            request.state.ip_whitelisted = True
            return await self.app(scope, receive, send)

        client_ip = get_client_ip(request)
        # P3 FIX: Store computed IP for downstream middleware reuse
        request.state.client_ip = client_ip

        # BAD BOT FILTERING: Check User-Agent for known malicious scanners
        if settings.security.BAD_BOT_FILTER_ENABLED:
            bad_bot_regex = _get_bad_bot_regex()
            if bad_bot_regex:
                user_agent = request.headers.get("user-agent", "")
                match = bad_bot_regex.search(user_agent)
                if match:
                    bad_bot = match.group(0)
                    event = create_security_event(
                        event_type=SecurityEventType.ATTACK_DETECTED,
                        client_ip=client_ip,
                        request_path=path,
                        reason=f"Blocked bad bot: {bad_bot}",
                        extra={"user_agent": user_agent},
                    )
                    log_security_event(event)

                    logger.warning(
                        "bad_bot_blocked",
                        bad_bot=bad_bot,
                        client_ip=client_ip,
                        user_agent=user_agent,
                    )
                    from src.app.core.errors import ErrorCode, middleware_error_response

                    response = middleware_error_response(
                        code=ErrorCode.FORBIDDEN,
                        message="Forbidden - Bad bot detected",
                        status_code=status.HTTP_403_FORBIDDEN,
                    )
                    return await response(scope, receive, send)

        # Check blacklist and whitelist in a single Redis pipeline
        if settings.security.IP_BLACKLIST_ENABLED or settings.security.IP_WHITELIST_ENABLED:
            is_blacklisted, is_whitelisted = await self._filter_service.check_ip_status(client_ip)

            if settings.security.IP_BLACKLIST_ENABLED and is_blacklisted:
                event = create_security_event(
                    event_type=SecurityEventType.IP_BLOCKED,
                    client_ip=client_ip,
                    request_path=path,
                    reason="IP is blacklisted",
                )
                log_security_event(event)

                logger.warning("IP blocked - blacklisted for", client_ip=client_ip, path=path)
                from src.app.core.errors import ErrorCode, middleware_error_response

                response = middleware_error_response(
                    code=ErrorCode.FORBIDDEN,
                    message="Forbidden - IP is blacklisted",
                    status_code=status.HTTP_403_FORBIDDEN,
                )
                return await response(scope, receive, send)

            if settings.security.IP_WHITELIST_ENABLED and is_whitelisted:
                # Log whitelisted access for audit trail
                event = create_security_event(
                    event_type=SecurityEventType.IP_WHITELISTED,
                    client_ip=client_ip,
                    request_path=path,
                    reason="Access granted via IP whitelist",
                )
                log_security_event(event)

                # Mark request as whitelisted for other middleware (e.g., rate limiting)
                request.state.ip_whitelisted = True

                logger.debug("IP allowed via whitelist for", client_ip=client_ip, path=path)
                return await self.app(scope, receive, send)

        # IP is allowed through
        request.state.ip_whitelisted = False
        return await self.app(scope, receive, send)
