"""Task service layer - encapsulates all task/job business logic."""

import hashlib
from uuid import UUID

from arq.jobs import Job as ArqJob

from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_arq
from src.app.modules.tasks.schemas import Job, TaskCreate, TaskStatus
from src.app.shared.exceptions import ServiceUnavailableError

logger = get_logger(__name__)


class QueueUnavailableError(ServiceUnavailableError):
    pass


class TaskService:
    """Service class for task/job-related business logic."""

    async def create_task(self, data: TaskCreate, tenant_id: UUID | None = None) -> Job:
        """Create a new background task.

        Idempotent per (tenant_id, message): re-submitting an identical
        request while the previous job still exists returns the SAME job id
        instead of failing (B7 FIX - previously raised ValueError -> HTTP 500).
        """
        message = data.message
        if not message or not message.strip():
            raise ValueError("Task message cannot be empty")

        arq = get_arq()
        if arq is None:
            raise QueueUnavailableError("Queue is not available")

        # P2-C: UUID is the sole tenant type; arq serializes kwargs with
        # orjson (no UUID support), so stringify at the enqueue boundary.
        tenant_str = str(tenant_id) if tenant_id is not None else None
        job_id_input = f"{tenant_str}:{message}".encode()
        deterministic_job_id = f"sample_task:{hashlib.sha256(job_id_input).hexdigest()}"
        job = await arq.enqueue_job(
            "sample_background_task",
            message,
            tenant_id=tenant_str,
            _job_id=deterministic_job_id,
        )
        if job is None:
            # B7 FIX: arq returns None when a job with this id already exists
            # and has not expired. That is an idempotent re-create, not a
            # failure - surface the existing deterministic id instead of a 500.
            return Job(id=deterministic_job_id)

        return Job(id=job.job_id)

    async def get_task(self, task_id: str, tenant_id: UUID) -> TaskStatus | None:
        """Get information about a specific background task.

        Parameters
        ----------
        task_id : str
            The ID of the task
        tenant_id : UUID
            The tenant ID to verify ownership Against

        Returns
        -------
        TaskStatus | None
            Task information if found, None otherwise

        Raises
        ------
        QueueUnavailableError
            If queue is not available
        """
        arq = get_arq()
        if arq is None:
            raise QueueUnavailableError("Queue is not available")

        job = ArqJob(task_id, arq)
        job_info = await job.info()
        if job_info is None:
            return None

        # Enforce tenant isolation (stored kwarg is a str; see create_task)
        if job_info.kwargs.get("tenant_id") != str(tenant_id):
            return None

        if job_info.job_id is None:
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

        return TaskStatus(
            id=job_info.job_id,
            status=status,
            result=result_str,
        )


# Singleton instance for import convenience
task_service = TaskService()
