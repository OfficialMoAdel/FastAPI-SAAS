from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.utils import cache


@pytest.mark.asyncio
async def test_pattern_invalidation_logic():
    # Mock Redis client
    mock_client = AsyncMock()

    # Mock pipeline object (sync methods, async execute)
    mock_pipeline = MagicMock()
    # Configure context manager for pipeline
    mock_pipeline.__aenter__.return_value = mock_pipeline
    mock_pipeline.__aexit__.return_value = None

    # execute must be awaitable
    mock_pipeline.execute = AsyncMock(return_value=[1, 1, 1])

    # client.pipeline() is sync and returns the pipeline object
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    # Mock smembers to return a set (awaitable)
    mock_client.smembers = AsyncMock(return_value={b"user:123:profile"})

    # Patch the client in cache module
    with patch("src.app.core.utils.cache.client", mock_client):
        # 1. Register patterns
        key = "user:123:profile"
        patterns = ["user:*:profile", "user:123:*"]
        ttl = 3600

        await cache.register_key_patterns(key, patterns, ttl)

        # Verify sadd and expire called (sync calls on pipeline)
        assert mock_pipeline.sadd.call_count >= 2
        assert mock_pipeline.expire.call_count >= 2
        mock_pipeline.execute.assert_called_once()

        # 2. Invalidate by pattern
        mock_pipeline.reset_mock()
        mock_pipeline.execute.return_value = [5, 1]  # 5 keys deleted, 1 index deleted

        deleted_count = await cache.invalidate_by_pattern("user:*:profile")

        assert deleted_count == 5
        mock_pipeline.delete.assert_any_call(b"user:123:profile")
        mock_pipeline.execute.assert_called_once()


@pytest.mark.asyncio
async def test_set_cache_with_patterns():
    mock_client = AsyncMock()
    mock_pipeline = MagicMock()
    mock_pipeline.__aenter__.return_value = mock_pipeline
    mock_pipeline.__aexit__.return_value = None
    mock_pipeline.execute = AsyncMock(return_value=[True, 1])

    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.utils.cache.client", mock_client):
        await cache.set_cache_with_patterns("key", "value", 60, patterns=["p1"])

        # Verify calls
        mock_pipeline.setex.assert_called_once()
        mock_pipeline.sadd.assert_called_once()
        mock_pipeline.expire.assert_called_once()
        mock_pipeline.execute.assert_called_once()
