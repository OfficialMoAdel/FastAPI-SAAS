"""Integration Tests: Event Bus with Real Async Operations.

Tests validate event bus behavior with actual async execution,
verifying parallel handling, priority ordering, and error isolation.
"""

import asyncio
from uuid import uuid4

import pytest

from src.app.core.events import EventPriority, event_bus

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestEventBusIntegration:
    """Test event bus with real async operations."""

    @pytest.fixture(autouse=True)
    def clear_handlers(self):
        """Clear event handlers before each test."""
        event_bus._handlers.clear()
        yield
        event_bus._handlers.clear()

    async def test_event_publish_subscribe(self):
        """Verify basic publish/subscribe works."""
        results = []

        @event_bus.subscribe("test.integration.basic")
        async def handler(data: dict):
            results.append(data["value"])

        await event_bus.publish("test.integration.basic", {"value": 42})
        await asyncio.sleep(0.05)  # Allow async handlers to complete

        assert results == [42]

    async def test_multiple_handlers_all_called(self):
        """Verify all subscribed handlers are called."""
        results = {"handler1": False, "handler2": False, "handler3": False}

        @event_bus.subscribe("test.integration.multi")
        async def handler1(data: dict):
            results["handler1"] = True

        @event_bus.subscribe("test.integration.multi")
        async def handler2(data: dict):
            results["handler2"] = True

        @event_bus.subscribe("test.integration.multi")
        async def handler3(data: dict):
            results["handler3"] = True

        await event_bus.publish("test.integration.multi", {})
        await asyncio.sleep(0.1)

        assert all(results.values()), f"Not all handlers called: {results}"

    async def test_critical_priority_sync_execution(self):
        """Verify CRITICAL priority executes handlers synchronously."""
        order = []

        @event_bus.subscribe("test.integration.critical")
        async def handler1(data: dict):
            await asyncio.sleep(0.01)
            order.append(1)

        @event_bus.subscribe("test.integration.critical")
        async def handler2(data: dict):
            await asyncio.sleep(0.01)
            order.append(2)

        await event_bus.publish(
            "test.integration.critical",
            {},
            priority=EventPriority.CRITICAL,
        )
        await asyncio.sleep(0.1)

        # Both handlers should complete
        assert len(order) == 2

    async def test_normal_priority_async_execution(self):
        """Verify NORMAL priority allows async execution."""
        results = []

        @event_bus.subscribe("test.integration.normal")
        async def slow_handler(data: dict):
            await asyncio.sleep(0.05)
            results.append("slow")

        @event_bus.subscribe("test.integration.normal")
        async def fast_handler(data: dict):
            results.append("fast")

        await event_bus.publish(
            "test.integration.normal",
            {},
            priority=EventPriority.NORMAL,
        )

        # Wait for all handlers
        await asyncio.sleep(0.15)

        # Both should complete (order may vary due to async)
        assert "fast" in results
        assert "slow" in results

    async def test_exception_isolation(self):
        """Verify one handler exception doesn't stop others."""
        results = {"success1": False, "success2": False}

        @event_bus.subscribe("test.integration.error")
        async def success_handler1(data: dict):
            results["success1"] = True

        @event_bus.subscribe("test.integration.error")
        async def failing_handler(data: dict):
            raise ValueError("Test error")

        @event_bus.subscribe("test.integration.error")
        async def success_handler2(data: dict):
            results["success2"] = True

        await event_bus.publish(
            "test.integration.error",
            {},
            priority=EventPriority.CRITICAL,
        )
        await asyncio.sleep(0.1)

        # Both success handlers should complete despite the error
        assert results["success1"], "success_handler1 should complete"
        assert results["success2"], "success_handler2 should complete"

    async def test_high_volume_events(self):
        """Test event bus handles high volume of events."""
        count = {"received": 0}

        @event_bus.subscribe("test.integration.volume")
        async def counter_handler(data: dict):
            count["received"] += 1

        # Publish many events
        event_count = 100
        for i in range(event_count):
            await event_bus.publish(
                "test.integration.volume",
                {"index": i},
                priority=EventPriority.NORMAL,
            )

        # Wait for processing
        await asyncio.sleep(0.5)

        assert count["received"] == event_count


class TestEventBusWithRedis:
    """Test event bus patterns that interact with Redis."""

    @pytest.fixture(autouse=True)
    def clear_handlers(self):
        """Clear event handlers before each test."""
        event_bus._handlers.clear()
        yield
        event_bus._handlers.clear()

    async def test_cache_invalidation_event(self, real_redis, clean_redis):
        """Test event triggers cache invalidation."""
        user_id = str(uuid4())
        cache_key = f"user:{user_id}"

        # Pre-populate cache
        await real_redis.setex(cache_key, 3600, '{"id": "' + user_id + '"}')

        invalidated = {"done": False}

        @event_bus.subscribe("user.updated")
        async def invalidate_cache(data: dict):
            await real_redis.delete(f"user:{data['user_id']}")
            invalidated["done"] = True

        # Publish update event
        await event_bus.publish(
            "user.updated",
            {"user_id": user_id},
            priority=EventPriority.CRITICAL,
        )
        await asyncio.sleep(0.1)

        # Cache should be invalidated
        assert invalidated["done"]
        assert await real_redis.get(cache_key) is None

    async def test_audit_log_event(self, real_redis, clean_redis):
        """Test audit log events are captured."""
        audit_key = "audit:log:test"

        @event_bus.subscribe("audit.action")
        async def log_audit(data: dict):
            await real_redis.lpush(
                audit_key,
                f"{data['action']}:{data['user_id']}",
            )

        # Publish audit events
        await event_bus.publish(
            "audit.action",
            {"action": "login", "user_id": "user1"},
            priority=EventPriority.CRITICAL,
        )
        await event_bus.publish(
            "audit.action",
            {"action": "logout", "user_id": "user1"},
            priority=EventPriority.CRITICAL,
        )
        await asyncio.sleep(0.1)

        # Check audit log
        logs = await real_redis.lrange(audit_key, 0, -1)
        assert len(logs) == 2

    async def test_rate_limit_exceeded_event(self, real_redis, clean_redis):
        """Test rate limit exceeded triggers notification event."""
        notifications = []

        @event_bus.subscribe("rate_limit.exceeded")
        async def notify_rate_limit(data: dict):
            notifications.append(data)

        # Simulate rate limit exceeded
        await event_bus.publish(
            "rate_limit.exceeded",
            {
                "user_id": "user123",
                "endpoint": "/api/v1/users",
                "limit": 100,
                "window": 60,
            },
            priority=EventPriority.CRITICAL,
        )
        await asyncio.sleep(0.1)

        assert len(notifications) == 1
        assert notifications[0]["endpoint"] == "/api/v1/users"


class TestEventBusConcurrency:
    """Test event bus under concurrent load."""

    @pytest.fixture(autouse=True)
    def clear_handlers(self):
        """Clear event handlers before each test."""
        event_bus._handlers.clear()
        yield
        event_bus._handlers.clear()

    async def test_concurrent_event_publishing(self):
        """Test concurrent event publishing doesn't cause issues."""
        count = {"received": 0}
        lock = asyncio.Lock()

        @event_bus.subscribe("test.integration.concurrent")
        async def thread_safe_handler(data: dict):
            async with lock:
                count["received"] += 1

        # Publish concurrently
        async def publish_events(n: int):
            for _ in range(n):
                await event_bus.publish(
                    "test.integration.concurrent",
                    {},
                    priority=EventPriority.NORMAL,
                )

        # 5 concurrent publishers, 20 events each
        await asyncio.gather(*[publish_events(20) for _ in range(5)])
        await asyncio.sleep(0.5)

        assert count["received"] == 100

    async def test_slow_handler_doesnt_block_others(self):
        """Verify slow handlers don't block other events."""
        fast_results = []
        slow_complete = {"done": False}

        @event_bus.subscribe("test.integration.slow")
        async def slow_handler(data: dict):
            await asyncio.sleep(0.2)
            slow_complete["done"] = True

        @event_bus.subscribe("test.integration.fast")
        async def fast_handler(data: dict):
            fast_results.append(data["value"])

        # Publish slow event first
        await event_bus.publish(
            "test.integration.slow",
            {},
            priority=EventPriority.NORMAL,
        )

        # Immediately publish fast events
        for i in range(5):
            await event_bus.publish(
                "test.integration.fast",
                {"value": i},
                priority=EventPriority.NORMAL,
            )

        # Fast events should complete quickly
        await asyncio.sleep(0.1)
        assert len(fast_results) == 5

        # Slow event completes later
        await asyncio.sleep(0.2)
        assert slow_complete["done"]
