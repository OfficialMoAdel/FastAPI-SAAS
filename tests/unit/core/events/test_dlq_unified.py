"""Phase 1 DLQ unification tests: bus + ARQ share the single ``arq:dlq`` queue."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from src.app.core.events import EventBus


class FakePipe:
    """Sync-record pipeline (mirrors the LPUSH+LTRIM+LLEN call shape)."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._results: list[Any] = []

    async def __aenter__(self) -> FakePipe:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    def lpush(self, key: str, value: str) -> None:
        store = self._redis.store.setdefault(key, [])
        store.insert(0, value)
        self._results.append(len(store))

    def ltrim(self, key: str, start: int, end: int) -> None:
        store = self._redis.store.setdefault(key, [])
        self._redis.store[key] = store[start : end + 1]
        self._results.append(True)

    def llen(self, key: str) -> None:
        self._results.append(len(self._redis.store.get(key, [])))

    async def execute(self) -> list[Any]:
        return self._results


class FakeRedis:
    """Minimal list-semantics Redis double shared by bus + monitor."""

    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}

    def pipeline(self, transaction: bool = True) -> FakePipe:
        return FakePipe(self)

    async def lpush(self, key: str, value: str) -> int:
        self.store.setdefault(key, []).insert(0, value)
        return len(self.store[key])

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.store.get(key, [])
        return items[start : end + 1]

    async def llen(self, key: str) -> int:
        return len(self.store.get(key, []))

    async def rpop(self, key: str) -> str | None:
        items = self.store.get(key, [])
        if not items:
            return None
        return items.pop()

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def bus(fake_redis: FakeRedis) -> EventBus:
    event_bus = EventBus()
    event_bus.set_redis_client(fake_redis)  # type: ignore[arg-type]
    return event_bus


async def _add_bus_failure(bus: EventBus, mock_settings: Any) -> None:
    with patch("src.app.core.config.settings", mock_settings):
        await bus.add_to_dlq(
            event_name="order.created",
            handler_name="send_confirmation",
            data={"order_id": "7"},
            error="SMTP down",
            attempt_count=3,
        )


@pytest.mark.asyncio
async def test_add_to_dlq_writes_unified_key(bus: EventBus, fake_redis: FakeRedis, mock_settings: Any) -> None:
    """Bus failures land on ``arq:dlq`` in monitor-compatible JSON; legacy key untouched."""
    await _add_bus_failure(bus, mock_settings)

    assert "arq:dlq" in fake_redis.store
    assert "event_bus:dlq" not in fake_redis.store
    assert EventBus.DLQ_REDIS_KEY == "arq:dlq"

    entry = json.loads(fake_redis.store["arq:dlq"][0])
    # Monitor shape (admin DLQItemResponse fields). job_id carries a
    # time_ns suffix so concurrent retries/fan-out never collide.
    assert entry["job_id"].startswith("bus:order.created:send_confirmation:")
    assert entry["job_id"] != "bus:order.created:send_confirmation:3"
    assert entry["function"] == "order.created"
    assert entry["error"] == "SMTP down"
    assert isinstance(entry["timestamp"], float)
    # Preserved bus context.
    assert entry["event_name"] == "order.created"
    assert entry["handler"] == "send_confirmation"
    assert entry["data"] == {"order_id": "7"}
    assert entry["attempt_count"] == 3


@pytest.mark.asyncio
async def test_bus_and_monitor_readers_agree_on_size(
    bus: EventBus, fake_redis: FakeRedis, mock_settings: Any
) -> None:
    """Bus readers and the admin monitor read the same surviving queue."""
    from src.app.core.job_monitoring import JobMonitoringService

    await _add_bus_failure(bus, mock_settings)
    await _add_bus_failure(bus, mock_settings)

    monitor = JobMonitoringService()
    with patch("src.app.core.job_monitoring.get_redis", return_value=fake_redis):
        assert await bus.get_dlq_size() == 2
        assert await monitor.get_dlq_size() == 2

        bus_entries = await bus.get_dlq_entries()
        assert len(bus_entries) == 2
        # Unique job_ids: the two identical failures must not collide.
        assert bus_entries[0]["job_id"] != bus_entries[1]["job_id"]

        monitor_items = await monitor.get_dead_letter_queue()
        assert len(monitor_items) == 2
        assert monitor_items[0]["function"] == "order.created"
        assert "timestamp_iso" in monitor_items[0]


@pytest.mark.asyncio
async def test_admin_get_dlq_sees_bus_failed_event(
    bus: EventBus, fake_redis: FakeRedis, mock_settings: Any
) -> None:
    """Admin GET /dlq (monitor path) surfaces a bus-failed event."""
    from src.app.api.v1.admin.jobs import get_dlq_items
    from src.app.core.job_monitoring import JobMonitoringService

    await _add_bus_failure(bus, mock_settings)

    monitor = JobMonitoringService()
    with (
        patch("src.app.core.job_monitoring.get_redis", return_value=fake_redis),
        patch("src.app.api.v1.admin.jobs.get_job_monitor", return_value=monitor),
    ):
        items = await get_dlq_items(limit=10, offset=0)

    assert len(items) == 1
    assert items[0].function == "order.created"
    assert items[0].error == "SMTP down"
    assert isinstance(items[0].job_id, str)
    assert items[0].job_id.startswith("bus:order.created:send_confirmation:")


@pytest.mark.asyncio
async def test_retry_dlq_entry_works_on_unified_key(
    bus: EventBus, fake_redis: FakeRedis, mock_settings: Any
) -> None:
    """FIFO retry pops from the surviving key and shrinks it."""
    await _add_bus_failure(bus, mock_settings)

    entry = await bus.retry_dlq_entry()
    assert entry is not None
    assert entry["event_name"] == "order.created"
    assert await bus.get_dlq_size() == 0
    assert await bus.retry_dlq_entry() is None


def test_strict_ordering_flag_declared_default_false() -> None:
    """EVENT_BUS_REQUIRE_STRICT_ORDERING exists and preserves the sequence=0 path."""
    from src.app.core.config.performance import EventSettings

    assert EventSettings().EVENT_BUS_REQUIRE_STRICT_ORDERING is False

    from src.app.core.config import settings

    assert settings.EVENT_BUS_REQUIRE_STRICT_ORDERING is False
