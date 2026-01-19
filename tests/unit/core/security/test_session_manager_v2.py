"""Unit tests for SessionManager v2 (Redis Hash implementation).

Tests the refactored session manager that uses Redis Hashes instead of Lists.
Verifies O(1) operations and elimination of race conditions.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.session_manager import SessionManager


@pytest.fixture
def mock_redis():
    """Mock Redis client with Hash operations."""
    redis = AsyncMock()
    redis.hexists = AsyncMock(return_value=False)
    redis.hlen = AsyncMock(return_value=0)
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.hdel = AsyncMock()
    redis.delete = AsyncMock()
    redis.expire = AsyncMock()
    redis.pipeline = MagicMock()

    # Setup pipeline mock
    pipe_mock = AsyncMock()
    pipe_mock.hset = MagicMock(return_value=pipe_mock)
    pipe_mock.hdel = MagicMock(return_value=pipe_mock)
    pipe_mock.expire = MagicMock(return_value=pipe_mock)
    pipe_mock.execute = AsyncMock()
    redis.pipeline.return_value = pipe_mock

    return redis


@pytest.fixture
def session_manager(mock_redis):
    """SessionManager instance with mocked Redis."""
    manager = SessionManager(redis_client=mock_redis, max_sessions=5)
    return manager


class TestSessionManagerHashOperations:
    """Test that all operations use Redis Hash commands."""

    @pytest.mark.asyncio
    async def test_create_session_uses_hset(self, session_manager, mock_redis):
        """Verify register_session uses HSET, not SET."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"

        # Act
        result = await session_manager.register_session(user_id, session_id)

        # Assert
        assert result["registered"] is True
        # Verify pipeline was used with HSET
        mock_redis.pipeline.assert_called()
        pipe = mock_redis.pipeline.return_value
        pipe.hset.assert_called()
        pipe.expire.assert_called()
        pipe.execute.assert_called()

    @pytest.mark.asyncio
    async def test_get_sessions_uses_hgetall(self, session_manager, mock_redis):
        """Verify get_active_sessions uses HGETALL, not GET."""
        # Arrange
        user_id = "user123"
        mock_redis.hgetall.return_value = {
            "session1": '{"session_id": "session1", "device_info": "Chrome", "created_at": "2024-01-01T00:00:00"}',
            "session2": '{"session_id": "session2", "device_info": "Firefox", "created_at": "2024-01-01T01:00:00"}',
        }

        # Act
        sessions = await session_manager.get_active_sessions(user_id)

        # Assert
        assert len(sessions) == 2
        mock_redis.hgetall.assert_called_once()
        # Verify it used the v2 hash key
        call_args = mock_redis.hgetall.call_args[0][0]
        assert "user_sessions_v2:" in call_args

    @pytest.mark.asyncio
    async def test_invalidate_session_uses_hdel(self, session_manager, mock_redis):
        """Verify invalidate_session uses HDEL."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"
        mock_redis.hexists.return_value = True

        # Act
        result = await session_manager.invalidate_session(user_id, session_id)

        # Assert
        assert result is True
        mock_redis.hexists.assert_called_once()
        mock_redis.hdel.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_sessions_uses_hlen(self, session_manager, mock_redis):
        """Verify count_active_sessions uses HLEN (O(1))."""
        # Arrange
        user_id = "user123"
        mock_redis.hlen.return_value = 3

        # Act
        count = await session_manager.count_active_sessions(user_id)

        # Assert
        assert count == 3
        mock_redis.hlen.assert_called_once()
        # Verify no GET or deserialization happened
        mock_redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_session_valid_uses_hexists(self, session_manager, mock_redis):
        """Verify is_session_valid uses HEXISTS (O(1))."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"
        mock_redis.hexists.return_value = True

        # Act
        is_valid = await session_manager.is_session_valid(user_id, session_id)

        # Assert
        assert is_valid is True
        mock_redis.hexists.assert_called_once()
        # Verify no GET or deserialization happened
        mock_redis.get.assert_not_called()


class TestSessionManagerRaceConditionFix:
    """Test that concurrent operations don't cause race conditions."""

    @pytest.mark.asyncio
    async def test_concurrent_session_create_no_race(self, mock_redis):
        """Create 10 sessions concurrently, verify all exist."""
        # Arrange
        manager = SessionManager(redis_client=mock_redis, max_sessions=10)
        user_id = "user123"

        # Mock HLEN to return increasing counts
        # Each session calls hlen twice: once to check count, once to get final count
        hlen_values = []
        for i in range(10):
            hlen_values.append(i)  # Initial count check
            hlen_values.append(i + 1)  # Final count after insert
        mock_redis.hlen.side_effect = hlen_values

        # Act - create 10 sessions concurrently
        tasks = []
        for i in range(10):
            session_id = f"session_{i}"
            tasks.append(manager.register_session(user_id, session_id))

        results = await asyncio.gather(*tasks)

        # Assert - all sessions registered
        assert all(r["registered"] for r in results)
        # Verify pipeline was used (atomic operations)
        assert mock_redis.pipeline.call_count == 10

    @pytest.mark.asyncio
    async def test_no_deserialization_on_create(self, session_manager, mock_redis):
        """Verify register_session doesn't GET before SET (no race window)."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"

        # Act
        await session_manager.register_session(user_id, session_id)

        # Assert - no GET operation (only HLEN for count check)
        mock_redis.get.assert_not_called()
        # Only HLEN, HSET, EXPIRE should be called
        mock_redis.hlen.assert_called()


class TestSessionManagerTTL:
    """Test that session TTL is properly maintained."""

    @pytest.mark.asyncio
    async def test_session_ttl_preserved(self, session_manager, mock_redis):
        """Verify EXPIRE is called after HSET."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"

        # Act
        await session_manager.register_session(user_id, session_id)

        # Assert
        pipe = mock_redis.pipeline.return_value
        pipe.expire.assert_called()
        # Verify TTL value
        expire_call = pipe.expire.call_args[0]
        assert expire_call[1] == SessionManager.SESSION_TTL_SECONDS


class TestSessionManagerDataIntegrity:
    """Test that session data is preserved correctly."""

    @pytest.mark.asyncio
    async def test_session_data_integrity(self, session_manager, mock_redis):
        """Verify session data matches after round-trip."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"
        device_info = "Chrome on Windows"

        # Mock HGETALL to return the session we just created
        def mock_hgetall(key):
            session_json = (
                f'{{"session_id": "{session_id}", '
                f'"device_info": "{device_info}", '
                f'"created_at": "2024-01-01T00:00:00"}}'
            )
            return {session_id: session_json}

        mock_redis.hgetall.side_effect = mock_hgetall

        # Act
        await session_manager.register_session(user_id, session_id, device_info)
        sessions = await session_manager.get_active_sessions(user_id)

        # Assert
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == session_id
        assert sessions[0]["device_info"] == device_info


class TestSessionManagerSessionLimit:
    """Test that session limit enforcement works correctly."""

    @pytest.mark.asyncio
    async def test_session_limit_enforced(self, mock_redis):
        """Verify oldest sessions are removed when limit is reached."""
        # Arrange
        manager = SessionManager(redis_client=mock_redis, max_sessions=3)
        user_id = "user123"

        # Mock HLEN to return 3 (at limit)
        mock_redis.hlen.return_value = 3

        # Mock HGETALL to return 3 existing sessions
        mock_redis.hgetall.return_value = {
            "session1": '{"session_id": "session1", "created_at": "2024-01-01T00:00:00"}',
            "session2": '{"session_id": "session2", "created_at": "2024-01-01T01:00:00"}',
            "session3": '{"session_id": "session3", "created_at": "2024-01-01T02:00:00"}',
        }

        # Act - register a 4th session
        result = await manager.register_session(user_id, "session4")

        # Assert
        assert result["registered"] is True
        assert len(result["invalidated_sessions"]) == 1
        # Verify oldest session was invalidated
        assert result["invalidated_sessions"][0]["session_id"] == "session1"

        # Verify HDEL was called to remove old session
        pipe = mock_redis.pipeline.return_value
        pipe.hdel.assert_called()


class TestSessionManagerInvalidation:
    """Test session invalidation operations."""

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_session_returns_false(self, session_manager, mock_redis):
        """Verify invalidating non-existent session returns False."""
        # Arrange
        user_id = "user123"
        session_id = "nonexistent"
        mock_redis.hexists.return_value = False

        # Act
        result = await session_manager.invalidate_session(user_id, session_id)

        # Assert
        assert result is False
        mock_redis.hdel.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidate_all_sessions_uses_delete(self, session_manager, mock_redis):
        """Verify invalidate_all_sessions uses DELETE on hash."""
        # Arrange
        user_id = "user123"
        mock_redis.hlen.return_value = 5

        # Act
        count = await session_manager.invalidate_all_sessions(user_id)

        # Assert
        assert count == 5
        mock_redis.delete.assert_called_once()
        # Verify it deleted the v2 hash key
        call_args = mock_redis.delete.call_args[0][0]
        assert "user_sessions_v2:" in call_args


class TestSessionManagerRedisUnavailable:
    """Test behavior when Redis is unavailable."""

    @pytest.mark.asyncio
    async def test_register_session_without_redis(self):
        """Verify graceful handling when Redis is not configured."""
        # Arrange
        manager = SessionManager(redis_client=None)

        # Act
        result = await manager.register_session("user123", "session_abc")

        # Assert
        assert result["registered"] is False
        assert result["reason"] == "redis_unavailable"

    @pytest.mark.asyncio
    async def test_is_session_valid_without_redis_fails_open(self):
        """Verify fail-open behavior when Redis is unavailable."""
        # Arrange
        manager = SessionManager(redis_client=None)

        # Act
        with patch("src.app.core.security.session_manager.logger") as mock_logger:
            is_valid = await manager.is_session_valid("user123", "session_abc")

        # Assert
        assert is_valid is True  # Fail open
        mock_logger.critical.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_sessions_without_redis_returns_zero(self):
        """Verify count returns 0 when Redis is unavailable."""
        # Arrange
        manager = SessionManager(redis_client=None)

        # Act
        count = await manager.count_active_sessions("user123")

        # Assert
        assert count == 0


class TestSessionManagerExistingSessionCheck:
    """Test that duplicate session registration is handled correctly."""

    @pytest.mark.asyncio
    async def test_register_existing_session_returns_already_exists(self, session_manager, mock_redis):
        """Verify registering same session twice returns already_exists."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"
        mock_redis.hexists.return_value = True

        # Act
        result = await session_manager.register_session(user_id, session_id)

        # Assert
        assert result["registered"] is True
        assert result.get("already_exists") is True
        # Verify no HSET was called (session already exists)
        pipe = mock_redis.pipeline.return_value
        pipe.hset.assert_not_called()
