"""Unit tests for Session Manager ZSET optimization (z28g.3).

Tests the O(log n) eviction using Redis ZSET + Hash hybrid structure.
Tests verify the legacy ZPOPMIN path when SESSION_USE_PIPELINE_EVICTION=False.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.session_manager import SessionManager

logger = logging.getLogger(__name__)


class TestZSETEvictionOrder:
    """Test that oldest sessions are evicted first using ZPOPMIN (legacy path)."""

    @pytest.mark.asyncio
    @patch("src.app.core.security.session_manager.settings")
    async def test_zset_eviction_oldest_first(self, mock_settings):
        """Verify oldest session is evicted when at limit (legacy path)."""
        # Arrange - force legacy path
        mock_settings.SESSION_USE_PIPELINE_EVICTION = False
        mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

        mock_redis = AsyncMock()
        mock_redis.hexists = AsyncMock(return_value=False)
        mock_redis.zcard = AsyncMock(return_value=3)  # At limit
        mock_redis.hlen = AsyncMock(return_value=3)

        # ZPOPMIN returns oldest session (lowest timestamp)
        mock_redis.zpopmin = AsyncMock(return_value=[("oldest_session", 1000.0)])
        mock_redis.hget = AsyncMock(
            return_value='{"session_id": "oldest_session", "created_at": "2024-01-01T00:00:00"}'
        )

        pipe = MagicMock()
        pipe.zadd = MagicMock(return_value=pipe)
        pipe.hset = MagicMock(return_value=pipe)
        pipe.hdel = MagicMock(return_value=pipe)
        pipe.expire = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe)

        manager = SessionManager(redis_client=mock_redis, max_sessions=3)

        # Act
        result = await manager.register_session("user1", "new_session")

        # Assert
        logger.info(f"Eviction result: {result}")
        assert result["registered"] is True
        assert len(result["invalidated_sessions"]) == 1
        assert result["invalidated_sessions"][0]["session_id"] == "oldest_session"

        # Verify ZPOPMIN was used (O(log n) eviction)
        mock_redis.zpopmin.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_eviction_when_under_limit(self):
        """Verify no eviction when under session limit."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.hexists = AsyncMock(return_value=False)
        mock_redis.zcard = AsyncMock(return_value=2)  # Under limit of 5
        mock_redis.hlen = AsyncMock(return_value=2)

        pipe = MagicMock()
        pipe.zadd = MagicMock(return_value=pipe)
        pipe.hset = MagicMock(return_value=pipe)
        pipe.expire = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe)

        manager = SessionManager(redis_client=mock_redis, max_sessions=5)

        # Act
        result = await manager.register_session("user1", "new_session")

        # Assert
        logger.info(f"Registration result: {result}")
        assert result["registered"] is True
        assert result.get("invalidated_sessions", []) == []

        # Verify ZPOPMIN was NOT called
        mock_redis.zpopmin.assert_not_called()


class TestZSETHashConsistency:
    """Test that ZSET and Hash remain in sync."""

    @pytest.mark.asyncio
    async def test_register_updates_both_structures(self):
        """Verify register_session updates both ZSET and Hash atomically."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.hexists = AsyncMock(return_value=False)
        mock_redis.zcard = AsyncMock(return_value=0)
        mock_redis.hlen = AsyncMock(return_value=0)

        pipe = MagicMock()
        pipe.zadd = MagicMock(return_value=pipe)
        pipe.hset = MagicMock(return_value=pipe)
        pipe.expire = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe)

        manager = SessionManager(redis_client=mock_redis)

        # Act
        await manager.register_session("user1", "session1", "Chrome")

        # Assert - pipeline should update both
        pipe.zadd.assert_called_once()
        pipe.hset.assert_called_once()
        pipe.execute.assert_called_once()

        # Verify key patterns
        zadd_key = pipe.zadd.call_args[0][0]
        hset_key = pipe.hset.call_args[0][0]
        logger.info(f"ZADD key: {zadd_key}, HSET key: {hset_key}")
        assert "user_sessions_order:" in zadd_key
        assert "user_sessions_v2:" in hset_key

    @pytest.mark.asyncio
    async def test_invalidate_removes_from_both(self):
        """Verify invalidate_session removes from both ZSET and Hash."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.hexists = AsyncMock(return_value=True)

        pipe = MagicMock()
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
    """Test that TTL is properly set on both structures."""

    @pytest.mark.asyncio
    async def test_ttl_set_on_both_keys(self):
        """Verify EXPIRE is called on both ZSET and Hash."""
        # Arrange
        mock_redis = AsyncMock()
        mock_redis.hexists = AsyncMock(return_value=False)
        mock_redis.zcard = AsyncMock(return_value=0)
        mock_redis.hlen = AsyncMock(return_value=0)

        pipe = MagicMock()
        pipe.zadd = MagicMock(return_value=pipe)
        pipe.hset = MagicMock(return_value=pipe)
        pipe.expire = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=pipe)

        manager = SessionManager(redis_client=mock_redis)

        # Act
        await manager.register_session("user1", "session1")

        # Assert - expire should be called twice
        assert pipe.expire.call_count == 2

        # Verify TTL value is correct (24 hours)
        for call in pipe.expire.call_args_list:
            ttl = call[0][1]
            logger.info(f"EXPIRE call with TTL: {ttl}")
            assert ttl == SessionManager.SESSION_TTL_SECONDS


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
