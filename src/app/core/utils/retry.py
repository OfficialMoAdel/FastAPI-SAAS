"""Lightweight retry and fallback utilities.

Provides:
- with_retry: async exponential backoff retry execution
- with_fallback: async primary/fallback execution
- with_fallback_sync: sync primary/fallback execution
- LuaScriptError: exception raised during Lua script execution failures
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from src.app.core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

class R1OptimizationError(Exception):
    """Base exception for optimization errors."""

    def __init__(self, message: str, optimization: str = "unknown") -> None:
        super().__init__(message)
        self.optimization = optimization
        self.timestamp = time.time()


class LuaScriptError(R1OptimizationError):
    """Error executing a Lua script."""

    def __init__(self, message: str, script_name: str = "unknown") -> None:
        super().__init__(message, optimization="lua_script")
        self.script_name = script_name

async def with_retry(
    fn: Callable[[], Awaitable[T]],
    max_attempts: int = 3,
    delay_seconds: float = 0.1,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[Exception, int], None] | None = None,
) -> T:
    """Execute function with exponential backoff retry.

    Args:
        fn: Async function to execute
        max_attempts: Maximum number of attempts
        delay_seconds: Initial delay between retries
        backoff_factor: Multiplier for delay on each retry
        exceptions: Tuple of exceptions to catch and retry
        on_retry: Optional callback on each retry (exception, attempt_number)

    Returns:
        Result from fn

    Raises:
        Exception: Last exception if all attempts fail
    """
    last_exception: Exception | None = None
    delay = delay_seconds

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except exceptions as e:
            last_exception = e

            if attempt == max_attempts:
                break

            if on_retry:
                on_retry(e, attempt)
            else:
                logger.debug(
                    "Attempt failed, retrying",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error=str(e),
                    delay=delay,
                )

            await asyncio.sleep(delay)
            delay *= backoff_factor

    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected: no exception but retry loop completed")


async def with_fallback(
    primary_fn: Callable[[], Awaitable[T]],
    fallback_fn: Callable[[], Awaitable[T]],
    error_msg: str = "Primary operation failed",
    log_level: int = logging.WARNING,
    reraise_on_fallback_failure: bool = True,
) -> T:
    """Execute primary function with automatic fallback on failure.

    Args:
        primary_fn: Async function to try first
        fallback_fn: Async function to use on failure
        error_msg: Message to log on primary failure
        log_level: Logging level for primary failure
        reraise_on_fallback_failure: Whether to raise if fallback also fails

    Returns:
        Result from primary_fn or fallback_fn
    """
    try:
        return await primary_fn()
    except asyncio.CancelledError:
        raise
    except Exception as primary_error:
        logger.warning(f"{error_msg}", error=str(primary_error))

        try:
            return await fallback_fn()
        except Exception as fallback_error:
            logger.error(
                "Fallback also failed after primary error",
                primary_error=str(primary_error),
                fallback_error=str(fallback_error),
            )
            if reraise_on_fallback_failure:
                if fallback_error is primary_error:
                    raise fallback_error
                raise fallback_error from primary_error
            raise


def with_fallback_sync(
    primary_fn: Callable[[], T],
    fallback_fn: Callable[[], T],
    error_msg: str = "Primary operation failed",
    log_level: int = logging.WARNING,
) -> T:
    """Synchronous version of with_fallback."""
    try:
        return primary_fn()
    except Exception as primary_error:
        logger.warning(f"{error_msg}", error=str(primary_error))
        return fallback_fn()


__all__ = [
    "LuaScriptError",
    "R1OptimizationError",
    "with_fallback",
    "with_fallback_sync",
    "with_retry",
]
