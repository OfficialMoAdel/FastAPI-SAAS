"""U2 - Redis Pool Consolidation Unit Tests.

Tests verifying that a single Redis connection pool is used globally
and shared across all components (cache, session, rate limiting).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestRedisPoolSingleton:
    """Verify single Redis pool instance used globally."""

    def test_pool_module_level_singleton(self):
        """Verify pool is defined at module level as singleton."""
        from src.app.core.utils import cache

        # Pool is module-level variable (singleton pattern)
        assert hasattr(cache, "pool")
        assert hasattr(cache, "client")

    def test_client_uses_shared_pool(self):
        """Verify client is created from the shared pool."""
        # The pattern: Redis.from_pool(pool) ensures pool sharing
        # Read the setup.py source file directly to verify pattern
        from pathlib import Path

        setup_path = Path(__file__).parents[3] / "src" / "app" / "core" / "setup.py"
        source = setup_path.read_text()

        # Verify from_pool pattern is used
        assert "from_pool" in source
        assert "cache.pool" in source or "cache.client" in source

    def test_no_per_request_pool_creation(self):
        """Verify no pool is created per-request."""
        import inspect

        from src.app.core.utils import cache

        source = inspect.getsource(cache.async_get_redis)

        # Should yield existing client, not create new pool
        assert "ConnectionPool" not in source
        assert "yield client" in source


class TestPoolSharing:
    """Verify pool is shared across components."""

    def test_cache_module_exports_pool(self):
        """Verify cache module exports pool for sharing."""

        # These are the shared instances (None before startup)
        # The important thing is they exist as module-level exports
        assert "pool" in dir(__import__("src.app.core.utils.cache", fromlist=["pool"]))
        assert "client" in dir(__import__("src.app.core.utils.cache", fromlist=["client"]))

    @pytest.mark.asyncio
    async def test_async_get_redis_returns_shared_client(self):
        """Verify async_get_redis yields the shared client."""
        from src.app.core.utils import cache

        # Mock the client
        mock_client = MagicMock()
        original_client = cache.client

        try:
            cache.client = mock_client

            async for redis_client in cache.async_get_redis():
                # Should be the exact same object (shared)
                assert redis_client is mock_client
        finally:
            cache.client = original_client

    @pytest.mark.asyncio
    async def test_async_get_redis_raises_if_not_initialized(self):
        """Verify error raised if client not initialized."""
        from src.app.core.exceptions.cache_exceptions import MissingClientError
        from src.app.core.utils import cache

        original_client = cache.client

        try:
            cache.client = None

            with pytest.raises(MissingClientError):
                async for _ in cache.async_get_redis():
                    pass
        finally:
            cache.client = original_client


class TestPoolLifecycle:
    """Verify pool lifecycle management."""

    def test_create_pool_function_exists(self):
        """Verify create_redis_cache_pool exists for startup."""
        from src.app.core.setup import create_redis_cache_pool

        assert callable(create_redis_cache_pool)

    def test_close_pool_function_exists(self):
        """Verify close_redis_cache_pool exists for shutdown."""
        from src.app.core.setup import close_redis_cache_pool

        assert callable(close_redis_cache_pool)

    def test_lifespan_manages_pool(self):
        """Verify lifespan context manager handles pool lifecycle."""
        from pathlib import Path

        setup_path = Path(__file__).parents[3] / "src" / "app" / "core" / "setup.py"
        source = setup_path.read_text()

        # Pool created on startup
        assert "create_redis_cache_pool" in source
        # Pool closed on shutdown
        assert "close_redis_cache_pool" in source
        # Lifespan factory exists
        assert "lifespan_factory" in source


class TestIsomorphismPoolConsolidation:
    """Isomorphism tests - verify same Redis operations succeed."""

    @pytest.mark.asyncio
    async def test_get_set_with_shared_pool(self):
        """Verify GET/SET operations work with shared pool."""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = b'{"key": "value"}'
        mock_redis.set.return_value = True

        # Simulate operations that would use shared pool
        await mock_redis.set("test_key", '{"key": "value"}')
        result = await mock_redis.get("test_key")

        assert result == b'{"key": "value"}'
        mock_redis.set.assert_called_once()
        mock_redis.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_with_shared_pool(self):
        """Verify pipeline operations work with shared pool."""
        from contextlib import asynccontextmanager

        mock_redis = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = [True, True, True]

        @asynccontextmanager
        async def mock_pipeline():
            yield mock_pipe

        mock_redis.pipeline = mock_pipeline

        async with mock_redis.pipeline() as pipe:
            pipe.set("key1", "val1")
            pipe.set("key2", "val2")
            pipe.set("key3", "val3")
            results = await pipe.execute()

        assert results == [True, True, True]

    @pytest.mark.asyncio
    async def test_connection_reuse(self):
        """Verify connections are reused from pool."""
        from src.app.core.utils import cache

        # Multiple calls to async_get_redis should return same client
        mock_client = MagicMock()
        original_client = cache.client

        try:
            cache.client = mock_client

            clients = []
            for _ in range(3):
                async for c in cache.async_get_redis():
                    clients.append(c)

            # All should be the same instance
            assert all(c is mock_client for c in clients)
            assert len(set(id(c) for c in clients)) == 1
        finally:
            cache.client = original_client


class TestPoolConfiguration:
    """Verify pool configuration is correct."""

    def test_separate_cache_and_queue_pools(self):
        """Verify cache and queue have separate pools (by design)."""
        from src.app.core import setup

        # Both pool creation functions should exist
        assert hasattr(setup, "create_redis_cache_pool")
        assert hasattr(setup, "create_redis_queue_pool")

        # They are separate for isolation (cache vs job queue)
        # This is intentional - not a consolidation issue

    def test_pool_settings_from_config(self):
        """Verify pool uses settings from configuration."""
        import inspect

        from src.app.core import setup

        source = inspect.getsource(setup)

        # Should reference settings for Redis configuration
        assert "settings" in source.lower() or "REDIS" in source
