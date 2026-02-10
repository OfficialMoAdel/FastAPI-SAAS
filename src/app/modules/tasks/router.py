"""Task API router - HTTP handlers delegating to service layer."""

from typing import Any

from fastapi import APIRouter, Depends

from ...api.dependencies import rate_limiter_dependency
from .schemas import Job, TaskCreate
from .service import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/task", response_model=Job, status_code=201, dependencies=[Depends(rate_limiter_dependency)])
async def create_task(body: TaskCreate) -> dict[str, str]:
    """Create a new background task.

    Parameters
    ----------
    body: TaskCreate
        Task creation request body containing the message.

    Returns
    -------
    dict[str, str]
        A dictionary containing the ID of the created task.
    """
    return await task_service.create_task(body.message)


@router.get("/task/{task_id}")
async def get_task(task_id: str) -> dict[str, Any] | None:
    """Get information about a specific background task.

    Parameters
    ----------
    task_id: str
        The ID of the task.

    Returns
    -------
    Optional[dict[str, Any]]
        A dictionary containing information about the task if found, or None otherwise.
    """
    return await task_service.get_task(task_id)
