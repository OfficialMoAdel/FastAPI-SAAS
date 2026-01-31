"""Request body size limit middleware for DoS protection.

Limits the maximum size of request bodies to prevent:
- Memory exhaustion attacks
- Denial of service via large payloads
- Chunked transfer encoding bypass attacks
"""

from collections.abc import AsyncIterator, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from structlog import get_logger

logger = get_logger(__name__)

# Default max body size: 10MB
DEFAULT_MAX_BODY_SIZE = 10 * 1024 * 1024


class BodySizeLimitExceeded(Exception):
    """Raised when request body size exceeds the configured limit."""

    pass


class LimitedBodyStream:
    """Wrapper that enforces size limits during streaming reads.

    This handles chunked transfer encoding attacks where Content-Length
    is missing or spoofed.
    """

    def __init__(self, stream: AsyncIterator[bytes], max_size: int):
        self._stream = stream
        self._max_size = max_size
        self._bytes_read = 0

    async def __anext__(self) -> bytes:
        chunk = await self._stream.__anext__()
        self._bytes_read += len(chunk)

        if self._bytes_read > self._max_size:
            raise BodySizeLimitExceeded(f"Request body exceeded limit of {self._max_size} bytes")

        return chunk

    def __aiter__(self) -> "LimitedBodyStream":
        return self


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to limit request body size.

    Enforces body size limits via:
    1. Content-Length header check (fast path for compliant clients)
    2. Stream counting for chunked/streaming requests (security path)

    Attributes
    ----------
        max_body_size: Maximum allowed body size in bytes
    """

    def __init__(self, app: ASGIApp, max_body_size: int = DEFAULT_MAX_BODY_SIZE):
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Check and enforce body size limit."""
        # Fast path: Check Content-Length header if present
        content_length = request.headers.get("content-length")

        if content_length:
            try:
                length = int(content_length)
                if length > self.max_body_size:
                    logger.warning(
                        "request_body_too_large",
                        content_length=length,
                        max_size=self.max_body_size,
                        path=request.url.path,
                        client=request.client.host if request.client else "unknown",
                    )
                    return Response(
                        content="Request body too large",
                        status_code=413,
                        headers={"X-Max-Body-Size": str(self.max_body_size)},
                    )
            except ValueError:
                pass  # Invalid Content-Length header, continue to stream check

        # For chunked/streaming requests, we wrap the body stream
        # This handles cases where Content-Length is missing or spoofed
        original_receive = request._receive
        bytes_received = 0
        max_size = self.max_body_size

        async def limited_receive():
            nonlocal bytes_received
            message = await original_receive()

            if message["type"] == "http.request":
                body = message.get("body", b"")
                bytes_received += len(body)

                if bytes_received > max_size:
                    logger.warning(
                        "chunked_request_body_too_large",
                        bytes_received=bytes_received,
                        max_size=max_size,
                        path=request.url.path,
                        client=request.client.host if request.client else "unknown",
                    )
                    # Return empty body to stop further reading
                    return {"type": "http.request", "body": b"", "more_body": False}

            return message

        request._receive = limited_receive

        try:
            response: Response = await call_next(request)

            # Check if we exceeded during streaming
            if bytes_received > self.max_body_size:
                return Response(
                    content="Request body too large",
                    status_code=413,
                    headers={"X-Max-Body-Size": str(self.max_body_size)},
                )

            return response
        except BodySizeLimitExceeded:
            return Response(
                content="Request body too large",
                status_code=413,
                headers={"X-Max-Body-Size": str(self.max_body_size)},
            )
