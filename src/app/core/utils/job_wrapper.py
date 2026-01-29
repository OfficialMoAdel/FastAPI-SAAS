import functools
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

from src.app.core.job_monitoring import get_job_monitor

F = TypeVar("F", bound=Callable[..., Any])


def monitor_job(function_name: str | None = None) -> Callable[[F], F]:
    """
    Decorator to monitor ARQ job execution.
    Records start, success (with duration), and failure (with error) to Redis.

    Args:
        function_name: Optional override for function name in metrics.
                       Defaults to func.__name__.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(ctx: dict | Any, *args: Any, **kwargs: Any) -> Any:
            monitor = get_job_monitor()
            # ARQ passes ctx as first argument. It's usually a dict.
            job_id = "unknown"
            if isinstance(ctx, dict):
                job_id = ctx.get("job_id", "unknown")
            elif hasattr(ctx, "job_id"):
                job_id = getattr(ctx, "job_id", "unknown")

            name = function_name or func.__name__

            await monitor.record_job_start(job_id, name)
            start_time = time.perf_counter()

            try:
                result = await func(ctx, *args, **kwargs)

                duration = time.perf_counter() - start_time
                await monitor.record_job_success(job_id, name, duration)
                return result
            except Exception as e:
                await monitor.record_job_failure(job_id, name, str(e))
                raise e

        return cast(F, wrapper)

    return decorator
