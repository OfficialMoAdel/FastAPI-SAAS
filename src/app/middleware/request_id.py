"""Request ID middleware for distributed tracing.

Generates a unique request ID for each request and adds it to:
- Response headers (X-Request-ID)
- Request state (request.state.request_id)
- Logging context
"""

import re
import uuid

from fastapi import Request
from structlog import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


from starlette.datastructures import MutableHeaders


class RequestIDMiddleware:
    """Middleware to generate and track request IDs for tracing.

    Features:
    - Generates UUID4 for each request
    - Respects incoming X-Request-ID header (for distributed tracing)
    - Adds request ID to response headers
    - Makes request ID available via request.state.request_id
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope, receive, send)
        request_id = request.headers.get(REQUEST_ID_HEADER)

        # Validate request_id to prevent log injection (M-02)
        # Validate request_id to prevent log injection (M-02)
        if request_id and not re.match(r"^[a-zA-Z0-9\-]{1,128}$", request_id):
            request_id = None

        if not request_id:
            request_id = str(uuid.uuid4())

        # Store in request state for downstream access
        request.state.request_id = request_id

        # Log request with ID
        logger.debug(
            "Request started",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault(REQUEST_ID_HEADER, request_id)
            await send(message)

        await self.app(scope, receive, send_wrapper)
