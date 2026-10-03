"""Task API router - HTTP handlers delegating to service layer."""

from fastapi import APIRouter, Depends, status

from src.app.api.deps import RequestContextDep
from src.app.core.logger import get_logger
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.tasks.schemas import Job, TaskCreate, TaskStatus
from src.app.modules.tasks.service import task_service
from src.app.shared.exceptions import ModelNotFoundError

logger = get_logger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

# S-38: this module had NO permission gate at all — only the ambient auth
# dependency — so any authenticated principal on any tier could enqueue
# background jobs and read job status. Every other feature router declares
# `dependencies=[Depends(requires_permission(...))]`; this one did not.
#
# `TASKS_MANAGE` is a new slug, so it is ungranted by default and these routes
# now fail CLOSED (403) until an operator grants it. That is the intended
# direction: the alternative was open to every authenticated caller.
_TASK_PERMISSION = [Depends(requires_permission(Permission.TASKS_MANAGE))]


@router.post(
    "/task",
    response_model=Job,
    status_code=status.HTTP_201_CREATED,
    dependencies=_TASK_PERMISSION,
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


@router.get("/task/{task_id}", response_model=TaskStatus, dependencies=_TASK_PERMISSION)
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
