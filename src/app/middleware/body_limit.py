"""Request body size limit middleware for DoS protection.

Limits the maximum size of request bodies to prevent:
- Memory exhaustion attacks
- Denial of service via large payloads
- Chunked transfer encoding bypass attacks
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog import get_logger

from src.app.core.errors import middleware_reject

logger = get_logger(__name__)

# Default max body size: 10MB
DEFAULT_MAX_BODY_SIZE = 10 * 1024 * 1024


class BodySizeLimitExceeded(Exception):
    """Raised when request body size exceeds the configured limit."""

    pass


class RequestBodyLimitMiddleware:
    """Middleware to limit request body size.

    Enforces body size limits via:
    1. Content-Length header check (fast path for compliant clients)
    2. Stream counting for chunked/streaming requests (security path)

    Attributes
    ----------
        max_body_size: Maximum allowed body size in bytes
    """

    def __init__(self, app: ASGIApp, max_body_size: int = DEFAULT_MAX_BODY_SIZE):
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check and enforce body size limit."""
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # SECURITY FIX: Prevent HTTP Request Smuggling via multiple Content-Length headers
        # P2 FIX (code-review): reject ALL multiples — proxies/CDNs differ on
        # first-vs-last wins, so even identical duplicates leave an
        # interpretation gap.
        content_lengths = [value for name, value in scope.get("headers", []) if name == b"content-length"]

        if len(content_lengths) > 1:
            logger.warning(
                "multiple_content_length_headers",
                path=scope.get("path", ""),
                count=len(content_lengths),
                client=scope.get("client", ["unknown"])[0] if scope.get("client") else "unknown",
            )
            await self._send_400(scope, receive, send, b"Bad Request - Multiple Content-Length headers")
            return

        content_length = content_lengths[0] if content_lengths else b""

        if content_length:
            try:
                length = int(content_length)
                if length > self.max_body_size:
                    logger.warning(
                        "request_body_too_large",
                        content_length=length,
                        max_size=self.max_body_size,
                        path=scope.get("path", ""),
                        client=scope.get("client", ["unknown"])[0] if scope.get("client") else "unknown",
                    )
                    await self._send_413(scope, receive, send)
                    return
            except ValueError:
                pass  # Invalid Content-Length header, continue to stream check

        bytes_received = 0
        max_size = self.max_body_size
        response_started = False

        async def _send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def limited_receive() -> Message:
            nonlocal bytes_received
            message = await receive()

            if message["type"] == "http.request":
                body = message.get("body", b"")
                bytes_received += len(body)

                if bytes_received > max_size:
                    logger.warning(
                        "chunked_request_body_too_large",
                        bytes_received=bytes_received,
                        max_size=max_size,
                        path=scope.get("path", ""),
                        client=scope.get("client", ["unknown"])[0] if scope.get("client") else "unknown",
                    )
                    raise BodySizeLimitExceeded(f"Request body exceeded limit of {max_size} bytes")

            return message

        try:
            await self.app(scope, limited_receive, _send_wrapper)
        except BodySizeLimitExceeded:
            if not response_started:
                await self._send_413(scope, receive, send)
            else:
                # P2 FIX (code-review): previously swallowed silently when the
                # response had started — client hung with no error and no
                # teardown. Log loudly; the connection closes on return.
                logger.warning(
                    "body_limit_exceeded_after_response_start",
                    path=scope.get("path", ""),
                    bytes_received=bytes_received,
                    client=scope.get("client", ["unknown"])[0] if scope.get("client") else "unknown",
                )

    async def _send_413(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Emit the standard error envelope (I12: was hand-rolled text/plain)."""
        response = middleware_reject("payload_too_large", max_size=self.max_body_size)
        await response(scope, receive, send)

    async def _send_400(self, scope: Scope, receive: Receive, send: Send, message: bytes) -> None:
        """Emit the standard error envelope (I12: was hand-rolled text/plain)."""
        response = middleware_reject(
            "invalid_input", message=message.decode("utf-8", errors="replace")
        )
        await response(scope, receive, send)
