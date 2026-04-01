"""Request ID middleware for distributed tracing.

Generates a unique request ID for each request and adds it to:
- Response headers (X-Request-ID)
- Request state (request.state.request_id)
- Logging context
"""

import re
import uuid
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from structlog import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to generate and track request IDs for tracing.

    Features:
    - Generates UUID4 for each request
    - Respects incoming X-Request-ID header (for distributed tracing)
    - Adds request ID to response headers
    - Makes request ID available via request.state.request_id
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and add request ID."""
        # Use existing header or generate new ID
        request_id = request.headers.get(REQUEST_ID_HEADER)

        # Validate request_id to prevent log injection (M-02)
        # ISSUE 20 FIX: Allow {} for valid UUID formats in some environments
        if request_id and not re.match(r"^[a-zA-Z0-9\-\{\}]{1,128}$", request_id):
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

        response: Response = await call_next(request)

        # Add to response headers
        response.headers[REQUEST_ID_HEADER] = request_id

        return response
