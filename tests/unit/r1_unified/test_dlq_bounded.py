"""Unit tests for P3-1: DLQ Bounded Size Optimization."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.events import EventBus


@pytest.mark.asyncio
async def test_bounded_dlq_uses_pipeline(mock_redis_client, mock_settings):
    """Test that bounded DLQ uses pipeline with LPUSH+LTRIM+LLEN."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Mock pipeline
    mock_pipeline = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.llen = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, True, 100])  # LPUSH, LTRIM, LLEN
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring.update_dlq_size") as mock_update:
            await event_bus.add_to_dlq(
                event_name="test.event",
                handler_name="test_handler",
                data={"test": "data"},
                error="Test error",
                attempt_count=3,
            )

    # Verify pipeline was used
    mock_redis_client.pipeline.assert_called_once()
    mock_pipeline.lpush.assert_called_once()
    mock_pipeline.ltrim.assert_called_once()
    mock_pipeline.llen.assert_called_once()
    mock_pipeline.execute.assert_called_once()

    # Verify LTRIM keeps newest entries (0 to max_size-1)
    ltrim_call = mock_pipeline.ltrim.call_args[0]
    assert ltrim_call[0] == "event_bus:dlq"
    assert ltrim_call[1] == 0
    assert ltrim_call[2] == 9999  # max_size - 1 (default 10000)

    # Verify metrics updated
    mock_update.assert_called_once_with(100, 10000)


@pytest.mark.asyncio
async def test_unbounded_dlq_uses_separate_calls(mock_redis_client, mock_settings):
    """Test that unbounded DLQ uses separate LPUSH+LLEN calls."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Disable bounded size
    mock_settings.DLQ_USE_BOUNDED_SIZE = False

    mock_redis_client.lpush = AsyncMock(return_value=1)
    mock_redis_client.llen = AsyncMock(return_value=50)

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring.update_dlq_size") as mock_update:
            await event_bus.add_to_dlq(
                event_name="test.event",
                handler_name="test_handler",
                data={"test": "data"},
                error="Test error",
                attempt_count=3,
            )

    # Verify separate calls (not pipeline)
    mock_redis_client.lpush.assert_called_once()
    mock_redis_client.llen.assert_called_once()
    mock_redis_client.pipeline.assert_not_called()

    # Verify metrics updated (no max_size for unbounded)
    mock_update.assert_called_once_with(50)


@pytest.mark.asyncio
async def test_dlq_max_size_enforced(mock_redis_client, mock_settings):
    """Test that DLQ max size is enforced via LTRIM."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Set custom max size
    mock_settings.DLQ_MAX_SIZE = 100

    # Mock pipeline
    mock_pipeline = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.llen = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, True, 100])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring.update_dlq_size"):
            await event_bus.add_to_dlq(
                event_name="test.event",
                handler_name="test_handler",
                data={"test": "data"},
                error="Test error",
                attempt_count=3,
            )

    # Verify LTRIM uses custom max size
    ltrim_call = mock_pipeline.ltrim.call_args[0]
    assert ltrim_call[2] == 99  # max_size - 1


@pytest.mark.asyncio
async def test_dlq_alert_threshold_warning(mock_redis_client, mock_settings):
    """Test that warning is logged when DLQ approaches capacity."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Set max size and alert threshold
    mock_settings.DLQ_MAX_SIZE = 1000
    mock_settings.DLQ_ALERT_THRESHOLD_PCT = 80

    # Mock pipeline returning size above threshold (850 > 800)
    mock_pipeline = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.llen = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, True, 850])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring.update_dlq_size"):
            with patch("src.app.core.events.logger") as mock_logger:
                await event_bus.add_to_dlq(
                    event_name="test.event",
                    handler_name="test_handler",
                    data={"test": "data"},
                    error="Test error",
                    attempt_count=3,
                )

    # Verify warning was logged (should be called twice: capacity warning + moved to DLQ)
    assert mock_logger.warning.call_count == 2

    # Find the capacity warning call
    capacity_warning = None
    for call in mock_logger.warning.call_args_list:
        if "DLQ approaching capacity" in call[0][0]:
            capacity_warning = call
            break

    assert capacity_warning is not None, "DLQ approaching capacity warning not found"
    assert capacity_warning[1]["dlq_size"] == 850
    assert capacity_warning[1]["max_size"] == 1000
    assert capacity_warning[1]["usage_pct"] == 85.0


@pytest.mark.asyncio
async def test_dlq_no_alert_below_threshold(mock_redis_client, mock_settings):
    """Test that no warning is logged when DLQ is below threshold."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Set max size and alert threshold
    mock_settings.DLQ_MAX_SIZE = 1000
    mock_settings.DLQ_ALERT_THRESHOLD_PCT = 80

    # Mock pipeline returning size below threshold (500 < 800)
    mock_pipeline = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.llen = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, True, 500])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring.update_dlq_size"):
            with patch("src.app.core.events.logger") as mock_logger:
                await event_bus.add_to_dlq(
                    event_name="test.event",
                    handler_name="test_handler",
                    data={"test": "data"},
                    error="Test error",
                    attempt_count=3,
                )

    # Verify no capacity warning (only the "moved to DLQ" warning)
    warning_calls = [call for call in mock_logger.warning.call_args_list
                     if "DLQ approaching capacity" in str(call)]
    assert len(warning_calls) == 0


@pytest.mark.asyncio
async def test_dlq_entry_format(mock_redis_client, mock_settings):
    """Test that DLQ entries have correct format with all required fields."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Mock pipeline
    mock_pipeline = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.llen = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, True, 1])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    test_data = {"user_id": 123, "action": "test"}
    test_error = "Connection timeout"

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring.update_dlq_size"):
            await event_bus.add_to_dlq(
                event_name="user.created",
                handler_name="send_welcome_email",
                data=test_data,
                error=test_error,
                attempt_count=3,
            )

    # Verify LPUSH was called with correct entry format
    lpush_call = mock_pipeline.lpush.call_args[0]
    assert lpush_call[0] == "event_bus:dlq"

    # Parse the JSON entry
    import json
    entry = json.loads(lpush_call[1])

    # Verify all required fields
    assert entry["event_name"] == "user.created"
    assert entry["handler"] == "send_welcome_email"
    assert entry["data"] == test_data
    assert entry["error"] == test_error
    assert entry["attempt_count"] == 3
    assert "failed_at" in entry

    # Verify timestamp is ISO format
    datetime.fromisoformat(entry["failed_at"])


@pytest.mark.asyncio
async def test_dlq_oldest_entries_discarded(mock_redis_client, mock_settings):
    """Test that LTRIM keeps newest entries and discards oldest."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Mock pipeline
    mock_pipeline = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.llen = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, True, 10000])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring.update_dlq_size"):
            await event_bus.add_to_dlq(
                event_name="test.event",
                handler_name="test_handler",
                data={"test": "data"},
                error="Test error",
                attempt_count=3,
            )

    # Verify LTRIM keeps range 0 to max_size-1 (newest entries)
    # LPUSH adds to head (index 0), so LTRIM 0 to N-1 keeps newest
    ltrim_call = mock_pipeline.ltrim.call_args[0]
    assert ltrim_call[1] == 0  # Start at head (newest)
    assert ltrim_call[2] == 9999  # End at max_size-1


@pytest.mark.asyncio
async def test_feature_flag_controls_optimization(mock_redis_client, mock_settings):
    """Test that feature flag controls bounded vs unbounded path."""
    event_bus = EventBus()
    event_bus.set_redis_client(mock_redis_client)

    # Test with flag enabled (bounded)
    mock_settings.DLQ_USE_BOUNDED_SIZE = True
    mock_pipeline = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.llen = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, True, 100])
    mock_redis_client.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring.update_dlq_size"):
            await event_bus.add_to_dlq(
                event_name="test.event",
                handler_name="test_handler",
                data={"test": "data"},
                error="Test error",
                attempt_count=3,
            )

    # Verify pipeline was used
    mock_redis_client.pipeline.assert_called_once()

    # Reset mocks
    mock_redis_client.reset_mock()
    mock_redis_client.lpush = AsyncMock(return_value=1)
    mock_redis_client.llen = AsyncMock(return_value=50)

    # Test with flag disabled (unbounded)
    mock_settings.DLQ_USE_BOUNDED_SIZE = False

    with patch("src.app.core.config.settings", mock_settings):
        with patch("src.app.core.monitoring.update_dlq_size"):
            await event_bus.add_to_dlq(
                event_name="test.event",
                handler_name="test_handler",
                data={"test": "data"},
                error="Test error",
                attempt_count=3,
            )

    # Verify separate calls were used (not pipeline)
    mock_redis_client.pipeline.assert_not_called()
    mock_redis_client.lpush.assert_called_once()
    mock_redis_client.llen.assert_called_once()
