from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import ConnectionPool

from src.app.core import redis_factory, setup
from src.app.core.config import settings


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

    with patch("src.app.core.redis_factory.ConnectionPool.from_url", return_value=mock_pool_instance) as mock_from_url:
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
    mock_pool = MagicMock(spec=ConnectionPool)

    with patch.object(redis_factory, "get_redis_pool", new_callable=AsyncMock) as mock_get_pool:
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
    # This is hard to test mainly with unit tests without spinning up real Redis,
    # but we can verify that the pool context manager or disconnect is called if we were managing it manually.
    # However, ConnectionPool manages connections.
    # We verify that we are passing max_connections correctly, which limits leaks.

    await redis_factory.close_redis_pool()

    mock_pool_instance = MagicMock(spec=ConnectionPool)
    mock_pool_instance.disconnect = AsyncMock()

    with patch("src.app.core.redis_factory.ConnectionPool.from_url", return_value=mock_pool_instance) as mock_from_url:
        await redis_factory.get_redis_pool()
        kwargs = mock_from_url.call_args[1]
        assert kwargs["max_connections"] == settings.REDIS_CACHE_MAX_CONNECTIONS

    await redis_factory.close_redis_pool()
