"""Readiness probes run CONCURRENTLY (Phase 4.1).

Before this, ``collect_readiness_results`` awaited the database, then Redis,
then the queue: three independent probes, each with its own five-second
timeout, so a ``/ready`` could take the SUM (15s) while a load balancer waited.
These tests pin the three properties that make the concurrent version safe:

* latency tracks the SLOWEST probe, not the sum;
* an exception escaping one probe cannot blank the other two answers;
* return order and per-dependency verdicts are unchanged, which is what the
  two route consumers (``modules/health/router.py`` and the compatibility
  alias) unpack positionally.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.health import HealthResult, collect_readiness_results

PROBE_DELAY = 0.15


def _healthy(delay: float = 0.0) -> AsyncMock:
    async def _probe(**_kwargs: object) -> HealthResult:
        await asyncio.sleep(delay)
        return HealthResult(status="healthy", latency_ms=delay * 1000)

    return AsyncMock(side_effect=_probe)


@pytest.fixture
def probes():
    """The three probes, patched where ``collect_readiness_results`` resolves them."""
    with (
        patch("src.app.core.health.check_database_health", new=_healthy()) as database,
        patch("src.app.core.health.check_redis_health", new=_healthy()) as redis,
        patch("src.app.core.health.check_redis_queue_health", new=_healthy()) as queue,
    ):
        yield database, redis, queue


class TestProbesRunConcurrently:
    @pytest.mark.asyncio
    async def test_latency_tracks_the_slowest_probe_not_the_sum(self) -> None:
        with (
            patch("src.app.core.health.check_database_health", new=_healthy(PROBE_DELAY)),
            patch("src.app.core.health.check_redis_health", new=_healthy(PROBE_DELAY)),
            patch("src.app.core.health.check_redis_queue_health", new=_healthy(PROBE_DELAY)),
        ):
            started = time.perf_counter()
            results = await collect_readiness_results(db=MagicMock(), redis=MagicMock())
            elapsed = time.perf_counter() - started

        assert all(result.status == "healthy" for result in results[:3])
        # Sequential would be 3 x PROBE_DELAY. Allow a wide margin so a loaded
        # CI box cannot turn scheduling jitter into a failure.
        assert elapsed < PROBE_DELAY * 2, f"probes were serialized: {elapsed:.3f}s for 3 x {PROBE_DELAY}s"

    @pytest.mark.asyncio
    async def test_all_probes_are_started_before_any_finishes(self) -> None:
        """Non-vacuity for the latency test: concurrent, not merely fast mocks."""
        order: list[str] = []

        def _recording(name: str) -> AsyncMock:
            async def _probe(**_kwargs: object) -> HealthResult:
                order.append(f"start:{name}")
                await asyncio.sleep(0.01)
                order.append(f"end:{name}")
                return HealthResult(status="healthy")

            return AsyncMock(side_effect=_probe)

        with (
            patch("src.app.core.health.check_database_health", new=_recording("database")),
            patch("src.app.core.health.check_redis_health", new=_recording("redis")),
            patch("src.app.core.health.check_redis_queue_health", new=_recording("queue")),
        ):
            await collect_readiness_results(db=MagicMock(), redis=MagicMock())

        assert order[:3] == ["start:database", "start:redis", "start:queue"]
        assert order[3:] == ["end:database", "end:redis", "end:queue"]


class TestContractIsUnchanged:
    @pytest.mark.asyncio
    async def test_return_order_is_database_redis_queue_events(self, probes) -> None:
        database, redis, queue = probes
        database.side_effect = None
        database.return_value = HealthResult(status="unhealthy", error="db down")
        redis.side_effect = None
        redis.return_value = HealthResult(status="degraded")
        queue.side_effect = None
        queue.return_value = HealthResult(status="healthy")

        result = await collect_readiness_results(db=MagicMock(), redis=MagicMock())

        assert result[0].status == "unhealthy", "position 0 is the database"
        assert result[1].status == "degraded", "position 1 is Redis"
        assert result[2].status == "healthy", "position 2 is the queue"
        # Position 3 is the event bus's readiness flag (a real bool in
        # production; the repository-wide autouse fixture mocks the event bus).
        assert not isinstance(result[3], HealthResult), "position 3 is event-bus readiness, not a probe result"

    @pytest.mark.asyncio
    async def test_one_raising_probe_does_not_blank_the_others(self) -> None:
        """A crash outside a probe's own try block is still a per-dependency answer."""
        with (
            patch(
                "src.app.core.health.check_database_health",
                new=AsyncMock(side_effect=RuntimeError("session exploded")),
            ),
            patch("src.app.core.health.check_redis_health", new=_healthy()),
            patch("src.app.core.health.check_redis_queue_health", new=_healthy()),
        ):
            database_result, redis_result, queue_result, _events = await collect_readiness_results(
                db=MagicMock(), redis=MagicMock()
            )

        assert database_result.status == "unhealthy"
        assert "Probe crashed" in str(database_result.error)
        assert redis_result.status == "healthy", "a database crash must not blank Redis"
        assert queue_result.status == "healthy", "a database crash must not blank the queue"

    @pytest.mark.asyncio
    async def test_the_slowest_probe_does_not_delay_the_others_result(self) -> None:
        """The failing probe is the slow one; the rest answer on their own time."""
        with (
            patch("src.app.core.health.check_database_health", new=_healthy(0.2)),
            patch(
                "src.app.core.health.check_redis_health",
                new=AsyncMock(side_effect=RuntimeError("redis client gone")),
            ),
            patch("src.app.core.health.check_redis_queue_health", new=_healthy()),
        ):
            result = await collect_readiness_results(db=MagicMock(), redis=MagicMock())

        assert result[0].status == "healthy"
        assert result[1].status == "unhealthy"
        assert result[2].status == "healthy"


class TestMissingDependencies:
    @pytest.mark.asyncio
    async def test_no_redis_client_is_degraded_not_a_crash(self) -> None:
        with (
            patch("src.app.core.health.check_database_health", new=_healthy()),
            patch("src.app.core.health.check_redis_queue_health", new=_healthy()),
            patch("src.app.core.redis_provider.get_redis", return_value=None),
        ):
            database_result, redis_result, queue_result, _events = await collect_readiness_results(db=MagicMock())

        assert database_result.status == "healthy"
        assert redis_result.status == "degraded"
        assert redis_result.error == "Redis not configured"
        assert queue_result.status == "healthy"

    @pytest.mark.asyncio
    async def test_a_missing_queue_pool_is_degraded_not_a_crash(self) -> None:
        with (
            patch("src.app.core.health.check_database_health", new=_healthy()),
            patch("src.app.core.health.check_redis_health", new=_healthy()),
            patch("src.app.core.utils.queue.pool", None),
        ):
            *_, queue_result, _events = await collect_readiness_results(db=MagicMock(), redis=MagicMock())

        assert queue_result.status == "degraded"
        assert queue_result.error == "Pool not initialized"


class TestProbeSessionOwnership:
    @pytest.mark.asyncio
    async def test_an_absent_db_opens_and_uses_its_own_session(self) -> None:
        """``db=None`` (the operator/compat-alias path) still probes over a session.

        The session must be OPEN while the probe runs — the pre-concurrency
        version opened it inline, and the refactor moved it into a helper, so
        the lifetime is pinned here rather than assumed.
        """
        session = MagicMock()
        opened = AsyncMock()
        opened.__aenter__.return_value = session
        opened.__aexit__.return_value = False

        with (
            patch("src.app.core.db.database.local_session", return_value=opened),
            patch("src.app.core.health.check_database_health", new=_healthy()) as database,
            patch("src.app.core.health.check_redis_health", new=_healthy()),
            patch("src.app.core.health.check_redis_queue_health", new=_healthy()),
        ):
            database_result, *_rest = await collect_readiness_results(redis=MagicMock())

        assert database_result.status == "healthy"
        database.assert_awaited_once_with(db=session)

    @pytest.mark.asyncio
    async def test_the_supplied_session_is_preferred_over_a_new_one(self) -> None:
        supplied = MagicMock()

        with (
            patch("src.app.core.db.database.local_session") as local_session,
            patch("src.app.core.health.check_database_health", new=_healthy()) as database,
            patch("src.app.core.health.check_redis_health", new=_healthy()),
            patch("src.app.core.health.check_redis_queue_health", new=_healthy()),
        ):
            await collect_readiness_results(db=supplied, redis=MagicMock())

        local_session.assert_not_called()
        database.assert_awaited_once_with(db=supplied)
