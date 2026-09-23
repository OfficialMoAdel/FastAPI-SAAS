"""Card 10: compose_phases contract — order is data, teardown is reverse + tail."""

import pytest

from src.app.core.setup import StartupPhase, compose_phases


async def _recorder(log: list[str], name: str, fail: bool = False) -> None:
    log.append(name)
    if fail:
        raise RuntimeError(f"{name} failed")


def _phase(log: list[str], name: str, *, teardown: bool = True, fail: bool = False) -> StartupPhase:
    async def start() -> None:
        await _recorder(log, f"start:{name}", fail)

    async def stop() -> None:
        log.append(f"stop:{name}")

    return StartupPhase(name, start, stop if teardown else None)


@pytest.mark.asyncio
async def test_order_start_forwards_teardown_reverse_tail() -> None:
    log: list[str] = []

    async def tail_one() -> None:
        log.append("tail:one")

    async def tail_two() -> None:
        log.append("tail:two")

    async with compose_phases(
        [_phase(log, "a"), _phase(log, "b"), _phase(log, "c")],
        tail=(tail_one, tail_two),
    ):
        log.append("yield")
    assert log == [
        "start:a",
        "start:b",
        "start:c",
        "yield",
        "stop:c",
        "stop:b",
        "stop:a",
        "tail:one",
        "tail:two",
    ]


@pytest.mark.asyncio
async def test_teardown_none_skipped() -> None:
    log: list[str] = []
    async with compose_phases([_phase(log, "a", teardown=False)]):
        pass
    assert log == ["start:a"]


@pytest.mark.asyncio
async def test_startup_failure_skips_teardown_by_default() -> None:
    log: list[str] = []

    async def tail() -> None:
        log.append("tail")

    with pytest.raises(RuntimeError, match="b failed"):
        async with compose_phases([_phase(log, "a"), _phase(log, "b", fail=True)], tail=(tail,)):
            pass
    # with_admin semantics: failed phase unwinds without tail
    assert log == ["start:a", "start:b"]


@pytest.mark.asyncio
async def test_startup_failure_runs_full_teardown_when_flagged() -> None:
    log: list[str] = []

    async def tail() -> None:
        log.append("tail")

    with pytest.raises(RuntimeError, match="b failed"):
        async with compose_phases(
            [_phase(log, "a"), _phase(log, "b", fail=True), _phase(log, "c")],
            tail=(tail,),
            teardown_on_startup_failure=True,
        ):
            pass
    # factory semantics: legacy finally ran every close even on failure
    assert log == ["start:a", "start:b", "stop:c", "stop:b", "stop:a", "tail"]
