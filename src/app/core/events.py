"""Event bus for publish/subscribe pattern across modules.

Supports priority-based event handling:
- CRITICAL: Synchronous, fire-and-forget (audit logs)
- NORMAL/LOW: Asynchronous via ARQ queue (emails, webhooks)
"""

from collections import defaultdict
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

from structlog import get_logger

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
    - Dead-letter queue (DLQ) for failed events after max retries
    - ARQ integration for background processing
    """

    # Maximum retry attempts before moving to DLQ
    MAX_RETRIES = 3
    # Retry delays in seconds: [60, 300, 900] = 1min, 5min, 15min
    RETRY_DELAYS = [60, 300, 900]

    def __init__(self) -> None:
        """Initialize event bus with handler registry."""
        self._handlers = defaultdict(list)
        self._arq_queue = None
        self._dlq: list[dict] = []  # In-memory DLQ (use Redis in production)

    def set_arq_queue(self, queue: Any) -> None:
        """Set ARQ queue for async event processing.

        Args:
            queue: ARQ queue instance
        """
        self._arq_queue = queue

    def subscribe(self, event_name: str) -> Callable:
        """Decorator to subscribe a handler to an event.

        Args:
            event_name: Name of event to subscribe to

        Returns:
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

    async def publish(
        self,
        event_name: str,
        data: dict[str, Any],
        priority: EventPriority = EventPriority.NORMAL,
    ) -> None:
        """Publish an event to all registered handlers.

        Args:
            event_name: Name of event
            data: Event payload data
            priority: Event priority (CRITICAL=sync, NORMAL/LOW=async)

        Example:
            await event_bus.publish(
                "user.created",
                {"user_id": str(user.id), "email": user.email},
                priority=EventPriority.NORMAL
            )
        """
        handlers = self._handlers.get(event_name, [])

        if not handlers:
            logger.debug("Event published no handlers", event_name=event_name)
            return

        logger.info(
            "Event published",
            event_name=event_name,
            priority=priority,
            handler_count=len(handlers),
        )

        if priority == EventPriority.CRITICAL:
            # Execute synchronously, fire-and-forget
            await self._publish_sync(event_name, data, handlers)
        else:
            # Queue for async execution via ARQ
            await self._publish_async(event_name, data, handlers, priority)

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
        for handler in handlers:
            try:
                await handler(data)
                logger.debug(
                    "Event handler success",
                    event_name=event_name,
                    handler=handler.__name__,
                )
            except Exception as e:
                logger.error(
                    "Event handler failed",
                    event_name=event_name,
                    handler=handler.__name__,
                    error=str(e),
                    exc_info=True,
                )
                # Continue with other handlers even if one fails

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

        # Enqueue each handler as a separate ARQ task
        for handler in handlers:
            try:
                await self._arq_queue.enqueue_job(
                    "process_event_handler",
                    event_name,
                    handler.__module__,
                    handler.__name__,
                    data,
                    _queue_name="default" if priority == EventPriority.NORMAL else "low_priority",
                )
                logger.debug(
                    "Event handler queued",
                    event_name=event_name,
                    handler=handler.__name__,
                    priority=priority,
                )
            except Exception as e:
                logger.error(
                    "Event handler queue failed",
                    event_name=event_name,
                    handler=handler.__name__,
                    error=str(e),
                    exc_info=True,
                )

    def get_handlers(self, event_name: str) -> list[Callable[..., Coroutine[Any, Any, None]]]:
        """Get all handlers registered for an event.

        Args:
            event_name: Name of event

        Returns:
            List of handler functions
        """
        return self._handlers.get(event_name, [])

    def add_to_dlq(
        self,
        event_name: str,
        handler_name: str,
        data: dict[str, Any],
        error: str,
        attempt_count: int,
    ) -> None:
        """Add failed event to dead-letter queue.

        Args:
            event_name: Name of event
            handler_name: Handler that failed
            data: Event payload
            error: Error message
            attempt_count: Number of attempts made
        """
        from datetime import UTC, datetime

        dlq_entry = {
            "event_name": event_name,
            "handler": handler_name,
            "data": data,
            "error": error,
            "attempt_count": attempt_count,
            "failed_at": datetime.now(UTC).isoformat(),
        }
        self._dlq.append(dlq_entry)
        logger.warning(
            "Event moved to DLQ",
            event_name=event_name,
            handler=handler_name,
            attempt_count=attempt_count,
            error=error,
        )

    def get_dlq_entries(self) -> list[dict]:
        """Get all dead-letter queue entries.

        Returns:
            List of failed event entries
        """
        return self._dlq.copy()

    def retry_dlq_entry(self, index: int) -> dict | None:
        """Remove and return a DLQ entry for retry.

        Args:
            index: Index of entry to retry

        Returns:
            DLQ entry or None if not found
        """
        if 0 <= index < len(self._dlq):
            return self._dlq.pop(index)
        return None

    def clear_dlq(self) -> int:
        """Clear all DLQ entries.

        Returns:
            Number of entries cleared
        """
        count = len(self._dlq)
        self._dlq.clear()
        return count


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
        await handler(data)

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
            event_bus.add_to_dlq(
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

