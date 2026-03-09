"""Unit tests for SessionManager ZSET + Hash implementation.

Tests the performance-optimized session manager using ZSET for O(log n) eviction.
"""

from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from src.app.core.security.session_manager import SessionManager


def _acm(pipe):
    """Make pipeline mock support 'async with' by returning itself on __aenter__."""
    async def __aenter__(*a): return pipe
    async def __aexit__(*a): return False
    pipe.__aenter__ = __aenter__
    pipe.__aexit__ = __aexit__
    return pipe


@pytest.fixture
def mock_pipeline():
    """Create a mock pipeline with sync methods and async execute."""
    pipe = MagicMock()
    _acm(pipe)
    pipe.hdel = MagicMock()
    pipe.hset = MagicMock()
    pipe.zadd = MagicMock()
    pipe.zrem = MagicMock()
    pipe.delete = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, 1, 1, 1])
    return pipe


@pytest.fixture
def mock_redis(mock_pipeline):
    """Mock Redis with ZSET + Hash operations."""
    redis = AsyncMock()
    redis.pipeline = MagicMock(return_value=mock_pipeline)
    redis.hexists = AsyncMock(return_value=False)
    redis.zcard = AsyncMock(return_value=0)
    redis.hlen = AsyncMock(return_value=0)
    redis.zpopmin = AsyncMock(return_value=[])
    redis.hget = AsyncMock(return_value=None)
    return redis


@pytest.fixture
def session_manager(mock_redis):
    return SessionManager(redis_client=mock_redis)


@pytest.mark.asyncio
async def test_register_session_zset_hash_write(session_manager, mock_redis, mock_pipeline):
    """Verify that registering a session writes to both ZSET and Hash using pipeline."""
    user_id = 123
    session_id = "sess-zset-1"
    device_info = "ZSET Device"

    await session_manager.register_session(user_id, session_id, device_info)

    # Verify pipeline was used
    mock_redis.pipeline.assert_called_once()

    # Verify ZADD on pipeline (for ordering)
    zset_key = f"user_sessions_order:{user_id}"
    mock_pipeline.zadd.assert_called_with(zset_key, {session_id: ANY})

    # Verify HSET on pipeline (for metadata)
    hash_key = f"user_sessions_v2:{user_id}"
    mock_pipeline.hset.assert_called_with(hash_key, session_id, ANY)

    # Verify Expiry on both keys
    assert mock_pipeline.expire.call_count == 2
    expire_calls = [call[0] for call in mock_pipeline.expire.call_args_list]
    assert any(zset_key in str(call) for call in expire_calls)
    assert any(hash_key in str(call) for call in expire_calls)

    # Verify pipeline executed
    mock_pipeline.execute.assert_called_once()


@pytest.mark.asyncio
async def test_is_session_valid_hash(session_manager, mock_redis):
    """Verify session validation using Hash HEXISTS (O(1))."""
    user_id = 123
    session_id = "sess-valid"
    mock_redis.hexists.return_value = True

    is_valid = await session_manager.is_session_valid(user_id, session_id)

    assert is_valid is True
    mock_redis.hexists.assert_called_once()
    call_args = mock_redis.hexists.call_args[0]
    assert "user_sessions_v2:" in call_args[0]
    assert call_args[1] == session_id


@pytest.mark.asyncio
async def test_invalidate_session_zset_hash_delete(session_manager, mock_redis, mock_pipeline):
    """Verify invalidation removes from both ZSET and Hash atomically."""
    user_id = 123
    session_id = "sess-to-delete"
    mock_redis.hexists.return_value = True

    result = await session_manager.invalidate_session(user_id, session_id)

    assert result is True

    # Verify pipeline was used for atomic delete
    mock_redis.pipeline.assert_called()

    # Verify HDEL on pipeline
    hash_key = f"user_sessions_v2:{user_id}"
    mock_pipeline.hdel.assert_called_with(hash_key, session_id)

    # Verify ZREM on pipeline
    zset_key = f"user_sessions_order:{user_id}"
    mock_pipeline.zrem.assert_called_with(zset_key, session_id)


@pytest.mark.asyncio
async def test_count_sessions_zcard(session_manager, mock_redis):
    """Verify O(1) counting using ZCARD."""
    user_id = 123
    mock_redis.zcard.return_value = 5

    count = await session_manager.count_active_sessions(user_id)

    assert count == 5
    mock_redis.zcard.assert_called_once()
    call_args = mock_redis.zcard.call_args[0]
    assert "user_sessions_order:" in call_args[0]


@pytest.mark.asyncio
async def test_count_sessions_hlen_fallback(session_manager, mock_redis):
    """Verify fallback to HLEN when ZSET doesn't exist (migration case)."""
    user_id = 123
    mock_redis.zcard.return_value = 0  # No ZSET yet
    mock_redis.hlen.return_value = 3  # Hash has data

    count = await session_manager.count_active_sessions(user_id)

    assert count == 3
    mock_redis.zcard.assert_called_once()
    mock_redis.hlen.assert_called_once()


@pytest.mark.asyncio
async def test_eviction_uses_zpopmin(mock_redis, mock_pipeline):
    """Verify O(log n) eviction using ZPOPMIN instead of HGETALL+sort (legacy path).

    Note: This tests the legacy ZPOPMIN path. When SESSION_USE_PIPELINE_EVICTION
    is True (default in R2-HYBRID), the pipelined ZRANGE path is used instead.
    """
    from unittest.mock import patch

    with patch("src.app.core.security.session_manager.settings") as mock_settings:
        mock_settings.SESSION_USE_PIPELINE_EVICTION = False
        mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

        manager = SessionManager(redis_client=mock_redis, max_sessions=2)
        user_id = 456

        # At limit
        mock_redis.zcard.return_value = 2

        # ZPOPMIN returns oldest session
        mock_redis.zpopmin.return_value = [("old-session", 1704067200.0)]
        mock_redis.hget.return_value = '{"session_id": "old-session", "created_at": "2024-01-01T00:00:00"}'

        result = await manager.register_session(user_id, "new-session")

        # Verify ZPOPMIN was called (O(log n) eviction)
        mock_redis.zpopmin.assert_called()

        # Verify old session was evicted
        assert len(result["invalidated_sessions"]) == 1
        assert result["invalidated_sessions"][0]["session_id"] == "old-session"

        # Verify HDEL was called for old session
        mock_pipeline.hdel.assert_called()
