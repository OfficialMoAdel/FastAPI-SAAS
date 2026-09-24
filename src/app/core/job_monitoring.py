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

        parsed_data["status"] = "queued"

        # arq stores enqueue_time as unix MILLISECONDS; expose it readably.
        enqueue_ms = parsed_data.get("enqueue_time")
        if isinstance(enqueue_ms, int | float) and enqueue_ms > 0:
            parsed_data["enqueued_at"] = datetime.fromtimestamp(enqueue_ms / 1000, tz=UTC).isoformat()

        return parsed_data

    async def record_job_start(self, job_id: str, function_name: str) -> None:
        """
        Record the start of a job for metrics.
        """
        client = get_redis()
        if not client:
            return

        async with client.pipeline() as pipe:
            # Increment total jobs for function
            pipe.hincrby(f"{JOB_STATS_PREFIX}{function_name}", "total", 1)
            # Set start time in a temporary key or update the job key if we manage it
            # For simplicity in this service, we update the global stats mostly.

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
            for key in keys:
                key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                func_name = key_str.replace(JOB_STATS_PREFIX, "")

                data = await client.hgetall(key)  # type: ignore[misc]
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
