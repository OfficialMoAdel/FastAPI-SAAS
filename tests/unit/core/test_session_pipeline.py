"""
Unit tests for Session Eviction Redis Pipeline Optimization (P2-1).

Tests the optimized ZRANGE + pipeline approach that reduces Redis roundtrips
from O(n) to O(1) during session eviction.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _acm(pipe):
    """Make a pipeline mock support 'async with' returning itself."""
    async def __aenter__(*a): return pipe
    async def __aexit__(*a): return False
    pipe.__aenter__ = __aenter__
    pipe.__aexit__ = __aexit__
    return pipe



class TestSessionPipelineOptimization:
    """Tests for session eviction pipeline optimization."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client with pipeline support."""
        redis = AsyncMock()
        redis.hexists = AsyncMock(return_value=False)
        redis.zcard = AsyncMock(return_value=5)  # At max sessions
        redis.hlen = AsyncMock(return_value=5)
        redis.zrange = AsyncMock(
            return_value=[
                (b"old_session_1", 1000.0),
                (b"old_session_2", 1001.0),
            ]
        )

        # Pipeline mock
        pipe = AsyncMock()
        _acm(pipe)
        pipe.execute = AsyncMock(
            return_value=[
                b'{"session_id": "old_session_1", "device_info": "Chrome"}',
                b'{"session_id": "old_session_2", "device_info": "Firefox"}',
            ]
        )
        redis.pipeline = MagicMock(return_value=pipe)

        return redis

    @pytest.fixture
    def session_manager(self, mock_redis: AsyncMock) -> Any:
        """Create SessionManager with mock Redis."""
        from src.app.core.security.session_manager import SessionManager

        manager = SessionManager(max_sessions=5)
        manager._redis_client = mock_redis
        return manager

    @pytest.mark.asyncio
    async def test_pipelined_eviction_reduces_roundtrips(self, session_manager: Any, mock_redis: AsyncMock) -> None:
        """Test that pipelined eviction uses O(1) roundtrips instead of O(n)."""
        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction") as mock_record:
                await session_manager.register_session(
                    user_id=123,
                    session_id="new_session",
                    device_info="Safari",
                )

                # Should have called record_session_eviction with low roundtrip count
                mock_record.assert_called_once()
                roundtrips = mock_record.call_args[0][0]
                # Pipelined: 1 (ZRANGE) + 1 (batch HGET) + 1 (batch DEL+ADD) = 3
                assert roundtrips <= 4, f"Expected <=4 roundtrips, got {roundtrips}"

    @pytest.mark.asyncio
    async def test_legacy_eviction_uses_more_roundtrips(self, session_manager: Any, mock_redis: AsyncMock) -> None:
        """Test that legacy eviction uses O(n) roundtrips."""
        # Setup for legacy path
        mock_redis.zpopmin = AsyncMock(return_value=[(b"old_session", 1000.0)])
        mock_redis.hget = AsyncMock(return_value=b'{"session_id": "old_session"}')

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = False
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction") as mock_record:
                await session_manager.register_session(
                    user_id=123,
                    session_id="new_session",
                    device_info="Safari",
                )

                mock_record.assert_called_once()
                roundtrips = mock_record.call_args[0][0]
                # Legacy: n * (1 ZPOPMIN + 1 HGET) + 1 final pipeline
                # For 2 sessions: 2*2 + 1 = 5 minimum
                assert roundtrips >= 2, f"Expected >=2 roundtrips for legacy, got {roundtrips}"

    @pytest.mark.asyncio
    async def test_pipelined_eviction_calls_zrange_not_zpopmin(
        self, session_manager: Any, mock_redis: AsyncMock
    ) -> None:
        """Test that pipelined path uses ZRANGE (non-destructive) instead of ZPOPMIN."""
        mock_redis.zpopmin = AsyncMock()  # Should NOT be called

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction"):
                await session_manager.register_session(
                    user_id=123,
                    session_id="new_session",
                    device_info="Safari",
                )

                # ZRANGE should be called, not ZPOPMIN
                mock_redis.zrange.assert_called()
                mock_redis.zpopmin.assert_not_called()

    @pytest.mark.asyncio
    async def test_legacy_eviction_calls_zpopmin(self, session_manager: Any, mock_redis: AsyncMock) -> None:
        """Test that legacy path uses ZPOPMIN."""
        mock_redis.zpopmin = AsyncMock(return_value=[(b"old_session", 1000.0)])
        mock_redis.hget = AsyncMock(return_value=b'{"session_id": "old_session"}')

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = False
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction"):
                await session_manager.register_session(
                    user_id=123,
                    session_id="new_session",
                    device_info="Safari",
                )

                # ZPOPMIN should be called for legacy path
                mock_redis.zpopmin.assert_called()

    @pytest.mark.asyncio
    async def test_batch_size_limit_respected(self, session_manager: Any, mock_redis: AsyncMock) -> None:
        """Test that eviction batch size is capped by SESSION_MAX_EVICTION_BATCH_SIZE."""
        # Simulate 100 sessions over limit
        mock_redis.zcard = AsyncMock(return_value=105)

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 10  # Cap at 10

            with patch("src.app.core.security.session_manager.record_session_eviction"):
                await session_manager.register_session(
                    user_id=123,
                    session_id="new_session",
                    device_info="Safari",
                )

                # ZRANGE should be called with capped range (0 to 9, i.e., 10 sessions)
                mock_redis.zrange.assert_called_once()
                call_args = mock_redis.zrange.call_args
                # Second arg is end index (sessions_to_remove - 1)
                assert call_args[0][2] <= 9, "Batch size should be capped at 10"

    @pytest.mark.asyncio
    async def test_pipelined_handles_orphaned_sessions(self, session_manager: Any, mock_redis: AsyncMock) -> None:
        """Test that pipelined eviction handles sessions in ZSET but not in hash."""
        # ZRANGE returns sessions
        mock_redis.zrange = AsyncMock(
            return_value=[
                (b"orphaned_session", 1000.0),
                (b"valid_session", 1001.0),
            ]
        )

        # Pipeline returns None for orphaned, valid data for other
        pipe = AsyncMock()
        _acm(pipe)
        pipe.execute = AsyncMock(
            side_effect=[
                [None, b'{"session_id": "valid_session"}'],  # First call: HGET batch
                [1, 1, 1, 1, 1, 1, 1, 1],  # Second call: DELETE + ADD batch
            ]
        )
        mock_redis.pipeline = MagicMock(return_value=pipe)

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction"):
                with patch("src.app.core.security.session_manager.logger") as mock_logger:
                    result = await session_manager.register_session(
                        user_id=123,
                        session_id="new_session",
                    )

                    # Should log warning for orphaned session
                    mock_logger.warning.assert_called()
                    assert result["registered"] is True


class TestSessionEvictionMetrics:
    """Tests for session eviction metrics recording."""

    @pytest.fixture
    def mock_redis_at_limit(self) -> AsyncMock:
        """Create mock Redis at session limit."""
        redis = AsyncMock()
        redis.hexists = AsyncMock(return_value=False)
        redis.zcard = AsyncMock(return_value=5)
        redis.hlen = AsyncMock(return_value=5)
        redis.zrange = AsyncMock(return_value=[(b"old", 1000.0)])

        pipe = AsyncMock()
        _acm(pipe)
        pipe.execute = AsyncMock(return_value=[b'{"session_id": "old"}'])
        redis.pipeline = MagicMock(return_value=pipe)

        return redis

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_eviction(self, mock_redis_at_limit: AsyncMock) -> None:
        """Test that metrics are recorded when sessions are evicted."""
        from src.app.core.security.session_manager import SessionManager

        manager = SessionManager(max_sessions=5)
        manager._redis_client = mock_redis_at_limit

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction") as mock_record:
                await manager.register_session(
                    user_id=123,
                    session_id="new_session",
                )

                mock_record.assert_called_once()
                # Verify roundtrips count is passed
                assert isinstance(mock_record.call_args[0][0], int)


class TestSessionFeatureFlagToggle:
    """Tests for feature flag toggling between paths."""

    @pytest.fixture
    def mock_redis_simple(self) -> AsyncMock:
        """Create simple mock Redis."""
        redis = AsyncMock()
        redis.hexists = AsyncMock(return_value=False)
        redis.zcard = AsyncMock(return_value=5)
        redis.hlen = AsyncMock(return_value=5)
        redis.zrange = AsyncMock(return_value=[(b"old", 1000.0)])
        redis.zpopmin = AsyncMock(return_value=[(b"old", 1000.0)])
        redis.hget = AsyncMock(return_value=b'{"session_id": "old"}')

        pipe = AsyncMock()
        _acm(pipe)
        pipe.execute = AsyncMock(return_value=[b'{"session_id": "old"}'])
        redis.pipeline = MagicMock(return_value=pipe)

        return redis

    @pytest.mark.asyncio
    async def test_feature_flag_true_uses_pipelined(self, mock_redis_simple: AsyncMock) -> None:
        """Test that feature flag True uses pipelined path."""
        from src.app.core.security.session_manager import SessionManager

        manager = SessionManager(max_sessions=5)
        manager._redis_client = mock_redis_simple

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction"):
                await manager.register_session(user_id=1, session_id="new")

                mock_redis_simple.zrange.assert_called()
                mock_redis_simple.zpopmin.assert_not_called()

    @pytest.mark.asyncio
    async def test_feature_flag_false_uses_legacy(self, mock_redis_simple: AsyncMock) -> None:
        """Test that feature flag False uses legacy path."""
        from src.app.core.security.session_manager import SessionManager

        manager = SessionManager(max_sessions=5)
        manager._redis_client = mock_redis_simple

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = False
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction"):
                await manager.register_session(user_id=1, session_id="new")

                mock_redis_simple.zpopmin.assert_called()
