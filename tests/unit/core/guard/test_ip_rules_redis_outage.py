"""The IP-rule lists must read as empty when Redis is unreachable.

``get_redis_client`` never substitutes ``None`` for the client — it RAISES
``MissingClientError``. The list readers did not catch it, so a Redis
outage turned ``GET /admin/ip/lists`` into a 500 instead of an empty list;
the sibling ``_clear_list`` already answered 0 in the same situation.
"""

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from src.app.core.exceptions.cache_exceptions import MissingClientError
from src.app.core.guard import ip_rules as ipm
from src.app.core.guard.ip_rules import IPFilterService


class TestListReadersSurviveARedisOutage:
    async def test_get_whitelist_returns_an_empty_list(self) -> None:
        service = IPFilterService()
        with patch(
            "src.app.core.guard.ip_rules.get_redis_client",
            side_effect=MissingClientError("no redis"),
        ):
            assert await service.get_whitelist() == []

    async def test_get_blacklist_returns_an_empty_list(self) -> None:
        service = IPFilterService()
        with patch(
            "src.app.core.guard.ip_rules.get_redis_client",
            side_effect=MissingClientError("no redis"),
        ):
            assert await service.get_blacklist() == []


# ============================================================================
# R2-15: a REAL outage is a RedisError, not a MissingClientError
# ============================================================================


class TestARealRedisErrorIsTheSameOutage:
    """`MissingClientError` means "no client configured". A connection failure is
    a different exception and used to escape as a 500 while the docstring above
    promised an empty list."""

    async def test_get_whitelist_survives_a_connection_error(self) -> None:
        service = IPFilterService()
        with patch(
            "src.app.core.guard.ip_rules.get_redis_client",
            side_effect=RedisError("connection refused"),
        ):
            assert await service.get_whitelist() == []

    async def test_get_blacklist_survives_a_timeout(self) -> None:
        service = IPFilterService()
        with patch(
            "src.app.core.guard.ip_rules.get_redis_client",
            side_effect=RedisTimeoutError("timed out"),
        ):
            assert await service.get_blacklist() == []

    async def test_clear_blacklist_survives_a_connection_error(self) -> None:
        service = IPFilterService()
        with patch(
            "src.app.core.guard.ip_rules.get_redis_client",
            side_effect=RedisError("connection refused"),
        ):
            assert await service.clear_blacklist() == 0


# ============================================================================
# R2-11: a writer landing mid-read must not re-poison the negative cache
# ============================================================================


class _InterleavingPipeline:
    """A pipeline whose execute() lets a writer land before it returns."""

    def __init__(self, on_execute) -> None:
        self._on_execute = on_execute
        self._results = [0]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    def exists(self, key: str) -> "_InterleavingPipeline":
        return self

    async def execute(self) -> list[int]:
        # The exact window: the reader has its answer, the writer has not been
        # observed yet, and the cache store is still ahead of it.
        self._on_execute()
        return self._results


class TestTheNegativeCacheCannotOutliveAnInvalidation:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        ipm._ip_status_negative_cache.clear()
        yield
        ipm._ip_status_negative_cache.clear()

    async def test_a_writer_landing_between_read_and_store_wins(self) -> None:
        service = IPFilterService()
        ip = "203.0.113.9"
        client = MagicMock()
        client.pipeline = MagicMock(
            return_value=_InterleavingPipeline(lambda: ipm.invalidate_ip_status_cache(ip))
        )

        @asynccontextmanager
        async def _client():
            yield client

        with patch("src.app.core.guard.ip_rules.get_redis_client", _client):
            status = await service.check_ip_status(ip)

        assert status == (False, False)
        assert ip not in ipm._ip_status_negative_cache, (
            "the reader stored a clean entry AFTER the writer invalidated the cache"
        )

    async def test_the_cache_still_fills_when_no_writer_interferes(self) -> None:
        """The epoch must not disable the optimization it protects."""
        service = IPFilterService()
        ip = "203.0.113.10"
        client = MagicMock()
        client.pipeline = MagicMock(return_value=_InterleavingPipeline(lambda: None))

        @asynccontextmanager
        async def _client():
            yield client

        with patch("src.app.core.guard.ip_rules.get_redis_client", _client):
            await service.check_ip_status(ip)

        assert ipm._ip_status_negative_cache[ip] == (False, False)

    async def test_a_cached_entry_still_short_circuits_redis(self) -> None:
        service = IPFilterService()
        ip = "203.0.113.11"
        ipm._ip_status_negative_cache[ip] = (False, False)
        client = MagicMock()
        client.pipeline = MagicMock(side_effect=AssertionError("cache hit must not touch Redis"))

        @asynccontextmanager
        async def _client():
            yield client

        with patch("src.app.core.guard.ip_rules.get_redis_client", _client):
            assert await service.check_ip_status(ip) == (False, False)
