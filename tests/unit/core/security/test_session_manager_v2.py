"""Unit tests for SessionManager v2 (Redis ZSET + Hash implementation).

Tests the refactored session manager that uses Redis ZSET + Hash hybrid
with atomic Lua scripts for race-condition-free operations.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.session_manager import SessionManager


def _mock_eval_success(final_count: int = 1, evicted_data: list[bytes] | None = None) -> AsyncMock:
    """Create a mock eval that returns Lua success result.

    Lua returns: [b"success", b"<count>", <evicted_data_1>, ...]
    """
    result = [b"success", str(final_count).encode()]
    if evicted_data:
        result.extend(evicted_data)
    return AsyncMock(return_value=result)


def _mock_eval_already_exists() -> AsyncMock:
    """Create a mock eval that returns Lua already_exists result."""
    return AsyncMock(return_value=[b"already_exists"])


@pytest.fixture
def mock_redis():
    """Mock Redis client with ZSET + Hash operations."""
    redis = AsyncMock()
    # Hash operations
    redis.hexists = AsyncMock(return_value=False)
    redis.hlen = AsyncMock(return_value=0)
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.hdel = AsyncMock()
    redis.hget = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    redis.expire = AsyncMock()
    # ZSET operations
    redis.zcard = AsyncMock(return_value=0)
    redis.zadd = AsyncMock()
    redis.zrem = AsyncMock()
    redis.zpopmin = AsyncMock(return_value=[])
    # Scan operation (for migration)
    redis.scan = AsyncMock(return_value=(0, []))
    # Lua eval - default: success with 1 active session, no evictions
    redis.eval = _mock_eval_success(final_count=1)

    # Setup pipeline mock - still needed for invalidate_session, invalidate_all_sessions
    pipe_mock = MagicMock()
    pipe_mock.hset = MagicMock(return_value=pipe_mock)
    pipe_mock.hdel = MagicMock(return_value=pipe_mock)
    pipe_mock.zadd = MagicMock(return_value=pipe_mock)
    pipe_mock.zrem = MagicMock(return_value=pipe_mock)
    pipe_mock.delete = MagicMock(return_value=pipe_mock)
    pipe_mock.expire = MagicMock(return_value=pipe_mock)
    pipe_mock.execute = AsyncMock(return_value=[1, 1, 1, 1])

    async def aenter(*args):
        return pipe_mock

    async def aexit(*args):
        pass

    pipe_mock.__aenter__ = aenter
    pipe_mock.__aexit__ = aexit
    redis.pipeline = MagicMock(return_value=pipe_mock)

    return redis


@pytest.fixture
def session_manager(mock_redis):
    """SessionManager instance with mocked Redis."""
    manager = SessionManager(redis_client=mock_redis, max_sessions=5)
    return manager


class TestSessionManagerHashOperations:
    """Test that all operations use Redis ZSET + Hash commands."""

    @pytest.mark.asyncio
    async def test_create_session_uses_eval(self, session_manager, mock_redis):
        """Verify register_session uses Lua eval for atomic operations."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"

        # Act
        result = await session_manager.register_session(user_id, session_id)

        # Assert
        assert result["registered"] is True
        # Verify Lua eval was called (atomic operation)
        mock_redis.eval.assert_called_once()

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
    async def test_invalidate_session_uses_hdel_and_zrem(self, session_manager, mock_redis):
        """Verify invalidate_session uses HDEL and ZREM via pipeline."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"
        mock_redis.hexists.return_value = True

        # Act
        result = await session_manager.invalidate_session(user_id, session_id)

        # Assert
        assert result is True
        mock_redis.hexists.assert_called_once()
        # Verify pipeline was used for atomic HDEL + ZREM
        pipe = mock_redis.pipeline.return_value
        pipe.hdel.assert_called()
        pipe.zrem.assert_called()
        pipe.execute.assert_called()

    @pytest.mark.asyncio
    async def test_count_sessions_uses_zcard_with_hlen_fallback(self, session_manager, mock_redis):
        """Verify count_active_sessions uses ZCARD (O(1)) with HLEN fallback."""
        # Arrange
        user_id = "user123"
        # ZCARD returns 0 (no ZSET yet), HLEN returns 3 (fallback)
        mock_redis.zcard.return_value = 0
        mock_redis.hlen.return_value = 3

        # Act
        count = await session_manager.count_active_sessions(user_id)

        # Assert
        assert count == 3
        mock_redis.zcard.assert_called_once()
        mock_redis.hlen.assert_called_once()  # Fallback triggered

    @pytest.mark.asyncio
    async def test_count_sessions_uses_zcard_directly(self, session_manager, mock_redis):
        """Verify count_active_sessions uses ZCARD directly when available."""
        # Arrange
        user_id = "user123"
        mock_redis.zcard.return_value = 5  # ZSET has data

        # Act
        count = await session_manager.count_active_sessions(user_id)

        # Assert
        assert count == 5
        mock_redis.zcard.assert_called_once()
        mock_redis.hlen.assert_not_called()  # No fallback needed

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


class TestSessionManagerRaceConditionFix:
    """Test that concurrent operations don't cause race conditions."""

    @pytest.mark.asyncio
    async def test_concurrent_session_create_no_race(self):
        """Create 10 sessions concurrently, verify all registered.

        With Lua scripts, all operations are atomic so no race conditions.
        """
        # Arrange - create fresh mock with proper setup
        mock_redis = AsyncMock()
        mock_redis.hexists = AsyncMock(return_value=False)
        # Lua eval returns success for each call
        mock_redis.eval = _mock_eval_success(final_count=1)

        manager = SessionManager(redis_client=mock_redis, max_sessions=10)
        user_id = "user123"

        # Act - create 10 sessions concurrently
        tasks = []
        for i in range(10):
            session_id = f"session_{i}"
            tasks.append(manager.register_session(user_id, session_id))

        results = await asyncio.gather(*tasks)

        # Assert - all sessions registered
        assert all(r["registered"] for r in results)
        # Verify eval was used for atomic operations (1 call per session)
        assert mock_redis.eval.call_count == 10

    @pytest.mark.asyncio
    async def test_no_deserialization_on_create(self, session_manager, mock_redis):
        """Verify register_session uses eval (no separate GET before SET)."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"

        # Act
        await session_manager.register_session(user_id, session_id)

        # Assert - no GET operation (Lua script handles everything atomically)
        mock_redis.get.assert_not_called()
        # eval should be called (atomic Lua script)
        mock_redis.eval.assert_called()


class TestSessionManagerTTL:
    """Test that session TTL is properly maintained."""

    @pytest.mark.asyncio
    async def test_session_ttl_set_via_lua(self, session_manager, mock_redis):
        """Verify EXPIRE is called within Lua script for both ZSET and Hash keys.

        The Lua script atomically sets TTL on both keys, so we verify
        the eval was called (which handles EXPIRE internally).
        """
        # Arrange
        user_id = "user123"
        session_id = "session_abc"

        # Act
        result = await session_manager.register_session(user_id, session_id)

        # Assert - eval was called (TTL is set atomically inside Lua)
        assert result["registered"] is True
        mock_redis.eval.assert_called_once()


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
                f'{{"session_id": "{session_id}", "device_info": "{device_info}", "created_at": "2024-01-01T00:00:00"}}'
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
    """Test that session limit enforcement works correctly with Lua eviction.

    The Lua script handles eviction atomically within a single roundtrip.
    """

    @pytest.mark.asyncio
    async def test_session_limit_enforced_with_eviction(self, mock_redis):
        """Verify oldest sessions are evicted when limit is reached (via Lua)."""
        # Arrange
        evicted_session_data = b'{"session_id": "session1", "created_at": "2024-01-01T00:00:00"}'
        mock_redis.eval = _mock_eval_success(
            final_count=3,
            evicted_data=[evicted_session_data],
        )

        manager = SessionManager(redis_client=mock_redis, max_sessions=3)
        user_id = "user123"

        # Act - register a 4th session (triggers eviction in Lua)
        result = await manager.register_session(user_id, "session4")

        # Assert
        assert result["registered"] is True
        assert len(result["invalidated_sessions"]) == 1
        assert result["invalidated_sessions"][0]["session_id"] == "session1"

        # Verify eval was called (atomic eviction + registration)
        mock_redis.eval.assert_called_once()


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
        # Verify pipeline was not used (early return)
        pipe = mock_redis.pipeline.return_value
        pipe.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidate_all_sessions_deletes_both_keys(self, session_manager, mock_redis):
        """Verify invalidate_all_sessions deletes both Hash and ZSET."""
        # Arrange
        user_id = "user123"
        mock_redis.hlen.return_value = 5

        # Act
        count = await session_manager.invalidate_all_sessions(user_id)

        # Assert
        assert count == 5
        # Verify pipeline was used
        pipe = mock_redis.pipeline.return_value
        # Delete should be called twice (Hash and ZSET)
        assert pipe.delete.call_count == 2
        pipe.execute.assert_called_once()


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
    async def test_is_session_valid_without_redis_fails_closed(self):
        """Verify fail-closed behavior when Redis is unavailable for security.

        SECURITY: When Redis is unavailable, sessions are rejected to prevent
        invalidated sessions from being accepted during outages.
        """
        # Arrange
        manager = SessionManager(redis_client=None)

        # Act & Assert - should fail closed (return False) for security
        is_valid = await manager.is_session_valid("user123", "session_abc")
        assert is_valid is False  # Fail closed for security

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
    async def test_register_existing_session_returns_already_exists(self, mock_redis):
        """Verify registering same session twice returns already_exists."""
        # Arrange
        user_id = "user123"
        session_id = "session_abc"
        # Lua returns already_exists when HEXISTS is true
        mock_redis.eval = _mock_eval_already_exists()

        manager = SessionManager(redis_client=mock_redis, max_sessions=5)

        # Act
        result = await manager.register_session(user_id, session_id)

        # Assert
        assert result["registered"] is True
        assert result.get("already_exists") is True


class TestSessionManagerZSETMigration:
    """Test ZSET migration functionality."""

    @pytest.mark.asyncio
    async def test_migrate_sessions_to_zset(self):
        """Verify migration populates ZSET from existing Hash data."""
        # Arrange - create fresh mock with proper scan setup
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock(return_value=(0, ["user_sessions_v2:123"]))
        mock_redis.hgetall = AsyncMock(
            return_value={
                "sess1": '{"session_id": "sess1", "created_at": "2024-01-01T00:00:00+00:00"}',
                "sess2": '{"session_id": "sess2", "created_at": "2024-01-01T01:00:00+00:00"}',
            }
        )

        # Setup pipeline mock
        pipe_mock = MagicMock()
        pipe_mock.zadd = MagicMock(return_value=pipe_mock)
        pipe_mock.expire = MagicMock(return_value=pipe_mock)
        pipe_mock.execute = AsyncMock(return_value=[1, 1])

        async def aenter(*args):
            return pipe_mock

        async def aexit(*args):
            pass

        pipe_mock.__aenter__ = aenter
        pipe_mock.__aexit__ = aexit
        mock_redis.pipeline = MagicMock(return_value=pipe_mock)

        manager = SessionManager(redis_client=mock_redis)

        # Act
        result = await manager.migrate_sessions_to_zset()

        # Assert
        assert result["migrated"] == 1
        assert len(result["errors"]) == 0
        # Verify ZADD was called via pipeline
        pipe_mock.zadd.assert_called()
        pipe_mock.expire.assert_called()


class TestSessionManagerDegradedMode:
    """Test degraded mode when Redis is unavailable but JWT exp is provided."""

    @pytest.mark.asyncio
    @patch("src.app.core.config.settings")
    async def test_degraded_mode_accepts_valid_jwt(self, mock_settings):
        """When Redis is down and JWT is still valid, accept the session ONLY IF fail_open is configured."""
        import time

        mock_settings.auth.AUDIT_LOG_FAILURE_MODE = "fail_open"
        manager = SessionManager(redis_client=None)
        future_exp = time.time() + 3600  # 1 hour from now

        is_valid = await manager.is_session_valid("user123", "session_abc", jwt_exp=future_exp)
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_degraded_mode_rejects_valid_jwt_when_fail_closed(self):
        """When Redis is down and JWT is valid, reject the session if configured to fail closed (default)."""
        import time

        manager = SessionManager(redis_client=None)
        future_exp = time.time() + 3600  # 1 hour from now

        is_valid = await manager.is_session_valid("user123", "session_abc", jwt_exp=future_exp)
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_degraded_mode_rejects_expired_jwt(self):
        """When Redis is down and JWT is expired, reject the session."""
        import time

        manager = SessionManager(redis_client=None)
        past_exp = time.time() - 60  # 1 minute ago

        is_valid = await manager.is_session_valid("user123", "session_abc", jwt_exp=past_exp)
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_no_jwt_exp_preserves_fail_closed(self):
        """Without jwt_exp, the original fail-closed behavior is preserved."""
        manager = SessionManager(redis_client=None)

        is_valid = await manager.is_session_valid("user123", "session_abc")
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_redis_available_ignores_jwt_exp(self, session_manager, mock_redis):
        """When Redis IS available, jwt_exp is ignored — normal HEXISTS path."""
        import time

        mock_redis.hexists.return_value = True
        future_exp = time.time() + 3600

        is_valid = await session_manager.is_session_valid("user123", "session_abc", jwt_exp=future_exp)
        assert is_valid is True
        mock_redis.hexists.assert_called_once()
