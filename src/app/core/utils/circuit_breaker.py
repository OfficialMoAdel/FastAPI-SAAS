"""Circuit breaker pattern for external service resilience.

Prevents cascade failures when external services (webhooks, email, etc.) are unavailable.
States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing recovery)
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

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
    # D6: when the probe slot was claimed. The claim is a LEASE: a claim older
    # than `recovery_timeout` belongs to a caller that died without releasing
    # it (a cancelled probe), and is reclaimed. Without this, one cancelled
    # probe wedges the breaker for the life of the process — every later
    # HALF_OPEN caller is rejected, which for the DB breaker means 503 for all
    # traffic.
    _probe_claimed_at: float = field(default=0.0, init=False)

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
        """Record successful call, potentially closing circuit.

        P0-4 fast path: a healthy CLOSED breaker with zero failures and no
        probe in flight is a locked-body no-op (the body only clears
        ``_probe_in_progress``, already False, then takes no branch), so
        return before the lock/imports. Every DB op wraps
        ``db_circuit_guard()``, making this the hottest path here.
        """
        if self._state is CircuitState.CLOSED and self._failure_count == 0 and not self._probe_in_progress:
            return
        from src.app.core.config import settings
        from src.app.core.monitoring.r1_unified_metrics import record_circuit_breaker_transition

        async with self._get_lock():
            # BUG FIX: Always clear the probe flag on success so the next HALF_OPEN
            # cycle can allow a new probe if the circuit opens again later.
            self._probe_in_progress = False
            self._probe_claimed_at = 0.0

            if self.state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._close()
                    logger.info(
                        "circuit_breaker_closed",
                        breaker=self.name,
                        success_threshold=self.success_threshold,
                    )
                    # Record state transition for monitoring
                    if settings.r1_unified.CIRCUIT_BREAKER_USE_DOUBLE_CHECK:
                        record_circuit_breaker_transition("closed")
            elif self.state == CircuitState.CLOSED and self._failure_count:
                # W3 FIX: a success in CLOSED proves the dependency is healthy.
                # Without resetting _failure_count here, failures accumulate
                # over the breaker's LIFETIME (across tenants/endpoints sharing
                # this instance) until failure_threshold trips it despite the
                # dependency currently being healthy.
                self._failure_count = 0

    async def record_failure(self, error: Exception | None = None) -> None:
        """Record failed call, potentially opening circuit."""
        from src.app.core.config import settings
        from src.app.core.monitoring.r1_unified_metrics import record_circuit_breaker_transition

        async with self._get_lock():
            # BUG FIX: Always clear probe flag on failure so the next HALF_OPEN
            # cycle allows a fresh probe after recovery_timeout elapses again.
            self._probe_in_progress = False
            self._probe_claimed_at = 0.0
            self._failure_count += 1
            self._last_failure_time = time.time()

            # BUG FIX: same virtual-HALF_OPEN issue as record_success — use the
            # `state` property (which computes HALF_OPEN) not `_state` (which is OPEN).
            if self.state == CircuitState.HALF_OPEN:
                self._open()
                logger.warning(
                    "circuit_breaker_reopened",
                    breaker=self.name,
                    error=str(error),
                )
                # Record state transition for monitoring
                if settings.r1_unified.CIRCUIT_BREAKER_USE_DOUBLE_CHECK:
                    record_circuit_breaker_transition("open")
            elif self._failure_count >= self.failure_threshold:
                self._open()
                logger.warning(
                    "circuit_breaker_opened",
                    breaker=self.name,
                    failure_threshold=self.failure_threshold,
                    error=str(error),
                )
                # Record state transition for monitoring
                if settings.r1_unified.CIRCUIT_BREAKER_USE_DOUBLE_CHECK:
                    record_circuit_breaker_transition("open")

    def _open(self) -> None:
        """Open the circuit (start rejecting requests)."""
        self._state = CircuitState.OPEN
        self._last_failure_time = time.time()
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
        self._probe_claimed_at = 0.0

    def reset_lock(self) -> None:
        """Reset the asyncio lock for new event loop (test isolation).

        Call this when the event loop changes to prevent 'Lock bound to
        different event loop' errors during test runs.
        """
        self._lock = asyncio.Lock()

    def try_acquire_probe(self) -> bool:
        """Try to acquire the HALF_OPEN probe slot (non-blocking, caller must hold lock externally).

        The claim is a LEASE (D6). A claim older than ``recovery_timeout``
        belongs to a caller that died without releasing it — a cancelled
        probe, most often — and is reclaimed here, so a lost claim can block
        the breaker for at most one recovery window instead of forever.
        """
        if self._probe_in_progress:
            if time.time() - self._probe_claimed_at < self.recovery_timeout:
                return False
            logger.warning(
                "circuit_breaker_stale_probe_reclaimed",
                breaker=self.name,
                claimed_for_s=round(time.time() - self._probe_claimed_at, 1),
            )
        self._probe_in_progress = True
        self._probe_claimed_at = time.time()
        return True

    async def _admit(self) -> tuple[bool, bool]:
        """Admit one call and report whether it claimed the HALF_OPEN probe."""
        async with self._get_lock():
            state = self.state
            if state == CircuitState.CLOSED:
                return True, False
            if state == CircuitState.OPEN:
                return False, False
            claimed = self.try_acquire_probe()
            return claimed, claimed

    async def allow_single_flight(self) -> bool:
        """Gate for enqueue-style callers during HALF_OPEN recovery.

        K9 FIX: ``is_closed`` returns True during virtual HALF_OPEN, so every
        concurrent caller passed the gate simultaneously and flooded the
        recovering dependency with enqueues. This method instead allows all
        calls when CLOSED, rejects all when OPEN, and admits exactly ONE
        caller in HALF_OPEN (it claims the probe slot; ``record_success`` /
        ``record_failure`` release it).

        The claim is a lease (see :meth:`try_acquire_probe`), so a caller that
        never releases it blocks the breaker for one recovery window at most.

        Callers that release the probe themselves must use
        :meth:`allow_single_flight_with_claim`: a call admitted while CLOSED
        must not release a probe another caller claimed after a state flip.
        """
        allowed, _ = await self._admit()
        return allowed

    async def allow_single_flight_with_claim(self) -> tuple[bool, bool]:
        """Admit one call and report whether this call owns the probe slot.

        Returns:
            ``(allowed, probe_claimed)``. ``probe_claimed`` is True only when
            the HALF_OPEN probe was claimed by THIS call, so the caller knows
            whether its release is an ownership-checked release or a no-op.
        """
        return await self._admit()

    @asynccontextmanager
    async def probe_gate(self) -> AsyncIterator[bool]:
        """Gate one call on the breaker, releasing the probe slot on every exit.

        ``is_closed`` is True during the virtual HALF_OPEN, so every caller
        that gates on it passes at once and floods a recovering dependency.
        This gate admits every call in CLOSED, none in OPEN, and exactly ONE
        in HALF_OPEN — the probe.

        The HALF_OPEN probe slot is released on EVERY exit path — success,
        failure, or cancellation — so a cancelled probe cannot wedge the
        breaker (the D6 failure mode). ``record_success``/``record_failure``
        inside the block may release it earlier; the release is idempotent.

        Only a call that actually claimed the probe releases it. A call
        admitted while CLOSED holds no probe, and if the breaker flips to
        HALF_OPEN while that call is in flight, an unconditional release
        would clear the claim of the caller that really owns the probe.

        Yields:
            True when the call may proceed.
        """
        allowed, probe_claimed = await self.allow_single_flight_with_claim()
        if not allowed:
            yield False
            return
        try:
            yield True
        finally:
            if probe_claimed:
                self.release_probe()

    def release_probe(self) -> None:
        """Release probe slot (idempotent)."""
        self._probe_in_progress = False
        self._probe_claimed_at = 0.0

    async def clear_probe_async(self) -> None:
        """Async-safe probe release for use in exception handlers."""
        try:
            async with self._get_lock():
                self._probe_in_progress = False
                self._probe_claimed_at = 0.0
        except Exception:
            self._probe_in_progress = False
            self._probe_claimed_at = 0.0


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
            probe_claimed = False

            # P4-1 Optimization: Double-check pattern for TOCTOU race prevention
            if settings.r1_unified.CIRCUIT_BREAKER_USE_DOUBLE_CHECK:
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
                            if not breaker.try_acquire_probe():
                                # Another request is already probing — reject immediately
                                recovery_in = breaker.recovery_timeout - (time.time() - breaker.last_failure_time)
                                raise CircuitBreakerOpen(breaker.name, max(0, recovery_in))
                            # try_acquire_probe stamps the lease, so this claim
                            # cannot be mistaken for a stale one.
                            probe_claimed = True
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
            except BaseException:
                # D6: a CANCELLED call is a BaseException, so the arm above
                # never runs and the HALF_OPEN probe slot would stay claimed
                # for the life of the process. Every later HALF_OPEN caller
                # would then be rejected with CircuitBreakerOpen — for the
                # DB breaker, 503 for all traffic until a restart.
                # Only clear a probe THIS call claimed: a CLOSED call that did
                # not claim one must not clear a probe claimed by another
                # caller after the state flipped.
                if probe_claimed:
                    await breaker.clear_probe_async()
                raise

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Named circuit-breaker singletons
#
# ``webhook_circuit`` / ``email_circuit`` / ``arq_circuit`` are constructed in
# ``core/resilience/breakers.py`` and re-exported from here lazily.
#
# They moved because a bare ``CircuitBreaker(...)`` does not register, and the
# admin panel reads only the registry — so these three were invisible to
# operations (unobservable, unresettable during an incident) while
# ``database`` and ``token_cache_circuit`` were visible purely because they are
# built with ``create_circuit_breaker()``, which registers as a side effect of
# construction. Moving them to the registering constructor closes the
# asymmetry.
#
# They moved *there* rather than being registered here because ``registry.py``
# imports this module at module level, so this module cannot import the
# registry back — the exact cycle that made ``_register_default_breakers()`` a
# ``pass`` with a commented-out call.
#
# P2-B: per-endpoint webhook breakers live in ``registry.get_webhook_breaker``
# (``webhook:{scope}``), which registers each scope on first use. The global
# below is what ``get_webhook_breaker(None)`` hands back.
# ---------------------------------------------------------------------------

_LAZY_BREAKERS = (
    "arq_circuit",
    "email_circuit",
    "reset_all_circuit_breakers",
    "webhook_circuit",
)

if TYPE_CHECKING:
    # Static-only re-exports: gives mypy the real objects for the lazy
    # __getattr__ facade below. Never executed at runtime.
    from src.app.core.resilience.breakers import arq_circuit as arq_circuit
    from src.app.core.resilience.breakers import email_circuit as email_circuit
    from src.app.core.resilience.breakers import (
        reset_all_circuit_breakers as reset_all_circuit_breakers,
    )
    from src.app.core.resilience.breakers import webhook_circuit as webhook_circuit


def __getattr__(name: str) -> Any:
    """Resolve the named breakers from their owning module.

    Lazy so ``core.utils.circuit_breaker`` never imports the registry at module
    level (see the note above); identity is preserved, so
    ``get_webhook_breaker(None) is webhook_circuit`` still holds.
    """
    if name in _LAZY_BREAKERS:
        from src.app.core.resilience import breakers

        return getattr(breakers, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ``reset_all_circuit_breakers`` moved with them: it has to know every named
# breaker, so it belongs next to the named breakers. It is re-exported lazily
# above for its existing import sites (tests/conftest.py).
