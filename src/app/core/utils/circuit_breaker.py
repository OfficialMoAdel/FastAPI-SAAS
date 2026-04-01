"""Circuit breaker pattern for external service resilience.

Prevents cascade failures when external services (webhooks, email, etc.) are unavailable.
States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing recovery)
"""

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, TypeVar

from src.app.core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, requests pass through
    OPEN = "open"  # Failing, requests are rejected immediately
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreaker:
    """Async circuit breaker for external service calls.

    Tracks failures and opens circuit to prevent repeated attempts to
    unavailable services. Automatically tests recovery after timeout.

    Attributes
    ----------
        name: Identifier for logging/metrics
        failure_threshold: Failures before opening circuit
        recovery_timeout: Seconds before testing recovery
        success_threshold: Successes in half-open before closing
    """

    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    success_threshold: int = 2

    # State tracking
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    # BUG FIX: Do NOT use default_factory=asyncio.Lock here.  Module-level
    # CircuitBreaker instances are created at import time, which can bind the
    # Lock to a stale/nonexistent event loop (DeprecationWarning / RuntimeError
    # in Python ≥ 3.10).  Use a None sentinel and create the lock lazily on
    # first acquisition so it is always bound to the running event loop.
    _lock: asyncio.Lock | None = field(default=None, init=False)
    # BUG FIX: Track whether a probe request is already in-flight during HALF_OPEN.
    # Without this flag, multiple concurrent requests can all probe simultaneously,
    # defeating the circuit breaker pattern and potentially overwhelming a recovering service.
    _probe_in_progress: bool = field(default=False, init=False)

    def _get_lock(self) -> asyncio.Lock:
        """Return the internal asyncio.Lock, creating it lazily on first access."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, checking for recovery timeout."""
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    @property
    def is_closed(self) -> bool:
        """Check if requests should be allowed."""
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    @property
    def last_failure_time(self) -> float:
        """Get the timestamp of the last recorded failure."""
        return self._last_failure_time

    @property
    def failure_count(self) -> int:
        """Get the current failure count."""
        return self._failure_count

    async def record_success(self) -> None:
        """Record successful call, potentially closing circuit."""
        from src.app.core.config import settings
        from src.app.core.monitoring.r1_unified_metrics import record_circuit_breaker_transition

        async with self._get_lock():
            # BUG FIX: Always clear the probe flag on success so the next HALF_OPEN
            # cycle can allow a new probe if the circuit opens again later.
            self._probe_in_progress = False

            # BUG FIX: The HALF_OPEN state is VIRTUAL — it is computed by the `state`
            # property (when _state==OPEN and recovery_timeout has elapsed), but
            # `_state` itself is still OPEN during the probe window. The old check
            # `self._state == CircuitState.HALF_OPEN` could never be True during a
            # real probe, so successful probes never closed the circuit (it stayed
            # visually HALF_OPEN forever). Use `self.state` (the property) instead.
            if self.state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._close()
                    logger.info(
                        "Circuit breaker '%s' closed after %d successful recovery tests",
                        self.name,
                        self.success_threshold,
                    )
                    # Record state transition for monitoring
                    if settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK:
                        record_circuit_breaker_transition("closed")

    async def record_failure(self, error: Exception | None = None) -> None:
        """Record failed call, potentially opening circuit."""
        from src.app.core.config import settings
        from src.app.core.monitoring.r1_unified_metrics import record_circuit_breaker_transition

        async with self._get_lock():
            # BUG FIX: Always clear probe flag on failure so the next HALF_OPEN
            # cycle allows a fresh probe after recovery_timeout elapses again.
            self._probe_in_progress = False
            self._failure_count += 1
            self._last_failure_time = time.time()

            # BUG FIX: same virtual-HALF_OPEN issue as record_success — use the
            # `state` property (which computes HALF_OPEN) not `_state` (which is OPEN).
            if self.state == CircuitState.HALF_OPEN:
                self._open()
                logger.warning(
                    "Circuit breaker '%s' re-opened after failure during recovery test | error=%s",
                    self.name,
                    str(error),
                )
                # Record state transition for monitoring
                if settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK:
                    record_circuit_breaker_transition("open")
            elif self._failure_count >= self.failure_threshold:
                self._open()
                logger.warning(
                    "Circuit breaker '%s' opened after %d failures | error=%s",
                    self.name,
                    self.failure_threshold,
                    str(error),
                )
                # Record state transition for monitoring
                if settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK:
                    record_circuit_breaker_transition("open")

    def _open(self) -> None:
        """Open the circuit (start rejecting requests)."""
        self._state = CircuitState.OPEN
        self._success_count = 0

    def _close(self) -> None:
        """Close the circuit (resume normal operation)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0

    def reset(self) -> None:
        """Reset circuit to closed state (for testing/admin)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._probe_in_progress = False

    def reset_lock(self) -> None:
        """Reset the asyncio lock for new event loop (test isolation).

        Call this when the event loop changes to prevent 'Lock bound to
        different event loop' errors during test runs.
        """
        self._lock = asyncio.Lock()


class CircuitBreakerOpen(Exception):
    """Raised when circuit is open and call is rejected."""

    def __init__(self, name: str, recovery_in: float):
        self.name = name
        self.recovery_in = recovery_in
        super().__init__(f"Circuit breaker '{name}' is open. Recovery in {recovery_in:.1f}s")


def circuit_breaker(
    breaker: CircuitBreaker,
) -> Callable[
    [Callable[..., Coroutine[Any, Any, T]]],
    Callable[..., Coroutine[Any, Any, T]],
]:
    """Decorator to wrap async functions with circuit breaker protection.

    Usage:
        webhook_breaker = CircuitBreaker(name="webhook", failure_threshold=5)

        @circuit_breaker(webhook_breaker)
        async def deliver_webhook(url: str, payload: dict):
            async with httpx.AsyncClient() as client:
                return await client.post(url, json=payload)
    """

    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]],
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            from src.app.core.config import settings

            state = breaker.state  # Fast path: no lock

            # P4-1 Optimization: Double-check pattern for TOCTOU race prevention
            if settings.CIRCUIT_BREAKER_USE_DOUBLE_CHECK:
                # Optimized path with double-check pattern
                if state == CircuitState.OPEN:
                    async with breaker._get_lock():
                        # Double-check under lock to prevent TOCTOU race
                        if breaker.state == CircuitState.OPEN:
                            recovery_in = breaker.recovery_timeout - (time.time() - breaker.last_failure_time)
                            raise CircuitBreakerOpen(breaker.name, max(0, recovery_in))
                        # State changed while acquiring lock, update local state
                        state = breaker.state

                # HALF_OPEN requires lock to prevent multiple probe requests
                if state == CircuitState.HALF_OPEN:
                    async with breaker._get_lock():
                        if breaker.state == CircuitState.HALF_OPEN:
                            # BUG FIX: Gate concurrent probes using _probe_in_progress flag.
                            # Previously, all concurrent requests in HALF_OPEN state were
                            # allowed to probe simultaneously, defeating the purpose. The dead
                            # code `if breaker._state == OPEN: breaker._state = HALF_OPEN` was
                            # also removed — it could never be True inside the HALF_OPEN guard.
                            if breaker._probe_in_progress:
                                # Another request is already probing — reject immediately
                                recovery_in = breaker.recovery_timeout - (time.time() - breaker.last_failure_time)
                                raise CircuitBreakerOpen(breaker.name, max(0, recovery_in))
                            # Claim the probe slot
                            breaker._probe_in_progress = True
                        elif breaker.state == CircuitState.OPEN:
                            # State changed to OPEN while acquiring lock
                            recovery_in = breaker.recovery_timeout - (time.time() - breaker.last_failure_time)
                            raise CircuitBreakerOpen(breaker.name, max(0, recovery_in))
                        # If CLOSED, continue without special handling
            else:
                # Original path (fallback)
                if state == CircuitState.OPEN:
                    recovery_in = breaker.recovery_timeout - (time.time() - breaker.last_failure_time)
                    raise CircuitBreakerOpen(breaker.name, max(0, recovery_in))

            try:
                result = await func(*args, **kwargs)
                await breaker.record_success()
                return result
            except Exception as e:
                await breaker.record_failure(e)
                raise

        return wrapper

    return decorator


# Pre-configured circuit breakers for common services
webhook_circuit = CircuitBreaker(
    name="webhook",
    failure_threshold=5,
    recovery_timeout=60.0,
    success_threshold=2,
)

email_circuit = CircuitBreaker(
    name="email",
    failure_threshold=3,
    recovery_timeout=120.0,  # Email servers may need longer recovery
    success_threshold=1,
)

# Circuit breaker for ARQ/Redis queue operations (used by EventBus)
arq_circuit = CircuitBreaker(
    name="arq_queue",
    failure_threshold=5,
    recovery_timeout=30.0,  # Shorter timeout for Redis recovery
    success_threshold=2,
)


def _register_default_breakers() -> None:
    """Register default circuit breakers.

    This is called at module load time to ensure critical breakers exist.
    """
    # Import inside function to avoid circular imports
    # from src.app.core.resilience.registry import ServiceType, get_registry

    # We cannot import from registry due to circular imports
    # The registry module imports CircuitBreaker from this module
    # So we should rely on explicit registration in resilience/__init__.py or app startup
    pass


# Auto-register defaults on import
# _register_default_breakers()


def reset_all_circuit_breakers() -> None:
    """Reset all module-level circuit breakers for test isolation.

    This function resets both the state and the asyncio.Lock for each
    module-level circuit breaker. Call this in test fixtures to prevent
    'Lock bound to different event loop' errors during coverage runs.
    """
    for breaker in (webhook_circuit, email_circuit, arq_circuit):
        breaker.reset()
        breaker.reset_lock()
