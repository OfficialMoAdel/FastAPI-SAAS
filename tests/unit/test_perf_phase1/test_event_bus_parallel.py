import asyncio
import time

import pytest

from src.app.core.events import EventBus, EventPriority


@pytest.fixture
def fresh_event_bus():
    """Fresh EventBus instance for testing."""
    return EventBus()


@pytest.mark.asyncio
async def test_parallel_execution_performance(fresh_event_bus):
    """Verify that handlers run in parallel.

    In sequential execution, 2 handlers taking 0.1s each will take ~0.2s total.
    In parallel execution, they should take ~0.1s total.
    """
    delay = 0.1

    @fresh_event_bus.subscribe("parallel.test")
    async def handler1(data: dict):
        await asyncio.sleep(delay)
        data["handler1"] = time.time()

    @fresh_event_bus.subscribe("parallel.test")
    async def handler2(data: dict):
        await asyncio.sleep(delay)
        data["handler2"] = time.time()

    start_time = time.time()
    data = {}

    # CRITICAL priority uses _publish_sync which is what we are targeting
    await fresh_event_bus.publish("parallel.test", data, priority=EventPriority.CRITICAL)
    end_time = time.time()

    duration = end_time - start_time

    # Assert it takes less than the sum of delays (indicating parallelism)
    # If sequential, it would be >= 0.2
    assert duration < (delay * 1.5), f"Execution took {duration}s, expected < {delay * 1.5}s"


@pytest.mark.asyncio
async def test_parallel_exception_isolation(fresh_event_bus):
    """Verify one failure doesn't stop others in parallel mode."""
    # Use a dict to store results - more reliable than list append in async context
    results = {"handlers_completed": 0, "success1": False, "success2": False}

    @fresh_event_bus.subscribe("parallel.error")
    async def handler_success_1(data: dict):
        results["success1"] = True
        results["handlers_completed"] += 1

    @fresh_event_bus.subscribe("parallel.error")
    async def handler_fail(data: dict):
        raise ValueError("Boom")

    @fresh_event_bus.subscribe("parallel.error")
    async def handler_success_2(data: dict):
        results["success2"] = True
        results["handlers_completed"] += 1

    await fresh_event_bus.publish("parallel.error", {}, priority=EventPriority.CRITICAL)

    # _publish_sync uses asyncio.create_task() for fire-and-forget execution
    # We need to yield control to allow background tasks to complete
    await asyncio.sleep(0.05)

    # Both success handlers should complete despite the failure
    assert results["success1"], "handler_success_1 should have completed"
    assert results["success2"], "handler_success_2 should have completed"
    assert results["handlers_completed"] == 2, "Both success handlers should complete"
