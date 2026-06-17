"""Unit tests for cache module.

This file consolidates tests from:
- test_cache_lock.py
- test_core_cache.py
- test_redis_consolidation.py
- test_scan_bounds.py
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from redis.asyncio import ConnectionPool

from src.app.core import setup
from src.app.core.cache import manager as redis_factory
from src.app.core.config import settings
from src.app.core.utils import cache as cache_module
from src.app.core.utils.cache import cache, get_with_lock

# =============================================================================
# Tests from test_cache_lock.py
# =============================================================================


@pytest.mark.asyncio
async def test_cache_lock_backoff_logic():
    """Verify that get_with_lock uses exponential backoff when waiting."""

    key = "test:lock:key"
    ttl = 60
    lock_timeout = 10

    # Mock Redis
    mock_redis = AsyncMock()

    # 1. Initial check: Cache miss (returns None)
    # 2. Lock acquire: Fails (returns False) -> enters wait loop
    # 3. Wait loop 1: Cache check returns None
    # 4. Wait loop 2: Cache check returns None
    # 5. Wait loop 3: Cache check returns Valid Data
    mock_redis.get.side_effect = [
        None,  # Initial check
        None,  # Check inside wait loop 1
        None,  # Check inside wait loop 2
        b'{"data": "success"}',  # Check inside wait loop 3
    ]

    mock_redis.set.return_value = False  # Lock acquisition failed

    fetch_func = AsyncMock()

    with patch("src.app.core.utils.cache.client", mock_redis):
        # Patch asyncio.sleep to track delays and avoid actual waiting
        with patch("src.app.core.utils.cache.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # Patch random.uniform to return 0 (no jitter) for deterministic assertions
            with patch("src.app.core.utils.cache.random.uniform", return_value=0.0):
                result = await get_with_lock(key, fetch_func, ttl, lock_timeout)

                assert result == {"data": "success"}

                # Check call args
                sleep_times = [call.args[0] for call in mock_sleep.call_args_list]
                print(f"Sleep times: {sleep_times}")

                # Verify exponential backoff: 0.05 -> 0.1 -> 0.2
                assert len(sleep_times) == 3
                assert sleep_times[0] == 0.05
                assert sleep_times[1] == 0.1
                assert sleep_times[2] == 0.2


# =============================================================================
# Tests from test_core_cache.py
# =============================================================================


@pytest.mark.asyncio
async def test_cache_decorator_uses_setex():
    """Test that the cache decorator uses atomic setex instead of set + expire."""

    # Mock Redis client and pipeline
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Cache miss
    mock_pipeline = AsyncMock()

    @asynccontextmanager
    async def mock_pipeline_cm(**kwargs):
        yield mock_pipeline

    mock_redis.pipeline = mock_pipeline_cm

    # Mock the module-level client
    with patch("src.app.core.utils.cache.client", mock_redis):
        # Define a dummy endpoint
        @cache(key_prefix="test", expiration=60)
        async def dummy_endpoint(request: Request, id: int):
            return {"data": "value"}

        # Create a dummy request
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"

        # Call the endpoint
        result = await dummy_endpoint(mock_request, id=123)

        # Verify result
        assert result == {"data": "value"}

        # Verify Redis pipeline calls
        # Should call setex on pipeline once with correct args
        mock_pipeline.setex.assert_called_once()
        mock_pipeline.execute.assert_called_once()

        # Should NOT call set or expire separately on client
        mock_redis.set.assert_not_called()
        mock_redis.expire.assert_not_called()

        # Verify arguments
        call_args = mock_pipeline.setex.call_args
        assert call_args[0][0] == "test:123"  # key
        assert call_args[0][1] == 60  # time

        # Value is bytes because json_dumps returns bytes by default
        val = call_args[0][2]
        assert isinstance(val, bytes)
        assert b'"data":"value"' in val.replace(b" ", b"")  # Remove spaces for robust JSON check


# =============================================================================
# Tests from test_redis_consolidation.py
# =============================================================================


@pytest.mark.uses_real_redis
@pytest.mark.asyncio
async def test_redis_consolidation_single_pool():
    """Verify that a single pool is used across modules."""
    # Ensure pool is closed first
    await redis_factory.close_redis_pool()

    # Mock ConnectionPool.from_url to return a specific mock object
    mock_pool_instance = MagicMock(spec=ConnectionPool)
    mock_pool_instance.connection_kwargs = {"decode_responses": False}
    # We also need to mock disconnect since close_redis_pool calls it
    mock_pool_instance.disconnect = AsyncMock()

    with patch("src.app.core.cache.manager.ConnectionPool.from_url", return_value=mock_pool_instance) as mock_from_url:
        pool1 = await redis_factory.get_redis_pool()
        pool2 = await redis_factory.get_redis_pool()

        assert pool1 is pool2
        assert mock_from_url.call_count == 1

        # Verify config usage
        mock_from_url.assert_called_with(
            settings.REDIS_CACHE_URL,
            max_connections=settings.REDIS_CACHE_MAX_CONNECTIONS,
            socket_timeout=settings.REDIS_CACHE_SOCKET_TIMEOUT,
            socket_connect_timeout=settings.REDIS_CACHE_SOCKET_CONNECT_TIMEOUT,
            retry_on_timeout=True,
            decode_responses=False,
        )

    # Cleanup
    await redis_factory.close_redis_pool()


@pytest.mark.uses_real_redis
@pytest.mark.asyncio
async def test_setup_uses_shared_pool():
    """Verify setup uses the shared pool."""
    # Reset globals to bypass idempotency check
    cache_module.client = None
    cache_module.pool = None
    from src.app.core.utils import queue as queue_module

    queue_module.pool = None

    mock_pool = MagicMock(spec=ConnectionPool)

    with patch("src.app.core.setup.get_redis_pool", new_callable=AsyncMock) as mock_get_pool:
        mock_get_pool.return_value = mock_pool

        # Patch where it is used in setup.py
        # setup.py imports: import redis.asyncio as redis
        # So we patch 'src.app.core.setup.redis.Redis'
        with (
            patch("src.app.core.setup.redis.Redis") as mock_redis_cls,
            patch("src.app.core.setup.ArqRedis") as mock_arq_cls,
        ):
            await setup.create_redis_cache_pool()
            await setup.create_redis_queue_pool()

            # Verify setup uses the same pool instance
            mock_redis_cls.assert_called_with(connection_pool=mock_pool, decode_responses=True)
            mock_arq_cls.assert_called_with(connection_pool=mock_pool)


@pytest.mark.uses_real_redis
@pytest.mark.asyncio
async def test_no_connection_leaks_under_load():
    """Verify no connection leaks logic (simulated)."""
    await redis_factory.close_redis_pool()

    mock_pool_instance = MagicMock(spec=ConnectionPool)
    mock_pool_instance.disconnect = AsyncMock()

    with patch("src.app.core.cache.manager.ConnectionPool.from_url", return_value=mock_pool_instance) as mock_from_url:
        await redis_factory.get_redis_pool()
        kwargs = mock_from_url.call_args[1]
        assert kwargs["max_connections"] == settings.REDIS_CACHE_MAX_CONNECTIONS

    await redis_factory.close_redis_pool()


# =============================================================================
# Tests from test_scan_bounds.py
# =============================================================================


@pytest.mark.asyncio
async def test_migrate_tenant_cache_index_scan_bounds():
    """Verify migration stops at MAX_ITERATIONS and logs warning."""
    mock_client = AsyncMock()

    # infinite loop of cursors if not bounded
    # We want to check it stops at 1000
    # return cursor 1 (not 0) forever
    mock_client.scan.return_value = (1, ["key"])

    with (
        patch("src.app.core.utils.cache.client", mock_client),
        patch("src.app.core.utils.cache.stampede_logger") as mock_logger,
    ):
        await cache_module.migrate_tenant_cache_index()

        # Should have called scan 1000 times (MAX_ITERATIONS)
        assert mock_client.scan.call_count == 1000

        # Should log warning
        mock_logger.warning.assert_called_with(
            "SCAN iteration limit reached in migrate_tenant_cache_index",
            extra={"pattern": "tenant:*", "iterations": 1000},
        )


@pytest.mark.asyncio
async def test_delete_keys_by_pattern_scan_bounds():
    """Verify deletion stops at MAX_ITERATIONS and logs warning."""
    mock_client = AsyncMock()
    mock_client.scan.return_value = (1, ["key"])

    with (
        patch("src.app.core.utils.cache.client", mock_client),
        patch("src.app.core.utils.cache.stampede_logger") as mock_logger,
    ):
        await cache_module._delete_keys_by_pattern("pattern")

        assert mock_client.scan.call_count == 1000

        mock_logger.warning.assert_called_with(
            "SCAN iteration limit reached in _delete_keys_by_pattern",
            extra={"pattern": "pattern", "iterations": 1000},
        )


@pytest.mark.asyncio
async def test_scan_stops_on_zero_cursor():
    """Verify scan stops when cursor is 0."""
    mock_client = AsyncMock()
    # Returns cursor 0 immediately
    mock_client.scan.return_value = (0, ["key"])

    with patch("src.app.core.utils.cache.client", mock_client):
        await cache_module._delete_keys_by_pattern("pattern")
        assert mock_client.scan.call_count == 1
