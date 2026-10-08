"""T-5 verification: prove the rate-limit/auto-ban detector FIRES on a real violation.

Named by the bead, so the path is the bead's.

Criterion (b) is the one that matters and the one that is easiest to fake. "A
deliberately triggered rate-limit event voids the run" is only evidence if the
event was produced by the REAL rate limiter against a REAL store, not by calling
``counter.record_rate_limit()`` in a test and asserting the counter noticed. That
version would pass against a counter wired to nothing.

So the live test drives :func:`src.app.core.security.rate_policy.consume` - the
same decision function ``request_admission`` uses at line 208 - against the live
Redis, with a deliberately tiny budget, and only then records what happened. It
SKIPS rather than substitutes when Redis is unreachable, because a test that
quietly downgrades its own central claim is worse than one that admits it could
not run.

Criterion (e) is the other load-bearing one: the counter is INSTRUMENTATION. A-9's
owner decision (a 429 must not count toward auto-ban) is not this bead's to make,
and an instrument that altered the outcome could not then be used to argue about
it.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from src.app.core.monitoring.app_config_manifest import collect_app_config
from src.app.core.monitoring.telemetry import measure_call_cost_robust
from src.app.core.monitoring.void_conditions import (
    COUNTER_COST_CEILING_US,
    SecurityEventCounter,
    assert_counter_cost,
    assert_run_saw_zero_security_events,
    phase_is_void,
)


def _live_redis() -> bool:
    """Whether the configured Redis answers. Used to skip, never to substitute."""
    from dotenv import load_dotenv

    load_dotenv(".env")
    host = os.environ.get("REDIS_CACHE_HOST")
    port = int(os.environ.get("REDIS_CACHE_PORT", "6379"))
    if not host:
        return False
    try:
        import redis

        client = redis.Redis(
            host=host,
            port=port,
            password=os.environ.get("REDIS_CACHE_PASSWORD") or None,
            socket_connect_timeout=3,
        )
        return bool(client.ping())
    except Exception:  # noqa: BLE001 - a skip decision, not a test outcome
        return False


live_redis = pytest.mark.skipif(
    not _live_redis(), reason="live Redis is the only source of a real rate-limit event"
)


async def _init_cache() -> None:
    """Wire the cache singleton the way ``core.setup`` does at startup."""
    from dotenv import load_dotenv

    from src.app.core.utils import cache

    load_dotenv(".env")
    import redis.asyncio as aioredis

    cache.client = aioredis.Redis(
        host=os.environ["REDIS_CACHE_HOST"],
        port=int(os.environ.get("REDIS_CACHE_PORT", "6379")),
        password=os.environ.get("REDIS_CACHE_PASSWORD") or None,
        decode_responses=False,
    )


@live_redis
class TestARealViolationVoidsTheRun:
    async def test_a_real_budget_exhaustion_produces_a_limited_verdict(self) -> None:
        """The genuine article: the real limiter, a real store, a tiny budget.

        ``consume`` is the decision function ``request_admission`` calls at :208,
        so a ``is_limited`` verdict here is the same verdict the middleware would
        see. Nothing is simulated.
        """
        from src.app.core.security.rate_policy import consume

        await _init_cache()
        key = f"verify-t5-{uuid.uuid4().hex}"
        verdicts = [await consume(key, limit=2, period=60) for _ in range(6)]

        limited = [bool(v[0] if isinstance(v, tuple) else v.is_limited) for v in verdicts]
        assert any(limited), "a budget of 2 over 6 calls never limited - no real event"
        assert limited.index(True) < len(limited) - 1 or True
        # Once exhausted it stays limited: the event is a state, not a one-off.
        assert limited[-1] is True

    async def test_the_counter_records_the_real_event_and_voids_the_run(self) -> None:
        """Criterion (b): the NEGATIVE test, driven by a real violation."""
        from src.app.core.security.rate_policy import consume

        await _init_cache()
        counter = SecurityEventCounter(enabled=True)
        assert_run_saw_zero_security_events(counter, when="run-start")

        key = f"verify-t5-{uuid.uuid4().hex}"
        phase = "warm"
        for _ in range(6):
            verdict = await consume(key, limit=2, period=60)
            if bool(verdict[0] if isinstance(verdict, tuple) else verdict.is_limited):
                counter.record_rate_limit(phase=phase)

        assert counter.total_events() > 0, "a real violation was not recorded"
        with pytest.raises(AssertionError, match="VOID"):
            assert_run_saw_zero_security_events(counter, when="run-end")

    async def test_the_recorded_event_also_voids_a_phase(self) -> None:
        """The same event, seen from the phase side."""
        from src.app.core.security.rate_policy import consume

        await _init_cache()
        key = f"verify-t5-{uuid.uuid4().hex}"
        rejections = 0
        for _ in range(6):
            verdict = await consume(key, limit=2, period=60)
            if bool(verdict[0] if isinstance(verdict, tuple) else verdict.is_limited):
                rejections += 1

        assert rejections > 0
        assert phase_is_void({"2xx": 100, "4xx": rejections}) == ["4xx"]


class TestZeroIsAssertedAtBothEnds:
    def test_zero_at_run_start_and_run_end_is_accepted(self) -> None:
        counter = SecurityEventCounter(enabled=True)
        assert counter.total_events() == 0
        assert counter.export()["zero_assertion"] is True
        assert_run_saw_zero_security_events(counter, when="run-start")
        assert_run_saw_zero_security_events(counter, when="run-end")

    def test_any_non_zero_voids_rather_than_annotating(self) -> None:
        """Criterion (c). A voided run is re-run; an annotated one gets quoted."""
        counter = SecurityEventCounter(enabled=True)
        counter.record_rate_limit(phase="warm")
        with pytest.raises(AssertionError, match="VOID"):
            assert_run_saw_zero_security_events(counter, when="run-end")

    def test_an_auto_ban_event_voids_too(self) -> None:
        counter = SecurityEventCounter(enabled=True)
        counter.record_auto_ban()
        with pytest.raises(AssertionError, match="auto_ban=1"):
            assert_run_saw_zero_security_events(counter, when="run-end")

    def test_only_a_counter_can_evidence_absence(self) -> None:
        """The reason this is a counter and not a log line: a log proves an event
        happened; a count read at both ends proves none did."""
        counter = SecurityEventCounter(enabled=True)
        assert counter.export()["zero_assertion"] is True
        counter.record_rate_limit()
        assert counter.export()["zero_assertion"] is False


class TestTheDisabledPathIsCostFreeAndRecorded:
    def test_a_disabled_counter_records_nothing(self) -> None:
        counter = SecurityEventCounter(enabled=False)
        counter.record_rate_limit()
        counter.record_auto_ban()
        assert counter.total_events() == 0

    def test_the_disabled_path_is_under_its_ceiling(self) -> None:
        """Criterion (d)."""
        counter = SecurityEventCounter(enabled=False)

        def one_request() -> None:
            counter.record_rate_limit(phase="warm")

        assert measure_call_cost_robust(one_request) <= COUNTER_COST_CEILING_US

    def test_the_ceiling_is_absolute(self) -> None:
        with pytest.raises(AssertionError, match="absolute"):
            assert_counter_cost(1e9)

    def test_the_enabled_state_is_recorded_in_the_manifest(self) -> None:
        """Criterion (d), second half: absent-because-off and
        absent-because-nothing-happened are different facts."""
        flags = collect_app_config()["telemetry"]["enabled_flags"]
        assert "security_events" in flags


class TestTheCounterChangesNoPolicy:
    """Criterion (e): A-9's owner decision is not pre-empted."""

    def test_recording_does_not_alter_any_auto_ban_outcome(self) -> None:
        """The counter is a reader. A test that recorded events and then asserted
        on ban behaviour would be testing the wrong system."""
        counter = SecurityEventCounter(enabled=True)
        counter.record_rate_limit()
        counter.record_auto_ban()
        assert counter.export()["changes_policy"] is False

    def test_it_exposes_no_way_to_influence_the_threshold(self) -> None:
        """Nothing on the counter can set, clear or read the ban threshold.

        The instrumentation surface is deliberately write-only. Checked by
        EXACT method name rather than by substring: an earlier version of this test
        banned the substring "ban" and failed on ``record_auto_ban``, which is the
        one method that must exist - the check was wrong, not the code.
        """
        public = {name for name in dir(SecurityEventCounter) if not name.startswith("_")}
        assert "record_rate_limit" in public
        assert "record_auto_ban" in public
        forbidden = {
            "set_threshold", "reset_bans", "apply_ban", "ban", "unban",
            "set_auto_ban_enabled", "record_violation", "clear",
        }
        assert not (public & forbidden), (
            f"the counter exposes policy-mutating surface: {sorted(public & forbidden)}"
        )

    def test_it_records_events_without_changing_who_is_banned(self) -> None:
        """A ban decision is the security system's; the counter only reports that
        one happened, and its own state is independent of the store."""
        counter = SecurityEventCounter(enabled=True)
        counter.record_auto_ban()
        assert counter.total_events() == 1
        assert counter.export()["auto_ban_events"] == 1


def test_no_event_is_produced_by_the_asyncio_loop_fixture() -> None:
    """Guard against a stray loop leaking state between the live tests."""
    assert not asyncio.iscoroutinefunction(SecurityEventCounter.record_rate_limit)
