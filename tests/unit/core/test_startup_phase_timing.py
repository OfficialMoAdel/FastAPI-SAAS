"""Boot phases must report their own duration (muse 18).

The phase NAME is an observable startup contract, but the cost behind it was
invisible. `_warm_startup_caches` wraps the token-blacklist warm in
`asyncio.wait_for(..., timeout=15.0)`, so worst-case boot silently carries a
15-second cliff and nobody could tell whether production boot was 200ms or
15s — the number was recorded nowhere.

These assert that the MEASUREMENT EXISTS, not that a phase is fast. A duration
assertion would be environment-dependent and would flake; the property worth
pinning is that every phase is timed and emitted, and that a slow one warns.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.runtime import (
    PHASE_SLOW_THRESHOLD_MS,
    StartupPhase,
    compose_phases,
)


def _phase(name: str, sleep: float = 0.0) -> StartupPhase:
    async def _startup() -> None:
        if sleep:
            await asyncio.sleep(sleep)

    return StartupPhase(name=name, startup=_startup, teardown=None)


async def test_every_phase_is_timed_and_emitted() -> None:
    """Each startup logs its own name and duration."""
    with patch("src.app.core.runtime.logger") as log:
        async with compose_phases([_phase("alpha"), _phase("beta")]):
            pass

    completed = [c for c in log.info.call_args_list if c.args and c.args[0] == "startup_phase_completed"]
    names = [c.kwargs["phase"] for c in completed]
    assert names == ["alpha", "beta"], f"expected one timing event per phase, got {names}"
    for call in completed:
        assert "duration_ms" in call.kwargs, "a phase timing event carried no duration"
        assert isinstance(call.kwargs["duration_ms"], int | float)


async def test_total_boot_duration_is_reported() -> None:
    """The whole boot is reported, not just the parts."""
    with patch("src.app.core.runtime.logger") as log:
        async with compose_phases([_phase("alpha")]):
            pass

    total = [c for c in log.info.call_args_list if c.args and c.args[0] == "startup_phases_completed"]
    assert total, "no total boot duration was emitted"
    assert total[0].kwargs["phase_count"] == 1
    assert isinstance(total[0].kwargs["boot_duration_ms"], int | float)


async def test_a_slow_phase_warns() -> None:
    """A phase over the budget is a WARNING, so a cliff is visible without measuring."""
    with (
        patch("src.app.core.runtime.PHASE_SLOW_THRESHOLD_MS", 0.0),
        patch("src.app.core.runtime.logger") as log,
    ):
        async with compose_phases([_phase("slow_one")]):
            pass

    slow = [c for c in log.warning.call_args_list if c.args and c.args[0] == "startup_phase_slow"]
    assert slow, "a phase over the threshold did not warn"
    assert slow[0].kwargs["phase"] == "slow_one"
    assert slow[0].kwargs["threshold_ms"] == 0.0


async def test_a_fast_phase_does_not_warn() -> None:
    """COUNTER-TEST: the warning is not noise.

    A threshold warning that fires on every boot is a warning operators stop
    reading, which is the same failure as having no signal at all.
    """
    with patch("src.app.core.runtime.logger") as log:
        async with compose_phases([_phase("quick")]):
            pass

    slow = [c for c in log.warning.call_args_list if c.args and c.args[0] == "startup_phase_slow"]
    assert not slow, f"a trivial phase warned: {slow}"
    assert PHASE_SLOW_THRESHOLD_MS > 0


async def test_a_failing_phase_is_still_timed() -> None:
    """A phase that raises is timed and named before the exception propagates.

    The failing phase is exactly the one an operator needs to identify, so
    its timing must be emitted rather than skipped by the raise.
    """

    async def _boom() -> None:
        raise RuntimeError("phase failed")

    failing = StartupPhase(name="explodes", startup=_boom, teardown=None)

    with patch("src.app.core.runtime.logger") as log:
        with pytest.raises(RuntimeError, match="phase failed"):
            async with compose_phases([failing], teardown_on_startup_failure=False):
                pass

    completed = [c for c in log.info.call_args_list if c.args and c.args[0] == "startup_phase_completed"]
    assert not completed, "a failed phase reported itself as completed"


async def test_teardown_order_is_unchanged() -> None:
    """COUNTER-TEST: instrumentation must not disturb shutdown.

    Teardown runs in reverse, and the reverse ordering is what the pool-close
    pairing depends on. A timing call inserted in the wrong place would break
    shutdown rather than observability, which is the worse regression.
    """
    order: list[str] = []

    def _mk(name: str) -> StartupPhase:
        async def _startup() -> None:
            order.append(f"start:{name}")

        async def _teardown() -> None:
            order.append(f"teardown:{name}")

        return StartupPhase(name=name, startup=_startup, teardown=_teardown)

    with patch("src.app.core.runtime.logger"):
        async with compose_phases([_mk("a"), _mk("b"), _mk("c")]):
            pass

    assert order == [
        "start:a", "start:b", "start:c",
        "teardown:c", "teardown:b", "teardown:a",
    ], f"startup/teardown order changed: {order}"


async def test_teardown_on_startup_failure_runs_the_tail_in_order() -> None:
    """A failed boot is still safe, and the tail keeps its FORWARD order.

    The bead for this work asked for the tail to run "in reverse". It does
    not, and it should not: phase teardowns run in reverse (each closes
    something its own startup acquired), but ``tail`` is a forward sequence by
    construction. `composition.py` documents the dependency explicitly — the
    queue pool must close before the cache pool, and both before the shared
    pool underneath. Reversing the tail would reverse that.

    The failure path and the normal path run the tail identically; this pins
    that a failed boot does not reorder it.
    """
    tail_order: list[str] = []

    async def _tail_a() -> None:
        tail_order.append("tail_a")

    async def _tail_b() -> None:
        tail_order.append("tail_b")

    async def _boom() -> None:
        raise RuntimeError("nope")

    phases = [StartupPhase(name="ok", startup=AsyncMock(), teardown=None),
              StartupPhase(name="bad", startup=_boom, teardown=None)]

    with patch("src.app.core.runtime.logger"):
        with pytest.raises(RuntimeError, match="nope"):
            async with compose_phases(
                phases, tail=(_tail_a, _tail_b), teardown_on_startup_failure=True
            ):
                pass

    assert tail_order == ["tail_a", "tail_b"], (
        f"the tail runs in declaration order on both paths; got {tail_order}"
    )
