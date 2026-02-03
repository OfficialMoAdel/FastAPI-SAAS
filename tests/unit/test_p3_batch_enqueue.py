import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock, call
from src.app.core.events import EventBus
from src.app.core.config import settings


@pytest.mark.asyncio
async def test_batch_enqueue_pipeline():
    """Verify batch enqueue uses single pipeline."""
    bus = EventBus()

    # Mock arq queue
    mock_queue = MagicMock()
    mock_pipeline = MagicMock()
    mock_pipeline.execute = AsyncMock()

    # Pipeline context manager
    # _publish_async_batch calls pipe = self._arq_queue.pipeline() directly (not as CM)
    # So we return the mock_pipeline directly
    mock_queue.pipeline.return_value = mock_pipeline

    # Fix: Ensure execute and enqueue_job return awaitables (coroutines)
    mock_pipeline.execute = AsyncMock(return_value=[True, True])
    mock_queue.enqueue_job = AsyncMock(return_value=True)

    bus._arq_queue = mock_queue

    # Enable batching
    with patch.object(settings.r1_unified, "EVENT_BUS_USE_BATCH_ENQUEUE", True):
        # We need >1 handlers to trigger batch
        h1 = AsyncMock()
        h2 = AsyncMock()
        bus.subscribe("TestEvent")(h1)
        bus.subscribe("TestEvent")(h2)

        # Make enqueue_job also return a valid coroutine in case fallback is triggered
        # or if it's called somewhere I missed.
        # But specifically mock_pipeline.execute needs to return awaitable.
        mock_pipeline.execute = AsyncMock(return_value=[True, True])

        # Ensure fallback doesn't happen due to exception in pipeline creation
        # (mock_pipeline_cm is already set up)

        await bus.publish("TestEvent", {"data": 1})

        # Verify pipeline used
        mock_queue.pipeline.assert_called_once()
        mock_pipeline.execute.assert_called_once()

        # Verify commands in pipeline (psetex or zadd or enqueue_job)
        # grep said "adds to pipe psetex and zadd" -> so likely direct Redis manipulation for ARQ
        # Verify psetex called twice (once for each handler job)
        assert mock_pipeline.psetex.call_count >= 2
