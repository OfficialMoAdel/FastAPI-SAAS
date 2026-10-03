"""S-07 — an oversized body is a SIZE violation, not an attack.

`RequestBodyLimitMiddleware` raises ``BodySizeLimitExceeded`` from inside
``limited_receive``, i.e. on the shared receive channel that every downstream
reader consumes. ``AttackDetectionMiddleware._scan_request`` drains that same
channel, and its broad ``except Exception`` used to swallow the size violation
and record an ``invalid_body`` / ``body_read_error`` attack verdict.

Consequence before the fix: a legitimate client over the size cap was reported
as an ATTACK — 400/403, and in ``ban`` mode a 24-hour IP ban — instead of the
413 the body-limit middleware exists to return.

The property pinned here: the size violation PROPAGATES out of attack
detection so the 413 handler wins, while genuine body-read errors still fail
closed. The second half guards against "fixing" S-07 by widening the re-raise
to cover every exception.
"""

from __future__ import annotations

import pytest
from starlette.types import Message, Receive, Scope

from src.app.middleware.attack_detection import AttackDetectionMiddleware
from src.app.middleware.body_limit import BodySizeLimitExceeded, RequestBodyLimitMiddleware


def _scope(path: str = "/api/v1/upload") -> Scope:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/octet-stream")],
        "client": ("10.0.0.1", 51234),
        "server": ("testserver", 80),
        "scheme": "http",
    }


def _oversized_receive(limit_bytes: int, chunk: int = 4096) -> Receive:
    """A chunked receive channel that trips the body limit part-way through."""
    state = {"sent": 0}

    async def receive() -> Message:
        state["sent"] += chunk
        return {
            "type": "http.request",
            "body": b"x" * chunk,
            "more_body": state["sent"] <= limit_bytes * 4,
        }

    return receive


async def _unreachable_app(scope: Scope, receive: Receive, send) -> None:
    raise AssertionError("the size violation must short-circuit before the app")


@pytest.mark.asyncio
async def test_oversized_chunked_body_yields_413_not_attack_verdict() -> None:
    """A chunked body over the cap must produce the body-limit 413.

    Exercised through the real middleware stack so the assertion covers the
    actual propagation path, not just the exception handler in isolation.
    """
    limit = 4096
    statuses: list[int] = []

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    # BodyLimit is outermost (per GUARD_ORDER), attack detection inner.
    await RequestBodyLimitMiddleware(AttackDetectionMiddleware(_unreachable_app), max_body_size=limit)(
        _scope(), _oversized_receive(limit), send
    )

    assert 413 in statuses, f"expected the 413 body-limit response, got {statuses}"


@pytest.mark.asyncio
async def test_scan_request_reraises_size_violation() -> None:
    """The size violation must escape _scan_request rather than be swallowed."""
    scanner = AttackDetectionMiddleware(_unreachable_app)

    async def receive() -> Message:
        raise BodySizeLimitExceeded("boom")

    with pytest.raises(BodySizeLimitExceeded):
        await scanner._scan_request("/api/v1/upload", b"", [(b"host", b"testserver")], "POST", receive)


@pytest.mark.asyncio
async def test_genuine_body_read_error_still_fails_closed() -> None:
    """A real read failure must NOT be treated as a size violation."""

    class Boom(Exception):
        pass

    scanner = AttackDetectionMiddleware(_unreachable_app)

    async def receive() -> Message:
        raise Boom("connection reset mid-body")

    # The broad handler swallows this and records an invalid_body verdict
    # rather than propagating — the fail-closed behaviour S-07 must not break.
    detected, _receive = await scanner._scan_request(
        "/api/v1/upload", b"", [(b"host", b"testserver")], "POST", receive
    )

    assert any(a.get("attack_type") == "invalid_body" for a in detected), detected
