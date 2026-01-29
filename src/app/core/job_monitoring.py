import logging
import time
from datetime import UTC, datetime
from typing import Any, Optional

from src.app.core.utils import cache

logger = logging.getLogger(__name__)

# Constants
JOB_PREFIX = "arq:job:"
JOB_STATS_PREFIX = "arq:stats:"
DLQ_KEY = "arq:dlq"  # Dead Letter Queue key


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
        if not cache.client:
            logger.warning("Redis client not initialized, cannot get job status")
            return {"status": "unknown", "error": "Redis unavailable"}

        key = f"{JOB_PREFIX}{job_id}"

        # arq stores job data as a hash
        # Fields typically include: function, args, kwargs, job_try, enqueue_time,
        # start_time, finish_time, result, status
        # However, arq actually serializes the job definition.
        # But for monitoring, we often rely on what we store or what arq stores.
        # Let's assume standard arq behavior + our custom stats tracking.

        # Check standard ARQ job result if available
        # Note: ARQ stores results with a specific key pattern or in the job object itself if using Result backend.
        # Here we query what's in Redis.

        data = await cache.client.hgetall(key)  # type: ignore
        if not data:
            return {"status": "not_found", "job_id": job_id}

        # Parse byte data to strings/types
        parsed_data = {}
        for k, v in data.items():
            k_str = k.decode("utf-8") if isinstance(k, bytes) else k
            v_val = v.decode("utf-8") if isinstance(v, bytes) else v
            parsed_data[k_str] = v_val

        # Determine status
        # status = parsed_data.get("status", "unknown") # Unused variable

        # Calculate duration if applicable
        start_ts = float(parsed_data.get("start_time", 0) or 0)
        finish_ts = float(parsed_data.get("finish_time", 0) or 0)
        duration = None
        if start_ts and finish_ts:
            duration = finish_ts - start_ts
        elif start_ts:
            duration = time.time() - start_ts  # Running duration

        parsed_data["duration_seconds"] = duration

        return parsed_data

    async def record_job_start(self, job_id: str, function_name: str) -> None:
        """
        Record the start of a job for metrics.
        """
        if not cache.client:
            return

        # now = time.time() # Unused variable
        pipe = cache.client.pipeline()

        # Increment total jobs for function
        pipe.hincrby(f"{JOB_STATS_PREFIX}{function_name}", "total", 1)
        # Set start time in a temporary key or update the job key if we manage it
        # For simplicity in this service, we update the global stats mostly.

        await pipe.execute()  # type: ignore

    async def record_job_success(self, job_id: str, function_name: str, duration: float) -> None:
        """
        Record a successful job execution.
        """
        if not cache.client:
            return

        pipe = cache.client.pipeline()
        key = f"{JOB_STATS_PREFIX}{function_name}"

        pipe.hincrby(key, "success", 1)
        # Update average duration (approximate) - simplistic approach
        # Better approach: store total duration and count, calculate avg on read
        pipe.hincrbyfloat(key, "total_duration", duration)

        await pipe.execute()  # type: ignore

    async def record_job_failure(self, job_id: str, function_name: str, error: str) -> None:
        """
        Record a failed job execution.
        """
        if not cache.client:
            return

        pipe = cache.client.pipeline()
        key = f"{JOB_STATS_PREFIX}{function_name}"

        pipe.hincrby(key, "failed", 1)

        # Add to Dead Letter Queue
        dlq_entry = f"{job_id}|{function_name}|{error}|{time.time()}"
        pipe.lpush(DLQ_KEY, dlq_entry)
        # Trim DLQ to prevent infinite growth (e.g., keep last 1000)
        pipe.ltrim(DLQ_KEY, 0, 999)

        await pipe.execute()  # type: ignore

    async def get_job_metrics(self) -> dict[str, dict[str, Any]]:
        """
        Get aggregated metrics for all job functions.
        """
        if not cache.client:
            return {}

        # Scan for stats keys
        cursor = 0
        stats = {}

        while True:
            cursor, keys = await cache.client.scan(cursor, match=f"{JOB_STATS_PREFIX}*", count=100)  # type: ignore
            for key in keys:
                key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                func_name = key_str.replace(JOB_STATS_PREFIX, "")

                data = await cache.client.hgetall(key)  # type: ignore
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
        if not cache.client:
            return []

        # LRANGE is 0-based inclusive
        items = await cache.client.lrange(DLQ_KEY, offset, offset + limit - 1)  # type: ignore
        parsed_items = []

        for item in items:
            item_str = item.decode("utf-8") if isinstance(item, bytes) else item
            try:
                # Format: job_id|function_name|error|timestamp
                parts = item_str.split("|", 3)
                if len(parts) >= 4:
                    parsed_items.append(
                        {
                            "job_id": parts[0],
                            "function": parts[1],
                            "error": parts[2],
                            "timestamp": float(parts[3]),
                            "timestamp_iso": datetime.fromtimestamp(float(parts[3]), tz=UTC).isoformat(),
                        }
                    )
                else:
                    parsed_items.append({"raw": item_str, "error": "parse_error"})
            except Exception as e:
                logger.error(f"Failed to parse DLQ item: {item_str}, error: {e}")
                parsed_items.append({"raw": item_str, "error": str(e)})

        return parsed_items

    async def get_dlq_size(self) -> int:
        """
        Get the current size of the Dead Letter Queue.
        """
        if not cache.client:
            return 0
        return await cache.client.llen(DLQ_KEY)  # type: ignore

    async def clear_dlq(self) -> bool:
        """
        Clear the Dead Letter Queue.
        """
        if not cache.client:
            return False
        await cache.client.delete(DLQ_KEY)  # type: ignore
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
_job_monitor: Optional[JobMonitoringService] = None


def get_job_monitor() -> JobMonitoringService:
    global _job_monitor
    if _job_monitor is None:
        _job_monitor = JobMonitoringService()
    return _job_monitor
