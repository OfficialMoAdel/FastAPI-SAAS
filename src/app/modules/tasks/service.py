"""Task service layer - encapsulates all task/job business logic."""

from typing import Any

from arq.jobs import Job as ArqJob
from fastapi import HTTPException

from ...core.utils import queue


class TaskService:
    """Service class for task/job-related business logic."""

    async def create_task(self, message: str) -> dict[str, str]:
        """Create a new background task.

        Parameters
        ----------
        message : str
            The message or data to be processed by the task

        Returns
        -------
        dict[str, str]
            Dictionary containing the task ID

        Raises
        ------
        HTTPException
            If queue is not available or task creation fails
        """
        if queue.pool is None:
            raise HTTPException(status_code=503, detail="Queue is not available")

        job = await queue.pool.enqueue_job("sample_background_task", message)
        if job is None:
            raise HTTPException(status_code=500, detail="Failed to create task")

        return {"id": job.job_id}

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get information about a specific background task.

        Parameters
        ----------
        task_id : str
            The ID of the task

        Returns
        -------
        dict[str, Any] | None
            Task information if found, None otherwise

        Raises
        ------
        HTTPException
            If queue is not available
        """
        if queue.pool is None:
            raise HTTPException(status_code=503, detail="Queue is not available")

        job = ArqJob(task_id, queue.pool)
        job_info = await job.info()
        if job_info is None:
            return None

        return job_info.__dict__


# Singleton instance for import convenience
task_service = TaskService()
