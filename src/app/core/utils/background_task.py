import asyncio
from collections.abc import Coroutine
from typing import Any

# Keep strong references to background tasks to prevent them from being garbage
# collected mid-execution by the Python event loop.
_active_tasks: set[asyncio.Task[Any]] = set()


def create_safe_task(coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task[Any]:
    """Create a background task safely by maintaining a strong reference.

    This prevents the Python garbage collector from destroying the task mid-execution
    if no other references to it exist.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # If there's no running loop, create the task in the default event loop context if possible
        task = asyncio.create_task(coro, name=name)
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)
        return task

    task = loop.create_task(coro, name=name)
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    return task
