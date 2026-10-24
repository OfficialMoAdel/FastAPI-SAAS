"""F-3(a): the request-ID debug log is GUARDED, so its cost is not paid.

WHY THIS IS A TEST AND NOT A COMMENT
    ``RequestIDMiddleware`` runs on EVERY request, and its ``logger.debug``
    kwarg dict (plus ``scope["path"]``) is built eagerly. Before F-3(a) the
    call was unguarded: every request paid the full structlog processor chain
    for a record the stdlib root logger discards at ``WARNING``. The guard is
    only load-bearing while the level-aware wrapper (A-3) is installed --
    under structlog's default ``BoundLoggerFilteringAtNotset``,
    ``is_enabled_for`` was structurally ``True`` and the guard was inert.

    The stub logger is injected rather than level-flipped: it pins the
    CONTRACT ("do not call debug unless is_enabled_for says so") directly, so
    the test fails if the guard is removed even if the ambient log level
    happens to be DEBUG.
"""

from __future__ import annotations

import logging
from typing import Any

from src.app.middleware import request_id as rid
from src.app.middleware.request_id import RequestIDMiddleware


class _RecordingLogger:
    """Minimal structlog-bound-logger stand-in that counts debug calls."""

    def __init__(self, debug_enabled: bool) -> None:
        self.debug_enabled = debug_enabled
        self.debug_calls = 0

    def is_enabled_for(self, level: int) -> bool:
        assert level == logging.DEBUG, "RequestIDMiddleware must test the DEBUG level"
        return self.debug_enabled

    def debug(self, *_args: Any, **_kwargs: Any) -> None:
        self.debug_calls += 1


async def _run_middleware() -> None:
    middleware = RequestIDMiddleware(app=_noop_app)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict[str, Any]) -> None:
        return None

    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/users/me",
        "query_string": b"",
        "headers": [],
    }
    await middleware(scope, receive, send)


async def _noop_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    return None


async def test_debug_is_not_called_when_the_level_is_off(monkeypatch: Any) -> None:
    recorder = _RecordingLogger(debug_enabled=False)
    monkeypatch.setattr(rid, "logger", recorder)
    await _run_middleware()
    assert recorder.debug_calls == 0, "unguarded logger.debug on the request path"


async def test_debug_is_called_when_the_level_is_on(monkeypatch: Any) -> None:
    recorder = _RecordingLogger(debug_enabled=True)
    monkeypatch.setattr(rid, "logger", recorder)
    await _run_middleware()
    assert recorder.debug_calls == 1, "the guard must not suppress a wanted record"
