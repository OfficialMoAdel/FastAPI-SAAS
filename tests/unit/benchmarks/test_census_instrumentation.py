"""H-8: the census instrument must count what it claims to count, and not charge the app for counting it.

Two defects, both confirmed against live Redis before any code changed:

1. PIPELINED COMMANDS WERE INVISIBLE. ``census.hook`` patched
   ``Redis.execute_command``. redis-py's ``Pipeline.execute_command`` only
   appends to ``command_stack`` (:1381); the flush packs the whole stack and
   sends it as ONE ``connection.send_packed_command`` write, so nothing on that
   path ever reached ``Redis.execute_command`` (:636). Measured: 5 x RPUSH plus
   5 x LRANGE in one pipeline counted 0; the same ten issued individually
   counted 10. ``census.txt`` shows no ``PIPE:`` entries on the refresh path
   while ``driver_count_io.out`` proves that path emits them, because
   driver_count_io hooked ``Pipeline.execute`` and census.py did not.

2. THE INSTRUMENTATION WAS INSIDE THE TIMED WINDOW. ``t0`` is taken in the
   request loop, and the hook runs because the request calls Redis, so
   ``traceback.extract_stack()`` - measured at 142 us/call on this host -
   executed between ``t0`` and ``elapsed``. Worse, the value it produced was
   already first-wins (``dict.setdefault``), so every repeat of an
   already-attributed command re-extracted a stack and threw it away.

This file pins the repairs. It deliberately does NOT assert the audit's inferred
~0.3 ms/command: that figure is an ``[Inference]``, and this test's job is to
separate instrumentation cost from measured wait, not to promote a guess to a
measurement.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio.client import Pipeline as AsyncPipeline
from redis.asyncio.client import Redis as AsyncRedis
from sqlalchemy import event
from sqlalchemy.engine import Engine

from src.benchmarks import census

REPO_ROOT = Path(__file__).resolve().parents[3]

PIPELINE_FIXTURE_COMMANDS = ("RPUSH", "LRANGE", "ZADD", "DEL", "RPUSH")


@pytest.fixture(autouse=True)
def _no_leaked_hooks() -> Iterator[None]:
    """Every test starts and ends with the census hooks installed but inactive.

    ``hook``/``unhook`` patch redis-py and SQLAlchemy classes. A leak here would
    tax every later test in the session, and a *missing* unhook is exactly that
    failure, so the fixture asserts the clean state rather than assuming it.
    """
    census.unhook()
    census.reset()
    try:
        yield
    finally:
        census.unhook()
        census.reset()


class _FakePipeline:
    """A pipeline that has staged commands and cannot execute.

    Only ``command_stack`` is exercised, because that is the entire contract:
    redis-py hands the census a pipeline whose ``command_stack`` holds
    ``(args, options)`` pairs and expects nothing else before it delegates to the
    real ``execute``.
    """

    def __init__(self, commands: tuple[str, ...]) -> None:
        self.command_stack: list[tuple[tuple[str, ...], dict[str, Any]]] = [((cmd,), {}) for cmd in commands]


# --------------------------------------------------------------------------
# (1) PIPELINE COUNTING
# --------------------------------------------------------------------------


def test_a_pipeline_of_n_commands_is_counted_as_exactly_n() -> None:
    census.reset()
    staged = _FakePipeline(PIPELINE_FIXTURE_COMMANDS)

    recorded = census._record_stacked_commands(staged)

    assert recorded == len(PIPELINE_FIXTURE_COMMANDS)
    assert sum(census.red.values()) == len(PIPELINE_FIXTURE_COMMANDS)
    assert census.red["RPUSH"] == 2
    assert census.red["LRANGE"] == 1
    assert census.red["ZADD"] == 1
    assert census.red["DEL"] == 1


def test_the_pipeline_commands_are_attributed_separately_from_direct_ones() -> None:
    """``pipelined`` must not be folded into ``red``, or the two stop being answers to different questions."""
    census.reset()
    census._record_command(("GET", "k"))
    census._record_stacked_commands(_FakePipeline(PIPELINE_FIXTURE_COMMANDS))

    assert census.red["GET"] == 1
    assert census.red["RPUSH"] == 2
    assert sum(census.pipelined.values()) == len(PIPELINE_FIXTURE_COMMANDS)
    assert census.pipelined["GET"] == 0
    assert census.pipelined["RPUSH"] == 2


def test_an_empty_pipeline_adds_nothing() -> None:
    census.reset()
    assert census._record_stacked_commands(_FakePipeline(())) == 0
    assert sum(census.red.values()) == 0


def test_a_pipeline_without_a_command_stack_is_not_an_error() -> None:
    """redis-py clears ``command_stack`` on reset, and the shim runs on every execute."""
    census.reset()
    assert census._record_stacked_commands(object()) == 0
    assert sum(census.red.values()) == 0


def test_the_hook_patches_pipeline_execute_and_the_redis_command_path() -> None:
    """Both seams must be patched, or one of the two defects returns.

    Patching only ``Redis.execute_command`` is the pre-fix state and is exactly
    why pipelined commands counted zero.
    """
    census.hook()
    assert AsyncPipeline.execute is not census._original_pipeline_execute
    assert AsyncRedis.execute_command is not census._original_execute_command


def test_unhook_restores_both_methods() -> None:
    census.hook()
    census.unhook()
    assert AsyncPipeline.execute is census._original_pipeline_execute
    assert AsyncRedis.execute_command is census._original_execute_command


def test_hooking_twice_does_not_double_count() -> None:
    """SQLAlchemy would raise on a duplicate listener, and the reader must not have to remember which."""
    census.hook()
    census.hook()
    census.unhook()
    census.hook()
    census.unhook()
    assert AsyncPipeline.execute is census._original_pipeline_execute


@pytest.mark.asyncio
async def test_the_pipeline_shim_records_before_delegating() -> None:
    """The count must be taken from ``command_stack``, which the real execute then drains.

    If the shim delegated first, the stack would already be empty and the
    census would report zero - a defect that looks exactly like the one this
    test exists to catch.
    """
    census.reset()
    observed: dict[str, int] = {}

    async def fake_original(self: Any, raise_on_error: bool = True) -> str:
        observed["staged_at_delegate_time"] = len(self.command_stack)
        return "delegated"

    class _Pipeline(_FakePipeline):
        async def execute(self, raise_on_error: bool = True) -> str:  # type: ignore[override]
            return await fake_original(self, raise_on_error=raise_on_error)

    shim = census._pipeline_execute
    try:
        census._original_pipeline_execute = fake_original  # type: ignore[assignment]
        assert await shim(_Pipeline(PIPELINE_FIXTURE_COMMANDS)) == "delegated"  # type: ignore[arg-type]
    finally:
        census._original_pipeline_execute = AsyncPipeline.execute  # type: ignore[assignment]
        assert asyncio_is_not_needed_here(shim)

    assert observed["staged_at_delegate_time"] == len(PIPELINE_FIXTURE_COMMANDS)
    assert sum(census.red.values()) == len(PIPELINE_FIXTURE_COMMANDS)


def asyncio_is_not_needed_here(shim: object) -> bool:
    """The shim under test is a coroutine function; guard that it stayed one."""
    import asyncio  # noqa: PLC0415

    return asyncio.iscoroutinefunction(shim)


# --------------------------------------------------------------------------
# (2) THE INSTRUMENTATION COST IS NOT FOLDED INTO THE MEASUREMENT
# --------------------------------------------------------------------------


def test_a_repeat_command_does_not_re_extract_its_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-command cost must amortise to zero after the first sighting.

    This is the load-bearing half of repair (2). ``stacks.setdefault`` was
    first-wins, so re-extracting on every repeat produced a value that was
    discarded - the census paid 142 us per Redis command for a dictionary it had
    already filled.
    """
    census.reset()
    calls = 0
    real_extract = census.traceback.extract_stack

    def counting_extract(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return real_extract(*args, **kwargs)

    monkeypatch.setattr(census.traceback, "extract_stack", counting_extract)

    for _ in range(50):
        census._record_command(("GET", "k"))
    assert calls == 1
    assert census.red["GET"] == 50

    census._record_command(("DEL", "k2"))
    assert calls == 2


def test_attribution_still_records_the_first_sighting_site() -> None:
    """Reducing the cost must not lose the attribution the census exists to produce."""
    census.reset()
    census._record_command(("GET", "k"))
    site = census.stacks["GET"]
    assert ":" in site
    assert "site-packages" not in site


def test_attribution_can_be_disabled_and_the_setting_is_restored() -> None:
    census.reset()
    previous = census.set_stack_attribution(False)
    try:
        census._record_command(("GET", "k"))
        assert census.red["GET"] == 1
        assert "GET" not in census.stacks
    finally:
        census.set_stack_attribution(previous)
    census._record_command(("GET", "k"))
    assert "GET" in census.stacks


@pytest.mark.asyncio
async def test_the_overhead_is_reported_separately_and_never_applied() -> None:
    """Two measurements, returned side by side. The delta is a label, not an edit.

    The census's job is to report what the instrumented application costs. If the
    attribution cost were subtracted from that figure, the number would be a
    statement about the harness rather than about the app, and the two would be
    indistinguishable in the output.
    """
    census.reset()

    async def call() -> None:
        return None

    overhead = await census.measure_attribution_overhead(call, iterations=3, warmup=1, repetitions=2)

    assert overhead.with_attribution.best_us >= 0.0
    assert overhead.without_attribution.best_us >= 0.0
    assert overhead.overhead_us == pytest.approx(
        overhead.with_attribution.best_us - overhead.without_attribution.best_us
    )
    assert overhead.with_attribution is not overhead.without_attribution
    assert overhead.with_attribution.name != overhead.without_attribution.name


@pytest.mark.asyncio
async def test_the_overhead_probe_measures_amortised_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both sides must clear ``stacks`` first, or the attributed side measures a warm cache.

    With attribution on, a pre-populated ``stacks`` means no ``extract_stack`` at
    all, so the "with" arm would be the *cheaper* one and the reported overhead
    would be negative. A negative overhead is a sign the measurement is wrong,
    not that the harness is free.

    The call here issues no Redis command, so this asserts the clearing directly
    rather than inferring it from an extraction count that would be zero either
    way.
    """
    census.reset()
    census.stacks["GET"] = "seeded.py:1"

    real_extract = census.traceback.extract_stack
    extractions: list[str] = []

    def counting_extract(*args: Any, **kwargs: Any) -> Any:
        extractions.append("stack")
        return real_extract(*args, **kwargs)

    monkeypatch.setattr(census.traceback, "extract_stack", counting_extract)

    async def call() -> None:
        return None

    await census.measure_attribution_overhead(call, iterations=2, warmup=1, repetitions=2)

    assert extractions == [], "the probe seeded stacks and something extracted anyway"
    assert "GET" not in census.stacks, "the probe did not clear stacks, so the attributed arm measured a warm cache"


def test_the_overhead_report_names_the_number_and_says_it_is_not_folded(capsys: pytest.CaptureFixture[str]) -> None:
    from src.benchmarks import protocol

    census.reset()

    def _result(name: str, minima: tuple[float, ...], medians: tuple[float, ...]) -> protocol.MicroResult:
        return protocol._build(name, "us", minima, medians, iterations=1, warmup=1, repetitions=2)

    overhead = census.AttributionOverhead(
        with_attribution=_result("with", (10.0, 11.0), (10.5,)),
        without_attribution=_result("without", (4.0, 5.0), (4.5,)),
    )
    assert overhead.overhead_us == pytest.approx(6.0)

    census._report_overhead(overhead, "GET /users/me")
    out = capsys.readouterr().out

    assert "INSTRUMENTATION OVERHEAD" in out
    assert "6.00us" in out
    assert "REPORTED SEPARATELY" in out
    assert "not added to or subtracted" in out


def test_the_census_reports_commands_per_request_not_round_trips() -> None:
    """The old label was wrong: a pipeline is one wire write carrying N commands.

    Scans for a *printed* label rather than any string containing ``Redis/``,
    because the module's own docstrings discuss the old label while explaining
    why it was wrong - and a test that forbids the explanation is a test that
    forbids the fix.
    """
    source = ast.parse(Path(census.__file__).read_text(encoding="utf-8"))
    printed: list[str] = []
    for node in ast.walk(source):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "print":
            continue
        for arg in node.args:
            # The label lives inside an f-string, so its text is a Constant
            # nested in a JoinedStr rather than an argument in its own right.
            printed.extend(
                part.value for part in ast.walk(arg) if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
    labels = [line for line in printed if "SQL/req" in line or "commands/req" in line or "pipelined" in line]
    assert labels == ["  SQL/req=", "  Redis commands/req=", "  of which pipelined: "], f"unexpected labels: {labels}"
    assert not any("Redis/req" in line for line in printed), "a command count is still labelled as a round trip"


def test_reset_clears_every_counter() -> None:
    census._record_command(("GET", "k"))
    census._record_stacked_commands(_FakePipeline(PIPELINE_FIXTURE_COMMANDS))
    census.sql["SELECT 1"] += 1

    census.reset()

    assert sum(census.red.values()) == 0
    assert sum(census.wire.values()) == 0
    assert sum(census.pipelined.values()) == 0
    assert census.stacks == {}
    assert sum(census.sql.values()) == 0


def test_the_sql_listener_is_attributed_once_per_statement_too() -> None:
    """Same fix, other seam: the listener re-extracted a stack per statement."""
    census.reset()
    census._sql_before_cursor_execute(None, None, "SELECT  1", None, None, None)
    assert census.sql["SELECT 1"] == 1
    assert census.stacks
    census.reset()
    census._sql_before_cursor_execute(None, None, "SELECT  1", None, None, None)
    assert census.sql["SELECT 1"] == 1


def test_the_hooks_are_wired_to_the_live_engine_and_client_classes() -> None:
    """Guard that ``hook`` targets the classes the app actually uses."""
    census.hook()
    assert census._sql_listener is not None
    assert event.contains(Engine, "before_cursor_execute", census._sql_listener), (
        "the SQL listener is registered on the Engine class, so the census would "
        "silently record no SQL if it were attached to anything narrower"
    )
    census.unhook()
    assert not event.contains(Engine, "before_cursor_execute", census._sql_listener)
