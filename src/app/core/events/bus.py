"""The EventBus class, its singleton instance, and the test-reset helper."""

import asyncio
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import UTC
from typing import Any, cast

from arq.connections import ArqRedis

from src.app.core import events as _pkg
from src.app.core.events.priority import EventPriority

_UNSET = object()


def _concurrency_limit() -> int:
    """Read the sync-handler concurrency bound live from settings (F13).

    ``getattr`` + fallback mirrors the RETRY_DELAYS pattern: tests patch
    ``_pkg.settings`` with a MagicMock, and a missing attribute must fall
    back to the configured default (50) rather than raising.
    """
    return int(getattr(_pkg.settings, "EVENT_BUS_CONCURRENCY", None) or 50)


class EventBus:
    """Central event bus for decoupled module communication.

    Features:
    - Priority-based event handling (CRITICAL=sync, NORMAL/LOW=async)
    - Dead-letter queue (DLQ) backed by Redis for production reliability
    - Event idempotency via Redis-backed duplicate detection
    - Event ordering guarantees via sequence numbers
    - ARQ integration for background processing
    """

    # Maximum retry attempts before moving to DLQ
    MAX_RETRIES = 3
    # M-9: retry backoff in seconds — configurable via EVENT_BUS_RETRY_DELAYS
    # (settings.events). Resolved per-instance in __init__ (workers may run
    # with different env than web). Class attr remains as documented default.
    RETRY_DELAYS: list[int] = [60, 300, 900]
    # Phase 1 DLQ unification: the single surviving queue is the monitor's
    # ``arq:dlq`` (see src/app/core/job_monitoring.py DLQ_KEY). The legacy
    # ``event_bus:dlq`` key is retired; every writer/reader below targets the
    # unified key so admin GET /dlq sees bus failures too.
    DLQ_REDIS_KEY = "arq:dlq"
    # Retired bus-only key (pre-unification). Kept as a named constant so
    # history/grep still resolves; nothing should read or write it.
    LEGACY_DLQ_REDIS_KEY = "event_bus:dlq"
    # Redis key prefix for event sequence counters (per event type)
    SEQUENCE_KEY_PREFIX = "event_bus:seq:"

    def __init__(self) -> None:
        """Initialize event bus with handler registry."""
        self._handlers: dict[str, list[Callable[..., Coroutine[Any, Any, None]]]] = defaultdict(list)
        self._arq_queue: ArqRedis | None = None
        self._redis_client: Any = _UNSET
        self._registration_complete = False
        # M-9: pull the backoff schedule from settings (default [60,300,900]).
        # Flat access matches every other EVENT_BUS_* read here; tests patch
        # _pkg.settings with a MagicMock, so fall back to the class default
        # when the attribute is missing.
        self.RETRY_DELAYS = list(getattr(_pkg.settings, "EVENT_BUS_RETRY_DELAYS", None) or type(self).RETRY_DELAYS)
        # P3-2: Semaphore to bound concurrent sync execution
        self._concurrency_semaphore = asyncio.Semaphore(_concurrency_limit())

    @property
    def is_ready(self) -> bool:
        """Check if event handlers have been registered."""
        return self._registration_complete

    def set_redis_client(self, redis_client: Any) -> None:
        """Set Redis client for DLQ persistence.

        Args:
            redis_client: Async Redis client instance
        """
        self._redis_client = redis_client
        _pkg.logger.info("Event bus Redis client configured for DLQ persistence")

    @property
    def redis_client(self) -> Any:
        """Call-time acquisition of Redis client from provider if not explicitly injected."""
        if self._redis_client is not _UNSET:
            return self._redis_client
        from src.app.core.redis_provider import get_redis

        return get_redis()

    def set_arq_queue(self, queue: Any) -> None:
        """Set ARQ queue for async event processing.

        Args:
            queue: ARQ queue instance
        """
        self._arq_queue = queue

    async def get_next_sequence(self, event_name: str) -> int:
        """Get the next sequence number for an event type.

        Sequence numbers are monotonically increasing per event type,
        enabling consumers to detect gaps and process events in order.

        Returns 0 if Redis is not configured (ordering not guaranteed).
        Returns -1 on Redis error (sentinel for unexpected failure — consumers
        should log/alert on negative sequence numbers).

        Args:
            event_name: Name of the event type

        Returns
        -------
            Next sequence number (starting from 1), 0 if Redis unavailable,
            or -1 on Redis error.
        """
        client = self.redis_client
        if not client:
            return 0  # No ordering guarantee without Redis

        seq_key = f"{self.SEQUENCE_KEY_PREFIX}{event_name}"
        try:
            return cast(int, await client.incr(seq_key))
        except Exception as e:
            _pkg.logger.error(
                "Failed to get sequence from Redis",
                event_name=event_name,
                error=str(e),
            )
            _pkg.logger.warning(
                "Falling back to -1 sentinel — ordering guarantees compromised",
                event_name=event_name,
            )
            # BUG FIX: Return -1 (not 0) so consumers can distinguish between
            # 'no Redis configured' (0) and 'Redis error occurred' (-1).
            # Previously both returned 0, making error cases invisible to consumers.
            return -1

    def subscribe(self, event_name: str) -> Callable:
        """Decorator to subscribe a handler to an event.

        Args:
            event_name: Name of event to subscribe to

        Returns
        -------
            Decorator function

        Example:
            @event_bus.subscribe("user.created")
            async def send_welcome_email(data: dict):
                ...
        """

        def decorator(func: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., Coroutine[Any, Any, None]]:
            self._handlers[event_name].append(func)
            _pkg.logger.info("Event handler registered", event_name=event_name, handler=func.__name__)
            return func

        return decorator

    def register_handler(
        self,
        event_name: str,
        handler: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        """Register a handler function for an event explicitly.

        Args:
            event_name: Name of event to subscribe to
            handler: Async function to handle the event
        """
        self._handlers[event_name].append(handler)
        _pkg.logger.info("Event handler registered", event_name=event_name, handler=handler.__name__)

    # TTL for processed event IDs (24 hours)
    IDEMPOTENCY_TTL_SECONDS = 86400
    # Redis key prefix for processed event IDs
    IDEMPOTENCY_KEY_PREFIX = "event_bus:processed:"

    async def _check_idempotency(self, event_id: str) -> bool:
        """Check if event was already processed using atomic Lua script.

        Uses SETNX pattern for race-condition-free idempotency check.
        The Lua script atomically checks and sets the key in a single operation.

        Args:
            event_id: Unique event identifier

        Returns:
            True if event is a duplicate (already processed), False if new

        Note:
            Caller must ensure self._redis_client is not None before calling.
        """
        client = self.redis_client
        if client is None:
            return False  # Can't check idempotency without Redis

        idempotency_key = f"{self.IDEMPOTENCY_KEY_PREFIX}{event_id}"

        if _pkg.settings.EVENT_BUS_USE_LUA_IDEMPOTENCY:
            # Optimized path: Atomic Lua script (no race condition)
            try:
                lua_manager = _pkg.get_lua_manager()
                result = await lua_manager.execute(
                    client,
                    "idempotency_check",
                    keys=[idempotency_key],
                    args=[str(self.IDEMPOTENCY_TTL_SECONDS), "1"],
                )
                # Script returns 1 if key was set (first time), 0 or None if exists
                is_duplicate = result is None or result == 0
                _pkg.record_event_idempotency(is_duplicate)
                return is_duplicate

            except Exception as e:
                # Fallback to original behavior on Lua script failure
                _pkg.logger.warning(
                    "Lua idempotency check failed, using fallback",
                    error=str(e),
                    event_id=event_id,
                )
                # Fall through to original implementation

        # Original path (fallback)
        # Use SETNX to atomically check and set
        is_new = await client.set(
            idempotency_key,
            "1",
            nx=True,
            ex=self.IDEMPOTENCY_TTL_SECONDS,
        )

        if not is_new:
            # Key already existed -> duplicate event
            _pkg.record_event_idempotency(is_duplicate=True)
            return True

        # Key was just set -> new event
        return False

    async def _peek_idempotency(self, event_id: str) -> bool:
        """H-6: read-only duplicate check — does NOT set the key.

        The pre-enqueue check must not claim the event: if the process dies
        before the enqueue, a re-publish must be allowed to retry. The SETNX
        (claim) happens in ``_mark_idempotent`` only after success.
        """
        client = self.redis_client
        if client is None:
            return False
        try:
            is_set = await client.exists(f"{self.IDEMPOTENCY_KEY_PREFIX}{event_id}")
            return bool(is_set)
        except Exception as e:
            # Fail-open on read errors: treat as not-duplicate so the
            # publish path proceeds (same availability posture as the old
            # Lua fallback).
            _pkg.logger.warning("idempotency_peek_failed_fail_open", error=str(e), event_id=event_id)
            return False

    async def _mark_idempotent(self, event_id: str) -> None:
        """H-6: claim the event id AFTER successful publish/enqueue.

        Identical claim semantics to the old ``_check_idempotency`` (SETNX +
        TTL, Lua or fallback), but timed so a crash between check and
        enqueue cannot permanently drop a re-published event.
        """
        client = self.redis_client
        if client is None:
            return
        idempotency_key = f"{self.IDEMPOTENCY_KEY_PREFIX}{event_id}"
        try:
            if _pkg.settings.EVENT_BUS_USE_LUA_IDEMPOTENCY:
                try:
                    lua_manager = _pkg.get_lua_manager()
                    await lua_manager.execute(
                        client,
                        "idempotency_check",
                        keys=[idempotency_key],
                        args=[str(self.IDEMPOTENCY_TTL_SECONDS), "1"],
                    )
                    return
                except Exception as e:
                    _pkg.logger.warning("Lua idempotency mark failed, using fallback", error=str(e), event_id=event_id)
            await client.set(idempotency_key, "1", nx=True, ex=self.IDEMPOTENCY_TTL_SECONDS)
        except Exception as e:
            # Claim failure is not fatal: worst case a re-publish runs the
            # handlers twice — the at-least-once tradeoff documented for
            # event delivery. Log for observability.
            _pkg.logger.warning("idempotency_mark_failed", error=str(e), event_id=event_id)

    async def publish(
        self,
        event_name: str,
        data: dict[str, Any],
        priority: EventPriority = EventPriority.NORMAL,
        event_id: str | None = None,
    ) -> None:
        """Publish an event to all registered handlers.

        Args:
            event_name: Name of event
            data: Event payload data
            priority: Event priority (CRITICAL=sync, NORMAL/LOW=async)
            event_id: Optional unique event ID for idempotency. If provided,
                      duplicate events with the same ID will be skipped.

        Example:
            await event_bus.publish(
                "user.created",
                {"user_id": str(user.id), "email": user.email},
                priority=EventPriority.NORMAL,
                event_id="user-created-12345-abc"
            )
        """
        # P2-7: resolve handlers BEFORE opening the OTel span. Handler-less
        # publishes skip the span, the idempotency peek, and the envelope
        # build below — all pure cost when nobody subscribes. Logs and the
        # None return are preserved verbatim.
        handlers = self._handlers.get(event_name, [])

        if not handlers:
            if not self._registration_complete:
                _pkg.logger.error(
                    "Event published before handler registration completed; event may be silently dropped",
                    event_name=event_name,
                )
            _pkg.logger.debug("Event published no handlers", event_name=event_name)
            return

        with _pkg.trace_span(
            "event_bus_publish",
            attributes={"event_name": event_name, "priority": priority.value, "event_id": event_id or "none"},
        ):
            # H-6: idempotency SETNX moved AFTER successful enqueue. The old
            # order (SETNX → enqueue) permanently dropped re-published events
            # when the process crashed between the two: the key was already
            # set, so every retry was treated as a duplicate and never
            # re-enqueued. Checking duplicates read-only first, then marking
            # post-enqueue, trades exactly-once for at-least-once handling
            # with crash recovery (the re-publish wins).
            # F11: the old comment claimed "exactly-once handling" — false
            # under concurrency (two publishers can both peek False and both
            # enqueue). Delivery is AT-LEAST-ONCE: handlers MUST be
            # idempotent; event_id dedup is best-effort, not a correctness
            # guarantee. The atomic `_check_idempotency` primitive remains for
            # callers that need claim semantics (F31).
            if event_id and self.redis_client:
                is_duplicate = await self._peek_idempotency(event_id)
                if is_duplicate:
                    _pkg.logger.info(
                        "Duplicate event skipped",
                        event_name=event_name,
                        event_id=event_id,
                    )
                    return

            # Handlers resolved above (P2-7 fast path); the no-handlers case
            # already returned before the span opened.

            # R5-10: Gate get_next_sequence behind a setting or consumer check
            # Avoids hitting Redis on every single event unless strict ordering is explicitly required
            if getattr(_pkg.settings, "EVENT_BUS_REQUIRE_STRICT_ORDERING", False):
                sequence = await self.get_next_sequence(event_name)
            else:
                sequence = 0

            # SECURITY FIX: Capture tenant context if available to propagate to background tasks
            # This is critical for RLS-enabled database operations in worker processes.
            # A6: packing lives behind the tenancy lifecycle seam — the worker
            # parses with the same key set, so the format cannot drift.
            from src.app.core.tenancy.context import get_current_tenant
            from src.app.core.tenancy.lifecycle import tenant_to_wire

            tenant = get_current_tenant()
            tenant_info = tenant_to_wire(tenant)

            # Inject sequence and tenant into event data for consumers
            enriched_data = {
                **data,
                "_event_meta": {
                    "sequence": sequence,
                    "event_id": event_id,
                    "event_name": event_name,
                    "tenant": tenant_info,
                },
            }

            _pkg.logger.info(
                "Event published",
                event_name=event_name,
                priority=priority,
                handler_count=len(handlers),
                event_id=event_id,
                sequence=sequence,
                tenant_id=tenant_info["id"] if tenant_info else None,
            )

            if priority == EventPriority.CRITICAL:
                # Execute synchronously, fire-and-forget
                sync_ok = await self._publish_sync(event_name, enriched_data, handlers)
            else:
                # Queue for async execution via ARQ
                # P0 FIX (code-review): propagate the fallback result — a
                # failed sync-fallback must NOT be marked idempotent or the
                # same event_id republish is a "duplicate" forever.
                sync_ok = await self._publish_async(event_name, enriched_data, handlers, priority)

            # H-6: mark the event processed only AFTER the handlers ran (sync
            # path) or the enqueue succeeded (async path). A crash before
            # this point leaves the key unset, so a re-publish re-runs the
            # event instead of silently dropping it.
            # P1-10 FIX: on the sync path, only mark idempotent when every
            # handler succeeded — a failed CRITICAL handler must not be
            # permanently deduplicated away; a re-publish with the same
            # event_id retries it.
            if event_id and self.redis_client and sync_ok:
                await self._mark_idempotent(event_id)

    async def _publish_sync(
        self,
        event_name: str,
        data: dict[str, Any],
        handlers: list[Callable[..., Coroutine[Any, Any, None]]],
        priority: EventPriority = EventPriority.CRITICAL,
    ) -> bool:
        """Execute handlers synchronously (for critical events).

        Args:
            event_name: Name of event
            data: Event payload
            handlers: List of handler functions
            priority: Event priority

        Returns:
            True if every handler succeeded; False if any failed (each
            failure is recorded in the dead-letter queue).
        """
        # P1-10 FIX: track failures so they reach the DLQ and suppress the
        # idempotent mark in publish() — previously a failed CRITICAL handler
        # was reduced to a log line and the event was then marked processed,
        # permanently losing it even on re-publish with the same event_id.
        failed_handlers: list[tuple[Callable, str]] = []

        async def _call_handler(handler: Callable) -> None:
            # P3-2: Acquire semaphore to bound concurrency
            async with self._concurrency_semaphore:
                try:
                    # Apply timeout protection
                    await asyncio.wait_for(
                        handler(data),
                        timeout=_pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS,
                    )
                    _pkg.logger.debug(
                        "Event handler success",
                        event_name=event_name,
                        handler=handler.__name__,
                    )
                except TimeoutError:
                    _pkg.logger.error(
                        "Event handler timed out",
                        event_name=event_name,
                        handler=handler.__name__,
                        timeout=_pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS,
                    )
                    failed_handlers.append(
                        (handler, f"timeout after {_pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS}s")
                    )
                    # We do not re-raise TimeoutError to prevent blocking other handlers
                    # in the gather group (though return_exceptions=True handles that too).
                except Exception as e:
                    _pkg.logger.error(
                        "Event handler failed",
                        event_name=event_name,
                        handler=handler.__name__,
                        error=str(e),
                        exc_info=True,
                    )
                    failed_handlers.append((handler, str(e)))

        if not handlers:
            return True

        # Execute handlers concurrently
        tasks = [_call_handler(handler) for handler in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log CRITICAL exceptions (they are already logged inside _call_handler but we can keep the logger.critical)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                _pkg.logger.critical(
                    "Event handler task failed with unhandled exception",
                    event_name=event_name,
                    handler=handlers[i].__name__,
                    error=str(result),
                    exc_info=True,
                )
                failed_handlers.append((handlers[i], str(result)))

        # P1-10: route each failure to the DLQ (mirroring _sync_fallback_handler)
        # so the event is recoverable instead of silently lost. A DLQ *write*
        # failure must not propagate into the publisher's request path
        # (add_to_dlq re-raises on Redis errors — P2-3): the failure is
        # already logged there, and sync_ok=False below still suppresses the
        # idempotent mark, so a re-publish with the same event_id retries.
        for handler, error in failed_handlers:
            await self._dlq_quietly(event_name, handler, data, error=error, attempt_count=1)

        return not failed_handlers

    async def _publish_async(
        self,
        event_name: str,
        data: dict[str, Any],
        handlers: list[Callable[..., Coroutine[Any, Any, None]]],
        priority: EventPriority,
    ) -> bool:
        """Queue handlers for async execution via ARQ.

        Returns True when every handler was enqueued or sync-fallback
        succeeded; False when a fallback also failed (caller must NOT mark
        idempotent so a republish retries).
        """
        if not self._arq_queue:
            _pkg.logger.warning(
                "Event handler ARQ not configured",
                event_name=event_name,
                message="ARQ queue not set, falling back to sync execution",
            )
            return await self._publish_sync(event_name, data, handlers)

        # P1-4: Parallel enqueue using asyncio.gather for reduced latency
        # Changes Sum(RTT) to Max(RTT) for multiple handlers

        # P3-3: Batch enqueue using Redis pipeline if enabled
        if _pkg.settings.EVENT_BUS_USE_BATCH_ENQUEUE and len(handlers) > 1:
            try:
                await self._publish_async_batch(event_name, data, handlers, priority)
                return True
            except Exception as e:
                _pkg.logger.warning(
                    "Batch enqueue failed, falling back to parallel enqueue",
                    event_name=event_name,
                    error=str(e),
                )
                # Fallback to original parallel enqueue logic below

        async def enqueue_handler(
            handler: Callable[..., Coroutine[Any, Any, None]],
        ) -> tuple[bool, Callable[..., Coroutine[Any, Any, None]], Exception | None]:
            """Enqueue a single handler, returning (success, handler, error)."""
            # K9 FIX: gate on single-flight semantics, not is_closed. During
            # virtual HALF_OPEN is_closed was True for everyone, letting all
            # concurrent publishes flood the recovering queue; now exactly one
            # probe enqueue passes and everyone else uses the sync fallback.
            if not await _pkg.arq_circuit.allow_single_flight():
                _pkg.logger.warning(
                    "ARQ circuit breaker open - falling back to sync execution",
                    event_name=event_name,
                    handler=handler.__name__,
                )
                # B8: route through the shared fallback so failures land in
                # the DLQ (the old inline block only logged them — during a
                # Redis outage, exactly when the circuit is open, log-only
                # meant permanent silent loss).
                fallback_ok = await self._sync_fallback_handler(
                    event_name, handler, data, reason="circuit open"
                )
                return (fallback_ok, handler, None if fallback_ok else Exception("sync fallback failed"))

            try:
                # Type assertion: we checked _arq_queue earlier
                assert self._arq_queue is not None, "ARQ queue should be set"
                # SECURITY FIX: Unified queue naming with _publish_async_batch.
                # Batch jobs prepend 'arq:queue' prefix; single jobs must match to avoid worker misses.
                from src.app.core.events.arq_adapter import get_queue_name

                q_name = get_queue_name(priority)
                await self._arq_queue.enqueue_job(
                    "process_event_handler",
                    event_name,
                    handler.__module__,
                    handler.__name__,
                    data,
                    _queue_name=q_name,
                )
                await _pkg.arq_circuit.record_success()
                _pkg.logger.debug(
                    "Event handler queued",
                    event_name=event_name,
                    handler=handler.__name__,
                    priority=priority,
                )
                return (True, handler, None)
            except Exception as e:
                await _pkg.arq_circuit.record_failure(e)
                _pkg.logger.error(
                    "Event handler queue failed",
                    event_name=event_name,
                    handler=handler.__name__,
                    error=str(e),
                    exc_info=True,
                )
                # B8: enqueue failure also routes through the shared fallback
                # (the old code only returned the failure tuple — handler was
                # never run, contradicting the documented B8 contract and the
                # parallel-enqueue test). DLQ write on fallback failure happens
                # inside _sync_fallback_handler.
                fallback_ok = await self._sync_fallback_handler(
                    event_name, handler, data, reason="queue failure"
                )
                return (fallback_ok, handler, None if fallback_ok else e)

        # Enqueue all handlers in parallel
        results = await asyncio.gather(
            *[enqueue_handler(h) for h in handlers],
            return_exceptions=True,
        )

        # Handle failures. NOTE: enqueue_handler already routes circuit-open
        # and enqueue failures through _sync_fallback_handler (B8) — including
        # the DLQ write on fallback failure. Re-running the fallback here
        # would DOUBLE-EXECUTE the handler (a real bug: first run's exception
        # hit the DLQ, then the outer loop re-invoked it). So this loop only
        # counts overall success and handles gather-level surprises.
        overall_ok = True
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                handler = handlers[i]
                # asyncio.gather caught an unexpected exception that escaped
                # enqueue_handler's own guards — extremely unlikely (its body
                # is fully guarded), but do not lose it.
                _pkg.logger.error(
                    "Unexpected error during parallel enqueue",
                    event_name=event_name,
                    handler=handler.__name__,
                    error=str(result),
                )
                await self._dlq_quietly(event_name, handler, data, error=str(result), attempt_count=0)
                overall_ok = False
                continue

            success, _handler, _error = result  # type: ignore[misc]
            overall_ok = overall_ok and bool(success)
        return overall_ok

    async def _dlq_quietly(
        self,
        event_name: str,
        handler: Callable[..., Coroutine[Any, Any, None]],
        data: dict[str, Any],
        error: str,
        attempt_count: int,
    ) -> None:
        """B9: write a DLQ entry without ever propagating a failure.

        ``add_to_dlq`` re-raises on Redis errors (correct for the ARQ worker
        path, where the raise triggers a job retry). But every *web* path that
        reaches the DLQ — sync fallback after enqueue failure, circuit-open
        fallback, CRITICAL sync-handler failure — runs inside a request whose
        primary work already succeeded; a Redis outage there must not 500 the
        request. The error log inside ``add_to_dlq`` plus this method's own
        log line are the audit trail when Redis is down.
        """
        try:
            await self.add_to_dlq(
                event_name=event_name,
                handler_name=getattr(handler, "__name__", repr(handler)),
                data=data,
                error=error,
                attempt_count=attempt_count,
            )
        except Exception as dlq_error:
            _pkg.logger.error(
                "Failed to route failed handler to DLQ",
                event_name=event_name,
                handler=getattr(handler, "__name__", repr(handler)),
                error=str(dlq_error),
            )

    async def _sync_fallback_handler(
        self,
        event_name: str,
        handler: Callable[..., Coroutine[Any, Any, None]],
        data: dict[str, Any],
        reason: str,
    ) -> bool:
        """A-2: single graceful-degradation path for a handler that failed to enqueue.

        Runs the handler synchronously with the configured timeout; on timeout
        or error, records the failure in the dead-letter queue. Consolidates the
        two previously duplicated inline blocks in ``_publish_async``.

        Returns True when the sync run succeeded, False otherwise (so the
        caller can withhold idempotent marking and allow a retry).
        """
        try:
            await asyncio.wait_for(
                handler(data),
                timeout=_pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS,
            )
            _pkg.logger.info(
                "Handler executed synchronously after " + reason,
                event_name=event_name,
                handler=handler.__name__,
            )
            return True
        except TimeoutError as e:
            _pkg.logger.error(
                "Sync fallback timed out after " + reason,
                event_name=event_name,
                handler=handler.__name__,
                timeout=_pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS,
            )
            await self._dlq_quietly(event_name, handler, data, error=str(e), attempt_count=0)
            return False
        except Exception as handler_error:
            _pkg.logger.error(
                "Sync fallback also failed",
                event_name=event_name,
                handler=handler.__name__,
                error=str(handler_error),
            )
            await self._dlq_quietly(event_name, handler, data, error=str(handler_error), attempt_count=0)
            return False

    async def _publish_async_batch(
        self,
        event_name: str,
        data: dict[str, Any],
        handlers: list[Callable[..., Coroutine[Any, Any, None]]],
        priority: EventPriority,
    ) -> bool:
        """Enqueue multiple handlers in a single Redis roundtrip using pipeline.

        Optimizes N roundtrips -> 1 roundtrip for multiple handlers.
        Returns True on success (raise on failure so the caller falls back).
        """
        if not self._arq_queue:
            raise RuntimeError("ARQ queue not configured")

        # Check circuit breaker once for the batch (K9 FIX: single-flight gate)
        if not await _pkg.arq_circuit.allow_single_flight():
            _pkg.logger.warning("ARQ circuit breaker open during batch enqueue", event_name=event_name)
            # Fallback to sync execution for all handlers
            return await self._publish_sync(event_name, data, handlers, priority=priority)

        from src.app.core.events.arq_adapter import enqueue_batch_jobs

        try:
            await enqueue_batch_jobs(
                self._arq_queue,
                event_name=event_name,
                handlers=handlers,
                data=data,
                priority=priority,
            )
            await _pkg.arq_circuit.record_success()

            _pkg.logger.debug(
                "Batch event handlers queued", event_name=event_name, count=len(handlers), priority=priority
            )
            return True

        except Exception as e:
            await _pkg.arq_circuit.record_failure(e)
            _pkg.logger.error(
                "Batch event handler queue failed",
                event_name=event_name,
                error=str(e),
                exc_info=True,
            )
            raise  # Re-raise to trigger fallback

    def get_handlers(self, event_name: str) -> list[Callable[..., Coroutine[Any, Any, None]]]:
        """Get all handlers registered for an event.

        Args:
            event_name: Name of event

        Returns
        -------
            List of handler functions
        """
        return self._handlers.get(event_name, [])

    async def add_to_dlq(
        self,
        event_name: str,
        handler_name: str,
        data: dict[str, Any],
        error: str,
        attempt_count: int,
    ) -> None:
        """Add failed event to dead-letter queue (Redis-backed).

        Phase 1 DLQ unification: writes the monitor's surviving queue
        (``arq:dlq``) in the monitor's JSON shape
        (``{job_id, function/event_name, error, timestamp}``) extended with
        the bus fields (``handler, data/payload, attempt_count, failed_at,
        source``), so admin GET /dlq sees bus failures. Bounded-size
        pipeline (LPUSH+LTRIM+LLEN) and alert-threshold semantics preserved.

        R1-UNIFIED P3-1: Added bounded size with LTRIM to prevent unbounded
        memory growth under sustained failure scenarios.

        When DLQ_USE_BOUNDED_SIZE is enabled:
        - Uses pipeline: LPUSH + LTRIM + LLEN (single roundtrip)
        - Caps size at DLQ_MAX_SIZE (default 10000)
        - Oldest entries automatically discarded when full
        - Alerts at DLQ_ALERT_THRESHOLD_PCT capacity

        Args:
            event_name: Name of event
            handler_name: Handler that failed
            data: Event payload
            error: Error message
            attempt_count: Number of attempts made
        """
        import time
        from datetime import datetime

        from src.app.core.config import settings
        from src.app.core.monitoring import update_dlq_size

        dlq_entry = {
            # Monitor-compatible identity (admin DLQItemResponse fields).
            # time_ns (not attempt_count) keeps the id unique under
            # retries/fan-out where event+handler+attempt repeats.
            "job_id": f"bus:{event_name}:{handler_name}:{time.time_ns()}",
            "function": event_name,
            "error": error,
            "timestamp": time.time(),
            # Bus context (preserved for retry/debugging).
            "event_name": event_name,
            "handler": handler_name,
            "data": data,
            "payload": data,
            "attempt_count": attempt_count,
            "failed_at": datetime.now(UTC).isoformat(),
            "source": "event_bus",
        }

        client = self.redis_client
        if client:
            try:
                r1 = getattr(settings, "r1_unified", settings)
                dlq_bounded = getattr(r1, "DLQ_USE_BOUNDED_SIZE", getattr(settings, "DLQ_USE_BOUNDED_SIZE", True))
                if dlq_bounded:
                    # P3-1 Optimized: Pipeline with LTRIM for bounded size
                    max_size = int(getattr(r1, "DLQ_MAX_SIZE", getattr(settings, "DLQ_MAX_SIZE", 10000)))
                    alert_threshold = float(
                        getattr(r1, "DLQ_ALERT_THRESHOLD_PCT", getattr(settings, "DLQ_ALERT_THRESHOLD_PCT", 80))
                    )
                    async with client.pipeline(transaction=True) as pipe:
                        pipe.lpush(self.DLQ_REDIS_KEY, _pkg.json_dumps(dlq_entry, as_str=True))
                        pipe.ltrim(self.DLQ_REDIS_KEY, 0, max_size - 1)  # Keep newest entries
                        pipe.llen(self.DLQ_REDIS_KEY)
                        results = await pipe.execute()

                    dlq_size = results[2]  # LLEN result
                    update_dlq_size(dlq_size, max_size)

                    # Warn when approaching capacity
                    threshold = max_size * alert_threshold / 100
                    if dlq_size > threshold:
                        _pkg.logger.warning(
                            "DLQ approaching capacity",
                            dlq_size=dlq_size,
                            max_size=max_size,
                            usage_pct=round(dlq_size * 100 / max_size, 1),
                        )
                else:
                    # Original path (fallback)
                    await client.lpush(self.DLQ_REDIS_KEY, _pkg.json_dumps(dlq_entry, as_str=True))
                    dlq_size = await client.llen(self.DLQ_REDIS_KEY)
                    update_dlq_size(dlq_size)
            except Exception as e:
                _pkg.logger.error("Failed to add to DLQ", error=str(e), event_name=event_name)
                # Re-raise to avoid silent failures
                raise
        else:
            _pkg.logger.warning(
                "Redis not configured for DLQ, event will be lost on restart",
                event_name=event_name,
            )

        _pkg.logger.warning(
            "Event moved to DLQ",
            event_name=event_name,
            handler=handler_name,
            attempt_count=attempt_count,
            error=error,
        )

    async def get_dlq_entries(self, limit: int = 100, source: str | None = None) -> list[dict]:
        """Get dead-letter queue entries from Redis (unified ``arq:dlq`` queue).

        Thin wrapper over the surviving monitor queue so scripts
        (e.g. scripts/r1_unified_baseline.py) keep working; admin reads the
        same key via JobMonitoringService.get_dead_letter_queue().

        The unified key mixes two failure domains (ARQ job entries and bus
        entries tagged ``source="event_bus"``). Pass ``source`` to read one
        domain only — ``"event_bus"`` for bus failures, ``"arq"`` for ARQ
        job failures (ARQ entries carry no ``source`` field).

        Args:
            limit: Maximum number of entries to return
            source: Optional failure-domain filter (``"event_bus"`` / ``"arq"``)

        Returns
        -------
            List of failed event entries
        """
        client = self.redis_client
        if not client:
            return []

        entries = await client.lrange(self.DLQ_REDIS_KEY, 0, limit - 1)
        # F17: decode defensively — one poisoned entry must not fail the
        # whole read (siblings already guard; this path did not).
        parsed: list[Any] = []
        for entry in entries:
            try:
                parsed.append(_pkg.json_loads(entry))
            except Exception:  # noqa: BLE001
                _pkg.logger.warning("event_bus_dlq_entry_undecodable")
                continue
        if source is not None:
            parsed = [e for e in parsed if isinstance(e, dict) and e.get("source", "arq") == source]
        return parsed

    async def get_dlq_size(self) -> int:
        """Get the number of entries in the unified DLQ (``arq:dlq``).

        Returns
        -------
            Number of DLQ entries
        """
        client = self.redis_client
        if not client:
            return 0
        return cast(int, await client.llen(self.DLQ_REDIS_KEY))

    async def retry_dlq_entry(self, source: str | None = None) -> dict | None:
        """Remove and return the oldest unified-DLQ entry for retry (FIFO).

        With ``source`` set, removes the oldest entry of that failure domain
        instead of whatever happens to be oldest — an ARQ job dict must never
        be returned as a bus retry (type confusion across the two domains).

        Args:
            source: Optional failure-domain filter (``"event_bus"`` / ``"arq"``)

        Returns
        -------
            DLQ entry or None if queue is empty
        """
        client = self.redis_client
        if not client:
            return None

        if source is None:
            # Use rpop (FIFO) to ensure we retry oldest entries
            entry = await client.rpop(self.DLQ_REDIS_KEY)
            if entry:
                return cast("dict[Any, Any]", _pkg.json_loads(entry))
            return None

        # P2: source-filtered pop runs server-side (single RTT) via the
        # dlq_pop_filtered script; the Python scan below is the fallback.
        raw = await _pkg.get_lua_manager().execute_or_fallback(
            client,
            "dlq_pop_filtered",
            keys=[self.DLQ_REDIS_KEY],
            args=[source],
            fallback_fn=lambda: self._retry_dlq_entry_raw_scan(source),
        )
        if not raw or not isinstance(raw, str | bytes):
            return None
        try:
            parsed = _pkg.json_loads(raw)
        except Exception:
            return None
        if isinstance(parsed, dict):
            return cast("dict[Any, Any]", parsed)
        return None

    async def _retry_dlq_entry_raw_scan(self, source: str) -> str | None:
        """Python fallback for source-filtered DLQ pop: scan oldest-first for
        the first match and remove exactly that serialized element (LREM
        count=1). Only JSON objects participate; unparseable entries are
        skipped. A missing ``source`` field counts as ``"arq"``."""
        client = self.redis_client
        assert client is not None
        # Source-filtered pop: scan oldest-first for the first match and
        # remove exactly that serialized element (LREM count=1).
        raw_entries = await client.lrange(self.DLQ_REDIS_KEY, 0, -1)
        for raw in reversed(raw_entries):
            try:
                parsed = _pkg.json_loads(raw)
            except Exception:
                continue
            if isinstance(parsed, dict) and parsed.get("source", "arq") == source:
                await client.lrem(self.DLQ_REDIS_KEY, 1, raw)
                return cast(str, raw)
        return None

    async def clear_dlq(self, source: str | None = None) -> int:
        """Clear unified-DLQ entries (``arq:dlq``).

        With ``source`` set, removes only that failure domain's entries so
        clearing bus failures can never delete ARQ job failures (and vice
        versa). Without ``source`` the whole key is deleted (legacy/admin
        escape hatch — prefer source-scoped clears).

        Args:
            source: Optional failure-domain filter (``"event_bus"`` / ``"arq"``)

        Returns
        -------
            Number of entries cleared
        """
        client = self.redis_client
        if not client:
            return 0

        if source is None:
            count = await client.llen(self.DLQ_REDIS_KEY)
            await client.delete(self.DLQ_REDIS_KEY)
            return cast(int, count)

        # P2: source-filtered clear runs server-side (single RTT) via the
        # dlq_clear_filtered script; the Python scan below is the fallback.
        cleared = await _pkg.get_lua_manager().execute_or_fallback(
            client,
            "dlq_clear_filtered",
            keys=[self.DLQ_REDIS_KEY],
            args=[source],
            fallback_fn=lambda: self._clear_dlq_scan(source),
        )
        return int(cleared or 0)

    async def _clear_dlq_scan(self, source: str) -> int:
        """Python fallback for source-filtered DLQ clear: head-to-tail scan,
        one LREM count=1 per match. Only JSON objects participate;
        unparseable entries are skipped. A missing ``source`` field counts
        as ``"arq"``."""
        client = self.redis_client
        assert client is not None
        raw_entries = await client.lrange(self.DLQ_REDIS_KEY, 0, -1)
        cleared = 0
        for raw in raw_entries:
            try:
                parsed = _pkg.json_loads(raw)
            except Exception:
                continue
            if isinstance(parsed, dict) and parsed.get("source", "arq") == source:
                await client.lrem(self.DLQ_REDIS_KEY, 1, raw)
                cleared += 1
        return cleared

    async def drain_legacy_dlq(self) -> int:
        """One-time drain of the retired ``event_bus:dlq`` key into ``arq:dlq``.

        Entries written before the Phase 1 unification were orphaned on the
        legacy key; this moves them onto the unified queue (tagging
        ``source="event_bus"`` when missing) so they stay visible to the
        admin DLQ view and the source-filtered readers above.

        Returns
        -------
            Number of entries moved
        """
        client = self.redis_client
        if not client:
            return 0

        moved = 0
        while True:
            raw = await client.rpop(self.LEGACY_DLQ_REDIS_KEY)
            if not raw:
                break
            try:
                parsed = _pkg.json_loads(raw)
            except Exception:
                continue
            if isinstance(parsed, dict):
                parsed.setdefault("source", "event_bus")
                raw = _pkg.json_dumps(parsed, as_str=True)
            else:
                # F18: legacy/non-dict entries were re-pushed untagged, so the
                # source filter silently mislabeled them "arq". Envelope them
                # so every unified-DLQ entry is a tagged dict; the original
                # payload is preserved verbatim under "payload".
                raw = _pkg.json_dumps({"source": "event_bus", "payload": parsed}, as_str=True)
            await client.lpush(self.DLQ_REDIS_KEY, raw)
            moved += 1
        return moved

    def register_all_handlers(self) -> None:
        """Pre-register all event handlers during startup.

        Delegates to application-level handler wiring in src.app.events to keep
        core infrastructure strictly decoupled from feature modules.
        """
        from src.app.events import register_application_event_handlers

        register_application_event_handlers(self)


# Global event bus instance
event_bus = EventBus()


def reset_event_bus() -> None:
    """Reset the event bus singleton for test isolation.

    Resets the existing singleton in place rather than rebinding it: most
    publishers bind the singleton at import time
    (``from src.app.core.events import event_bus``), so replacing ``_pkg.event_bus``
    would leave those modules publishing to a stale instance while tests inspect
    the new one.

    Use this in test fixtures to ensure clean state between tests.
    """
    fresh = EventBus()
    existing = _pkg.event_bus
    # Copy fresh state onto the existing instance so all import-time bindings observe it.
    existing._handlers = fresh._handlers
    existing._registration_complete = fresh._registration_complete
    existing._redis_client = fresh._redis_client
    existing._arq_queue = fresh._arq_queue
    existing.RETRY_DELAYS = fresh.RETRY_DELAYS  # M-9: keep backoff in lock-step
    # F13: the semaphore is construction-time state — a reset that keeps the
    # old one leaks the previous limit (and any acquired permits). Recreate
    # it so tests that patch EVENT_BUS_CONCURRENCY actually take effect.
    existing._concurrency_semaphore = asyncio.Semaphore(_concurrency_limit())
    _pkg.logger.debug("Event bus singleton reset")
