import asyncio
from collections.abc import Coroutine
from typing import Any

from src.app.core.logger import get_logger

logger = get_logger(__name__)

# Keep strong references to background tasks to prevent them from being garbage
# collected mid-execution by the Python event loop.
_active_tasks: set[asyncio.Task[Any]] = set()


def _handle_task_result(task: asyncio.Task[Any]) -> None:
    """Callback to remove task reference and log unhandled exceptions."""
    _active_tasks.discard(task)
    try:
        # Retrieve the result to raise any unhandled exception inside the task
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(
            "Unhandled exception in background task",
            task_name=task.get_name(),
            error=str(e),
            exc_info=True,
        )


def create_safe_task(coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task[Any]:
    """Create a background task safely by maintaining a strong reference.

    This prevents the Python garbage collector from destroying the task mid-execution
    if no other references to it exist. It also ensures unhandled exceptions within
    the task are logged rather than silently swallowed.
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
    task.add_done_callback(_handle_task_result)
    return task
