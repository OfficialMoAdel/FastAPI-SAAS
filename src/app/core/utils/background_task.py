import asyncio
from collections.abc import Coroutine, Mapping
from functools import partial
from typing import Any

from src.app.core.logger import get_logger

logger = get_logger(__name__)

# Keep strong references to background tasks to prevent them from being garbage
# collected mid-execution by the Python event loop.
_active_tasks: set[asyncio.Task[Any]] = set()


def _handle_task_result(task: asyncio.Task[Any], log_context: Mapping[str, Any] | None = None) -> None:
    """Callback to remove task reference and log unhandled exceptions.

    ``log_context`` is merged into the failure log so the ONE handler that owns
    this responsibility can still name what the task was doing (R2-17). Callers
    used to add a second done-callback purely to get that context, which logged
    every failure twice.
    """
    _active_tasks.discard(task)
    try:
        # Retrieve the result to raise any unhandled exception inside the task
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        fields: dict[str, Any] = {
            "task_name": task.get_name(),
            "error": str(e),
            "exc_info": True,
        }
        if log_context:
            # Explicit fields win: a context key must not be able to shadow the
            # error that actually occurred.
            fields = {**log_context, **fields}
        logger.error("Unhandled exception in background task", **fields)


def create_safe_task(
    coro: Coroutine[Any, Any, Any],
    name: str | None = None,
    *,
    log_context: Mapping[str, Any] | None = None,
) -> asyncio.Task[Any]:
    """Create a background task safely by maintaining a strong reference.

    This prevents the Python garbage collector from destroying the task mid-execution
    if no other references to it exist. It also ensures unhandled exceptions within
    the task are logged rather than silently swallowed.

    Args:
        coro: The coroutine to run.
        name: Optional task name, surfaced in the failure log.
        log_context: Extra structured fields for the failure log (e.g.
            ``{"action": "user.created"}``). The single owner of task-failure
            logging reads this; do not add a second done-callback to log again.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # If there's no running loop, we cannot create a task.
        # Let it fail visibly rather than trying to use asyncio.create_task
        # which would also throw the same RuntimeError.
        logger.error("Cannot create safe task: No running event loop.", task_name=name)
        raise

    task = loop.create_task(coro, name=name)
    _active_tasks.add(task)
    task.add_done_callback(partial(_handle_task_result, log_context=log_context))
    return task
