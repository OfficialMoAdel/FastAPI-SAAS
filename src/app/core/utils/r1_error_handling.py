"""
R1-UNIFIED Error Handling Utilities.

Provides unified error handling patterns for R1-UNIFIED optimizations:
- Custom exception hierarchy
- Graceful fallback execution
- Error logging and tracking
- Retry utilities

Usage:
    from src.app.core.utils.r1_error_handling import with_fallback, R1OptimizationError

    result = await with_fallback(
        primary_fn=lambda: optimized_operation(),
        fallback_fn=lambda: original_operation(),
        error_msg="Optimization failed",
    )
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from src.app.core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


# =============================================================================
# Exception Hierarchy
# =============================================================================


class R1OptimizationError(Exception):
    """Base exception for R1-UNIFIED optimization errors.

    All optimization-related exceptions inherit from this,
    allowing easy catching of any optimization failure.
    """

    def __init__(self, message: str, optimization: str = "unknown") -> None:
        super().__init__(message)
        self.optimization = optimization
        self.timestamp = time.time()


class LuaScriptError(R1OptimizationError):
    """Error executing a Lua script."""

    def __init__(self, message: str, script_name: str = "unknown") -> None:
        super().__init__(message, optimization="lua_script")
        self.script_name = script_name


class CacheError(R1OptimizationError):
    """Error with cache operations."""

    def __init__(self, message: str, cache_key: str | None = None) -> None:
        super().__init__(message, optimization="cache")
        self.cache_key = cache_key


class SessionError(R1OptimizationError):
    """Error with session operations."""

    def __init__(self, message: str, session_id: str | None = None) -> None:
        super().__init__(message, optimization="session")
        self.session_id = session_id


class RBACError(R1OptimizationError):
    """Error with RBAC operations."""

    def __init__(self, message: str, user_id: int | str | None = None) -> None:
        super().__init__(message, optimization="rbac")
        self.user_id = user_id


class DLQError(R1OptimizationError):
    """Error with Dead-Letter Queue operations."""

    def __init__(self, message: str, event_id: str | None = None) -> None:
        super().__init__(message, optimization="dlq")
        self.event_id = event_id


class PasskeyError(R1OptimizationError):
    """Error with passkey operations."""

    def __init__(self, message: str, credential_id: str | None = None) -> None:
        super().__init__(message, optimization="passkey")
        self.credential_id = credential_id


class CircuitBreakerError(R1OptimizationError):
    """Error with circuit breaker operations."""

    def __init__(self, message: str, circuit_name: str = "arq") -> None:
        super().__init__(message, optimization="circuit_breaker")
        self.circuit_name = circuit_name


# =============================================================================
# Fallback Utilities
# =============================================================================


async def with_fallback(
    primary_fn: Callable[[], Awaitable[T]],
    fallback_fn: Callable[[], Awaitable[T]],
    error_msg: str = "Primary operation failed",
    log_level: int = logging.WARNING,
    reraise_on_fallback_failure: bool = True,
) -> T:
    """Execute primary function with automatic fallback on failure.

    This is the core pattern for R1-UNIFIED optimizations:
    try the optimized path first, fall back to original on error.

    Args:
        primary_fn: Async function to try first (optimized path)
        fallback_fn: Async function to use on failure (original path)
        error_msg: Message to log on primary failure
        log_level: Logging level for primary failure
        reraise_on_fallback_failure: Whether to raise if fallback also fails

    Returns:
        Result from primary_fn or fallback_fn

    Raises:
        Exception: If both primary and fallback fail and reraise_on_fallback_failure=True

    Example:
        async def optimized():
            return await lua_manager.execute(redis, "fast_op", ...)

        async def original():
            return await redis.get(key)

        result = await with_fallback(optimized, original, "Lua script failed")
    """
    try:
        return await primary_fn()
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
                raise fallback_error from primary_error
            raise


def with_fallback_sync(
    primary_fn: Callable[[], T],
    fallback_fn: Callable[[], T],
    error_msg: str = "Primary operation failed",
    log_level: int = logging.WARNING,
) -> T:
    """Synchronous version of with_fallback.

    Args:
        primary_fn: Function to try first
        fallback_fn: Function to use on failure
        error_msg: Message to log on primary failure
        log_level: Logging level for primary failure

    Returns:
        Result from primary_fn or fallback_fn
    """
    try:
        return primary_fn()
    except Exception as primary_error:
        logger.warning(f"{error_msg}", error=str(primary_error))
        return fallback_fn()


# =============================================================================
# Retry Utilities
# =============================================================================


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


# =============================================================================
# Decorator Utilities
# =============================================================================


def fallback_on_error(
    fallback_value: T,
    exceptions: tuple[type[Exception], ...] = (R1OptimizationError,),
    log_level: int = logging.WARNING,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator that returns a fallback value on specific exceptions.

    Args:
        fallback_value: Value to return on exception
        exceptions: Tuple of exceptions to catch
        log_level: Logging level for caught exceptions

    Returns:
        Decorated function

    Example:
        @fallback_on_error(fallback_value=[], exceptions=(CacheError,))
        async def get_cached_items():
            return await cache.get("items")
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return await fn(*args, **kwargs)
            except exceptions as e:
                logger.warning(f"{fn.__name__} failed, using fallback", error=str(e))
                return fallback_value

        return wrapper

    return decorator


def log_optimization_error(
    optimization_name: str,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator that logs R1 optimization errors with context.

    Args:
        optimization_name: Name of the optimization for logging

    Returns:
        Decorated function
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return await fn(*args, **kwargs)
            except R1OptimizationError as e:
                logger.error(
                    f"[R1-{optimization_name}] {fn.__name__} failed",
                    optimization=e.optimization,
                    function=fn.__name__,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                raise
            except Exception as e:
                logger.error(
                    "Unexpected error in R1 optimization",
                    optimization_name=optimization_name,
                    function=fn.__name__,
                    error=str(e),
                )
                raise

        return wrapper

    return decorator


# =============================================================================
# Context Managers
# =============================================================================


class ErrorTracker:
    """Track errors for a specific optimization.

    Useful for monitoring error rates and triggering alerts.

    Example:
        tracker = ErrorTracker("xfetch")
        async with tracker.track():
            result = await xfetch_operation()

        if tracker.error_rate > 0.1:
            logger.warning("High error rate for xfetch")
    """

    def __init__(self, optimization_name: str, window_size: int = 100) -> None:
        """Initialize error tracker.

        Args:
            optimization_name: Name for logging
            window_size: Number of operations to track
        """
        self.name = optimization_name
        self.window_size = window_size
        self._results: list[bool] = []  # True = success, False = error
        self._error_count = 0
        self._total_count = 0

    def record_success(self) -> None:
        """Record a successful operation."""
        self._record(success=True)

    def record_error(self) -> None:
        """Record a failed operation."""
        self._record(success=False)

    def _record(self, success: bool) -> None:
        """Record an operation result."""
        self._total_count += 1

        if len(self._results) >= self.window_size:
            # Remove oldest result
            oldest = self._results.pop(0)
            if not oldest:
                self._error_count -= 1

        self._results.append(success)
        if not success:
            self._error_count += 1

    @property
    def error_rate(self) -> float:
        """Get current error rate (0.0 to 1.0)."""
        if not self._results:
            return 0.0
        return self._error_count / len(self._results)

    @property
    def success_rate(self) -> float:
        """Get current success rate (0.0 to 1.0)."""
        return 1.0 - self.error_rate

    def get_stats(self) -> dict[str, Any]:
        """Get tracker statistics."""
        return {
            "optimization": self.name,
            "window_size": self.window_size,
            "current_window": len(self._results),
            "error_rate": self.error_rate,
            "success_rate": self.success_rate,
            "total_operations": self._total_count,
            "errors_in_window": self._error_count,
        }


# =============================================================================
# Convenience Functions
# =============================================================================


def safe_get(
    data: dict[str, Any],
    key: str,
    default: T,
    expected_type: type[T] | None = None,
) -> T:
    """Safely get a value from a dict with type checking.

    Args:
        data: Dictionary to get value from
        key: Key to look up
        default: Default value if key missing or wrong type
        expected_type: Expected type of value

    Returns:
        Value from dict or default
    """
    value = data.get(key)

    if value is None:
        return default

    if expected_type is not None and not isinstance(value, expected_type):
        logger.debug(
            "Type mismatch for key",
            key=key,
            expected=expected_type.__name__,
            got=type(value).__name__,
        )
        return default

    return value  # type: ignore
