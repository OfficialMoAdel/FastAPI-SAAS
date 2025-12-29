"""Event bus for publish/subscribe pattern across modules.

Supports priority-based event handling:
- CRITICAL: Synchronous, fire-and-forget (audit logs)
- NORMAL/LOW: Asynchronous via ARQ queue (emails, webhooks)
"""

import asyncio
from collections import defaultdict
from enum import Enum
from typing import Any, Callable, Coroutine

from structlog import get_logger

logger = get_logger(__name__)


class EventPriority(str, Enum):
    """Event priority levels."""

    CRITICAL = "CRITICAL"  # Sync execution, no retry
    NORMAL = "NORMAL"  # Async via ARQ, with retry
    LOW = "LOW"  # Async via ARQ, with retry, lower priority


class EventBus:
    """Central event bus for decoupled module communication."""

    def __init__(self) -> None:
        """Initialize event bus with handler registry."""
        self._handlers: dict[str, list[Callable[..., Coroutine[Any, Any, None]]]] = defaultdict(list)
        self._arq_queue: Any = None  # Will be set during app startup

    def set_arq_queue(self, queue: Any) -> None:
        """Set ARQ queue for async event processing.

        Args:
            queue: ARQ queue instance
        """
        self._arq_queue = queue

    def subscribe(self, event_name: str) -> Callable:
        """Decorator to subscribe a handler to an event.

        Args:
            event_name: Name of the event to subscribe to

        Returns:
            Decorator function

        Example:
            @event_bus.subscribe("user.created")
            async def send_welcome_email(data: dict):
                ...
        """

        def decorator(func: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., Coroutine[Any, Any, None]]:
            self._handlers[event_name].append(func)
            logger.info("event_handler_registered", event=event_name, handler=func.__name__)
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
            event_name: Name of the event
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
            logger.debug("event_published_no_handlers", event=event_name)
            return

        logger.info(
            "event_published",
            event=event_name,
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
            event_name: Name of the event
            data: Event payload
            handlers: List of handler functions
        """
        for handler in handlers:
            try:
                await handler(data)
                logger.debug(
                    "event_handler_success",
                    event=event_name,
                    handler=handler.__name__,
                )
            except Exception as e:
                logger.error(
                    "event_handler_failed",
                    event=event_name,
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
            event_name: Name of the event
            data: Event payload
            handlers: List of handler functions
            priority: Event priority for queue scheduling
        """
        if not self._arq_queue:
            logger.warning(
                "event_handler_arq_not_configured",
                event=event_name,
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
                    "event_handler_queued",
                    event=event_name,
                    handler=handler.__name__,
                    priority=priority,
                )
            except Exception as e:
                logger.error(
                    "event_handler_queue_failed",
                    event=event_name,
                    handler=handler.__name__,
                    error=str(e),
                    exc_info=True,
                )

    def get_handlers(self, event_name: str) -> list[Callable[..., Coroutine[Any, Any, None]]]:
        """Get all handlers registered for an event.

        Args:
            event_name: Name of the event

        Returns:
            List of handler functions
        """
        return self._handlers.get(event_name, [])


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
        ctx: ARQ context
        event_name: Name of the event
        handler_module: Module containing the handler
        handler_name: Name of the handler function
        data: Event payload
    """
    try:
        # Dynamically import and execute the handler
        import importlib

        module = importlib.import_module(handler_module)
        handler = getattr(module, handler_name)
        await handler(data)

        logger.info(
            "arq_event_handler_success",
            event=event_name,
            handler=f"{handler_module}.{handler_name}",
        )
    except Exception as e:
        logger.error(
            "arq_event_handler_failed",
            event=event_name,
            handler=f"{handler_module}.{handler_name}",
            error=str(e),
            exc_info=True,
        )
        raise  # Re-raise for ARQ retry logic
