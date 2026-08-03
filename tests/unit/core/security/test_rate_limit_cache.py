from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.rate_limit_cache import (
    POLICY_KEY_PREFIX,
    _tier_policy_l1,
    get_tier_policy,
    invalidate_tier_policy,
    warm_policy_cache,
)


@pytest.fixture(autouse=True)
def clear_l1_cache():
    _tier_policy_l1.clear()

@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock_pipe = AsyncMock()
    mock_pipe.__aenter__.return_value = mock_pipe
    mock.pipeline = MagicMock(return_value=mock_pipe)
    with patch("src.app.core.security.rate_limit_cache.redis_client", mock):
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
    with patch("src.app.core.security.rate_limit_cache.redis_client", None):
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
    with patch("src.app.core.security.rate_limit_cache.redis_client", None):
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
    mock_redis.delete.assert_called_once_with(f"{POLICY_KEY_PREFIX}1")


@pytest.mark.asyncio
async def test_invalidate_tier_policy_no_redis() -> None:
    with patch("src.app.core.security.rate_limit_cache.redis_client", None):
        result = await invalidate_tier_policy(1)
        assert result is False


@pytest.mark.asyncio
async def test_invalidate_tier_policy_exception(mock_redis) -> None:
    mock_redis.delete.side_effect = Exception("Redis delete error")
    result = await invalidate_tier_policy(1)
    assert result is False
