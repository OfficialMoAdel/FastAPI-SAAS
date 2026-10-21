# NOTE (P2-E): the ~10 ``type: ignore`` comments below are deliberate —
# the redis-py async client returns untyped ``Any`` for get/scan/pipeline
# results, and these call sites re-shape into typed containers immediately.
from datetime import UTC, datetime
from typing import Any

from src.app.core.dead_letter import DeadLetterQueue
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis

logger = get_logger(__name__)

# Constants
JOB_PREFIX = "arq:job:"
JOB_STATS_PREFIX = "arq:stats:"
# F2: where a job's START time is kept. The monitoring service recorded the
# start of a job and then threw the timestamp away, so the status endpoint
# could never say when a job began.
JOB_START_PREFIX = "arq:job-start:"
# The start time outlives the arq result (keep_result, an hour) so the status
# view still explains a job that finished long ago.
JOB_START_TTL_SECONDS = 86_400
DLQ_KEY = DeadLetterQueue.key  # Backward-compatible name; policy lives in DeadLetterQueue.


class JobMonitoringService:
    """
    Service for monitoring background jobs (ARQ) and managing Dead Letter Queue.
    """

    async def get_job_status(self, job_id: str) -> dict[str, Any]:
        """
        Get the status and details of a specific job.

        Args:
            job_id: The ID of the job to query

        Returns:
            Dict containing job status, result, and metadata
        """
        client = get_redis()
        if not client:
            logger.warning("Redis client not initialized, cannot get job status")
            return {"status": "unknown", "error": "Redis unavailable"}

        key = f"{JOB_PREFIX}{job_id}"

        # K5 FIX: arq stores the job definition as a serialized STRING blob
        # (psetex in events/bus.py and inside arq itself), not a hash. The old
        # hgetall here raised WRONGTYPE -> 500. Read the string and parse JSON.
        from src.app.core.json_utils import json_loads

        blob = await client.get(key)  # type: ignore[misc]
        if not blob:
            return {"status": "not_found", "job_id": job_id}

        try:
            parsed_data: dict[str, Any] = json_loads(blob)  # type: ignore[arg-type]
            if not isinstance(parsed_data, dict):
                return {"status": "unknown", "job_id": job_id, "raw": str(blob)[:500]}
        except Exception:  # noqa: BLE001 - P1-17: corrupt payload falls back to unknown status
            logger.warning("Failed to deserialize arq job payload", job_id=job_id)
            return {"status": "unknown", "job_id": job_id, "raw": str(blob)[:500]}

        parsed_data["status"] = await self._job_status(job_id)

        # F4: arq's blob stores the enqueue time under `et` (unix
        # MILLISECONDS). The old read looked for `enqueue_time`, a key the blob
        # never has, so the conversion was dead code and the field stayed
        # unset. The value is also handed over as a datetime, not the raw
        # millisecond integer: the response schema types it as a datetime, and
        # an integer would be read as SECONDS and land far in the future.
        enqueue_ms = parsed_data.get("et")
        if isinstance(enqueue_ms, int | float) and enqueue_ms > 0:
            enqueued_at = datetime.fromtimestamp(enqueue_ms / 1000, tz=UTC)
            parsed_data["enqueue_time"] = enqueued_at
            parsed_data["enqueued_at"] = enqueued_at.isoformat()

        # F1: the blob's `f` is the job's function name; the response schema
        # calls it `function`.
        function_name = parsed_data.get("f")
        if isinstance(function_name, str):
            parsed_data["function"] = function_name

        # F2: the start time the monitoring service recorded when the job
        # began.
        started_raw = await client.get(f"{JOB_START_PREFIX}{job_id}")  # type: ignore[misc]
        if isinstance(started_raw, bytes):
            started_raw = started_raw.decode("utf-8", errors="replace")
        if isinstance(started_raw, str):
            try:
                parsed_data["start_time"] = datetime.fromisoformat(started_raw)
            except ValueError:
                logger.warning("job_start_time_unreadable", job_id=job_id)

        return parsed_data

    async def _job_status(self, job_id: str) -> str:
        """The REAL status of a job, from arq's own bookkeeping.

        F1: the blob is the job DEFINITION — arq stores ``t``/``f``/``a``/``k``/
        ``et`` in it and no status at all — so the old code answered
        "queued" for every job, including the ones that had failed. arq
        knows the truth: the result key, the in-progress key and the queue
        score.

        A job whose definition outlived arq's result (keep_result is an
        hour) is reported as "unknown" rather than guessed: its outcome is
        genuinely no longer knowable.
        """
        from arq.jobs import Job as ArqJob

        from src.app.core.redis_provider import get_arq

        arq = get_arq()
        if arq is None:
            return "unknown"
        try:
            status = await ArqJob(job_id, arq).status()
        except Exception as e:  # noqa: BLE001 - a monitoring read never fails the request
            logger.warning("job_status_lookup_failed", job_id=job_id, error=str(e))
            return "unknown"
        if status.value == "not_found":
            return "unknown"
        return status.value

    async def record_job_start(self, job_id: str, function_name: str) -> None:
        """
        Record the start of a job for metrics.
        """
        client = get_redis()
        if not client:
            return

        started_at = datetime.now(UTC)
        async with client.pipeline() as pipe:
            # Increment total jobs for function
            pipe.hincrby(f"{JOB_STATS_PREFIX}{function_name}", "total", 1)
            # F2: persist the START time. The old comment claimed this was
            # done "if we manage it" — it was not, so nothing could say when
            # a job began and the elapsed time could not be attributed to
            # one.
            pipe.set(
                f"{JOB_START_PREFIX}{job_id}",
                started_at.isoformat(),
                ex=JOB_START_TTL_SECONDS,
            )

            await pipe.execute()  # type: ignore[misc]

    async def record_job_success(self, job_id: str, function_name: str, duration: float) -> None:
        """
        Record a successful job execution.
        """
        client = get_redis()
        if not client:
            return

        async with client.pipeline() as pipe:
            key = f"{JOB_STATS_PREFIX}{function_name}"

            pipe.hincrby(key, "success", 1)
            # Update average duration (approximate) - simplistic approach
            # Better approach: store total duration and count, calculate avg on read
            pipe.hincrbyfloat(key, "total_duration", duration)

            await pipe.execute()  # type: ignore[misc]

    async def record_job_failure(self, job_id: str, function_name: str, error: str) -> None:
        """
        Record a failed job execution.
        """
        client = get_redis()
        if not client:
            return

        dlq = DeadLetterQueue(redis_client=client)
        dlq_entry = dlq.arq_entry(job_id, function_name, error)

        async with client.pipeline() as pipe:
            key = f"{JOB_STATS_PREFIX}{function_name}"

            pipe.hincrby(key, "failed", 1)
            dlq.stage(pipe, dlq_entry)

            await pipe.execute()  # type: ignore[misc]

    async def get_job_metrics(self) -> dict[str, dict[str, Any]]:
        """
        Get aggregated metrics for all job functions.
        """
        client = get_redis()
        if not client:
            return {}

        # Scan for stats keys
        cursor = 0
        stats = {}

        while True:
            cursor, keys = await client.scan(cursor, match=f"{JOB_STATS_PREFIX}*", count=100)  # type: ignore[no-any-return]
            if keys:
                async with client.pipeline(transaction=False) as pipe:  # type: ignore[no-any-return]
                    for key in keys:
                        pipe.hgetall(key)
                    datas = await pipe.execute()  # type: ignore[misc]

                for key, data in zip(keys, datas, strict=True):
                    key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                    func_name = key_str.replace(JOB_STATS_PREFIX, "")

                    if data:
                        stats[func_name] = self._parse_metrics(data)

            if cursor == 0:
                break

        return stats

    def _parse_metrics(self, data: dict[Any, Any]) -> dict[str, Any]:
        """
        Helper to parse Redis hash data into typed metrics.
        """
        parsed: dict[str, Any] = {}
        for k, v in data.items():
            k_str = k.decode("utf-8") if isinstance(k, bytes) else k
            v_str = v.decode("utf-8") if isinstance(v, bytes) else v

            if k_str in ("total", "success", "failed"):
                parsed[k_str] = int(v_str)
            elif k_str == "total_duration":
                parsed[k_str] = float(v_str)
            else:
                parsed[k_str] = v_str

        # Calculate derived metrics
        success = parsed.get("success", 0)
        total_duration = parsed.get("total_duration", 0.0)
        if success > 0:
            parsed["avg_duration"] = total_duration / success
        else:
            parsed["avg_duration"] = 0.0

        return parsed

    async def get_dead_letter_queue(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """
        Get items from the Dead Letter Queue.
        """
        return await DeadLetterQueue(redis_client=get_redis()).read(
            limit=limit,
            offset=offset,
            include_invalid=True,
            include_legacy=True,
        )

    async def get_dlq_size(self) -> int:
        """
        Get the current size of the Dead Letter Queue.
        """
        return await DeadLetterQueue(redis_client=get_redis()).size()

    async def clear_dlq(self) -> bool:
        """
        Clear the Dead Letter Queue.
        """
        client = get_redis()
        if not client:
            return False
        await DeadLetterQueue(redis_client=client).clear()
        return True

    async def get_summary(self) -> dict[str, Any]:
        """
        Get a high-level summary of job system health.
        """
        metrics = await self.get_job_metrics()
        dlq_size = await self.get_dlq_size()

        total_jobs = sum(m.get("total", 0) for m in metrics.values())
        total_failures = sum(m.get("failed", 0) for m in metrics.values())

        return {
            "total_jobs_processed": total_jobs,
            "total_failures": total_failures,
            "active_functions": list(metrics.keys()),
            "dlq_size": dlq_size,
            "status": "healthy" if dlq_size < 100 else "warning",  # Simple health check logic
        }


# Singleton instance
_job_monitor: JobMonitoringService | None = None


def get_job_monitor() -> JobMonitoringService:
    global _job_monitor
    if _job_monitor is None:
        _job_monitor = JobMonitoringService()
    return _job_monitor
