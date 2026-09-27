"""Template cache invalidation must be fail-soft like the rest of the module.

``invalidate_template_cache`` is called after an override write/delete has
ALREADY COMMITTED. A Redis error there propagated out, which did two bad
things at once: it 500'd a successful mutation, and the stale template
stayed in the cache for the full TEMPLATE_CACHE_TTL. The override is the
source of truth and the cache is a read-through, so a failed drop must
never fail the write.

Every other function in the module already swallowed Redis errors at debug
level; this was the one exception.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.notifications import template_cache
from src.app.modules.notifications.template_cache import (
    get_cached_template,
    invalidate_template_cache,
    store_cached_template,
)

TENANT = uuid4()


def _client(delete_error: Exception | None = None) -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock()
    client.set = AsyncMock()
    client.delete = AsyncMock(side_effect=delete_error) if delete_error else AsyncMock()
    return client


class TestInvalidateIsFailSoft:
    @pytest.mark.asyncio
    async def test_redis_error_does_not_propagate(self) -> None:
        """Pre-fix this raised out of an already-committed write."""
        client = _client(delete_error=RuntimeError("redis down"))

        with patch.object(template_cache, "get_redis", return_value=client):
            await invalidate_template_cache(TENANT, "welcome")  # must not raise

    @pytest.mark.asyncio
    async def test_redis_error_is_logged_at_debug(self) -> None:
        client = _client(delete_error=RuntimeError("redis down"))

        with (
            patch.object(template_cache, "get_redis", return_value=client),
            patch.object(template_cache, "logger") as mock_logger,
        ):
            await invalidate_template_cache(TENANT, "welcome")

        assert mock_logger.debug.called, "a swallowed failure must still be traceable"
        assert not mock_logger.error.called, "a cache drop is not an error"

    @pytest.mark.asyncio
    async def test_happy_path_still_deletes(self) -> None:
        client = _client()

        with patch.object(template_cache, "get_redis", return_value=client):
            await invalidate_template_cache(TENANT, "welcome")

        client.delete.assert_awaited_once()
        assert "welcome" in client.delete.await_args.args[0]

    @pytest.mark.asyncio
    async def test_no_client_is_a_no_op(self) -> None:
        with patch.object(template_cache, "get_redis", return_value=None):
            await invalidate_template_cache(TENANT, "welcome")  # must not raise

    @pytest.mark.asyncio
    async def test_module_is_uniformly_fail_soft(self) -> None:
        """Every Redis-touching function in the module swallows errors.

        This is the invariant the original exception broke: a reader could
        not tell which function had a different failure posture.
        """
        client = _client(delete_error=RuntimeError("redis down"))
        client.get = AsyncMock(side_effect=RuntimeError("redis down"))
        client.set = AsyncMock(side_effect=RuntimeError("redis down"))

        with patch.object(template_cache, "get_redis", return_value=client):
            assert await get_cached_template(TENANT, "welcome") is None
            await store_cached_template(TENANT, "welcome", ("s", "<p/>"))
            await invalidate_template_cache(TENANT, "welcome")
