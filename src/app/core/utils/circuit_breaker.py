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
    # Ownership token for the lease above. `try_acquire_probe` stamps a fresh
    # token on every successful claim and returns it; `release_probe` /
    # `record_success` / `record_failure` / `clear_probe_async` release ONLY
    # when handed the token that is still current. Without it, a call admitted
    # while CLOSED that later failed cleared the lease of a DIFFERENT caller
    # that claimed the probe after the breaker flipped to HALF_OPEN — two
    # probes were then admitted against a recovering dependency, which is the
    # single-flight guarantee this whole mechanism exists to provide.
    _probe_token: int = field(default=0, init=False)

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

    async def record_success(self, probe_token: int | None = None) -> None:
        """Record successful call, potentially closing circuit.

        ``probe_token`` is the lease returned by :meth:`try_acquire_probe`.
        Pass it when this call owns the HALF_OPEN probe so the lease is
        released on success. Omit it (or pass ``None``) when the call was
        admitted while CLOSED: it holds no lease, and releasing one it does
        not own would admit a second probe against a recovering dependency.

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
            # Release the lease ONLY when this call owns it (see `_probe_token`).
            self._release_probe_if_owned(probe_token)

            # Capture the state once, BEFORE the probe release above, so a
            # recovery-window boundary cannot be crossed between two reads of
            # the computed `state` property.
            previous_state = self.state

            if previous_state == CircuitState.HALF_OPEN:
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
            elif previous_state == CircuitState.CLOSED and self._failure_count:
                # W3 FIX: a success in CLOSED proves the dependency is healthy.
                # Without resetting _failure_count here, failures accumulate
                # over the breaker's LIFETIME (across tenants/endpoints sharing
                # this instance) until failure_threshold trips it despite the
                # dependency currently being healthy.
                self._failure_count = 0

    async def record_failure(self, error: Exception | None = None, probe_token: int | None = None) -> None:
        """Record failed call, potentially opening circuit.

        ``probe_token`` has the same ownership semantics as in
        :meth:`record_success`: a failed HALF_OPEN probe releases the lease it
        claimed; a failed call admitted while CLOSED releases nothing.

        The transition is chosen from the state observed BEFORE this failure is
        recorded, because ``state`` is a COMPUTED property — it reports
        HALF_OPEN from ``OPEN`` plus an elapsed recovery window. The previous
        implementation refreshed ``_last_failure_time`` first and only then
        read ``state``, which made the HALF_OPEN arm unreachable for any
        ``recovery_timeout > 0``: a failed probe logged ``opened`` instead of
        ``reopened``, and a straggler from a CLOSED-admitted call re-opened the
        circuit, resetting the very recovery window it was already waiting on.
        """
        from src.app.core.config import settings
        from src.app.core.monitoring.r1_unified_metrics import record_circuit_breaker_transition

        async with self._get_lock():
            previous_state = self.state

            self._release_probe_if_owned(probe_token)
            self._failure_count += 1

            if previous_state == CircuitState.HALF_OPEN:
                # The probe failed: reopen and start a fresh recovery window.
                self._open()
                logger.warning(
                    "circuit_breaker_reopened",
                    breaker=self.name,
                    error=str(error),
                )
                # Record state transition for monitoring
                if settings.r1_unified.CIRCUIT_BREAKER_USE_DOUBLE_CHECK:
                    record_circuit_breaker_transition("open")
            elif previous_state == CircuitState.OPEN:
                # A straggler from a call admitted before the circuit opened.
                # It is already failing closed, and `_last_failure_time` is
                # left untouched on purpose: refreshing it here extended OPEN
                # by up to one recovery_timeout per in-flight call, so under
                # load the breaker could never reach its recovery window.
                return
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
        # `_probe_token` is deliberately NOT reset: it is a monotonic lease id,
        # and rewinding it would let a stale holder's token match a lease minted
        # after the reset (the ABA case). Clearing `_probe_in_progress` above is
        # what invalidates the outstanding lease.

    def reset_lock(self) -> None:
        """Reset the asyncio lock for new event loop (test isolation).

        Call this when the event loop changes to prevent 'Lock bound to
        different event loop' errors during test runs.
        """
        self._lock = asyncio.Lock()

    def try_acquire_probe(self) -> int | None:
        """Try to acquire the HALF_OPEN probe slot (non-blocking, caller must hold lock externally).

        The claim is a LEASE (D6). A claim older than ``recovery_timeout``
        belongs to a caller that died without releasing it — a cancelled
        probe, most often — and is reclaimed here, so a lost claim can block
        the breaker for at most one recovery window instead of forever.

        Returns:
            The lease token on success, ``None`` when another caller holds a
            fresh claim. The token is what makes the release ownership-checked
            (see :meth:`release_probe`). It is always a positive int, so the
            existing ``if not try_acquire_probe():`` idiom keeps meaning
            "could not claim".
        """
        if self._probe_in_progress:
            if time.time() - self._probe_claimed_at < self.recovery_timeout:
                return None
            logger.warning(
                "circuit_breaker_stale_probe_reclaimed",
                breaker=self.name,
                claimed_for_s=round(time.time() - self._probe_claimed_at, 1),
            )
        self._probe_token += 1
        self._probe_in_progress = True
        self._probe_claimed_at = time.time()
        return self._probe_token

    async def _admit(self) -> tuple[bool, int | None]:
        """Admit one call and report the probe lease it claimed (or ``None``)."""
        async with self._get_lock():
            state = self.state
            if state == CircuitState.CLOSED:
                return True, None
            if state == CircuitState.OPEN:
                return False, None
            token = self.try_acquire_probe()
            return (token is not None), token

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

    async def allow_single_flight_with_claim(self) -> tuple[bool, int | None]:
        """Admit one call and report the probe lease this call owns.

        Returns:
            ``(allowed, probe_token)``. ``probe_token`` is the lease this call
            claimed, or ``None`` when it claimed nothing (CLOSED admission, or
            OPEN rejection). Pass it to :meth:`release_probe` /
            ``record_success`` / ``record_failure`` so the release is
            ownership-checked: a CLOSED-admitted call must never clear a lease
            another caller took after the breaker flipped.
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
        allowed, probe_token = await self.allow_single_flight_with_claim()
        if not allowed:
            yield False
            return
        try:
            yield True
        finally:
            if probe_token is not None:
                self.release_probe(probe_token)

    def _release_probe_if_owned(self, probe_token: int | None) -> None:
        """Drop the lease only when ``probe_token`` is the current one.

        Caller must already hold the lock (``record_success`` /
        ``record_failure`` do; ``release_probe`` is the lock-free form).
        """
        if not probe_token or probe_token != self._probe_token:
            return
        self._probe_in_progress = False
        self._probe_claimed_at = 0.0

    def release_probe(self, probe_token: int | None = None) -> None:
        """Release probe slot (idempotent).

        Args:
            probe_token: The lease returned by :meth:`try_acquire_probe`.
                When given, the release happens ONLY if this call still owns
                that lease. ``None`` is the unconditional form, reserved for
                ``reset()`` and administrative paths.
        """
        if probe_token is None:
            self._probe_in_progress = False
            self._probe_claimed_at = 0.0
            return
        self._release_probe_if_owned(probe_token)

    async def clear_probe_async(self, probe_token: int | None = None) -> None:
        """Async-safe probe release for use in exception handlers.

        Same ownership semantics as :meth:`release_probe`: pass the lease to
        release only what this call claimed.
        """
        try:
            async with self._get_lock():
                self.release_probe(probe_token)
        except Exception:
            # Lock acquisition itself failed (a shutdown / loop-teardown
            # edge): fall back to a direct write so a cancelled probe still
            # cannot wedge the breaker. Still ownership-checked.
            self.release_probe(probe_token)


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
            probe_token: int | None = None

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
                            probe_token = breaker.try_acquire_probe()
                            if probe_token is None:
                                # Another request is already probing — reject immediately
                                recovery_in = breaker.recovery_timeout - (time.time() - breaker.last_failure_time)
                                raise CircuitBreakerOpen(breaker.name, max(0, recovery_in))
                            # try_acquire_probe stamps the lease, so this claim
                            # cannot be mistaken for a stale one.
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
                await breaker.record_success(probe_token)
                return result
            except Exception as e:
                await breaker.record_failure(e, probe_token)
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
                if probe_token is not None:
                    await breaker.clear_probe_async(probe_token)
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
