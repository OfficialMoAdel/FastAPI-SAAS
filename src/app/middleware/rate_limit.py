"""Native ASGI adapter for request admission, preserving streaming responses."""

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.app.core.errors import middleware_reject
from src.app.core.logger import get_logger
from src.app.core.security.auto_ban import BanInfo
from src.app.core.security.logging import SecurityEvent, SecurityEventType, log_security_event
from src.app.core.security.request_admission import RequestAdmission, RequestAdmissionService
from src.app.middleware.request_id import request_id_from_scope, wrap_send_with_request_id
from src.app.shared.exceptions import ServiceUnavailableError

logger = get_logger(__name__)


async def _reject_banned_ip(
    client_ip: str,
    request: Request,
    ban_info: BanInfo,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
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
    response = middleware_reject(
        "banned_ip",
        ban_reason=ban_info.reason,
        ban_expires_at=ban_info.expires_at,
    )
    await response(scope, receive, send)


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, admission: RequestAdmission | None = None) -> None:
        self.app = app
        self.admission = admission if admission is not None else RequestAdmissionService()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        # P2: this middleware runs OUTSIDE RequestIDMiddleware (which is
        # innermost), so its 403/429/503 short-circuits would otherwise lack
        # X-Request-ID. Echo the same generate-or-propagate ID on every
        # response here; setdefault keeps the inner middleware's header
        # authoritative on pass-through responses.
        send = wrap_send_with_request_id(send, request_id_from_scope(scope))
        try:
            outcome = await self.admission.evaluate(request)
        except ServiceUnavailableError:
            # An unfinished evaluation cannot establish ban/auth safety.
            # Counter fail-open policy belongs to the admission helpers.
            logger.exception("Request admission backend unavailable")
            response = middleware_reject("admission_unavailable")
            await response(scope, receive, send)
            return
        except Exception:
            # Translate only evaluation failures; downstream errors and
            # BaseException cancellation must propagate without a second response.
            logger.exception("Request admission evaluation failed")
            response = middleware_reject("internal_error")
            await response(scope, receive, send)
            return
        if outcome.action == "passthrough":
            await self.app(scope, receive, send)
            return
        if outcome.action == "reject_banned":
            ban_info = outcome.ban_info
            if ban_info is None:
                logger.error(
                    "RateLimitMiddleware: reject_banned without ban_info; failing closed",
                )
                response = middleware_reject("internal_error")
                await response(scope, receive, send)
                return
            await _reject_banned_ip(outcome.client_ip, request, ban_info, scope, receive, send)
            return

        verdict = outcome.verdict
        if verdict is None:
            logger.error(
                "RateLimitMiddleware: action %s without verdict; failing closed",
                outcome.action,
            )
            response = middleware_reject("internal_error")
            await response(scope, receive, send)
            return
        headers = verdict.headers
        if outcome.action == "reject_auto_ban":
            response = middleware_reject(
                "auto_banned",
                headers=verdict.headers,
                violation_count=outcome.violation_count,
            )
            await response(scope, receive, send)
            return
        if outcome.action == "limited":
            response = middleware_reject(
                "rate_limited",
                headers=verdict.headers,
            )
            await response(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                resp_headers = MutableHeaders(scope=message)
                for name, value in headers.items():
                    resp_headers[name] = value
            await send(message)

        await self.app(scope, receive, send_wrapper)
