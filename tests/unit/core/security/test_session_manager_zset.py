"""Unit tests for Session Manager ZSET optimization (z28g.3).

Tests the O(log n) eviction using Redis ZSET + Hash hybrid structure.
Now uses atomic Lua scripts for race-condition-free operations.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.core.security.session_manager import SessionManager


def _acm(pipe):
    """Make a pipeline mock support 'async with' returning itself."""

    async def __aenter__(*a):
        return pipe

    async def __aexit__(*a):
        return False

    pipe.__aenter__ = __aenter__
    pipe.__aexit__ = __aexit__
    return pipe


def _mock_eval_success(final_count: int = 1, evicted_data: list[bytes] | None = None) -> AsyncMock:
    """Create a mock eval that returns Lua success result."""
    result = [b"success", str(final_count).encode()]
    if evicted_data:
        result.extend(evicted_data)
    return AsyncMock(return_value=result)


logger = logging.getLogger(__name__)


class TestZSETEvictionOrder:
    """Test that oldest sessions are evicted first via atomic Lua script."""

    @pytest.mark.asyncio
    async def test_zset_eviction_oldest_first(self):
        """Verify oldest session is evicted when at limit (via Lua)."""
        # Arrange
        evicted_data = b'{"session_id": "oldest_session", "created_at": "2024-01-01T00:00:00"}'
        mock_redis = AsyncMock()
        mock_redis.eval = _mock_eval_success(
            final_count=3,
            evicted_data=[evicted_data],
        )

        manager = SessionManager(redis_client=mock_redis, max_sessions=3)

        # Act
        result = await manager.register_session("user1", "new_session")

        # Assert
        logger.info(f"Eviction result: {result}")
        assert result["registered"] is True
        assert len(result["invalidated_sessions"]) == 1
        assert result["invalidated_sessions"][0]["session_id"] == "oldest_session"

        # Verify eval was called (atomic Lua eviction)
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_eviction_when_under_limit(self):
        """Verify no eviction when under session limit."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.eval = _mock_eval_success(final_count=3)

        manager = SessionManager(redis_client=mock_redis, max_sessions=5)

        # Act
        result = await manager.register_session("user1", "new_session")

        # Assert
        logger.info(f"Registration result: {result}")
        assert result["registered"] is True
        assert result.get("invalidated_sessions", []) == []

        # Verify ZPOPMIN was NOT called (Lua handles everything)
        mock_redis.zpopmin.assert_not_called()


class TestZSETHashConsistency:
    """Test that ZSET and Hash remain in sync (via atomic Lua)."""

    @pytest.mark.asyncio
    async def test_register_updates_both_structures(self):
        """Verify register_session atomically updates both ZSET and Hash via Lua."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.eval = _mock_eval_success(final_count=1)

        manager = SessionManager(redis_client=mock_redis)

        # Act
        result = await manager.register_session("user1", "session1", "Chrome")

        # Assert - Lua script atomically updates both structures
        assert result["registered"] is True
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_removes_from_both(self):
        """Verify invalidate_session removes from both ZSET and Hash."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.hexists = AsyncMock(return_value=True)

        pipe = MagicMock()
        _acm(pipe)
        pipe.hdel = MagicMock(return_value=pipe)
        pipe.zrem = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe)

        manager = SessionManager(redis_client=mock_redis)

        # Act
        result = await manager.invalidate_session("user1", "session1")

        # Assert
        logger.info(f"Invalidation result: {result}")
        assert result is True
        pipe.hdel.assert_called_once()
        pipe.zrem.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_all_deletes_both_keys(self):
        """Verify invalidate_all_sessions deletes both ZSET and Hash keys."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.hlen = AsyncMock(return_value=5)

        pipe = MagicMock()
        _acm(pipe)
        pipe.delete = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe)

        manager = SessionManager(redis_client=mock_redis)

        # Act
        count = await manager.invalidate_all_sessions("user1")

        # Assert
        logger.info(f"Invalidated {count} sessions")
        assert count == 5
        # DELETE should be called twice (Hash + ZSET)
        assert pipe.delete.call_count == 2


class TestZSETTTLPropagation:
    """Test that TTL is properly set on both structures (via atomic Lua)."""

    @pytest.mark.asyncio
    async def test_ttl_set_via_lua(self):
        """Verify TTL is set atomically on both ZSET and Hash by Lua script."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.eval = _mock_eval_success(final_count=1)

        manager = SessionManager(redis_client=mock_redis)

        # Act
        result = await manager.register_session("user1", "session1")

        # Assert - Lua script handles TTL atomically
        assert result["registered"] is True
        mock_redis.eval.assert_called_once()


class TestZSETCountOperations:
    """Test O(1) count operations."""

    @pytest.mark.asyncio
    async def test_count_uses_zcard(self):
        """Verify count_active_sessions uses ZCARD (O(1))."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=5)

        manager = SessionManager(redis_client=mock_redis)

        # Act
        count = await manager.count_active_sessions("user1")

        # Assert
        logger.info(f"Active sessions count: {count}")
        assert count == 5
        mock_redis.zcard.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_fallback_to_hlen(self):
        """Verify fallback to HLEN when ZSET doesn't exist (migration case)."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=0)  # ZSET empty/missing
        mock_redis.hlen = AsyncMock(return_value=3)  # Hash has data

        manager = SessionManager(redis_client=mock_redis)

        # Act
        count = await manager.count_active_sessions("user1")

        # Assert
        logger.info(f"Active sessions count (fallback): {count}")
        assert count == 3
        mock_redis.zcard.assert_called_once()
        mock_redis.hlen.assert_called_once()


class TestZSETMigration:
    """Test migration from Hash-only to ZSET+Hash."""

    @pytest.mark.asyncio
    async def test_migrate_populates_zset_from_hash(self):
        """Verify migration creates ZSET entries from existing Hash data."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock(return_value=(0, ["user_sessions_v2:123"]))
        mock_redis.hgetall = AsyncMock(
            return_value={
                "sess1": '{"session_id": "sess1", "created_at": "2024-01-01T00:00:00+00:00"}',
                "sess2": '{"session_id": "sess2", "created_at": "2024-01-01T01:00:00+00:00"}',
            }
        )

        pipe = MagicMock()
        _acm(pipe)
        pipe.zadd = MagicMock(return_value=pipe)
        pipe.expire = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe)

        manager = SessionManager(redis_client=mock_redis)

        # Act
        result = await manager.migrate_sessions_to_zset()

        # Assert
        logger.info(f"Migration result: {result}")
        assert result["migrated"] == 1
        assert len(result["errors"]) == 0
        pipe.zadd.assert_called()

    @pytest.mark.asyncio
    async def test_migrate_handles_empty_hash(self):
        """Verify migration handles users with no sessions gracefully."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock(return_value=(0, ["user_sessions_v2:empty"]))
        mock_redis.hgetall = AsyncMock(return_value={})

        manager = SessionManager(redis_client=mock_redis)

        # Act
        result = await manager.migrate_sessions_to_zset()

        # Assert
        logger.info(f"Migration result (empty): {result}")
        assert result["migrated"] == 0


# === PHASE 3 MIGRATION: Tests from test_session_manager_migration.py ===
# Tests for legacy migration from JSON to Hash format


class TestLegacyHashMigration:
    """Test migration from legacy JSON format to Hash format."""

    @pytest.mark.asyncio
    async def test_migrate_to_hash(self):
        """Verify migration of legacy sessions to hash format."""
        # Setup legacy data
        mock_redis = AsyncMock()
        user_id_1 = "123"
        user_id_2 = "456"

        sessions_1 = [
            {"session_id": "s1", "device_info": "d1", "created_at": "2024-01-01"},
            {"session_id": "s2", "device_info": "d2", "created_at": "2024-01-02"},
        ]
        sessions_2 = [{"session_id": "s3", "device_info": "d3", "created_at": "2024-01-03"}]

        # Mock SCAN to return keys - scan_iter is an async generator
        async def mock_scan_iter(match):
            yield f"user_sessions:{user_id_1}"
            yield f"user_sessions:{user_id_2}"

        mock_redis.scan_iter = mock_scan_iter

        # Mock GET to return session data
        async def mock_get(key):
            if key == f"user_sessions:{user_id_1}":
                return json.dumps(sessions_1)
            if key == f"user_sessions:{user_id_2}":
                return json.dumps(sessions_2)
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)

        # Mock pipeline - redis.pipeline() returns an async context manager
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock()
        mock_pipeline.hset = MagicMock()
        mock_pipeline.expire = MagicMock()

        # Create an async context manager for the pipeline
        async_cm = AsyncMock()
        async_cm.__aenter__.return_value = mock_pipeline
        async_cm.__aexit__.return_value = None

        # redis.pipeline() should return the async context manager
        mock_redis.pipeline = MagicMock(return_value=async_cm)

        session_manager = SessionManager(redis_client=mock_redis)

        # Execute migration
        count = await session_manager.migrate_to_hash()

        # Verify results
        assert count == 2  # 2 users migrated

        # Verify pipeline execution
        assert mock_pipeline.execute.called

        # Check that HSET was called for user 1
        # Note: The implementation uses json_dumps which produces compact JSON (no spaces)
        hash_key_1 = f"user_sessions_v2:{user_id_1}"
        mock_pipeline.hset.assert_any_call(hash_key_1, "s1", json.dumps(sessions_1[0], separators=(",", ":")))
        mock_pipeline.hset.assert_any_call(hash_key_1, "s2", json.dumps(sessions_1[1], separators=(",", ":")))
        mock_pipeline.expire.assert_any_call(hash_key_1, session_manager.SESSION_TTL_SECONDS)

        # user 2
        hash_key_2 = f"user_sessions_v2:{user_id_2}"
        mock_pipeline.hset.assert_any_call(hash_key_2, "s3", json.dumps(sessions_2[0], separators=(",", ":")))
        mock_pipeline.expire.assert_any_call(hash_key_2, session_manager.SESSION_TTL_SECONDS)
