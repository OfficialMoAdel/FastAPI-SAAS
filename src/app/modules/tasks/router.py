"""Task API router - HTTP handlers delegating to service layer."""


from fastapi import APIRouter, Depends, HTTPException

from ...api.dependencies import get_current_user, rate_limiter_dependency
from .schemas import Job, TaskCreate, TaskStatus
from .service import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/task", response_model=Job, status_code=201, dependencies=[Depends(rate_limiter_dependency)])
async def create_task(
    body: TaskCreate,
    current_user: dict = Depends(get_current_user),
) -> dict[str, str]:
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


@router.get("/task/{task_id}", response_model=TaskStatus)
async def get_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
) -> TaskStatus:
    """Get information about a specific background task.

    Parameters
    ----------
    task_id: str
        The ID of the task.

    Returns
    -------
    TaskStatus
        A dictionary containing information about the task.

    Raises
    ------
    HTTPException
        404 if task not found.
    """
    result = await task_service.get_task(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatus(**result)
