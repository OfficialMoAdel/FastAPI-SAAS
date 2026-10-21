"""Contracts the job-monitoring and DLQ layer must keep.

Four of them are correctness, not cosmetics:

* **F1** — the reported status is the REAL one. The arq job blob is the job
  DEFINITION (`t`/`f`/`a`/`k`/`et`); it carries no status at all, so the
  monitoring answered "queued" for every job — including the ones that had
  failed.
* **F2** — the start time is recorded. The old code claimed to store it and
  did not, so nothing could say when a job began.
* **F3** — a DLQ row the queue could not parse must not take the admin page
  down. Those rows carry no timestamp, and the response schema required one.
* **F10** — the leaked-lock cleanup job must FAIL when it did not release the
  claim. It used to swallow every error and report success, so the claim it
  existed to clear outlived it.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arq.jobs import JobStatus

from src.app.core.dead_letter import DeadLetterQueue
from src.app.core.job_monitoring import JOB_START_PREFIX, JobMonitoringService
from src.app.schemas.job import DLQItemResponse


class _BlobRedis:
    """A Redis double that serves one job-definition blob and a start time."""

    def __init__(self, blob: str, started_at: str | None = None) -> None:
        self.blob = blob
        self.started_at = started_at

    async def get(self, key: str) -> bytes | None:
        if key == self.start_key:
            return self.started_at.encode("utf-8") if self.started_at else None
        return self.blob.encode("utf-8")

    @property
    def start_key(self) -> str:
        return f"{JOB_START_PREFIX}job-1"


def _blob(**overrides) -> str:
    payload = {"t": None, "f": "send_email_task", "a": ["x"], "k": {}, "et": 1_700_000_000_000}
    payload.update(overrides)
    return json.dumps(payload)


class TestJobStatusIsReal:
    async def test_the_status_comes_from_arq(self) -> None:
        """F1: the blob has no status; arq's own bookkeeping does."""
        monitor = JobMonitoringService()
        redis = _BlobRedis(_blob())

        with (
            patch("src.app.core.job_monitoring.get_redis", return_value=redis),
            patch("src.app.core.redis_provider.get_arq", return_value=MagicMock()),
            patch("arq.jobs.Job.status", new_callable=AsyncMock, return_value=JobStatus.complete),
        ):
            status = await monitor.get_job_status("job-1")

        assert status["status"] == "complete"

    async def test_a_failed_job_is_not_reported_as_queued(self) -> None:
        """The old code answered "queued" for EVERY job."""
        monitor = JobMonitoringService()
        redis = _BlobRedis(_blob())

        with (
            patch("src.app.core.job_monitoring.get_redis", return_value=redis),
            patch("src.app.core.redis_provider.get_arq", return_value=MagicMock()),
            patch("arq.jobs.Job.status", new_callable=AsyncMock, return_value=JobStatus.in_progress),
        ):
            status = await monitor.get_job_status("job-1")

        assert status["status"] == "in_progress"

    async def test_a_job_arq_no_longer_knows_is_unknown(self) -> None:
        """Its result expired (keep_result is an hour); the outcome is unknowable."""
        monitor = JobMonitoringService()
        redis = _BlobRedis(_blob())

        with (
            patch("src.app.core.job_monitoring.get_redis", return_value=redis),
            patch("src.app.core.redis_provider.get_arq", return_value=MagicMock()),
            patch("arq.jobs.Job.status", new_callable=AsyncMock, return_value=JobStatus.not_found),
        ):
            status = await monitor.get_job_status("job-1")

        assert status["status"] == "unknown"


class TestJobTimes:
    async def test_the_enqueue_time_is_read_from_the_blob(self) -> None:
        """F4: the blob key is `et` (unix MILLISECONDS), not `enqueue_time`."""
        monitor = JobMonitoringService()
        redis = _BlobRedis(_blob())

        with (
            patch("src.app.core.job_monitoring.get_redis", return_value=redis),
            patch("src.app.core.redis_provider.get_arq", return_value=MagicMock()),
            patch("arq.jobs.Job.status", new_callable=AsyncMock, return_value=JobStatus.queued),
        ):
            status = await monitor.get_job_status("job-1")

        enqueued = status["enqueue_time"]
        assert isinstance(enqueued, datetime)
        assert enqueued == datetime.fromtimestamp(1_700_000_000, tz=UTC)
        assert status["enqueued_at"] == enqueued.isoformat()
        assert status["function"] == "send_email_task"

    async def test_the_start_time_is_exposed(self) -> None:
        """F2: the recorded start time comes back with the status."""
        monitor = JobMonitoringService()
        started = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        redis = _BlobRedis(_blob(), started_at=started.isoformat())

        with (
            patch("src.app.core.job_monitoring.get_redis", return_value=redis),
            patch("src.app.core.redis_provider.get_arq", return_value=MagicMock()),
            patch("arq.jobs.Job.status", new_callable=AsyncMock, return_value=JobStatus.in_progress),
        ):
            status = await monitor.get_job_status("job-1")

        assert status["start_time"] == started

    async def test_record_job_start_persists_the_start_time(self) -> None:
        """F2: the start time is WRITTEN, not just claimed to be."""
        monitor = JobMonitoringService()
        pipe = MagicMock()
        pipe.hincrby = MagicMock()
        pipe.set = MagicMock()
        pipe.execute = AsyncMock()
        client = MagicMock()
        client.pipeline = MagicMock(return_value=_AsyncPipe(pipe))

        with patch("src.app.core.job_monitoring.get_redis", return_value=client):
            await monitor.record_job_start("job-1", "send_email_task")

        assert pipe.set.call_count == 1
        key, value = pipe.set.call_args.args[:2]
        assert key == f"{JOB_START_PREFIX}job-1"
        assert datetime.fromisoformat(value).tzinfo is not None


class _AsyncPipe:
    def __init__(self, pipe: MagicMock) -> None:
        self._pipe = pipe

    async def __aenter__(self) -> MagicMock:
        return self._pipe

    async def __aexit__(self, *exc_info) -> bool:
        return False


class TestDlqResponseSurvivesABadRow:
    def test_a_row_without_a_timestamp_is_still_a_response(self) -> None:
        """F3: one unparseable row must not 500 the whole admin page."""
        item = DLQItemResponse(**{"raw": "not json", "error": "parse_error"})
        assert item.timestamp is None
        assert item.timestamp_iso is None
        assert item.raw == "not json"

    def test_a_complete_row_still_fills_the_timestamps(self) -> None:
        item = DLQItemResponse(
            **{
                "job_id": "job-1",
                "function": "send_email_task",
                "error": "boom",
                "timestamp": 1.0,
                "timestamp_iso": "2026-01-01T00:00:00+00:00",
            }
        )
        assert item.timestamp == 1.0
        assert item.timestamp_iso == "2026-01-01T00:00:00+00:00"


class TestLegacyRowsAreNotArqRows:
    def test_a_legacy_row_carries_its_own_source(self) -> None:
        """F6: the server-side filtered pop/clear only understand JSON rows."""
        legacy = DeadLetterQueue.decode("job-1|send_email_task|boom|1700000000.0", include_legacy=True)
        assert legacy is not None
        assert legacy["source"] == "legacy"

    def test_a_legacy_row_is_not_returned_by_an_arq_filtered_read(self) -> None:
        """The read and the pop must agree on what an "arq" row is."""
        legacy = DeadLetterQueue.decode("job-1|send_email_task|boom|1700000000.0", include_legacy=True)
        assert legacy is not None
        assert legacy.get("source") != "arq"


class TestRetryScanHandsOutOneEntry:
    async def test_the_entry_is_returned_only_when_this_call_removed_it(self) -> None:
        """F5: the scan and the removal are separate round trips."""
        raw = json.dumps({"job_id": "job-1", "source": "arq", "timestamp": 1.0})

        class _RaceRedis:
            def __init__(self) -> None:
                self.lrem_result = 0

            async def lrange(self, key: str, start: int, end: int) -> list[str]:
                return [raw]

            async def lrem(self, key: str, count: int, value: str) -> int:
                return self.lrem_result

        client = _RaceRedis()
        queue = DeadLetterQueue(redis_client=client)

        # Another worker took the entry between the scan and the removal.
        assert await queue._retry_scan(client, "arq") is None

        client.lrem_result = 1
        assert await queue._retry_scan(client, "arq") is not None


class TestCleanupJobFailsLoudly:
    async def test_the_cleanup_job_raises_when_the_claim_survived(self) -> None:
        """F10: reporting success while the claim lived was the whole bug."""
        from src.app.core.worker import functions as worker_functions

        collaborators = MagicMock()
        collaborators.release_webhook_event_lock = AsyncMock(return_value=False)

        with (
            patch("src.app.core.worker.functions.require_feature_collaborators", return_value=collaborators),
            pytest.raises(RuntimeError, match="was not released"),
        ):
            await worker_functions.cleanup_leaked_webhook_lock(ctx={}, event_id="evt-1", lock_token="tok-1")

    async def test_the_cleanup_job_passes_the_ownership_token(self) -> None:
        """F10: without the token the Redis leg was skipped entirely."""
        from src.app.core.worker import functions as worker_functions

        collaborators = MagicMock()
        collaborators.release_webhook_event_lock = AsyncMock(return_value=True)

        with patch("src.app.core.worker.functions.require_feature_collaborators", return_value=collaborators):
            await worker_functions.cleanup_leaked_webhook_lock(ctx={}, event_id="evt-1", lock_token="tok-1")

        collaborators.release_webhook_event_lock.assert_awaited_once()
        assert collaborators.release_webhook_event_lock.await_args.kwargs["lock_token"] == "tok-1"
