"""Task service layer - encapsulates all task/job business logic."""

from typing import Any

from arq.jobs import Job as ArqJob

from src.app.core.utils import queue


class QueueUnavailableError(Exception):
    pass


class TaskService:
    """Service class for task/job-related business logic."""

    async def create_task(self, message: str, tenant_id: str | None = None) -> dict[str, str]:
        """Create a new background task.

        Parameters
        ----------
        message : str
            The message or data to be processed by the task
        tenant_id : str | None
            The tenant ID associated with the task for isolation

        Returns
        -------
        dict[str, str]
            Dictionary containing the task ID

        Raises
        ------
        QueueUnavailableError
            If queue is not available
        ValueError
            If task creation fails
        """
        if queue.pool is None:
            raise QueueUnavailableError("Queue is not available")

        job = await queue.pool.enqueue_job("sample_background_task", message, tenant_id=tenant_id)
        if job is None:
            raise ValueError("Failed to create task")

        return {"id": job.job_id}

    async def get_task(self, task_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
        """Get information about a specific background task.

        Parameters
        ----------
        task_id : str
            The ID of the task
        tenant_id : str | None
            The tenant ID to verify ownership Against

        Returns
        -------
        dict[str, Any] | None
            Task information if found, None otherwise

        Raises
        ------
        QueueUnavailableError
            If queue is not available
        """
        if queue.pool is None:
            raise QueueUnavailableError("Queue is not available")

        job = ArqJob(task_id, queue.pool)
        job_info = await job.info()
        if job_info is None:
            return None

        # Enforce tenant isolation if tenant_id is provided
        if tenant_id and job_info.kwargs.get("tenant_id") != tenant_id:
            return None

        # Convert ARQ JobResult to dict using vars() for cleaner serialization
        return vars(job_info)


# Singleton instance for import convenience
task_service = TaskService()
