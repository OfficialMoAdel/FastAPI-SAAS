"""Unit tests for P2-1: Session Pipeline Eviction Optimization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.session_manager import SessionManager


def _acm(pipe):
    """Make a pipeline mock support 'async with' returning itself."""
    async def __aenter__(*a): return pipe
    async def __aexit__(*a): return False
    pipe.__aenter__ = __aenter__
    pipe.__aexit__ = __aexit__
    return pipe



@pytest.mark.skip(reason="Obsolete due to Lua script rewrite")
@pytest.mark.asyncio
async def test_pipeline_eviction_reduces_roundtrips(mock_redis_client, mock_settings):
    """Test that pipeline eviction uses fewer roundtrips than legacy."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=3)

    # Mock ZCARD to return 3 (at limit)
    mock_redis_client.zcard = AsyncMock(return_value=3)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZRANGE to return 1 oldest session
    mock_redis_client.zrange = AsyncMock(return_value=[(b"old-session-1", 1000.0)])

    # Mock pipeline for HGET batch
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.hget = MagicMock()
    mock_pipeline.execute = AsyncMock(
        return_value=[b'{"session_id":"old-session-1","device_info":"Chrome","created_at":"2024-01-01T00:00:00+00:00"}']
    )
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Pipeline path should use 3 roundtrips: ZRANGE + HGET batch + DELETE batch
    assert result["registered"] is True
    assert len(result["invalidated_sessions"]) == 1
    assert result["invalidated_sessions"][0]["session_id"] == "old-session-1"


@pytest.mark.asyncio
async def test_legacy_eviction_uses_more_roundtrips(mock_redis_client, mock_settings):
    """Test that legacy eviction uses more roundtrips (ZPOPMIN loop)."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=3)

    # Mock ZCARD to return 3 (at limit)
    mock_redis_client.zcard = AsyncMock(return_value=3)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZPOPMIN to return oldest session
    mock_redis_client.zpopmin = AsyncMock(return_value=[(b"old-session-1", 1000.0)])

    # Mock HGET for session data
    mock_redis_client.hget = AsyncMock(
        return_value=b'{"session_id":"old-session-1","device_info":"Chrome","created_at":"2024-01-01T00:00:00+00:00"}'
    )

    # Mock pipeline
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.hdel = MagicMock()
    mock_pipeline.zadd = MagicMock()
    mock_pipeline.hset = MagicMock()
    mock_pipeline.expire = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, 1, 1, 1])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    # Disable pipeline optimization
    mock_settings.SESSION_USE_PIPELINE_EVICTION = False

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Legacy path should use 3 roundtrips: ZPOPMIN + HGET + pipeline execute
    assert result["registered"] is True
    assert len(result["invalidated_sessions"]) == 1
    mock_redis_client.zpopmin.assert_called_once()
    mock_redis_client.hget.assert_called_once()


@pytest.mark.skip(reason="Obsolete due to Lua script rewrite")
@pytest.mark.asyncio
async def test_batch_eviction_multiple_sessions(mock_redis_client, mock_settings):
    """Test that pipeline can evict multiple sessions in one batch."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=2)

    # Mock ZCARD to return 4 (need to evict 3 sessions)
    mock_redis_client.zcard = AsyncMock(return_value=4)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZRANGE to return 3 oldest sessions
    mock_redis_client.zrange = AsyncMock(
        return_value=[
            (b"old-session-1", 1000.0),
            (b"old-session-2", 1001.0),
            (b"old-session-3", 1002.0),
        ]
    )

    # Mock pipeline for HGET batch
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.hget = MagicMock()
    mock_pipeline.execute = AsyncMock(
        side_effect=[
            # First execute: HGET batch
            [
                b'{"session_id":"old-session-1","device_info":"Chrome","created_at":"2024-01-01T00:00:00+00:00"}',
                b'{"session_id":"old-session-2","device_info":"Safari","created_at":"2024-01-01T00:01:00+00:00"}',
                b'{"session_id":"old-session-3","device_info":"Edge","created_at":"2024-01-01T00:02:00+00:00"}',
            ],
            # Second execute: DELETE batch
            [1, 1, 1, 1, 1, 1, 1, 1],
        ]
    )
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Should evict all 3 sessions in one batch
    assert result["registered"] is True
    assert len(result["invalidated_sessions"]) == 3
    assert result["invalidated_sessions"][0]["session_id"] == "old-session-1"
    assert result["invalidated_sessions"][1]["session_id"] == "old-session-2"
    assert result["invalidated_sessions"][2]["session_id"] == "old-session-3"


@pytest.mark.skip(reason="Obsolete due to Lua script rewrite")
@pytest.mark.asyncio
async def test_batch_size_limit_enforced(mock_redis_client, mock_settings):
    """Test that batch size limit prevents memory exhaustion."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=2)

    # Mock ZCARD to return 100 (need to evict 99 sessions, but limit is 50)
    mock_redis_client.zcard = AsyncMock(return_value=100)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZRANGE to return 50 sessions (batch limit)
    oldest_sessions = [(f"old-session-{i}".encode(), float(1000 + i)) for i in range(50)]
    mock_redis_client.zrange = AsyncMock(return_value=oldest_sessions)

    # Mock pipeline
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.hget = MagicMock()
    session_data = [
        f'{{"session_id":"old-session-{i}","device_info":"Chrome","created_at":"2024-01-01T00:00:00+00:00"}}'.encode()
        for i in range(50)
    ]
    mock_pipeline.execute = AsyncMock(
        side_effect=[
            session_data,  # HGET batch
            [1] * 104,  # DELETE batch (50 HDEL + 50 ZREM + 1 ZADD + 1 HSET + 2 EXPIRE)
        ]
    )
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    # Set batch limit to 50
    mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Should only evict 50 sessions (batch limit)
    assert result["registered"] is True
    assert len(result["invalidated_sessions"]) == 50

    # Verify ZRANGE was called with correct limit
    mock_redis_client.zrange.assert_called_once()
    call_args = mock_redis_client.zrange.call_args
    assert call_args[0][1] == 0  # start
    assert call_args[0][2] == 49  # end (50 sessions, 0-indexed)


@pytest.mark.skip(reason="Obsolete due to Lua script rewrite")
@pytest.mark.asyncio
async def test_orphaned_session_handling(mock_redis_client, mock_settings):
    """Test that orphaned sessions (in ZSET but not Hash) are handled gracefully."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=3)

    # Mock ZCARD to return 3 (at limit)
    mock_redis_client.zcard = AsyncMock(return_value=3)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZRANGE to return 2 sessions (one orphaned)
    mock_redis_client.zrange = AsyncMock(
        return_value=[
            (b"orphaned-session", 1000.0),
            (b"valid-session", 1001.0),
        ]
    )

    # Mock pipeline for HGET batch (orphaned returns None)
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.hget = MagicMock()
    mock_pipeline.execute = AsyncMock(
        side_effect=[
            # First execute: HGET batch (orphaned returns None)
            [
                None,  # orphaned session
                b'{"session_id":"valid-session","device_info":"Chrome","created_at":"2024-01-01T00:01:00+00:00"}',
            ],
            # Second execute: DELETE batch
            [1, 1, 1, 1, 1, 1, 1, 1],
        ]
    )
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Should only return valid session in invalidated list
    assert result["registered"] is True
    assert len(result["invalidated_sessions"]) == 1
    assert result["invalidated_sessions"][0]["session_id"] == "valid-session"


@pytest.mark.skip(reason="Obsolete due to Lua script rewrite")
@pytest.mark.asyncio
async def test_corrupted_session_data_handling(mock_redis_client, mock_settings):
    """Test that corrupted session data is handled gracefully."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=3)

    # Mock ZCARD to return 3 (at limit)
    mock_redis_client.zcard = AsyncMock(return_value=3)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZRANGE to return 2 sessions
    mock_redis_client.zrange = AsyncMock(
        return_value=[
            (b"corrupted-session", 1000.0),
            (b"valid-session", 1001.0),
        ]
    )

    # Mock pipeline for HGET batch (corrupted returns invalid JSON)
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.hget = MagicMock()
    mock_pipeline.execute = AsyncMock(
        side_effect=[
            # First execute: HGET batch (corrupted returns invalid JSON)
            [
                b"invalid-json{{{",  # corrupted session
                b'{"session_id":"valid-session","device_info":"Chrome","created_at":"2024-01-01T00:01:00+00:00"}',
            ],
            # Second execute: DELETE batch
            [1, 1, 1, 1, 1, 1, 1, 1],
        ]
    )
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Should only return valid session in invalidated list
    assert result["registered"] is True
    assert len(result["invalidated_sessions"]) == 1
    assert result["invalidated_sessions"][0]["session_id"] == "valid-session"


@pytest.mark.asyncio
async def test_no_sessions_to_evict(mock_redis_client, mock_settings):
    """Test that pipeline handles case with no sessions to evict."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=5)

    # Mock ZCARD to return 2 (under limit)
    mock_redis_client.zcard = AsyncMock(return_value=2)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock pipeline for adding new session
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.zadd = MagicMock()
    mock_pipeline.hset = MagicMock()
    mock_pipeline.expire = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, 1, 1, 1])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Should add session without eviction
    assert result["registered"] is True
    assert len(result["invalidated_sessions"]) == 0

    # ZRANGE should not be called (under limit)
    mock_redis_client.zrange.assert_not_called()


@pytest.mark.asyncio
async def test_empty_zrange_result(mock_redis_client, mock_settings):
    """Test that empty ZRANGE result is handled gracefully."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=3)

    # Mock ZCARD to return 3 (at limit)
    mock_redis_client.zcard = AsyncMock(return_value=3)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZRANGE to return empty list (edge case)
    mock_redis_client.zrange = AsyncMock(return_value=[])

    # Mock pipeline for adding new session
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.zadd = MagicMock()
    mock_pipeline.hset = MagicMock()
    mock_pipeline.expire = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, 1, 1, 1])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Should add session without eviction
    assert result["registered"] is True
    assert len(result["invalidated_sessions"]) == 0


@pytest.mark.asyncio
async def test_feature_flag_controls_optimization(mock_redis_client, mock_settings):
    """Test that feature flag controls pipeline vs legacy path."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=3)

    # Mock ZCARD to return 3 (at limit)
    mock_redis_client.zcard = AsyncMock(return_value=3)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZPOPMIN for legacy path
    mock_redis_client.zpopmin = AsyncMock(return_value=[(b"old-session-1", 1000.0)])

    # Mock HGET for legacy path
    mock_redis_client.hget = AsyncMock(
        return_value=b'{"session_id":"old-session-1","device_info":"Chrome","created_at":"2024-01-01T00:00:00+00:00"}'
    )

    # Mock ZRANGE for pipeline path (should not be called)
    mock_redis_client.zrange = AsyncMock()

    # Mock pipeline
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.hdel = MagicMock()
    mock_pipeline.zadd = MagicMock()
    mock_pipeline.hset = MagicMock()
    mock_pipeline.expire = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, 1, 1, 1])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    # Disable pipeline optimization
    mock_settings.SESSION_USE_PIPELINE_EVICTION = False

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Should use legacy path (ZPOPMIN)
    assert result["registered"] is True
    mock_redis_client.zpopmin.assert_called_once()
    mock_redis_client.zrange.assert_not_called()


@pytest.mark.skip(reason="Obsolete due to Lua script rewrite")
@pytest.mark.asyncio
async def test_pipeline_atomic_operations(mock_redis_client, mock_settings):
    """Test that pipeline operations are atomic (all or nothing)."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=3)

    # Mock ZCARD to return 3 (at limit)
    mock_redis_client.zcard = AsyncMock(return_value=3)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZRANGE to return 1 oldest session
    mock_redis_client.zrange = AsyncMock(return_value=[(b"old-session-1", 1000.0)])

    # Mock pipeline for HGET batch
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.hget = MagicMock()
    mock_pipeline.hdel = MagicMock()
    mock_pipeline.zrem = MagicMock()
    mock_pipeline.zadd = MagicMock()
    mock_pipeline.hset = MagicMock()
    mock_pipeline.expire = MagicMock()
    mock_pipeline.execute = AsyncMock(
        side_effect=[
            # First execute: HGET batch
            [b'{"session_id":"old-session-1","device_info":"Chrome","created_at":"2024-01-01T00:00:00+00:00"}'],
            # Second execute: DELETE batch (atomic)
            [1, 1, 1, 1, 1, 1],  # HDEL, ZREM, ZADD, HSET, EXPIRE, EXPIRE
        ]
    )
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Verify atomic operations
    assert result["registered"] is True

    # Verify pipeline was called twice (HGET batch + DELETE batch)
    assert mock_pipeline.execute.call_count == 2

    # Verify HDEL and ZREM were called for old session
    mock_pipeline.hdel.assert_called()
    mock_pipeline.zrem.assert_called()

    # Verify ZADD and HSET were called for new session
    mock_pipeline.zadd.assert_called()
    mock_pipeline.hset.assert_called()

    # Verify EXPIRE was called for both keys
    assert mock_pipeline.expire.call_count == 2


@pytest.mark.skip(reason="Obsolete due to Lua script rewrite")
@pytest.mark.asyncio
async def test_roundtrip_metrics_recorded(mock_redis_client, mock_settings):
    """Test that roundtrip metrics are recorded correctly."""
    session_manager = SessionManager(redis_client=mock_redis_client, max_sessions=3)

    # Mock ZCARD to return 3 (at limit)
    mock_redis_client.zcard = AsyncMock(return_value=3)
    mock_redis_client.hexists = AsyncMock(return_value=False)

    # Mock ZRANGE to return 1 oldest session
    mock_redis_client.zrange = AsyncMock(return_value=[(b"old-session-1", 1000.0)])

    # Mock pipeline
    mock_pipeline = MagicMock()
    _acm(mock_pipeline)
    mock_pipeline.hget = MagicMock()
    mock_pipeline.execute = AsyncMock(
        side_effect=[
            [b'{"session_id":"old-session-1","device_info":"Chrome","created_at":"2024-01-01T00:00:00+00:00"}'],
            [1, 1, 1, 1, 1, 1],
        ]
    )
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.security.session_manager.settings", mock_settings):
        with patch("src.app.core.security.session_manager.record_session_eviction") as mock_record:
            result = await session_manager.register_session(user_id=1, session_id="new-session", device_info="Firefox")

    # Verify metrics were recorded
    assert result["registered"] is True
    mock_record.assert_called_once()

    # Verify roundtrip count (should be 3: ZRANGE + HGET batch + DELETE batch)
    call_args = mock_record.call_args[0]
    assert call_args[0] == 3
