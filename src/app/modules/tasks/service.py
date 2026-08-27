"""Task service layer - encapsulates all task/job business logic."""

from typing import Any

from arq.jobs import Job as ArqJob

from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_arq

logger = get_logger(__name__)


class QueueUnavailableError(Exception):
    pass


class TaskService:
    """Service class for task/job-related business logic."""

    async def create_task(self, message: str, tenant_id: str | None = None) -> dict[str, str]:
        """Create a new background task.

        Idempotent per (tenant_id, message): re-submitting an identical
        request while the previous job still exists returns the SAME job id
        instead of failing (B7 FIX - previously raised ValueError -> HTTP 500).
        """
        arq = get_arq()
        if arq is None:
            raise QueueUnavailableError("Queue is not available")

        import hashlib

        job_id_input = f"{tenant_id}:{message}".encode()
        deterministic_job_id = f"sample_task:{hashlib.sha256(job_id_input).hexdigest()}"

        job = await arq.enqueue_job(
            "sample_background_task",
            message,
            tenant_id=tenant_id,
            _job_id=deterministic_job_id,
        )
        if job is None:
            # B7 FIX: arq returns None when a job with this id already exists
            # and has not expired. That is an idempotent re-create, not a
            # failure - surface the existing deterministic id instead of a 500.
            return {"id": deterministic_job_id}

        return {"id": job.job_id}

    async def get_task(self, task_id: str, tenant_id: str) -> dict[str, Any] | None:
        """Get information about a specific background task.

        Parameters
        ----------
        task_id : str
            The ID of the task
        tenant_id : str
            The tenant ID to verify ownership Against

        Returns
        -------
        dict[str, Any] | None
            Task information if found, None otherwise

        Raises
        ------
        QueueUnavailableError
            If queue is not available
        ValueError
            If tenant_id is missing
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for task isolation")

        arq = get_arq()
        if arq is None:
            raise QueueUnavailableError("Queue is not available")

        job = ArqJob(task_id, arq)
        job_info = await job.info()
        if job_info is None:
            return None

        # Enforce tenant isolation
        if job_info.kwargs.get("tenant_id") != tenant_id:
            return None

        # Get the current status from ARQ
        status_enum = await job.status()
        status = status_enum.value if hasattr(status_enum, "value") else str(status_enum)

        # Get result if job is finished
        result_str = None
        if status == "complete":
            try:
                res_info = await job.result_info()
                if res_info and res_info.result is not None:
                    result_str = str(res_info.result)
            except Exception as e:
                # B13 FIX: silent pass produced result:null with zero
                # telemetry; log so deserialization failures are observable.
                logger.warning(
                    "task_result_deserialization_failed",
                    task_id=task_id,
                    error=str(e),
                )
        elif status == "failed":
            result_str = "Task failed"

        return {
            "id": job_info.job_id,
            "status": status,
            "result": result_str,
        }


# Singleton instance for import convenience
task_service = TaskService()
