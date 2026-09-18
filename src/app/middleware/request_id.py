"""Request ID middleware for distributed tracing.

Generates a unique request ID for each request and adds it to:
- Response headers (X-Request-ID)
- Request state (request.state.request_id)
- Logging context
"""

import re
import uuid

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog import get_logger

from src.app.core.middleware_state import set_request_id

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES = b"x-request-id"
_REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-]{1,128}$")


def resolve_request_id(incoming_id: str | None) -> str:
    """Generate-or-propagate a request ID.

    Returns the incoming ID when it passes the log-injection guard (M-02);
    otherwise mints a UUID4. Pure — safe to call from any guard layer.
    """
    if incoming_id and _REQUEST_ID_PATTERN.match(incoming_id):
        return incoming_id
    return str(uuid.uuid4())


def request_id_from_scope(scope: Scope) -> str:
    """Generate-or-propagate a request ID from raw ASGI scope headers.

    This middleware runs innermost (see ``core/guard/ordering.py``), so
    outer guards (IPFilter/RateLimit) reject before it executes. Reject
    paths call this helper to echo the SAME generate-or-propagate ID on
    their responses instead of omitting ``X-Request-ID``.
    """
    incoming_id: str | None = None
    for name, value in scope.get("headers", []):
        if name.lower() == _REQUEST_ID_HEADER_BYTES:
            incoming_id = value.decode("latin-1")
            break
    return resolve_request_id(incoming_id)


def wrap_send_with_request_id(send: Send, request_id: str) -> Send:
    """Echo ``request_id`` on the response-start message.

    Shared by this middleware and outer-guard reject paths so every
    response — including 403/429 short-circuits — carries X-Request-ID.
    """

    async def sender(message: Message) -> None:
        if message["type"] == "http.response.start":
            MutableHeaders(scope=message).setdefault(REQUEST_ID_HEADER, request_id)
        await send(message)

    return sender


class RequestIDMiddleware:
    """Middleware to generate and track request IDs for tracing.

    Features:
    - Generates UUID4 for each request
    - Respects incoming X-Request-ID header (for distributed tracing)
    - Adds request ID to response headers
    - Makes request ID available via request.state.request_id
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request_id = request_id_from_scope(scope)

        request = Request(scope, receive, send)

        # Store in request state for downstream access
        set_request_id(request.state, request_id)

        # Log request with ID
        logger.debug(
            "Request started",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        await self.app(scope, receive, wrap_send_with_request_id(send, request_id))
