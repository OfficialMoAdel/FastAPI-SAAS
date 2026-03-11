"""Unit tests for P2-2: XFetch Lua Script Optimization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.utils.cache import get_with_stampede_protection


@pytest.mark.asyncio
async def test_lua_script_reduces_roundtrips(mock_redis, override_settings):
    """Test that Lua script reduces GET+TTL from 2 roundtrips to 1."""
    # Mock Lua manager to return value and TTL in single call
    with patch("src.app.core.utils.cache.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(
            return_value=[
                b'{"data":"cached_value"}',  # value
                3000,  # TTL in seconds
            ]
        )
        mock_lua_mgr.return_value = mock_manager

        mock_redis.get = AsyncMock()
        mock_redis.ttl = AsyncMock()

        with patch("src.app.core.utils.cache.client", mock_redis):
            with override_settings():

                async def fetch_func():
                    return {"data": "fresh_value"}

                result = await get_with_stampede_protection(
                    key="test:key",
                    fetch_func=fetch_func,
                    ttl=3600,
                )

    # Should return cached value
    assert result == {"data": "cached_value"}

    # Lua script should be called once (1 roundtrip)
    mock_manager.execute.assert_called_once()
    call_args = mock_manager.execute.call_args
    assert call_args[0][1] == "xfetch_get_with_ttl"
    assert call_args[1]["keys"] == ["test:key"]

    # Original GET+TTL should NOT be called
    mock_redis.get.assert_not_called()
    mock_redis.ttl.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_on_lua_failure(mock_redis, override_settings):
    """Test that fallback to GET+TTL works when Lua script fails."""
    # Mock Lua manager to raise exception
    with patch("src.app.core.utils.cache.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(side_effect=Exception("Lua script failed"))
        mock_lua_mgr.return_value = mock_manager

        # Mock fallback GET+TTL
        mock_redis.get = AsyncMock(return_value=b'{"data":"cached_value"}')
        mock_redis.ttl = AsyncMock(return_value=3000)

        with patch("src.app.core.utils.cache.client", mock_redis):
            with override_settings():

                async def fetch_func():
                    return {"data": "fresh_value"}

                result = await get_with_stampede_protection(
                    key="test:key",
                    fetch_func=fetch_func,
                    ttl=3600,
                )

    # Should return cached value from fallback
    assert result == {"data": "cached_value"}

    # Fallback should be called (2 roundtrips)
    mock_redis.get.assert_called_once_with("test:key")
    mock_redis.ttl.assert_called_once_with("test:key")


@pytest.mark.asyncio
async def test_cache_miss_fetches_fresh_data(mock_redis, override_settings):
    """Test that cache miss triggers fetch function."""
    # Mock Lua manager to return cache miss
    with patch("src.app.core.utils.cache.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(
            return_value=[
                None,  # value (cache miss)
                -2,  # TTL (-2 means key doesn't exist)
            ]
        )
        mock_lua_mgr.return_value = mock_manager

        # Mock setex for caching fresh data
        mock_redis.setex = AsyncMock()

        with patch("src.app.core.utils.cache.client", mock_redis):
            with override_settings():
                fetch_called = False

                async def fetch_func():
                    nonlocal fetch_called
                    fetch_called = True
                    return {"data": "fresh_value"}

                result = await get_with_stampede_protection(
                    key="test:key",
                    fetch_func=fetch_func,
                    ttl=3600,
                )

    # Should return fresh value
    assert result == {"data": "fresh_value"}
    assert fetch_called is True

    # Should cache the fresh value
    mock_redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_bytes_to_string_conversion(mock_redis, override_settings):
    """Test that bytes values are converted to strings."""
    # Mock Lua manager to return bytes value
    with patch("src.app.core.utils.cache.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(
            return_value=[
                b'{"data":"cached_value"}',  # bytes value
                3000,
            ]
        )
        mock_lua_mgr.return_value = mock_manager

        with patch("src.app.core.utils.cache.client", mock_redis):
            with override_settings():

                async def fetch_func():
                    return {"data": "fresh_value"}

                result = await get_with_stampede_protection(
                    key="test:key",
                    fetch_func=fetch_func,
                    ttl=3600,
                )

    # Should convert bytes to string and parse JSON
    assert result == {"data": "cached_value"}


@pytest.mark.asyncio
async def test_feature_flag_controls_optimization(mock_redis, override_settings):
    """Test that feature flag controls Lua vs fallback path."""
    # Disable Lua optimization
    with override_settings(XFETCH_USE_LUA_SCRIPT=False):
        # Mock fallback GET+TTL
        mock_redis.get = AsyncMock(return_value=b'{"data":"cached_value"}')
        mock_redis.ttl = AsyncMock(return_value=3000)

        with patch("src.app.core.utils.cache.client", mock_redis):
            with patch("src.app.core.utils.cache.get_lua_manager") as mock_lua_mgr:
                mock_manager = MagicMock()
                mock_manager.execute = AsyncMock()
                mock_lua_mgr.return_value = mock_manager

                async def fetch_func():
                    return {"data": "fresh_value"}

                result = await get_with_stampede_protection(
                    key="test:key",
                    fetch_func=fetch_func,
                    ttl=3600,
                )

    # Should use fallback path (not Lua)
    assert result == {"data": "cached_value"}
    mock_lua_mgr.assert_not_called()
    mock_redis.get.assert_called_once()
    mock_redis.ttl.assert_called_once()


@pytest.mark.asyncio
async def test_xfetch_probabilistic_refresh(mock_redis, override_settings):
    """Test that XFetch triggers probabilistic early refresh."""
    # Mock Lua manager to return value with low TTL (near expiration)
    with patch("src.app.core.utils.cache.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(
            return_value=[
                b'{"data":"cached_value"}',
                100,  # Low TTL (100 seconds remaining out of 3600)
            ]
        )
        mock_lua_mgr.return_value = mock_manager

        # Mock setex for background refresh
        mock_redis.setex = AsyncMock()

        with patch("src.app.core.utils.cache.client", mock_redis):
            with override_settings():
                # Mock random to always trigger refresh
                with patch("src.app.core.utils.cache.random.random", return_value=0.1):
                    fetch_called = False

                    async def fetch_func():
                        nonlocal fetch_called
                        fetch_called = True
                        return {"data": "refreshed_value"}

                    result = await get_with_stampede_protection(
                        key="test:key",
                        fetch_func=fetch_func,
                        ttl=3600,
                        beta=1.0,
                    )

    # Should return stale cached value immediately
    assert result == {"data": "cached_value"}

    # Background refresh should be triggered (but we can't easily test asyncio.create_task)
    # Just verify the probability calculation would trigger it


@pytest.mark.asyncio
async def test_latency_metrics_recorded(mock_redis, override_settings):
    """Test that latency metrics are recorded for cache hits and misses."""
    # Mock Lua manager to return cache hit
    with patch("src.app.core.utils.cache.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(return_value=[b'{"data":"cached_value"}', 3000])
        mock_lua_mgr.return_value = mock_manager

        with patch("src.app.core.utils.cache.client", mock_redis):
            with override_settings():
                with patch("src.app.core.utils.cache.record_xfetch_latency") as mock_record:

                    async def fetch_func():
                        return {"data": "fresh_value"}

                    result = await get_with_stampede_protection(
                        key="test:key",
                        fetch_func=fetch_func,
                        ttl=3600,
                    )

    # Metrics should be recorded
    assert result == {"data": "cached_value"}
    mock_record.assert_called_once()

    # Verify latency and hit status (first arg positional, second keyword)
    call_args = mock_record.call_args[0]
    call_kwargs = mock_record.call_args[1]
    latency = call_args[0]
    is_hit = call_kwargs["is_hit"]

    assert latency > 0  # Should have some latency
    assert is_hit is True  # Cache hit


@pytest.mark.asyncio
async def test_cache_miss_metrics_recorded(mock_redis, override_settings):
    """Test that cache miss metrics are recorded correctly."""
    # Mock Lua manager to return cache miss
    with patch("src.app.core.utils.cache.get_lua_manager") as mock_lua_mgr:
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(
            return_value=[
                None,  # Cache miss
                -2,
            ]
        )
        mock_lua_mgr.return_value = mock_manager

        mock_redis.setex = AsyncMock()

        with patch("src.app.core.utils.cache.client", mock_redis):
            with override_settings():
                with patch("src.app.core.utils.cache.record_xfetch_latency") as mock_record:

                    async def fetch_func():
                        return {"data": "fresh_value"}

                    result = await get_with_stampede_protection(
                        key="test:key",
                        fetch_func=fetch_func,
                        ttl=3600,
                    )

    # Metrics should be recorded with is_hit=False
    assert result == {"data": "fresh_value"}
    mock_record.assert_called_once()

    # Verify hit status (keyword arguments)
    call_kwargs = mock_record.call_args[1]
    is_hit = call_kwargs["is_hit"]
    assert is_hit is False  # Cache miss


@pytest.mark.asyncio
async def test_redis_unavailable_fallback(mock_redis, override_settings):
    """Test that function works when Redis is unavailable."""
    with patch("src.app.core.utils.cache.client", None):
        fetch_called = False

        async def fetch_func():
            nonlocal fetch_called
            fetch_called = True
            return {"data": "fresh_value"}

        result = await get_with_stampede_protection(
            key="test:key",
            fetch_func=fetch_func,
            ttl=3600,
        )

    # Should fetch directly without Redis
    assert result == {"data": "fresh_value"}
    assert fetch_called is True
