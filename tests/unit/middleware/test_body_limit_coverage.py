"""Unit tests for RequestBodyLimitMiddleware (ASGI)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.app.middleware.body_limit import (
    BodySizeLimitExceeded,
    LimitedBodyStream,
    RequestBodyLimitMiddleware,
)

DEFAULT_MAX = 10 * 1024 * 1024


async def _make_scope(headers=None, path="/upload") -> dict:
    return {
        "type": "http",
        "headers": headers or [],
        "path": path,
        "client": ["127.0.0.1", 1234],
    }


async def _run_middleware(middleware, scope, body_chunks, send_calls):
    async def receive():
        if body_chunks:
            return {"type": "http.request", "body": body_chunks.pop(0), "more_body": bool(body_chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        send_calls.append(message)

    await middleware(scope, receive, send)


async def test_non_http_scope_passes_through() -> None:
    calls = []
    app = AsyncMock()
    middleware = RequestBodyLimitMiddleware(app, DEFAULT_MAX)
    scope = {"type": "websocket"}
    await middleware(scope, AsyncMock(), calls.append)
    app.assert_awaited_once()


async def test_small_body_passes() -> None:
    app = AsyncMock()
    middleware = RequestBodyLimitMiddleware(app, DEFAULT_MAX)
    scope = await _make_scope()
    send_calls = []
    await _run_middleware(middleware, scope, [b"small body"], send_calls)
    app.assert_awaited_once()


async def test_content_length_too_large_returns_413() -> None:
    app = AsyncMock()
    middleware = RequestBodyLimitMiddleware(app, 100)
    scope = await _make_scope(headers=[(b"content-length", b"1000")])
    send_calls = []
    await _run_middleware(middleware, scope, [], send_calls)
    app.assert_not_awaited()
    assert send_calls[0]["status"] == 413


async def test_multiple_conflicting_content_length_returns_400() -> None:
    app = AsyncMock()
    middleware = RequestBodyLimitMiddleware(app, DEFAULT_MAX)
    scope = await _make_scope(headers=[(b"content-length", b"10"), (b"content-length", b"20")])
    send_calls = []
    await _run_middleware(middleware, scope, [], send_calls)
    app.assert_not_awaited()
    assert send_calls[0]["status"] == 400


async def test_chunked_body_too_large_raises_413() -> None:
    async def _app(s, receive, send):
        while True:
            msg = await receive()
            if not msg.get("more_body", False):
                break

    middleware = RequestBodyLimitMiddleware(_app, 10)
    scope = await _make_scope()
    send_calls = []
    # chunks exceed 10 bytes total
    await _run_middleware(middleware, scope, [b"aaaaaaaaaa", b"bbbb"], send_calls)
    assert send_calls[0]["status"] == 413


async def test_invalid_content_length_falls_through_to_stream() -> None:
    app = AsyncMock()
    middleware = RequestBodyLimitMiddleware(app, DEFAULT_MAX)
    scope = await _make_scope(headers=[(b"content-length", b"not-a-number")])
    send_calls = []
    await _run_middleware(middleware, scope, [b"ok"], send_calls)
    app.assert_awaited_once()


async def test_limited_body_stream_raises_on_exceed() -> None:
    async def fake_stream():
        yield b"x" * 5
        yield b"x" * 5
        yield b"x" * 5  # total 15 > 10

    stream = LimitedBodyStream(fake_stream(), max_size=10)
    with pytest.raises(BodySizeLimitExceeded):
        async for _ in stream:
            pass


async def test_limited_body_stream_ok_within_limit() -> None:
    async def fake_stream():
        yield b"x" * 3
        yield b"x" * 3

    stream = LimitedBodyStream(fake_stream(), max_size=10)
    total = 0
    async for chunk in stream:
        total += len(chunk)
    assert total == 6
