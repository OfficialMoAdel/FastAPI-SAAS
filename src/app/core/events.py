"""Event bus for publish/subscribe pattern across modules.

Supports priority-based event handling:
- CRITICAL: Synchronous, fire-and-forget (audit logs)
- NORMAL/LOW: Asynchronous via ARQ queue (emails, webhooks)
"""

import asyncio
import importlib
from collections import defaultdict
from collections.abc import Callable, Coroutine
from enum import Enum
from pathlib import Path
from typing import Any

from structlog import get_logger

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.monitoring.r1_unified_metrics import record_event_idempotency
from src.app.core.utils.lua_manager import get_lua_manager

from .config import settings
from .utils.circuit_breaker import arq_circuit

logger = get_logger(__name__)


class EventPriority(str, Enum):
    """Event priority levels."""

    CRITICAL = "CRITICAL"
    NORMAL = "NORMAL"
    LOW = "LOW"


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
        self._arq_queue = None
        self._redis_client = None  # Set via set_redis_client()
        self._registration_complete = False

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
        logger.info("Event bus Redis client configured for DLQ persistence")

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

        Args:
            event_name: Name of the event type

        Returns
        -------
            Next sequence number (starting from 1)
        """
        if not self._redis_client:
            return 0  # No ordering guarantee without Redis

        seq_key = f"{self.SEQUENCE_KEY_PREFIX}{event_name}"
        return await self._redis_client.incr(seq_key)

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
            logger.info("Event handler registered", event_name=event_name, handler=func.__name__)
            return func

        return decorator

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
        if self._redis_client is None:
            return False  # Can't check idempotency without Redis

        idempotency_key = f"{self.IDEMPOTENCY_KEY_PREFIX}{event_id}"

        if settings.r1_unified.EVENT_BUS_USE_LUA_IDEMPOTENCY:
            # Optimized path: Atomic Lua script (no race condition)
            try:
                lua_manager = get_lua_manager()
                result = await lua_manager.execute(
                    self._redis_client,
                    "idempotency_check",
                    keys=[idempotency_key],
                    args=[str(self.IDEMPOTENCY_TTL_SECONDS), "1"],
                )
                # Script returns 1 if key was set (first time), 0 if exists
                is_duplicate = bool(result == 0)
                record_event_idempotency(is_duplicate)
                return is_duplicate

            except Exception as e:
                # Fallback to original behavior on Lua script failure
                logger.warning(
                    "Lua idempotency check failed, using fallback",
                    error=str(e),
                    event_id=event_id,
                )
                # Fall through to original implementation

        # Original path (has race condition but works as fallback)
        already_processed = await self._redis_client.get(idempotency_key)
        if already_processed:
            record_event_idempotency(is_duplicate=True)
            return True

        # Mark event as processed with TTL
        await self._redis_client.setex(
            idempotency_key,
            self.IDEMPOTENCY_TTL_SECONDS,
            "1",
        )
        record_event_idempotency(is_duplicate=False)
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
        # Check for duplicate event if event_id provided
        if event_id and self._redis_client:
            is_duplicate = await self._check_idempotency(event_id)
            if is_duplicate:
                logger.info(
                    "Duplicate event skipped",
                    event_name=event_name,
                    event_id=event_id,
                )
                return

        handlers = self._handlers.get(event_name, [])

        if not handlers:
            logger.debug("Event published no handlers", event_name=event_name)
            return

        # Get sequence number for ordering guarantees
        sequence = await self.get_next_sequence(event_name)

        # Inject sequence into event data for consumers
        enriched_data = {
            **data,
            "_event_meta": {
                "sequence": sequence,
                "event_id": event_id,
                "event_name": event_name,
            },
        }

        logger.info(
            "Event published",
            event_name=event_name,
            priority=priority,
            handler_count=len(handlers),
            event_id=event_id,
            sequence=sequence,
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
    ) -> None:
        """Execute handlers synchronously (for critical events).

        Args:
            event_name: Name of event
            data: Event payload
            handlers: List of handler functions
        """

        async def _call_handler(handler: Callable) -> None:
            try:
                # Apply timeout protection
                await asyncio.wait_for(
                    handler(data),
                    timeout=settings.EVENT_HANDLER_TIMEOUT_SECONDS,
                )
                logger.debug(
                    "Event handler success",
                    event_name=event_name,
                    handler=handler.__name__,
                )
            except TimeoutError:
                logger.error(
                    "Event handler timed out",
                    event_name=event_name,
                    handler=handler.__name__,
                    timeout=settings.EVENT_HANDLER_TIMEOUT_SECONDS,
                )
                # We do not re-raise TimeoutError to prevent blocking other handlers
                # in the gather group (though return_exceptions=True handles that too).
            except Exception as e:
                logger.error(
                    "Event handler failed",
                    event_name=event_name,
                    handler=handler.__name__,
                    error=str(e),
                    exc_info=True,
                )

        if not handlers:
            return

        # Fire-and-forget: Schedule handlers as background tasks
        # Do NOT await - this allows the request to return immediately
        for handler in handlers:
            asyncio.create_task(_call_handler(handler))

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
            logger.warning(
                "Event handler ARQ not configured",
                event_name=event_name,
                message="ARQ queue not set, falling back to sync execution",
            )
            await self._publish_sync(event_name, data, handlers)
            return

        # Check circuit breaker once before batch operation
        if not arq_circuit.is_closed:
            logger.warning(
                "ARQ circuit breaker open - falling back to sync execution",
                event_name=event_name,
                handler_count=len(handlers),
            )
            await self._publish_sync(event_name, data, handlers)
            return

        from datetime import datetime, timezone
        from uuid import uuid4

        # Use pipeline for batch enqueue to reduce RTT
        # We manually construct the ARQ job payload to avoid N calls to enqueue_job
        try:
            pipe = self._arq_queue.redis.pipeline()

            # Determine Redis key for queue
            # ARQ default is "arq:queue".
            # We map NORMAL -> "arq:queue", LOW -> "arq:queue:low"
            if priority == EventPriority.NORMAL:
                queue_name = "arq:queue"
            else:
                queue_name = "arq:queue:low"

            count = 0
            for handler in handlers:
                job_id = str(uuid4())
                enqueue_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

                # Construct ARQ job payload matches arq.jobs.JobDef
                job_payload = {
                    "function": "process_event_handler",
                    "args": (event_name, handler.__module__, handler.__name__, data),
                    "kwargs": {},
                    "job_try": 0,
                    "enqueue_time": enqueue_time_ms,
                    "job_id": job_id,
                }

                pipe.rpush(queue_name, json_dumps(job_payload))
                count += 1

            if count > 0:
                await pipe.execute()
                await arq_circuit.record_success()
                logger.debug(
                    "Enqueued event handlers",
                    count=count,
                    event_name=event_name,
                    priority=priority,
                )

        except Exception as e:
            await arq_circuit.record_failure(e)
            logger.error(
                "Batch enqueue failed - falling back to sync execution",
                event_name=event_name,
                error=str(e),
                exc_info=True,
            )
            # Fallback to sync execution for ALL handlers
            await self._publish_sync(event_name, data, handlers)

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
        from datetime import UTC, datetime

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

        if self._redis_client:
            try:
                if settings.r1_unified.DLQ_USE_BOUNDED_SIZE:
                    # P3-1 Optimized: Pipeline with LTRIM for bounded size
                    max_size = settings.r1_unified.DLQ_MAX_SIZE
                    alert_threshold = settings.r1_unified.DLQ_ALERT_THRESHOLD_PCT

                    pipe = self._redis_client.pipeline()
                    pipe.lpush(self.DLQ_REDIS_KEY, json_dumps(dlq_entry, as_str=True))
                    pipe.ltrim(self.DLQ_REDIS_KEY, 0, max_size - 1)  # Keep newest entries
                    pipe.llen(self.DLQ_REDIS_KEY)
                    results = await pipe.execute()

                    dlq_size = results[2]  # LLEN result
                    update_dlq_size(dlq_size, max_size)

                    # Warn when approaching capacity
                    threshold = max_size * alert_threshold / 100
                    if dlq_size > threshold:
                        logger.warning(
                            "DLQ approaching capacity",
                            dlq_size=dlq_size,
                            max_size=max_size,
                            usage_pct=round(dlq_size * 100 / max_size, 1),
                        )
                else:
                    # Original path (fallback)
                    await self._redis_client.lpush(self.DLQ_REDIS_KEY, json_dumps(dlq_entry, as_str=True))
                    dlq_size = await self._redis_client.llen(self.DLQ_REDIS_KEY)
                    update_dlq_size(dlq_size)
            except Exception as e:
                logger.error("Failed to add to DLQ", error=str(e), event_name=event_name)
                # Re-raise to avoid silent failures
                raise
        else:
            logger.warning(
                "Redis not configured for DLQ, event will be lost on restart",
                event_name=event_name,
            )

        logger.warning(
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
        if not self._redis_client:
            return []

        entries = await self._redis_client.lrange(self.DLQ_REDIS_KEY, 0, limit - 1)
        return [json_loads(entry) for entry in entries]

    async def get_dlq_size(self) -> int:
        """Get the number of entries in the DLQ.

        Returns
        -------
            Number of DLQ entries
        """
        if not self._redis_client:
            return 0
        return await self._redis_client.llen(self.DLQ_REDIS_KEY)

    async def retry_dlq_entry(self) -> dict | None:
        """Remove and return the oldest DLQ entry for retry (FIFO).

        Returns
        -------
            DLQ entry or None if queue is empty
        """
        if not self._redis_client:
            return None

        entry = await self._redis_client.rpop(self.DLQ_REDIS_KEY)
        if entry:
            return json_loads(entry)
        return None

    async def clear_dlq(self) -> int:
        """Clear all DLQ entries.

        Returns
        -------
            Number of entries cleared
        """
        if not self._redis_client:
            return 0

        count = await self._redis_client.llen(self.DLQ_REDIS_KEY)
        await self._redis_client.delete(self.DLQ_REDIS_KEY)
        return count

    def _scan_handler_modules(self) -> list[str]:
        """Scan for service modules that might contain event handlers.

        Returns
        -------
            List of module paths (e.g. "src.app.modules.users.service")
        """

        # Find the modules directory
        current_file = Path(__file__)
        modules_dir = current_file.parent.parent / "modules"

        if not modules_dir.exists():
            logger.warning("Modules directory not found for event handler scanning", path=str(modules_dir))
            return []

        logger.info("Scanning for event handlers", modules_dir=str(modules_dir))

        module_paths = []
        for item in modules_dir.iterdir():
            if item.is_dir() and (item / "__init__.py").exists():
                module_name = item.name
                # Skip __pycache__ and private modules
                if module_name.startswith("_"):
                    continue

                # Check if service.py exists
                if (item / "service.py").exists():
                    module_paths.append(f"src.app.modules.{module_name}.service")

        return module_paths

    def register_all_handlers(self) -> None:
        """Pre-register all event handlers during startup.

        Scans the `src.app.modules` package for `service` modules and imports them.
        This triggers the `@event_bus.subscribe` decorators, registering the handlers.
        """

        service_modules = self._scan_handler_modules()
        count = 0

        for service_module_path in service_modules:
            try:
                importlib.import_module(service_module_path)
                count += 1
                logger.debug("Imported module for event handlers", module=service_module_path)
            except Exception as e:
                # Log error but don't fail startup completely
                logger.error(
                    "Failed to import module during event handler registration",
                    module=service_module_path,
                    error=str(e),
                )

        logger.info(
            "Event handler registration complete",
            modules_scanned=count,
            total_handlers=sum(len(handlers) for handlers in self._handlers.values()),
        )
        self._registration_complete = True


# Global event bus instance
event_bus = EventBus()


# ARQ worker function to process queued event handlers
async def process_event_handler(
    ctx: dict[str, Any],
    event_name: str,
    handler_module: str,
    handler_name: str,
    data: dict[str, Any],
) -> None:
    """ARQ worker function to execute event handlers asynchronously.

    Args:
        ctx: ARQ context (includes job_try for retry count)
        event_name: Name of event
        handler_module: Module containing handler
        handler_name: Name of handler function
        data: Event payload
    """
    job_try = ctx.get("job_try", 1)

    try:
        # Dynamically import and execute handler
        import importlib

        module = importlib.import_module(handler_module)
        handler = getattr(module, handler_name)

        # Apply timeout protection for async worker execution
        try:
            timeout_val = settings.EVENT_HANDLER_TIMEOUT_SECONDS
            await asyncio.wait_for(
                handler(data),
                timeout=timeout_val,
            )
        except TimeoutError:
            logger.error(
                "ARQ event handler timed out",
                event_name=event_name,
                handler=f"{handler_module}.{handler_name}",
                timeout=timeout_val,
            )
            raise TimeoutError(f"Handler timed out after {timeout_val}s")

        logger.info(
            "ARQ event handler success",
            event_name=event_name,
            handler=f"{handler_module}.{handler_name}",
        )
    except Exception as e:
        handler_full_name = f"{handler_module}.{handler_name}"

        # Check if max retries exceeded
        if job_try >= EventBus.MAX_RETRIES:
            logger.error(
                "ARQ event handler max retries exceeded - moving to DLQ",
                event_name=event_name,
                handler=handler_full_name,
                attempt_count=job_try,
                error=str(e),
            )
            # Add to dead-letter queue
            await event_bus.add_to_dlq(
                event_name=event_name,
                handler_name=handler_full_name,
                data=data,
                error=str(e),
                attempt_count=job_try,
            )
            # Don't re-raise - event is now in DLQ
            return

        logger.warning(
            "ARQ event handler failed - will retry",
            event_name=event_name,
            handler=handler_full_name,
            attempt=job_try,
            max_attempts=EventBus.MAX_RETRIES,
            error=str(e),
        )
        raise  # Re-raise for ARQ retry logic
