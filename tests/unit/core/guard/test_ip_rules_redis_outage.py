"""The IP-rule lists must read as empty when Redis is unreachable.

``get_redis_client`` never substitutes ``None`` for the client — it RAISES
``MissingClientError``. The list readers did not catch it, so a Redis
outage turned ``GET /admin/ip/lists`` into a 500 instead of an empty list;
the sibling ``_clear_list`` already answered 0 in the same situation.
"""

from unittest.mock import patch

from src.app.core.exceptions.cache_exceptions import MissingClientError
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
