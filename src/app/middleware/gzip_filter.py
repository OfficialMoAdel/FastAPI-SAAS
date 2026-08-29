"""GZip filter middleware - exempts certain content types from compression.

Wraps Starlette's GZipMiddleware to skip compression for:
- text/event-stream (SSE): Compression adds latency to real-time streams
- application/octet-stream: Already binary, minimal compression benefit
- Content types with existing compression (gzip, br, zstd)

R4-09 post-review fix: the response Content-Type is only known when the
response starts — AFTER GZipMiddleware has chosen a responder. A previous
attempt stripped Content-Encoding from the start message outside GZipMiddleware,
but the body had already been compressed, corrupting every exempt response
≥500 bytes. The correct lever is the one GZipMiddleware itself documents:
it passes responses through untouched when a Content-Encoding is already set.
This middleware therefore marks exempt responses on the INSIDE (between the
inner app and GZipMiddleware) with ``Content-Encoding: identity`` so GZip
skips them, and strips that synthetic header on the OUTSIDE so clients never
see it. ``Content-Encoding: identity`` means "no encoding" and is never emitted
for real responses in this app.
"""

from fastapi.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Content types to exempt from GZip compression
GZIP_EXEMPT_TYPES = frozenset(
    {
        "text/event-stream",
        "application/octet-stream",
        "application/zip",
        "application/gzip",
        "image/",  # prefix match
        "video/",  # prefix match
        "audio/",  # prefix match
    }
)


def _is_exempt_content_type(content_type: str) -> bool:
    ct = content_type.split(";", 1)[0].strip().lower()
    if not ct:
        return False
    for pat in GZIP_EXEMPT_TYPES:
        if pat.endswith("/"):
            if ct.startswith(pat):
                return True
        elif ct == pat:
            return True
    return False


def _content_type_of(message: Message) -> str:
    for k, v in message.get("headers", []):  # type: ignore[arg-type]
        if k.lower() == b"content-type":  # type: ignore[union-attr]
            return v.decode("utf-8", errors="ignore")  # type: ignore[union-attr, no-any-return]
    return ""


class GZipFilterMiddleware:
    """Pure ASGI middleware that conditionally bypasses GZip for exempt content types.

    Uses Starlette's own "content-encoding already set → identity pass-through"
    branch (``content_encoding_set``) instead of stripping headers after
    compression. The exempt decision is made on the response Content-Type at
    ``http.response.start`` time, where GZipMiddleware itself makes it.
    """

    def __init__(self, app: ASGIApp, minimum_size: int = 500, compresslevel: int = 6) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        accept = headers.get(b"accept", b"").decode("utf-8", errors="ignore")
        if "text/event-stream" in accept:
            # Known SSE request - bypass GZip entirely (avoids gzip setup cost).
            await self.app(scope, receive, send)
            return

        # Per-request flag shared between the inner marker and the outer stripper.
        exempt: dict[str, bool] = {"v": False}

        # Inner gate: marks exempt responses so GZipMiddleware skips them.
        inner_app = self.app

        class _ExemptMarker:
            def __init__(self, app: ASGIApp) -> None:
                self._app = app

            async def __call__(self, s: Scope, r: Receive, inner_send: Send) -> None:
                async def _send(message: Message) -> None:
                    if message["type"] == "http.response.start":
                        ct = _content_type_of(message)
                        has_ce = any(
                            k.lower() == b"content-encoding"  # type: ignore[union-attr]
                            for k, _ in message.get("headers", [])  # type: ignore[arg-type]
                        )
                        if ct and _is_exempt_content_type(ct) and not has_ce:
                            exempt["v"] = True
                            message["headers"] = [  # type: ignore[arg-type]
                                *message.get("headers", []),  # type: ignore[arg-type]
                                (b"content-encoding", b"identity"),
                            ]
                    await inner_send(message)

                await self._app(s, r, _send)

        marker_app = _ExemptMarker(inner_app)
        gzip_app = GZipMiddleware(marker_app, minimum_size=self.minimum_size, compresslevel=self.compresslevel)

        async def _outer_send(message: Message) -> None:
            if message["type"] == "http.response.start" and exempt["v"]:
                # Strip the synthetic marker so clients never see Content-Encoding: identity.
                message["headers"] = [  # type: ignore[arg-type]
                    (k, v)
                    for k, v in message.get("headers", [])  # type: ignore[arg-type]
                    if not (
                        k.lower() == b"content-encoding"  # type: ignore[union-attr]
                        and v.strip().lower() == b"identity"  # type: ignore[union-attr]
                    )
                ]
            await send(message)

        await gzip_app(scope, receive, _outer_send)
