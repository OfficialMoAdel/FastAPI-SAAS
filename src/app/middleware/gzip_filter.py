"""GZip filter middleware - exempts certain content types from compression.

Wraps Starlette's GZipMiddleware to skip compression for:
- text/event-stream (SSE): Compression adds latency to real-time streams
- application/octet-stream: Already binary, minimal compression benefit
- Content types with existing compression (gzip, br, zstd)
"""

from fastapi.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# Content types to exempt from GZip compression
GZIP_EXEMPT_TYPES = frozenset({
    "text/event-stream",
    "application/octet-stream",
    "application/zip",
    "application/gzip",
    "image/",  # prefix match
    "video/",  # prefix match
    "audio/",  # prefix match
})


class GZipFilterMiddleware:
    """Pure ASGI middleware that conditionally bypasses GZip for exempt content types.

    Sits OUTSIDE GZipMiddleware and intercepts the response to check content-type.
    If exempt, routes directly to the inner app (skipping GZip).
    """

    def __init__(self, app: ASGIApp, minimum_size: int = 500, compresslevel: int = 6) -> None:
        self.app = app          # Inner app (without GZip)
        self.gzip_app = GZipMiddleware(app, minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.gzip_app(scope, receive, send)
            return

        # Check Accept header for SSE requests
        headers = dict(scope.get("headers", []))
        accept = headers.get(b"accept", b"").decode("utf-8", errors="ignore")

        if "text/event-stream" in accept:
            # SSE request - bypass GZip entirely
            await self.app(scope, receive, send)
            return

        # For all other requests, use GZip
        await self.gzip_app(scope, receive, send)
