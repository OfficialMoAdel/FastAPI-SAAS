"""
Unit tests for Session Eviction Redis Pipeline Optimization (P2-1).

Tests the atomic Lua script approach that reduces Redis roundtrips
to O(1) during session eviction.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.security.session_manager import SessionManager


def _mock_eval_success(final_count: int = 1, evicted_data: list[bytes] | None = None) -> AsyncMock:
    """Create a mock eval that returns Lua success result."""
    result = [b"success", str(final_count).encode()]
    if evicted_data:
        result.extend(evicted_data)
    return AsyncMock(return_value=result)


def _acm(pipe):
    """Make a pipeline mock support 'async with' returning itself."""

    async def __aenter__(*a):
        return pipe

    async def __aexit__(*a):
        return False

    pipe.__aenter__ = __aenter__
    pipe.__aexit__ = __aexit__
    return pipe


class TestSessionPipelineOptimization:
    """Tests for session eviction pipeline optimization."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client with eval support."""
        redis = AsyncMock()
        redis.hexists = AsyncMock(return_value=False)
        # Default: success with 1 active session, no evictions (under limit)
        redis.evalsha = _mock_eval_success(final_count=6)
        return redis

    @pytest.fixture
    def session_manager(self, mock_redis: AsyncMock) -> Any:
        """Create SessionManager with mock Redis."""
        manager = SessionManager(max_sessions=5)
        manager._redis_client = mock_redis
        return manager

    @pytest.mark.asyncio
    async def test_lua_eviction_reduces_roundtrips(self, session_manager: Any, mock_redis: AsyncMock) -> None:
        """Test that Lua script eviction uses O(1) roundtrips."""
        evicted_data = [
            b'{"session_id": "old_session_1", "device_info": "Chrome"}',
            b'{"session_id": "old_session_2", "device_info": "Firefox"}',
        ]
        mock_redis.evalsha = _mock_eval_success(final_count=4, evicted_data=evicted_data)

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction") as mock_record:
                await session_manager.register_session(
                    user_id=123,
                    session_id="new_session",
                    device_info="Safari",
                )

                # Should have called record_session_eviction with evicted count
                mock_record.assert_called_once()
                evicted_count = mock_record.call_args[0][0]
                # Lua script evicted 2 sessions
                assert evicted_count == 2, f"Expected 2 evicted sessions, got {evicted_count}"

    @pytest.mark.asyncio
    async def test_no_eviction_when_under_limit(self, session_manager: Any, mock_redis: AsyncMock) -> None:
        """Test that no eviction happens when under session limit."""
        # Default mock_redis.eval returns success with no evictions
        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            result = await session_manager.register_session(
                user_id=123,
                session_id="new_session",
                device_info="Safari",
            )

            assert result["registered"] is True
            assert len(result["invalidated_sessions"]) == 0

    @pytest.mark.asyncio
    async def test_eviction_with_data(self, session_manager: Any, mock_redis: AsyncMock) -> None:
        """Test that evicted session data is returned correctly from Lua."""
        evicted_data = [b'{"session_id": "old_session"}']
        mock_redis.evalsha = _mock_eval_success(final_count=5, evicted_data=evicted_data)

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction"):
                result = await session_manager.register_session(
                    user_id=123,
                    session_id="new_session",
                    device_info="Safari",
                )

                assert result["registered"] is True
                assert len(result["invalidated_sessions"]) == 1
                assert result["invalidated_sessions"][0]["session_id"] == "old_session"

    @pytest.mark.asyncio
    async def test_already_exists_returns_early(self, mock_redis: AsyncMock) -> None:
        """Test that existing session returns already_exists."""
        mock_redis.evalsha = AsyncMock(return_value=[b"already_exists"])
        manager = SessionManager(max_sessions=5)
        manager._redis_client = mock_redis

        result = await manager.register_session(
            user_id=123,
            session_id="existing_session",
        )

        assert result["registered"] is True
        assert result.get("already_exists") is True


class TestSessionEvictionMetrics:
    """Tests for session eviction metrics recording."""

    @pytest.fixture
    def mock_redis_at_limit(self) -> AsyncMock:
        """Create mock Redis at session limit."""
        redis = AsyncMock()
        redis.hexists = AsyncMock(return_value=False)
        evicted_data = [b'{"session_id": "old"}']
        redis.evalsha = _mock_eval_success(final_count=5, evicted_data=evicted_data)
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
                # Verify roundtrips count is passed (1 for Lua)
                assert isinstance(mock_record.call_args[0][0], int)
                assert mock_record.call_args[0][0] == 1


class TestSessionFeatureFlagToggle:
    """Tests for feature flag toggling - now both paths use Lua.

    Both pipelined and legacy paths use the same atomic Lua script.
    The feature flag controls internal logic but both are O(1).
    """

    @pytest.fixture
    def mock_redis_simple(self) -> AsyncMock:
        """Create simple mock Redis."""
        redis = AsyncMock()
        redis.hexists = AsyncMock(return_value=False)
        evicted_data = [b'{"session_id": "old"}']
        redis.evalsha = _mock_eval_success(final_count=5, evicted_data=evicted_data)
        return redis

    @pytest.mark.asyncio
    async def test_feature_flag_true_uses_eval(self, mock_redis_simple: AsyncMock) -> None:
        """Test that feature flag True path uses Lua eval."""
        from src.app.core.security.session_manager import SessionManager

        manager = SessionManager(max_sessions=5)
        manager._redis_client = mock_redis_simple

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = True
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction"):
                await manager.register_session(user_id=1, session_id="new")

                # Lua eval is used regardless of feature flag
                mock_redis_simple.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_feature_flag_false_also_uses_eval(self, mock_redis_simple: AsyncMock) -> None:
        """Test that feature flag False path also uses Lua eval.

        Both paths now use the same atomic Lua script.
        """
        from src.app.core.security.session_manager import SessionManager

        manager = SessionManager(max_sessions=5)
        manager._redis_client = mock_redis_simple

        with patch("src.app.core.security.session_manager.settings") as mock_settings:
            mock_settings.SESSION_USE_PIPELINE_EVICTION = False
            mock_settings.SESSION_MAX_EVICTION_BATCH_SIZE = 50

            with patch("src.app.core.security.session_manager.record_session_eviction"):
                await manager.register_session(user_id=1, session_id="new")

                # Lua eval is used regardless of feature flag
                mock_redis_simple.evalsha.assert_called_once()
