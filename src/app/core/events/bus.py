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
    # Retry delays in seconds: [60, 300, 900] = 1min, 5min, 15min
    RETRY_DELAYS = [60, 300, 900]
    # Redis key for DLQ list
    DLQ_REDIS_KEY = "event_bus:dlq"
    # Redis key prefix for event sequence counters (per event type)
    SEQUENCE_KEY_PREFIX = "event_bus:seq:"

    def __init__(self) -> None:
        """Initialize event bus with handler registry."""
        self._handlers: dict[str, list[Callable[..., Coroutine[Any, Any, None]]]] = defaultdict(list)
        self._arq_queue: ArqRedis | None = None
        self._redis_client: Any = _UNSET
        self._registration_complete = False
        # P3-2: Semaphore to bound concurrent sync execution
        self._concurrency_semaphore = asyncio.Semaphore(_pkg.settings.EVENT_BUS_CONCURRENCY)

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
        _pkg.record_event_idempotency(is_duplicate=False)
        return False

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
        with _pkg.trace_span(
            "event_bus_publish",
            attributes={"event_name": event_name, "priority": priority.value, "event_id": event_id or "none"},
        ):
            # Check for duplicate event if event_id provided
            if event_id and self.redis_client:
                is_duplicate = await self._check_idempotency(event_id)
                if is_duplicate:
                    _pkg.logger.info(
                        "Duplicate event skipped",
                        event_name=event_name,
                        event_id=event_id,
                    )
                    return

            handlers = self._handlers.get(event_name, [])

            if not handlers:
                if not self._registration_complete:
                    _pkg.logger.error(
                        "Event published before handler registration completed; event may be silently dropped",
                        event_name=event_name,
                    )
                _pkg.logger.debug("Event published no handlers", event_name=event_name)
                return

            # R5-10: Gate get_next_sequence behind a setting or consumer check
            # Avoids hitting Redis on every single event unless strict ordering is explicitly required
            if getattr(_pkg.settings, "EVENT_BUS_REQUIRE_STRICT_ORDERING", False):
                sequence = await self.get_next_sequence(event_name)
            else:
                sequence = 0

            # SECURITY FIX: Capture tenant context if available to propagate to background tasks
            # This is critical for RLS-enabled database operations in worker processes.
            from src.app.core.tenancy.context import get_current_tenant

            tenant = get_current_tenant()
            tenant_info = None
            if tenant:
                tenant_info = {
                    "id": str(tenant.id),
                    "slug": tenant.slug,
                    "name": tenant.name,
                    "is_active": tenant.is_active,
                }

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
                await self._publish_sync(event_name, enriched_data, handlers)
            else:
                # Queue for async execution via ARQ
                await self._publish_async(event_name, enriched_data, handlers, priority)

    async def _publish_sync(
        self,
        event_name: str,
        data: dict[str, Any],
        handlers: list[Callable[..., Coroutine[Any, Any, None]]],
        priority: EventPriority = EventPriority.CRITICAL,
    ) -> None:
        """Execute handlers synchronously (for critical events).

        Args:
            event_name: Name of event
            data: Event payload
            handlers: List of handler functions
        """

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

        if not handlers:
            return

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

    async def _publish_async(
        self,
        event_name: str,
        data: dict[str, Any],
        handlers: list[Callable[..., Coroutine[Any, Any, None]]],
        priority: EventPriority,
    ) -> None:
        """Queue handlers for async execution via ARQ.

        Args:
            event_name: Name of event
            data: Event payload
            handlers: List of handler functions
            priority: Event priority for queue scheduling
        """
        if not self._arq_queue:
            _pkg.logger.warning(
                "Event handler ARQ not configured",
                event_name=event_name,
                message="ARQ queue not set, falling back to sync execution",
            )
            await self._publish_sync(event_name, data, handlers)
            return

        # P1-4: Parallel enqueue using asyncio.gather for reduced latency
        # Changes Sum(RTT) to Max(RTT) for multiple handlers

        # P3-3: Batch enqueue using Redis pipeline if enabled
        if _pkg.settings.EVENT_BUS_USE_BATCH_ENQUEUE and len(handlers) > 1:
            try:
                await self._publish_async_batch(event_name, data, handlers, priority)
                return
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
                try:
                    await asyncio.wait_for(
                        handler(data),
                        timeout=_pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    _pkg.logger.error(
                        "Fallback sync handler timed out",
                        event_name=event_name,
                        handler=handler.__name__,
                        timeout=_pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS,
                    )
                except Exception as e:
                    _pkg.logger.error(
                        "Fallback sync handler failed",
                        event_name=event_name,
                        handler=handler.__name__,
                        error=str(e),
                    )
                return (True, handler, None)  # Handled via fallback

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
                return (False, handler, e)

        # Enqueue all handlers in parallel
        results = await asyncio.gather(
            *[enqueue_handler(h) for h in handlers],
            return_exceptions=True,
        )

        # Handle any failures with sync fallback
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                handler = handlers[i]
                # asyncio.gather caught an unexpected exception
                _pkg.logger.error(
                    "Unexpected error during parallel enqueue",
                    event_name=event_name,
                    handler=handler.__name__,
                    error=str(result),
                )

                await self._sync_fallback_handler(event_name, handler, data, reason="gather queue failure")
                continue

            # Unpack result tuple (narrowed to tuple by isinstance check above)
            success, handler, error = result  # type: ignore[misc]
            if not success and error is not None:
                await self._sync_fallback_handler(event_name, handler, data, reason="queue failure")

    async def _sync_fallback_handler(
        self,
        event_name: str,
        handler: Callable[..., Coroutine[Any, Any, None]],
        data: dict[str, Any],
        reason: str,
    ) -> None:
        """A-2: single graceful-degradation path for a handler that failed to enqueue.

        Runs the handler synchronously with the configured timeout; on timeout
        or error, records the failure in the dead-letter queue. Consolidates the
        two previously duplicated inline blocks in ``_publish_async``.
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
        except TimeoutError as e:
            _pkg.logger.error(
                "Sync fallback timed out after " + reason,
                event_name=event_name,
                handler=handler.__name__,
                timeout=_pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS,
            )
            await self.add_to_dlq(
                event_name=event_name,
                handler_name=getattr(handler, "__name__", repr(handler)),
                data=data,
                error=str(e),
                attempt_count=0,
            )
        except Exception as handler_error:
            _pkg.logger.error(
                "Sync fallback also failed",
                event_name=event_name,
                handler=handler.__name__,
                error=str(handler_error),
            )
            await self.add_to_dlq(
                event_name=event_name,
                handler_name=getattr(handler, "__name__", repr(handler)),
                data=data,
                error=str(handler_error),
                attempt_count=0,
            )

    async def _publish_async_batch(
        self,
        event_name: str,
        data: dict[str, Any],
        handlers: list[Callable[..., Coroutine[Any, Any, None]]],
        priority: EventPriority,
    ) -> None:
        """Enqueue multiple handlers in a single Redis roundtrip using pipeline.

        Optimizes N roundtrips -> 1 roundtrip for multiple handlers.
        """
        if not self._arq_queue:
            raise RuntimeError("ARQ queue not configured")

        # Check circuit breaker once for the batch (K9 FIX: single-flight gate)
        if not await _pkg.arq_circuit.allow_single_flight():
            _pkg.logger.warning("ARQ circuit breaker open during batch enqueue", event_name=event_name)
            # Fallback to sync execution for all handlers
            await self._publish_sync(event_name, data, handlers, priority=priority)
            return

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
        from datetime import datetime

        from src.app.core.config import settings
        from src.app.core.monitoring import update_dlq_size

        dlq_entry = {
            "event_name": event_name,
            "handler": handler_name,
            "data": data,
            "error": error,
            "attempt_count": attempt_count,
            "failed_at": datetime.now(UTC).isoformat(),
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

    async def get_dlq_entries(self, limit: int = 100) -> list[dict]:
        """Get dead-letter queue entries from Redis.

        Args:
            limit: Maximum number of entries to return

        Returns
        -------
            List of failed event entries
        """
        client = self.redis_client
        if not client:
            return []

        entries = await client.lrange(self.DLQ_REDIS_KEY, 0, limit - 1)
        return [_pkg.json_loads(entry) for entry in entries]

    async def get_dlq_size(self) -> int:
        """Get the number of entries in the DLQ.

        Returns
        -------
            Number of DLQ entries
        """
        client = self.redis_client
        if not client:
            return 0
        return cast(int, await client.llen(self.DLQ_REDIS_KEY))

    async def retry_dlq_entry(self) -> dict | None:
        """Remove and return the oldest DLQ entry for retry (FIFO).

        Returns
        -------
            DLQ entry or None if queue is empty
        """
        client = self.redis_client
        if not client:
            return None

        # Use rpop (FIFO) to ensure we retry oldest entries
        entry = await client.rpop(self.DLQ_REDIS_KEY)
        if entry:
            return cast("dict[Any, Any]", _pkg.json_loads(entry))
        return None

    async def clear_dlq(self) -> int:
        """Clear all DLQ entries.

        Returns
        -------
            Number of entries cleared
        """
        client = self.redis_client
        if not client:
            return 0

        count = await client.llen(self.DLQ_REDIS_KEY)
        await client.delete(self.DLQ_REDIS_KEY)
        return cast(int, count)

    def register_all_handlers(self) -> None:
        """Pre-register all event handlers during startup.

        Explicitly imports and registers module-level event handlers without dynamic scanning.
        """
        try:
            from src.app.modules.audit_logs.service import handle_audit_log_event
            from src.app.modules.notifications.service import (
                handle_notification_request_event,
                handle_notification_send_event,
            )
            from src.app.modules.webhooks.service import handle_webhook_trigger_event, webhook_event_adapter

            self.register_handler("audit.log", handle_audit_log_event)
            self.register_handler("notification.send", handle_notification_send_event)
            self.register_handler("notification.request", handle_notification_request_event)
            self.register_handler("webhook.trigger", handle_webhook_trigger_event)

            # R1-H9: Route common business events to the webhook dispatcher.
            # webhook_event_adapter is a MODULE-LEVEL function (see its
            # definition in webhooks/service.py): the async ARQ path enqueues
            # handler.__module__ + handler.__name__ and the worker re-imports
            # it. A per-event closure failed getattr() in the worker and every
            # business-event webhook landed in the DLQ as a "Permanent Error".
            # The adapter recovers the event type from _event_meta.event_name.
            business_events = [
                "user.created",
                "user.deleted",
                "user.updated",
                "api_key.created",
                "api_key.deleted",
                "sso.login",
                "auth.mfa.enabled",
                "auth.mfa.disabled",
                "auth.login",
                "auth.logout",
            ]
            for evt in business_events:
                self.register_handler(evt, webhook_event_adapter)

            _pkg.logger.info(
                "Event handler registration complete",
                total_handlers=sum(len(handlers) for handlers in self._handlers.values()),
            )
            self._registration_complete = True
        except Exception as e:
            _pkg.logger.error(
                "Failed to register event handlers during startup",
                error=str(e),
                exc_info=True,
            )


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
    _pkg.logger.debug("Event bus singleton reset")
