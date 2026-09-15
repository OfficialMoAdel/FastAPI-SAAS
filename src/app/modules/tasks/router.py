"""Task API router - HTTP handlers delegating to service layer."""

from fastapi import APIRouter, status

from src.app.api.deps import RequestContextDep
from src.app.core.logger import get_logger
from src.app.modules.tasks.schemas import Job, TaskCreate, TaskStatus
from src.app.modules.tasks.service import task_service
from src.app.shared.exceptions import ModelNotFoundError

logger = get_logger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post(
    "/task",
    response_model=Job,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    body: TaskCreate,
    ctx: RequestContextDep,
) -> Job:
    """Create a new background task.

    Parameters
    ----------
    body: TaskCreate
        Task creation request body containing the message.

    Returns
    -------
    Job
        The created task.
    """
    return await task_service.create_task(body, tenant_id=ctx.tenant_id)


@router.get("/task/{task_id}", response_model=TaskStatus)
async def get_task(
    task_id: str,
    ctx: RequestContextDep,
) -> TaskStatus:
    """Get information about a specific background task.

    Parameters
    ----------
    task_id: str
        The ID of the task.

    Returns
    -------
    TaskStatus
        Information about the task.

    Raises
    ------
    ModelNotFoundError
        404 if task not found.
    """
    result = await task_service.get_task(task_id, tenant_id=ctx.tenant_id)
    if result is None:
        raise ModelNotFoundError("Task", task_id)
    return result
