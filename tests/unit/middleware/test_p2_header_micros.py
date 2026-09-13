"""P2 micro tests: security-headers single build + gzip accept scan.

Proves byte-identical observable behavior after removing per-request work:
- SecurityHeadersMiddleware: precomputed HSTS/permissions values emitted verbatim.
- GZipFilterMiddleware: SSE accept bypasses gzip; duplicate accept headers keep
  last-wins semantics (matching the old dict-lookup behavior).
"""

from unittest.mock import MagicMock, patch

import pytest

from src.app.core.config import EnvironmentOption
from src.app.middleware.gzip_filter import GZipFilterMiddleware
from src.app.middleware.security_headers import _PERMISSIONS_POLICY, SecurityHeadersMiddleware


def _http_scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {"type": "http", "headers": headers}


async def _null_receive() -> dict:
    return {"type": "http.disconnect"}


class TestSecurityHeadersPrebuilt:
    @pytest.mark.asyncio
    async def test_hsts_value_uses_constructor_max_age(self) -> None:
        mw = SecurityHeadersMiddleware(app=MagicMock(), hsts_max_age=60)
        assert mw._hsts_value == "max-age=60; includeSubDomains; preload"

        seen: dict = {}

        async def fake_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        # Test env is LOCAL (HSTS off by default); force the emission path.
        # R5-MICRO: header pairs are frozen at __init__, so force the env gate
        # BEFORE construction instead of mutating the instance afterwards.
        with patch(
            "src.app.middleware.security_headers.settings.env.ENVIRONMENT",
            EnvironmentOption.STAGING,
        ):
            mw3 = SecurityHeadersMiddleware(app=fake_app, hsts_max_age=60)

        async def send(msg):
            if msg["type"] == "http.response.start":
                for k, v in msg["headers"]:
                    # MutableHeaders stores keys lowercase; HTTP names are
                    # case-insensitive, so normalize for assertion.
                    seen[k.decode().lower()] = v.decode()

        await mw3(_http_scope([]), _null_receive, send)
        assert seen["strict-transport-security"] == "max-age=60; includeSubDomains; preload"
        assert seen["permissions-policy"] == _PERMISSIONS_POLICY
        assert seen["x-frame-options"] == "DENY"
        assert "content-security-policy" in seen

    def test_permissions_policy_constant_shape(self) -> None:
        assert "microphone=()" in _PERMISSIONS_POLICY
        assert "geolocation=()" in _PERMISSIONS_POLICY


class TestGzipAcceptScan:
    @pytest.mark.asyncio
    async def test_sse_accept_bypasses_gzip(self) -> None:
        async def fake_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"data: x"})

        mw = GZipFilterMiddleware(app=fake_app)
        scope = _http_scope([(b"accept", b"text/event-stream")])
        bodies: list = []

        async def send(msg):
            if msg["type"] == "http.response.body":
                bodies.append(msg["body"])

        await mw(scope, _null_receive, send)
        assert bodies == [b"data: x"]
        # Bypass path never sets the gzip-path scope flag.
        assert "gzip_filter_exempt" not in scope

    @pytest.mark.asyncio
    async def test_normal_accept_takes_gzip_path(self) -> None:
        async def fake_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"hello"})

        mw = GZipFilterMiddleware(app=fake_app)
        scope = _http_scope([(b"accept", b"application/json")])
        bodies: list = []

        async def send(msg):
            if msg["type"] == "http.response.body":
                bodies.append(msg["body"])

        await mw(scope, _null_receive, send)
        assert bodies == [b"hello"]
        assert scope["gzip_filter_exempt"] is False

    @pytest.mark.asyncio
    async def test_duplicate_accept_last_wins(self) -> None:
        """Matches the old dict(...) lookup, which kept the last occurrence."""
        async def fake_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"hello"})

        mw = GZipFilterMiddleware(app=fake_app)
        scope = _http_scope([(b"accept", b"text/event-stream"), (b"accept", b"application/json")])
        bodies: list = []

        async def send(msg):
            if msg["type"] == "http.response.body":
                bodies.append(msg["body"])

        await mw(scope, _null_receive, send)
        assert bodies == [b"hello"]
        assert scope["gzip_filter_exempt"] is False
