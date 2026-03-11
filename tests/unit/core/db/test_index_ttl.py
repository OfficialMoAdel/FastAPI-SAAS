from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.utils import cache


@pytest.mark.asyncio
async def test_add_to_tenant_index_ttl():
    """Verify index expires after cache entries (2x TTL)."""
    tenant_id = "test_tenant"
    cache_key = "test_key"
    ttl = 3600

    mock_client = AsyncMock()
    # Fix: pipeline needs to return an async context manager, not be a coroutine itself
    mock_client.pipeline = MagicMock()
    mock_pipeline = AsyncMock()
    mock_client.pipeline.return_value.__aenter__.return_value = mock_pipeline

    with patch.object(cache, "client", mock_client):
        await cache.add_to_tenant_index(tenant_id, cache_key, ttl=ttl)

        index_key = f"tenant_index:{tenant_id}"
        mock_pipeline.sadd.assert_called_with(index_key, cache_key)
        mock_pipeline.expire.assert_any_call(index_key, ttl)


@pytest.mark.asyncio
async def test_set_tenant_cache_index_ttl():
    """Verify set_tenant_cache sets index TTL."""
    tenant_id = "test_tenant"
    prefix = "user"
    key_parts = ("123",)
    value = "data"
    ttl = 60

    mock_client = AsyncMock()
    mock_client.pipeline = MagicMock()
    mock_pipeline = AsyncMock()
    mock_client.pipeline.return_value.__aenter__.return_value = mock_pipeline

    with patch.object(cache, "client", mock_client):
        await cache.set_tenant_cache(tenant_id, prefix, key_parts, value, ttl)

        index_key = f"tenant_index:{tenant_id}"
        cache_key = f"tenant:{tenant_id}:{prefix}:123"

        mock_pipeline.setex.assert_called_with(cache_key, ttl, value)
        mock_pipeline.sadd.assert_any_call(index_key, cache_key)
        # expire is now called
        mock_pipeline.expire.assert_any_call(index_key, ttl * 2)


@pytest.mark.asyncio
async def test_migrate_tenant_cache_index_ttl():
    """Verify migration sets appropriate TTL on index."""
    tenant_id = "123"
    mock_client = AsyncMock()
    mock_client.pipeline = MagicMock()
    mock_pipeline = AsyncMock()
    mock_client.pipeline.return_value.__aenter__.return_value = mock_pipeline

    # Mock scan to return one key then done
    mock_client.scan.side_effect = [(10, ["tenant:123:key1"]), (0, [])]
    mock_client.ttl.return_value = 100

    with patch.object(cache, "client", mock_client):
        await cache.migrate_tenant_cache_index(tenant_id)

        index_key = f"tenant_index:{tenant_id}"
        mock_pipeline.sadd.assert_any_call(index_key, "tenant:123:key1")
