from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.rate_limit_cache import (
    _POLICY_INVALIDATION_CHANNEL,
    POLICY_KEY_PREFIX,
    _tier_policy_l1,
    _tier_policy_path_map,
    get_tier_policy,
    get_tier_policy_limits,
    invalidate_tier_policy,
    warm_policy_cache,
)


@pytest.fixture(autouse=True)
def clear_l1_cache():
    _tier_policy_l1.clear()
    _tier_policy_path_map.clear()

@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock_pipe = AsyncMock()
    mock_pipe.__aenter__.return_value = mock_pipe
    mock.pipeline = MagicMock(return_value=mock_pipe)
    with patch("src.app.core.utils.cache.client", mock):
        yield mock





@pytest.mark.asyncio
async def test_get_tier_policy_success(mock_redis) -> None:
    mock_redis.get.return_value = '[{"limit": 100, "window": 60}]'

    result = await get_tier_policy(1)

    assert result == [{"limit": 100, "window": 60}]
    mock_redis.get.assert_called_once_with(f"{POLICY_KEY_PREFIX}1")


@pytest.mark.asyncio
async def test_get_tier_policy_not_found(mock_redis) -> None:
    mock_redis.get.return_value = None

    result = await get_tier_policy(1)

    assert result is None


@pytest.mark.asyncio
async def test_get_tier_policy_no_redis() -> None:
    with patch("src.app.core.utils.cache.client", None):
        result = await get_tier_policy(1)
        assert result is None


@pytest.mark.asyncio
async def test_get_tier_policy_exception(mock_redis) -> None:
    mock_redis.get.side_effect = Exception("Redis error")
    result = await get_tier_policy(1)
    assert result is None


@pytest.mark.asyncio
async def test_warm_policy_cache_success(mock_redis) -> None:
    tier_policies = {
        1: [{"limit": 100, "window": 60}],
        2: [{"limit": 1000, "window": 3600}],
    }

    result = await warm_policy_cache(tier_policies)

    assert result == 2
    assert mock_redis.pipeline.return_value.setex.call_count == 2
    assert mock_redis.pipeline.return_value.execute.call_count == 1


@pytest.mark.asyncio
async def test_warm_policy_cache_empty(mock_redis) -> None:
    result = await warm_policy_cache({})
    assert result == 0
    assert mock_redis.pipeline.return_value.setex.call_count == 0


@pytest.mark.asyncio
async def test_warm_policy_cache_no_redis() -> None:
    with patch("src.app.core.utils.cache.client", None):
        result = await warm_policy_cache({1: []})
        assert result == 0


@pytest.mark.asyncio
async def test_warm_policy_cache_exception(mock_redis) -> None:
    mock_redis.pipeline.return_value.execute.side_effect = Exception("Pipeline error")
    tier_policies = {1: []}

    result = await warm_policy_cache(tier_policies)

    assert result == 0


@pytest.mark.asyncio
async def test_invalidate_tier_policy_success(mock_redis) -> None:
    result = await invalidate_tier_policy(1)
    assert result is True
    mock_redis.pipeline.assert_called_once_with(transaction=False)
    pipe = mock_redis.pipeline.return_value.__aenter__.return_value
    pipe.delete.assert_called_once_with(f"{POLICY_KEY_PREFIX}1")
    pipe.publish.assert_called_once_with(_POLICY_INVALIDATION_CHANNEL, "1")
    pipe.execute.assert_called_once()


@pytest.mark.asyncio
async def test_invalidate_tier_policy_no_redis() -> None:
    with patch("src.app.core.utils.cache.client", None):
        result = await invalidate_tier_policy(1)
        assert result is False


@pytest.mark.asyncio
async def test_invalidate_tier_policy_exception(mock_redis) -> None:
    pipe = mock_redis.pipeline.return_value.__aenter__.return_value
    pipe.execute.side_effect = Exception("Redis pipeline error")
    result = await invalidate_tier_policy(1)
    assert result is False


@pytest.mark.asyncio
async def test_get_tier_policy_limits_compile_and_hit(mock_redis) -> None:
    mock_redis.get.return_value = '[{"path": "/api/v1/posts", "limit": 120, "period": 60}]'

    limit, period = await get_tier_policy_limits(1, "/api/v1/posts")
    assert limit == 120
    assert period == 60

    # Second call must serve from the compiled path map (no extra Redis GET).
    assert 1 in _tier_policy_path_map
    calls_before = mock_redis.get.call_count
    limit, period = await get_tier_policy_limits(1, "/api/v1/posts")
    assert limit == 120
    assert period == 60
    assert mock_redis.get.call_count == calls_before


@pytest.mark.asyncio
async def test_get_tier_policy_limits_path_miss(mock_redis) -> None:
    mock_redis.get.return_value = '[{"path": "/api/v1/posts", "limit": 120, "period": 60}]'

    limit, period = await get_tier_policy_limits(1, "/api/v1/other")
    assert limit is None
    assert period is None


@pytest.mark.asyncio
async def test_get_tier_policy_limits_no_policies(mock_redis) -> None:
    mock_redis.get.return_value = None

    limit, period = await get_tier_policy_limits(1, "/api/v1/posts")
    assert limit is None
    assert period is None
    # Falsy tier policies must not poison the map with an empty dict.
    assert 1 not in _tier_policy_path_map


@pytest.mark.asyncio
async def test_policy_invalidation_clears_path_map(mock_redis) -> None:
    mock_redis.get.return_value = '[{"path": "/api/v1/posts", "limit": 120, "period": 60}]'
    await get_tier_policy_limits(1, "/api/v1/posts")
    assert 1 in _tier_policy_path_map

    # Simulate the pub/sub invalidation handler for a single tier.
    from src.app.core.security.rate_limit_cache import _on_policy_invalidation

    _on_policy_invalidation("1")
    assert 1 not in _tier_policy_path_map
    assert 1 not in _tier_policy_l1
