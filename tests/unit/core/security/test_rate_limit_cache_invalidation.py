"""The tier-policy invalidation must drop BOTH L1 layers.

The L1 seam owns the policy list AND the compiled per-path map, and
``get_tier_policy_limits`` consults the map FIRST. Popping only the list
therefore left the OLD limits in force for up to the map's own 300 s TTL —
in the very process that performed the write, while every other worker was
already correct (their invalidation went through the seam).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security import rate_limit_cache


def _pipeline() -> MagicMock:
    """A Redis pipeline mock usable as an async context manager."""
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    pipe.delete = MagicMock()
    pipe.publish = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=pipe)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.fixture(autouse=True)
def clear_policy_caches():
    """Empty both L1 layers before and after each test."""
    rate_limit_cache._tier_policy_l1.clear()
    rate_limit_cache._tier_policy_path_map.clear()
    yield
    rate_limit_cache._tier_policy_l1.clear()
    rate_limit_cache._tier_policy_path_map.clear()


class TestInvalidateTierPolicy:
    async def test_the_compiled_path_map_is_dropped_too(self) -> None:
        """The map outliving the list is what kept the old limits in force."""
        tier_id = 7
        rate_limit_cache._tier_policy_l1[tier_id] = [{"path": "/x", "limit": 10, "period": 60}]
        rate_limit_cache._tier_policy_path_map[tier_id] = {"/x": (10, 60)}

        redis = MagicMock()
        redis.pipeline = MagicMock(return_value=_pipeline())

        with patch("src.app.core.security.rate_limit_cache.get_redis", return_value=redis):
            invalidated = await rate_limit_cache.invalidate_tier_policy(tier_id)

        assert invalidated is True
        assert tier_id not in rate_limit_cache._tier_policy_l1
        assert tier_id not in rate_limit_cache._tier_policy_path_map

    async def test_the_limits_resolve_again_after_the_invalidation(self) -> None:
        """A stale map would answer the OLD limits for the same path."""
        tier_id = 11
        rate_limit_cache._tier_policy_l1[tier_id] = [{"path": "/x", "limit": 10, "period": 60}]
        rate_limit_cache._tier_policy_path_map[tier_id] = {"/x": (10, 60)}

        redis = MagicMock()
        redis.pipeline = MagicMock(return_value=_pipeline())

        with patch("src.app.core.security.rate_limit_cache.get_redis", return_value=redis):
            await rate_limit_cache.invalidate_tier_policy(tier_id)

        with patch("src.app.core.security.rate_limit_cache.get_redis", return_value=None):
            # No Redis and no L1 list -> the map must not answer either.
            assert await rate_limit_cache.get_tier_policy_limits(tier_id, "/x") == (None, None)

    async def test_a_redis_outage_still_drops_the_local_layers(self) -> None:
        """The local drop must not depend on Redis being reachable."""
        tier_id = 13
        rate_limit_cache._tier_policy_l1[tier_id] = [{"path": "/x", "limit": 10, "period": 60}]
        rate_limit_cache._tier_policy_path_map[tier_id] = {"/x": (10, 60)}

        with patch("src.app.core.security.rate_limit_cache.get_redis", return_value=None):
            invalidated = await rate_limit_cache.invalidate_tier_policy(tier_id)

        assert invalidated is False
        assert tier_id not in rate_limit_cache._tier_policy_l1
        assert tier_id not in rate_limit_cache._tier_policy_path_map
