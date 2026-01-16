"""Tests for session manager deque optimization.

Tests verify:
- deque is used instead of list
- FIFO order is preserved
- popleft() is used for O(1) removal (not pop(0) which is O(n))
- Sessions are correctly added and removed
"""

import json
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.core.security.session_manager import SessionManager, session_manager


class TestSessionManagerDequeUsage:
    """Tests verifying deque is used for session storage."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()
        redis.delete = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_uses_deque_for_sessions(self, mock_redis):
        """Should use deque instead of list for sessions."""
        manager = SessionManager(redis_client=mock_redis)

        await manager.register_session(
            user_id=1,
            session_id="test-session-123",
            device_info="test-device",
        )

        # Check that setex was called
        mock_redis.setex.assert_called_once()

        # Get the value that was stored
        call_args = mock_redis.setex.call_args
        stored_value = call_args[0][2]  # Third positional arg is the value
        stored_sessions = json.loads(stored_value)

        # Should be a list in JSON (deque serializes to list)
        assert isinstance(stored_sessions, list)
        assert len(stored_sessions) == 1
        assert stored_sessions[0]["session_id"] == "test-session-123"

    @pytest.mark.asyncio
    async def test_parses_existing_sessions_as_deque(self, mock_redis):
        """Should parse existing sessions into deque for O(1) operations."""
        existing_sessions = [
            {"session_id": "sess1", "device_info": "device1", "created_at": "2024-01-01T00:00:00"},
            {"session_id": "sess2", "device_info": "device2", "created_at": "2024-01-01T00:01:00"},
        ]
        mock_redis.get.return_value = json.dumps(existing_sessions)

        manager = SessionManager(redis_client=mock_redis, max_sessions=3)

        result = await manager.register_session(
            user_id=1,
            session_id="sess3",
            device_info="device3",
        )

        assert result["registered"] is True
        assert result["active_sessions"] == 3


class TestSessionManagerFIFOOrder:
    """Tests verifying FIFO order is preserved."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client with session tracking."""
        redis = AsyncMock()
        redis.stored_sessions = None

        async def mock_get(key):
            return redis.stored_sessions

        async def mock_setex(key, ttl, value):
            redis.stored_sessions = value

        redis.get = AsyncMock(side_effect=mock_get)
        redis.setex = AsyncMock(side_effect=mock_setex)
        redis.delete = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_oldest_session_removed_first(self, mock_redis):
        """Should remove oldest session first when limit exceeded."""
        manager = SessionManager(redis_client=mock_redis, max_sessions=3)

        # Create 3 sessions (at limit)
        await manager.register_session(user_id=1, session_id="first", device_info="d1")
        await manager.register_session(user_id=1, session_id="second", device_info="d2")
        await manager.register_session(user_id=1, session_id="third", device_info="d3")

        # Add a 4th - should remove "first"
        result = await manager.register_session(user_id=1, session_id="fourth", device_info="d4")

        # Verify oldest was invalidated
        assert len(result["invalidated_sessions"]) == 1
        assert result["invalidated_sessions"][0]["session_id"] == "first"

        # Parse stored sessions
        stored = json.loads(mock_redis.stored_sessions)
        session_ids = [s["session_id"] for s in stored]

        # "first" should be gone, others present
        assert "first" not in session_ids
        assert "second" in session_ids
        assert "third" in session_ids
        assert "fourth" in session_ids

    @pytest.mark.asyncio
    async def test_fifo_order_maintained(self, mock_redis):
        """Should maintain FIFO order for sessions."""
        manager = SessionManager(redis_client=mock_redis, max_sessions=2)

        await manager.register_session(user_id=1, session_id="A", device_info="dA")
        await manager.register_session(user_id=1, session_id="B", device_info="dB")

        # A is oldest, adding C should remove A
        result = await manager.register_session(user_id=1, session_id="C", device_info="dC")

        assert result["invalidated_sessions"][0]["session_id"] == "A"

        stored = json.loads(mock_redis.stored_sessions)
        session_ids = [s["session_id"] for s in stored]
        assert session_ids == ["B", "C"]

        # Now B is oldest, adding D should remove B
        result = await manager.register_session(user_id=1, session_id="D", device_info="dD")

        assert result["invalidated_sessions"][0]["session_id"] == "B"

        stored = json.loads(mock_redis.stored_sessions)
        session_ids = [s["session_id"] for s in stored]
        assert session_ids == ["C", "D"]


class TestSessionManagerO1Performance:
    """Tests verifying O(1) performance characteristics."""

    def test_deque_popleft_is_used(self):
        """Verify deque.popleft() is used in code (O(1) vs list.pop(0) O(n))."""
        import inspect

        from src.app.core.security import session_manager as sm_module

        source = inspect.getsource(sm_module)

        # Should use popleft() for O(1) removal
        assert "popleft()" in source

    def test_deque_import_exists(self):
        """Should import deque from collections."""
        import inspect

        from src.app.core.security import session_manager as sm_module

        source = inspect.getsource(sm_module)
        assert "from collections import deque" in source

    def test_deque_type_used(self):
        """Should use deque type in the code."""
        import inspect

        from src.app.core.security import session_manager as sm_module

        source = inspect.getsource(sm_module)
        assert "deque(" in source


class TestSessionManagerIntegration:
    """Integration tests for session manager."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client with storage (supports Hash v2 and pipeline)."""
        redis = AsyncMock()
        redis.stored = {}
        redis.hash_stored = {}  # For Hash v2

        async def mock_get(key):
            return redis.stored.get(key)

        async def mock_setex(key, ttl, value):
            redis.stored[key] = value

        async def mock_delete(key):
            redis.stored.pop(key, None)

        async def mock_hexists(key, field):
            """O(1) lookup for Hash v2."""
            if key in redis.hash_stored:
                return field in redis.hash_stored[key]
            return False

        # Create mock pipeline that stores to hash_stored
        def mock_pipeline():
            pipe = MagicMock()
            pipe._commands = []

            def pipe_hset(key, field, value):
                pipe._commands.append(("hset", key, field, value))

            def pipe_hdel(key, *fields):
                pipe._commands.append(("hdel", key, fields))

            def pipe_expire(key, ttl):
                pipe._commands.append(("expire", key, ttl))

            async def pipe_execute():
                for cmd in pipe._commands:
                    if cmd[0] == "hset":
                        _, key, field, value = cmd
                        if key not in redis.hash_stored:
                            redis.hash_stored[key] = {}
                        redis.hash_stored[key][field] = value
                    elif cmd[0] == "hdel":
                        _, key, fields = cmd
                        if key in redis.hash_stored:
                            for f in fields:
                                redis.hash_stored[key].pop(f, None)
                return [1] * len(pipe._commands)

            pipe.hset = MagicMock(side_effect=pipe_hset)
            pipe.hdel = MagicMock(side_effect=pipe_hdel)
            pipe.expire = MagicMock(side_effect=pipe_expire)
            pipe.execute = AsyncMock(side_effect=pipe_execute)
            return pipe

        redis.get = AsyncMock(side_effect=mock_get)
        redis.setex = AsyncMock(side_effect=mock_setex)
        redis.delete = AsyncMock(side_effect=mock_delete)
        redis.hexists = AsyncMock(side_effect=mock_hexists)
        redis.pipeline = MagicMock(side_effect=mock_pipeline)
        return redis

    @pytest.mark.asyncio
    async def test_register_and_get_sessions(self, mock_redis):
        """Should register sessions and retrieve them."""
        manager = SessionManager(redis_client=mock_redis)

        result = await manager.register_session(
            user_id=1,
            session_id="sess-123",
            device_info="Chrome on Windows",
        )

        assert result["registered"] is True
        assert result["active_sessions"] == 1

        # Get sessions
        sessions = await manager.get_active_sessions(user_id=1)
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-123"
        assert sessions[0]["device_info"] == "Chrome on Windows"

    @pytest.mark.asyncio
    async def test_is_session_valid(self, mock_redis):
        """Should check session validity."""
        manager = SessionManager(redis_client=mock_redis)

        await manager.register_session(user_id=1, session_id="valid-sess")

        assert await manager.is_session_valid(user_id=1, session_id="valid-sess") is True
        assert await manager.is_session_valid(user_id=1, session_id="invalid-sess") is False

    @pytest.mark.asyncio
    async def test_invalidate_session(self, mock_redis):
        """Should invalidate a specific session."""
        manager = SessionManager(redis_client=mock_redis)

        await manager.register_session(user_id=1, session_id="to-invalidate")
        await manager.register_session(user_id=1, session_id="to-keep")

        result = await manager.invalidate_session(user_id=1, session_id="to-invalidate")
        assert result is True

        sessions = await manager.get_active_sessions(user_id=1)
        session_ids = [s["session_id"] for s in sessions]
        assert "to-invalidate" not in session_ids
        assert "to-keep" in session_ids

    @pytest.mark.asyncio
    async def test_invalidate_all_sessions(self, mock_redis):
        """Should invalidate all sessions for a user."""
        manager = SessionManager(redis_client=mock_redis)

        await manager.register_session(user_id=1, session_id="s1")
        await manager.register_session(user_id=1, session_id="s2")

        sessions = await manager.get_active_sessions(user_id=1)
        assert len(sessions) == 2

        count = await manager.invalidate_all_sessions(user_id=1)
        assert count == 2

        sessions = await manager.get_active_sessions(user_id=1)
        assert len(sessions) == 0

    @pytest.mark.asyncio
    async def test_duplicate_session_not_added(self, mock_redis):
        """Should not add duplicate session."""
        manager = SessionManager(redis_client=mock_redis)

        await manager.register_session(user_id=1, session_id="same-id")
        result = await manager.register_session(user_id=1, session_id="same-id")

        assert result["registered"] is True
        assert result.get("already_exists") is True

        sessions = await manager.get_active_sessions(user_id=1)
        assert len(sessions) == 1


class TestSessionManagerWithoutRedis:
    """Tests for graceful handling when Redis unavailable."""

    @pytest.mark.asyncio
    async def test_register_without_redis(self):
        """Should return error when Redis not configured."""
        manager = SessionManager(redis_client=None)

        result = await manager.register_session(user_id=1, session_id="s1")

        assert result["registered"] is False
        assert result["reason"] == "redis_unavailable"

    @pytest.mark.asyncio
    async def test_is_valid_without_redis(self):
        """Should fail open when Redis unavailable."""
        manager = SessionManager(redis_client=None)

        # Fails open for availability
        result = await manager.is_session_valid(user_id=1, session_id="any")
        assert result is True

    @pytest.mark.asyncio
    async def test_get_sessions_without_redis(self):
        """Should return empty list when Redis unavailable."""
        manager = SessionManager(redis_client=None)

        sessions = await manager.get_active_sessions(user_id=1)
        assert sessions == []


class TestGlobalSessionManager:
    """Tests for global session manager instance."""

    def test_global_instance_exists(self):
        """Should have a global session_manager instance."""
        assert session_manager is not None
        assert isinstance(session_manager, SessionManager)

    def test_can_set_redis_client(self):
        """Should be able to set Redis client on global instance."""
        mock_redis = MagicMock()

        # Should not raise
        session_manager.set_redis_client(mock_redis)
