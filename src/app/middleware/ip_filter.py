"""IP Filter Middleware for whitelist/blacklist enforcement.

This middleware checks incoming requests against IP whitelist and blacklist
stored in Redis. Whitelisted IPs bypass restrictions, blacklisted IPs are blocked.
"""

import logging
from typing import cast

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.security.logging import SecurityEventType, create_security_event, log_security_event
from src.app.modules.security.service import IPFilterService

logger = logging.getLogger(__name__)

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

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP with trusted proxy validation.

        SECURITY FIX: Only trust X-Forwarded-For/X-Real-IP headers when
        the request comes from a trusted proxy. Prevents IP spoofing attacks.
        """
        client_ip = ""

        # Check if request comes from trusted proxy first
        if request.client and request.client.host:
            direct_ip = request.client.host

            # Validate trusted proxy before trusting forwarded headers
            if self._is_trusted_proxy(direct_ip):
                # Trust X-Forwarded-For from trusted proxies
                forwarded_for = request.headers.get("X-Forwarded-For")
                if forwarded_for:
                    # Get the first IP in the chain (original client)
                    client_ip = forwarded_for.split(",")[0].strip()
                else:
                    # Fall back to X-Real-IP
                    real_ip = request.headers.get("X-Real-IP")
                    if real_ip:
                        client_ip = real_ip

            if not client_ip:
                client_ip = direct_ip
        else:
            client_ip = "unknown"

        return client_ip

    def _is_trusted_proxy(self, ip: str) -> bool:
        """Check if IP is a trusted proxy.

        Currently checks if IP is localhost/private network.
        In production, configure TRUSTED_PROXIES setting.
        """
        import ipaddress

        try:
            addr = ipaddress.ip_address(ip)
            # Trust loopback and private networks
            return addr.is_loopback or addr.is_private
        except ValueError:
            return False

    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from IP filtering."""
        return any(path.startswith(exempt) for exempt in EXEMPT_PATHS)

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request through IP filter."""
        path = request.url.path

        # Skip exempt paths
        if self._is_exempt_path(path):
            response = await call_next(request)
            return cast(Response, response)

        client_ip = self._get_client_ip(request)

        # BAD BOT FILTERING: Check User-Agent for known malicious scanners
        if settings.BAD_BOT_FILTER_ENABLED:
            user_agent = request.headers.get("user-agent", "").lower()
            for bad_bot in settings.BAD_BOT_USER_AGENTS:
                if bad_bot in user_agent:
                    event = create_security_event(
                        event_type=SecurityEventType.ATTACK_DETECTED,
                        client_ip=client_ip,
                        request_path=path,
                        reason=f"Blocked bad bot: {bad_bot}",
                        extra={"user_agent": user_agent},
                    )
                    log_security_event(event)

                    logger.warning(f"Blocked bad bot: {bad_bot} | ip={client_ip} | user_agent={user_agent}")
                    return Response(
                        content='{"detail":"Forbidden - Bad bot detected"}',
                        status_code=403,
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

                logger.debug(f"IP {client_ip} allowed via whitelist for {path}")
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

            logger.warning(f"IP {client_ip} blocked - not in whitelist for {path}")
            return Response(
                content='{"detail":"Forbidden - IP not whitelisted"}',
                status_code=403,
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

                logger.warning(f"IP {client_ip} blocked - blacklisted for {path}")
                return Response(
                    content='{"detail":"Forbidden - IP is blacklisted"}',
                    status_code=403,
                    media_type="application/json",
                )

        # IP is allowed through
        request.state.ip_whitelisted = False
        response = await call_next(request)
        return cast(Response, response)
