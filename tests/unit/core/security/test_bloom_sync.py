"""P2-3: Bloom Filter Periodic Sync Tests.

Verifies that Bloom filter syncs periodically via ARQ cron.
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestBloomFilterSync:
    """Tests for Bloom filter periodic sync."""

    def test_sync_task_registered_in_worker(self) -> None:
        """Verify sync_bloom_filter_task is in worker functions."""
        from src.app.core.worker.functions import sync_bloom_filter_task
        from src.app.core.worker.settings import WorkerSettings

        # Verify task exists in worker functions
        assert sync_bloom_filter_task is not None
        assert sync_bloom_filter_task in WorkerSettings.functions

    def test_cron_job_configured(self) -> None:
        """Verify cron job is configured for Bloom filter sync."""
        from src.app.core.worker.settings import WorkerSettings

        # Check cron_jobs list for bloom filter sync
        cron_funcs = [job.coroutine for job in WorkerSettings.cron_jobs]

        from src.app.core.worker.functions import sync_bloom_filter_task

        assert sync_bloom_filter_task in cron_funcs

    def test_cron_runs_every_5_minutes(self) -> None:
        """Verify cron runs every 5 minutes."""
        # Find the bloom filter sync cron job
        from src.app.core.worker.functions import sync_bloom_filter_task
        from src.app.core.worker.settings import WorkerSettings

        bloom_cron = None
        for job in WorkerSettings.cron_jobs:
            if job.coroutine == sync_bloom_filter_task:
                bloom_cron = job
                break

        assert bloom_cron is not None
        # Check it runs at 5-minute intervals (0, 5, 10, ..., 55)
        assert bloom_cron.minute == {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}

    @pytest.mark.asyncio
    async def test_sync_calls_bloom_filter_function(self) -> None:
        """Verify sync task calls sync_bloom_filter_from_redis."""
        from src.app.core.worker.functions import sync_bloom_filter_task

        with patch(
            "src.app.core.security.bloom_filter.sync_bloom_filter_from_redis",
            new_callable=AsyncMock,
        ) as mock_sync:
            mock_sync.return_value = 100

            ctx: dict = {}
            result = await sync_bloom_filter_task(ctx)

            mock_sync.assert_called_once()
            assert "100" in result

    @pytest.mark.asyncio
    async def test_sync_handles_errors(self) -> None:
        """Verify sync task handles errors gracefully."""
        from src.app.core.worker.functions import sync_bloom_filter_task

        with patch(
            "src.app.core.security.bloom_filter.sync_bloom_filter_from_redis",
            new_callable=AsyncMock,
        ) as mock_sync:
            mock_sync.side_effect = Exception("Redis connection failed")

            ctx: dict = {}

            with pytest.raises(Exception, match="Redis connection failed"):
                await sync_bloom_filter_task(ctx)
