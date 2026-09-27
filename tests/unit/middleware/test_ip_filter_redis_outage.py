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

from src.app.core.exceptions.cache_exceptions import MissingClientError
from src.app.middleware.ip_filter import IPFilterMiddleware


def _scope(path: str = "/api/v1/users") -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"user-agent", b"pytest")],
        "client": ("1.2.3.4", 1234),
        "server": ("testserver", 80),
    }



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
