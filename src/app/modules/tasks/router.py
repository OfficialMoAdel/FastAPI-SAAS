"""Task API router - HTTP handlers delegating to service layer."""

from fastapi import APIRouter, Depends, HTTPException, status

from src.app.api.dependencies import get_current_user, rate_limiter_dependency
from src.app.core.logger import get_logger
from src.app.modules.tasks.schemas import Job, TaskCreate, TaskStatus
from src.app.modules.tasks.service import QueueUnavailableError, task_service

logger = get_logger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

from src.app.api.dependencies import AuthenticatedUser


@router.post(
    "/task",
    response_model=Job,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter_dependency)],
)
async def create_task(
    body: TaskCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Job:
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
    try:
        result = await task_service.create_task(body.message, tenant_id=str(current_user.tenant_id))
        return Job(**result)
    except QueueUnavailableError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue is not available")
    except ValueError as e:
        logger.error("Task creation failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while creating the task"
        )


@router.get("/task/{task_id}", response_model=TaskStatus)
async def get_task(
    task_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
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
    try:
        result = await task_service.get_task(task_id, tenant_id=str(current_user.tenant_id))
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        return TaskStatus(**result)
    except QueueUnavailableError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue is not available")
