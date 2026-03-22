"""IP Filter Middleware for whitelist/blacklist enforcement.

This middleware checks incoming requests against IP whitelist and blacklist
stored in Redis. Whitelisted IPs bypass restrictions, blacklisted IPs are blocked.
"""

from typing import cast

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security.logging import SecurityEventType, create_security_event, log_security_event
from src.app.core.utils.ip_utils import get_client_ip
from src.app.modules.security.service import IPFilterService

logger = get_logger(__name__)

EXEMPT_PATHS: frozenset[str] = frozenset(
    [
        # Full API paths (used at runtime via Starlette route matching)
        "/api/v1/health",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/metrics",
        # Short paths (used in unit tests and some proxy setups)
        "/health",
        "/health/live",
        "/health/ready",
    ]
)

import re

# Compile bad bot regex once at module level for O(1) matching
# instead of O(N) iteration per request.
_BAD_BOT_REGEX = None
if settings.BAD_BOT_USER_AGENTS:
    _escaped_bots = [re.escape(bot) for bot in settings.BAD_BOT_USER_AGENTS]
    _BAD_BOT_REGEX = re.compile("|".join(_escaped_bots), re.IGNORECASE)


class IPFilterMiddleware(BaseHTTPMiddleware):
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

    def __init__(self, app: "ASGIApp") -> None:
        super().__init__(app)
        self._filter_service = IPFilterService()

    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from IP filtering.

        BUG FIX: The original `startswith` check matched paths that merely *begin*
        with an exempt string (e.g. /health-probe matched /health, bypassing the
        IP filter). Now we require either an exact match or a true sub-path
        (path starts with exempt + "/").
        """
        if path in EXEMPT_PATHS:
            return True
        return any(path.startswith(exempt + "/") for exempt in EXEMPT_PATHS)

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request through IP filter."""
        path = request.url.path

        # Skip exempt paths
        if self._is_exempt_path(path):
            response = await call_next(request)
            return cast(Response, response)

        client_ip = get_client_ip(request)

        # BAD BOT FILTERING: Check User-Agent for known malicious scanners
        if settings.BAD_BOT_FILTER_ENABLED and _BAD_BOT_REGEX:
            user_agent = request.headers.get("user-agent", "")
            match = _BAD_BOT_REGEX.search(user_agent)
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
                    "Blocked bad bot: | ip= | user_agent=",
                    bad_bot=bad_bot,
                    client_ip=client_ip,
                    user_agent=user_agent,
                )
                return Response(
                    content='{"detail":"Forbidden - Bad bot detected"}',
                    status_code=status.HTTP_403_FORBIDDEN,
                    media_type="application/json",
                )

        # Check whitelist first (if enabled)
        if settings.IP_WHITELIST_ENABLED:
            is_whitelisted = await self._filter_service.is_whitelisted(client_ip)

            if is_whitelisted:
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
                response = await call_next(request)
                return cast(Response, response)

            # Whitelist is enabled but IP not in it - block
            event = create_security_event(
                event_type=SecurityEventType.IP_BLOCKED,
                client_ip=client_ip,
                request_path=path,
                reason="IP not in whitelist",
            )
            log_security_event(event)

            logger.warning("IP blocked - not in whitelist for", client_ip=client_ip, path=path)
            return Response(
                content='{"detail":"Forbidden - IP not whitelisted"}',
                status_code=status.HTTP_403_FORBIDDEN,
                media_type="application/json",
            )

        # Check blacklist (if enabled and whitelist not enforced)
        if settings.IP_BLACKLIST_ENABLED:
            is_blacklisted = await self._filter_service.is_blacklisted(client_ip)

            if is_blacklisted:
                event = create_security_event(
                    event_type=SecurityEventType.IP_BLOCKED,
                    client_ip=client_ip,
                    request_path=path,
                    reason="IP is blacklisted",
                )
                log_security_event(event)

                logger.warning("IP blocked - blacklisted for", client_ip=client_ip, path=path)
                return Response(
                    content='{"detail":"Forbidden - IP is blacklisted"}',
                    status_code=status.HTTP_403_FORBIDDEN,
                    media_type="application/json",
                )

        # IP is allowed through
        request.state.ip_whitelisted = False
        response = await call_next(request)
        return cast(Response, response)
