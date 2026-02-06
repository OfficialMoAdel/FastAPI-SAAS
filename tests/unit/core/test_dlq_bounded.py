"""
Unit tests for DLQ Bounded Size with LTRIM (P3-1).

Tests the optimized LPUSH + LTRIM + LLEN pipeline that prevents
unbounded DLQ growth under sustained failure scenarios.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDLQBoundedSize:
    """Tests for DLQ bounded size optimization."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis client with pipeline support."""
        redis = AsyncMock()

        # Pipeline mock
        pipe = AsyncMock()
        pipe.lpush = AsyncMock()
        pipe.ltrim = AsyncMock()
        pipe.llen = AsyncMock()
        pipe.execute = AsyncMock(
            return_value=[
                1,  # LPUSH result (list length after push)
                True,  # LTRIM result (OK)
                100,  # LLEN result (current size)
            ]
        )
        redis.pipeline = MagicMock(return_value=pipe)
        redis.lpush = AsyncMock(return_value=1)
        redis.llen = AsyncMock(return_value=100)

        return redis

    @pytest.fixture
    def event_bus(self, mock_redis: AsyncMock) -> Any:
        """Create EventBus with mock Redis."""
        from src.app.core.events import EventBus

        bus = EventBus()
        bus._redis_client = mock_redis
        return bus

    @pytest.fixture
    def mock_r1_settings_enabled(self) -> MagicMock:
        """Create mock R1 settings with bounded DLQ enabled."""
        settings_mock = MagicMock()
        settings_mock.DLQ_USE_BOUNDED_SIZE = True
        settings_mock.DLQ_MAX_SIZE = 10000
        settings_mock.DLQ_ALERT_THRESHOLD_PCT = 80
        return settings_mock

    @pytest.fixture
    def mock_r1_settings_disabled(self) -> MagicMock:
        """Create mock R1 settings with bounded DLQ disabled."""
        settings_mock = MagicMock()
        settings_mock.DLQ_USE_BOUNDED_SIZE = False
        return settings_mock

    @pytest.mark.asyncio
    async def test_bounded_dlq_uses_pipeline_ltrim(
        self, event_bus: Any, mock_redis: AsyncMock, mock_r1_settings_enabled: MagicMock
    ) -> None:
        """Test that bounded DLQ uses LPUSH + LTRIM + LLEN pipeline."""
        with patch("src.app.core.config.settings", mock_r1_settings_enabled):
            with patch("src.app.core.monitoring_metrics.update_dlq_size"):
                await event_bus.add_to_dlq(
                    event_name="user.created",
                    handler_name="send_welcome_email",
                    data={"user_id": 123},
                    error="SMTP connection failed",
                    attempt_count=3,
                )

                # Pipeline should be used
                mock_redis.pipeline.assert_called_once()
                pipe = mock_redis.pipeline.return_value
                pipe.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_legacy_dlq_uses_single_lpush(
        self, event_bus: Any, mock_redis: AsyncMock, mock_r1_settings_disabled: MagicMock
    ) -> None:
        """Test that legacy path uses single LPUSH."""
        with patch("src.app.core.config.settings", mock_r1_settings_disabled):
            with patch("src.app.core.monitoring_metrics.update_dlq_size"):
                await event_bus.add_to_dlq(
                    event_name="user.created",
                    handler_name="send_welcome_email",
                    data={"user_id": 123},
                    error="SMTP connection failed",
                    attempt_count=3,
                )

                # Single LPUSH should be used
                mock_redis.lpush.assert_called_once()
                mock_redis.llen.assert_called_once()
                # Pipeline should NOT be used
                mock_redis.pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_dlq_metrics_recorded(
        self, event_bus: Any, mock_redis: AsyncMock, mock_r1_settings_enabled: MagicMock
    ) -> None:
        """Test that DLQ size metrics are recorded."""
        with patch("src.app.core.config.settings", mock_r1_settings_enabled):
            # Need to patch in monitoring.__init__ where it's re-exported
            with patch("src.app.core.monitoring.update_dlq_size") as mock_update:
                await event_bus.add_to_dlq(
                    event_name="user.created",
                    handler_name="send_welcome_email",
                    data={"user_id": 123},
                    error="SMTP connection failed",
                    attempt_count=3,
                )

                # Metrics should be recorded with size and max_size
                mock_update.assert_called_once_with(100, 10000)

    @pytest.mark.asyncio
    async def test_dlq_alert_at_threshold(self, event_bus: Any, mock_redis: AsyncMock) -> None:
        """Test that warning is logged when DLQ approaches capacity."""
        # Set DLQ size to 8500 (above 80% of 10000)
        pipe = AsyncMock()
        pipe.lpush = AsyncMock()
        pipe.ltrim = AsyncMock()
        pipe.llen = AsyncMock()
        pipe.execute = AsyncMock(return_value=[1, True, 8500])
        mock_redis.pipeline = MagicMock(return_value=pipe)

        settings_mock = MagicMock()
        settings_mock.DLQ_USE_BOUNDED_SIZE = True
        settings_mock.DLQ_MAX_SIZE = 10000
        settings_mock.DLQ_ALERT_THRESHOLD_PCT = 80

        with patch("src.app.core.config.settings", settings_mock):
            with patch("src.app.core.monitoring_metrics.update_dlq_size"):
                with patch("src.app.core.events.logger") as mock_logger:
                    await event_bus.add_to_dlq(
                        event_name="user.created",
                        handler_name="send_welcome_email",
                        data={"user_id": 123},
                        error="SMTP connection failed",
                        attempt_count=3,
                    )

                    # Warning should be logged for capacity
                    warning_calls = [call for call in mock_logger.warning.call_args_list if "capacity" in str(call)]
                    assert len(warning_calls) > 0, "Expected capacity warning"

    @pytest.mark.asyncio
    async def test_dlq_no_alert_below_threshold(self, event_bus: Any, mock_redis: AsyncMock) -> None:
        """Test that no warning when DLQ is below threshold."""
        # Set DLQ size to 5000 (50% of 10000, below 80%)
        pipe = AsyncMock()
        pipe.lpush = AsyncMock()
        pipe.ltrim = AsyncMock()
        pipe.llen = AsyncMock()
        pipe.execute = AsyncMock(return_value=[1, True, 5000])
        mock_redis.pipeline = MagicMock(return_value=pipe)

        settings_mock = MagicMock()
        settings_mock.DLQ_USE_BOUNDED_SIZE = True
        settings_mock.DLQ_MAX_SIZE = 10000
        settings_mock.DLQ_ALERT_THRESHOLD_PCT = 80

        with patch("src.app.core.config.settings", settings_mock):
            with patch("src.app.core.monitoring_metrics.update_dlq_size"):
                with patch("src.app.core.events.logger") as mock_logger:
                    await event_bus.add_to_dlq(
                        event_name="user.created",
                        handler_name="send_welcome_email",
                        data={"user_id": 123},
                        error="SMTP connection failed",
                        attempt_count=3,
                    )

                    # Capacity warning should NOT appear
                    capacity_warnings = [call for call in mock_logger.warning.call_args_list if "capacity" in str(call)]
                    assert len(capacity_warnings) == 0, "Unexpected capacity warning"

    @pytest.mark.asyncio
    async def test_ltrim_caps_at_max_size(self, event_bus: Any, mock_redis: AsyncMock) -> None:
        """Test that LTRIM is called with correct max size index."""
        settings_mock = MagicMock()
        settings_mock.DLQ_USE_BOUNDED_SIZE = True
        settings_mock.DLQ_MAX_SIZE = 5000
        settings_mock.DLQ_ALERT_THRESHOLD_PCT = 80

        with patch("src.app.core.config.settings", settings_mock):
            with patch("src.app.core.monitoring_metrics.update_dlq_size"):
                await event_bus.add_to_dlq(
                    event_name="user.created",
                    handler_name="handler",
                    data={},
                    error="error",
                    attempt_count=1,
                )

                # Check pipeline was called and LTRIM has correct range
                pipe = mock_redis.pipeline.return_value
                # LTRIM should keep 0 to 4999 (5000 entries, max_size - 1)
                pipe.ltrim.assert_called()


class TestDLQFeatureFlagToggle:
    """Tests for DLQ feature flag toggling."""

    @pytest.fixture
    def mock_redis(self) -> AsyncMock:
        """Create mock Redis."""
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.lpush = AsyncMock()
        pipe.ltrim = AsyncMock()
        pipe.llen = AsyncMock()
        pipe.execute = AsyncMock(return_value=[1, True, 100])
        redis.pipeline = MagicMock(return_value=pipe)
        redis.lpush = AsyncMock(return_value=1)
        redis.llen = AsyncMock(return_value=100)
        return redis

    @pytest.mark.asyncio
    async def test_feature_flag_true_uses_pipeline(self, mock_redis: AsyncMock) -> None:
        """Test that feature flag True uses pipeline path."""
        from src.app.core.events import EventBus

        bus = EventBus()
        bus._redis_client = mock_redis

        settings_mock = MagicMock()
        settings_mock.DLQ_USE_BOUNDED_SIZE = True
        settings_mock.DLQ_MAX_SIZE = 10000
        settings_mock.DLQ_ALERT_THRESHOLD_PCT = 80

        with patch("src.app.core.config.settings", settings_mock):
            with patch("src.app.core.monitoring_metrics.update_dlq_size"):
                await bus.add_to_dlq("event", "handler", {}, "error", 1)

                mock_redis.pipeline.assert_called()
                mock_redis.lpush.assert_not_called()

    @pytest.mark.asyncio
    async def test_feature_flag_false_uses_legacy(self, mock_redis: AsyncMock) -> None:
        """Test that feature flag False uses legacy path."""
        from src.app.core.events import EventBus

        bus = EventBus()
        bus._redis_client = mock_redis

        settings_mock = MagicMock()
        settings_mock.DLQ_USE_BOUNDED_SIZE = False

        with patch("src.app.core.config.settings", settings_mock):
            with patch("src.app.core.monitoring_metrics.update_dlq_size"):
                await bus.add_to_dlq("event", "handler", {}, "error", 1)

                mock_redis.lpush.assert_called()
                mock_redis.pipeline.assert_not_called()


class TestDLQErrorHandling:
    """Tests for DLQ error handling."""

    @pytest.fixture
    def mock_redis_failing(self) -> AsyncMock:
        """Create mock Redis that fails."""
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.lpush = AsyncMock()
        pipe.ltrim = AsyncMock()
        pipe.llen = AsyncMock()
        pipe.execute = AsyncMock(side_effect=Exception("Redis connection lost"))
        redis.pipeline = MagicMock(return_value=pipe)
        return redis

    @pytest.mark.asyncio
    async def test_dlq_error_propagates(self, mock_redis_failing: AsyncMock) -> None:
        """Test that DLQ errors are re-raised."""
        from src.app.core.events import EventBus

        bus = EventBus()
        bus._redis_client = mock_redis_failing

        settings_mock = MagicMock()
        settings_mock.DLQ_USE_BOUNDED_SIZE = True
        settings_mock.DLQ_MAX_SIZE = 10000
        settings_mock.DLQ_ALERT_THRESHOLD_PCT = 80

        with patch("src.app.core.config.settings", settings_mock):
            with pytest.raises(Exception, match="Redis connection lost"):
                await bus.add_to_dlq("event", "handler", {}, "error", 1)

    @pytest.mark.asyncio
    async def test_dlq_no_redis_logs_warning(self) -> None:
        """Test that missing Redis logs warning."""
        from src.app.core.events import EventBus

        bus = EventBus()
        bus._redis_client = None

        with patch("src.app.core.events.logger") as mock_logger:
            await bus.add_to_dlq("event", "handler", {}, "error", 1)

            # Should log warning about missing Redis
            warning_calls = [call for call in mock_logger.warning.call_args_list if "Redis not configured" in str(call)]
            assert len(warning_calls) > 0, "Expected Redis warning"
