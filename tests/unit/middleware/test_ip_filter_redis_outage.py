"""A Redis outage must not turn the IP-list guard into a 500.

``IPFilterMiddleware`` reads the blacklist/whitelist through
``IPFilterService.check_ip_status``, which reaches Redis via
``core.guard.ip_rules.get_redis_client``. That helper delegates to
``cache.async_get_redis``, which RAISES ``MissingClientError`` before it
yields — it never yields ``None``.

So with no Redis client the exception propagated straight out of the
middleware, and ``AppException.http_status = 500`` turned a backend outage
into an unhandled 500. The middleware must instead reject with the 503 the
admission path already uses (``rate_limit.py`` wraps the same class of
failure the same way).
"""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from src.app.core.exceptions.cache_exceptions import MissingClientError
from src.app.middleware.ip_filter import IPFilterMiddleware


def _scope(path: str = "/api/v1/users", ua: bytes = b"pytest") -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"user-agent", ua)],
        "client": ("1.2.3.4", 1234),
        "server": ("testserver", 80),
    }


async def _echo_app(scope, receive, send) -> None:
    """A downstream app that always answers 200."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})



@pytest.mark.asyncio
async def test_whitelisted_ip_bypasses_the_bad_bot_filter() -> None:
    """A whitelisted IP must survive a scanner User-Agent.

    The bad-bot check used to run BEFORE the whitelist lookup, so
    ``User-Agent: sqlmap/1.0`` from a whitelisted IP was rejected at
    ip_filter.py:151 and never reached ``set_whitelisted(..., True)`` —
    leaving the whitelist entry inert AND the ip_whitelisted trust signal
    unset, which downstream consumers (attack_detection.py:654,
    request_admission.py:96) then read as "untrusted".

    Both flags are set explicitly: conftest.py:66 defaults
    BAD_BOT_FILTER_ENABLED=false globally, which is why the existing e2e
    test for this behaviour passed without ever exercising the branch.
    """
    service = AsyncMock()
    service.check_ip_status = AsyncMock(return_value=(False, True))

    with patch("src.app.middleware.ip_filter.settings.security.IP_WHITELIST_ENABLED", True), patch(
        "src.app.middleware.ip_filter.settings.security.IP_BLACKLIST_ENABLED", True
    ), patch("src.app.middleware.ip_filter.settings.security.BAD_BOT_FILTER_ENABLED", True):
        middleware = IPFilterMiddleware(_echo_app, ip_rules=service)

        async def _receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        sent: dict = {}

        async def _send(message: dict) -> None:
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]

        await middleware(_scope(ua=b"sqlmap/1.0"), _receive, _send)

    assert sent["status"] == 200, (
        f"whitelisted IP with a scanner UA was rejected ({sent['status']}); "
        "the bad-bot check still precedes the whitelist lookup"
    )


@pytest.mark.asyncio
async def test_non_whitelisted_scanner_ua_is_still_rejected() -> None:
    """The bad-bot filter must keep working for everyone else."""
    service = AsyncMock()
    service.check_ip_status = AsyncMock(return_value=(False, False))

    with patch("src.app.middleware.ip_filter.settings.security.IP_WHITELIST_ENABLED", True), patch(
        "src.app.middleware.ip_filter.settings.security.IP_BLACKLIST_ENABLED", True
    ), patch("src.app.middleware.ip_filter.settings.security.BAD_BOT_FILTER_ENABLED", True):
        middleware = IPFilterMiddleware(_echo_app, ip_rules=service)

        async def _receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        sent: dict = {}

        async def _send(message: dict) -> None:
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]

        await middleware(_scope(ua=b"sqlmap/1.0"), _receive, _send)

    assert sent["status"] == 403, f"scanner UA was not rejected (got {sent['status']})"


@pytest.mark.asyncio
async def test_whitelisted_request_is_marked_trusted_for_downstream() -> None:
    """The ip_whitelisted trust signal must actually be set.

    attack_detection.py:654 skips detection and request_admission.py:96
    takes its whitelist branch off this flag; a whitelisted request that
    never sets it is treated as untrusted everywhere downstream.
    """
    service = AsyncMock()
    service.check_ip_status = AsyncMock(return_value=(False, True))
    seen: dict = {}

    async def _capture(scope, receive, send):
        request = Request(scope, receive)
        seen["whitelisted"] = getattr(request.state, "ip_whitelisted", None)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    with patch("src.app.middleware.ip_filter.settings.security.IP_WHITELIST_ENABLED", True), patch(
        "src.app.middleware.ip_filter.settings.security.IP_BLACKLIST_ENABLED", True
    ), patch("src.app.middleware.ip_filter.settings.security.BAD_BOT_FILTER_ENABLED", True):
        middleware = IPFilterMiddleware(_capture, ip_rules=service)

        async def _receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(message: dict) -> None:
            return None

        await middleware(_scope(ua=b"sqlmap/1.0"), _receive, _send)

    assert seen["whitelisted"] is True, (
        f"ip_whitelisted was {seen['whitelisted']!r} on a whitelisted request; "
        "downstream guards read this as untrusted"
    )


@pytest.mark.asyncio
async def test_missing_redis_client_yields_503_not_500() -> None:
    """Pre-fix this returns 500: the MissingClientError escaped the middleware."""
    service = AsyncMock()
    service.check_ip_status = AsyncMock(side_effect=MissingClientError("no client"))

    with patch("src.app.middleware.ip_filter.settings.security.IP_WHITELIST_ENABLED", True), patch(
        "src.app.middleware.ip_filter.settings.security.IP_BLACKLIST_ENABLED", True
    ), patch("src.app.middleware.ip_filter.settings.security.BAD_BOT_FILTER_ENABLED", False):
        middleware = IPFilterMiddleware(AsyncMock(), ip_rules=service)
        sent: dict = {}

        async def _receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(message: dict) -> None:
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]

        await middleware(_scope(), _receive, _send)

    assert sent["status"] == 503, f"expected 503 for an unavailable IP-list backend, got {sent['status']}"


@pytest.mark.asyncio
async def test_healthy_backend_still_passes_through() -> None:
    """The wrapper must not swallow a working path."""
    reached: list[str] = []

    async def _app(scope, receive, send) -> None:
        reached.append("downstream")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    service = AsyncMock()
    service.check_ip_status = AsyncMock(return_value=(False, False))

    with patch("src.app.middleware.ip_filter.settings.security.IP_WHITELIST_ENABLED", True), patch(
        "src.app.middleware.ip_filter.settings.security.IP_BLACKLIST_ENABLED", True
    ), patch("src.app.middleware.ip_filter.settings.security.BAD_BOT_FILTER_ENABLED", False):
        middleware = IPFilterMiddleware(_app, ip_rules=service)

        async def _receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(message: dict) -> None:
            return None

        await middleware(_scope(), _receive, _send)

    assert reached == ["downstream"], "a healthy IP-list backend must still reach the app"
    service.check_ip_status.assert_awaited_once()
