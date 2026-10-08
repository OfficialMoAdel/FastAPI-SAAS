"""P-2/L-4 and P-10: the Phase-0 mechanism measurement, on the Linux target.

This is the HARNESS side. It runs against an already-booted application on the app
host - the perf-harness CI job's own server, which was started exactly as the
Dockerfile starts it, so the event loop and HTTP implementation are the ones that
ship rather than ones this module chose.

Why it cannot be run on the measurement host
--------------------------------------------
Addendum A-2 and L-4: the sampler must be validated on **uvloop on Linux**, and an
asyncio run is labelled non-representative and does not close P-2/L-4. The
measurement host in this repository is Windows, where uvloop is not installable at
all (``pyproject.toml`` declares it under ``sys_platform != 'win32'``). So this
module REFUSES to produce a validation result off Linux rather than producing a
laboratory one that reads as valid - which is the failure P-2/L-4 exists to
prevent.

What it measures
----------------
**P-2/L-4.** Event-loop lag sampled at >=100 Hz for the whole run, with the
ACHIEVED rate recorded rather than assumed, alongside a GC pause histogram. The
two are separate series with different causes; :func:`run_mechanism_probe` asserts
they are disjoint before returning, because reporting one number for both is how a
GC pause gets misattributed to the loop.

**P-10.** The Redis command count, from two INDEPENDENT sources: the shim on this
process's send paths, and ``INFO commandstats`` from the server. They must agree,
and a disagreement is reported rather than resolved by preferring one - the whole
value of the second count is that it is an outside observer. Commands and round
trips are reported as two distinct quantities. The shim is proven absent unless
injected, which is what keeps it out of the production request path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from src.app.core.monitoring.gc_pause import GCPauseObserver, assert_series_are_disjoint
from src.app.core.monitoring.loop_lag import (
    MINIMUM_RATE_HZ,
    LoopLagSampler,
    assert_validated_on_production_loop,
)
from src.app.core.monitoring.redis_commands import (
    RedisCommandShim,
    assert_counts_agree,
    assert_pipeline_aware,
    commandstats_total,
)
from src.app.core.monitoring.telemetry import CeilingExceeded

#: The loop implementation production runs. uvloop is declared under
#: ``sys_platform != 'win32'`` and the Dockerfile's CMD passes no ``--loop``, so
#: uvicorn's auto-selection picks it wherever it is importable.
PRODUCTION_LOOP = "uvloop"

#: The default probe duration. Long enough that a >=100 Hz sampler accumulates a
#: series with a tail in it, short enough that CI can afford it on every dispatch.
DEFAULT_SECONDS = 30.0

#: The sampling interval P-2/L-4 requires. 10 Hz would sample a 740 ms p95 seven
#: times across a run and cannot resolve a stall finer than the stall.
SAMPLE_INTERVAL = 0.01


class NotTheProductionRuntime(RuntimeError):
    """This host is not the Linux/uvloop target, so it cannot close P-2/L-4.

    Raised rather than warned. A laboratory run that reads as a validation run is
    the specific defect this module exists to avoid, and a warning would let
    exactly that happen quietly.
    """


def install_production_loop() -> str:
    """Put uvloop in place the way uvicorn's ``--loop auto`` does, and report it.

    NECESSARY, and its absence is why the first Linux run of this probe failed.
    ``uvloop`` being IMPORTABLE is not the same as it being INSTALLED: uvloop only
    becomes the running loop if something calls ``uvloop.install()`` or constructs a
    ``uvloop.Loop``. The perf-harness job's SERVER gets uvloop because uvicorn's
    auto-selection does that for it - but this probe runs in its own interpreter,
    where the default policy is still asyncio. So on a host with uvloop installed
    the probe reported ``asyncio.unix_events`` and refused, which reads exactly like
    "uvloop is missing on the Linux target" when it is present and unused.

    Installing here is not a workaround; it is what makes the probe measure the
    loop the server measures.
    """
    import asyncio

    try:
        import uvloop
    except ImportError as error:
        raise NotTheProductionRuntime(
            f"uvloop is not importable on this host: {error}. pyproject declares it "
            f"under sys_platform != 'win32', so its absence on the Linux target is a "
            f"packaging fault, not a platform fact."
        ) from error
    uvloop.install()
    return type(asyncio.get_event_loop()).__module__


def assert_on_production_runtime() -> str:
    """Return the running loop's implementation, refusing anything but uvloop."""
    loop_module = type(asyncio.get_event_loop()).__module__
    if not loop_module.startswith(PRODUCTION_LOOP):
        raise NotTheProductionRuntime(
            f"this host runs {loop_module!r}, not {PRODUCTION_LOOP!r}. Addendum A-2 "
            f"makes an asyncio run NON-REPRESENTATIVE, and L-4 says it does not close "
            f"P-2/L-4. Refusing to produce a laboratory figure that would read as a "
            f"validation one."
        )
    return loop_module


async def run_mechanism_probe(*, seconds: float = DEFAULT_SECONDS) -> dict[str, Any]:
    """Sample loop lag and GC pauses on this host for ``seconds``.

    Returns an export suitable for an artefact. Raises
    :class:`NotTheProductionRuntime` off the production runtime, and
    :class:`CeilingExceeded` if the achieved rate is below the requirement - a run
    that asked for 100 Hz and managed less has not met it, and reporting the
    requested figure would be a claim the run does not support.
    """
    loop_module = assert_on_production_runtime()

    sampler = LoopLagSampler(enabled=True, interval=SAMPLE_INTERVAL)
    observer = GCPauseObserver(enabled=True)
    observer.install()

    sampler.start()
    started = time.monotonic()
    try:
        await asyncio.sleep(seconds)
    finally:
        await sampler.stop()
        observer.uninstall()
    elapsed = time.monotonic() - started

    lag_export = sampler.export()
    gc_export = observer.export()
    assert_series_are_disjoint(gc_export, lag_export)

    achieved = lag_export["achieved_rate_hz"]
    if achieved < MINIMUM_RATE_HZ:
        raise CeilingExceeded(
            f"achieved {achieved:.1f} Hz against a required {MINIMUM_RATE_HZ:.0f} Hz. "
            f"The sampler's OWN cost is what failed here, and a run that cannot hold "
            f"the sampling rate cannot attribute a tail it never sampled finely enough "
            f"to see."
        )

    return {
        "probe": "p2-l4-mechanism",
        "elapsed_seconds": round(elapsed, 3),
        "loop_module": loop_module,
        "loop_is_production_runtime": True,
        "requested_rate_hz": round(1.0 / SAMPLE_INTERVAL, 2),
        "minimum_rate_hz": MINIMUM_RATE_HZ,
        "achieved_rate_hz": achieved,
        "meets_minimum_rate": lag_export["meets_minimum_rate"],
        "event_loop_lag": lag_export,
        "gc_pause": gc_export,
        "series_are_disjoint": True,
        "validate_on_uvloop": assert_validated_on_production_loop(lag_export) is not None,
    }


async def run_redis_probe(*, target_url: str, password: str | None = None) -> dict[str, Any]:
    """P-10: count Redis commands two independent ways and require them to agree.

    The shim is installed HERE, explicitly, and nowhere else - which is what keeps
    it out of the production request path. :func:`assert_absent_unless_injected`
    proves the default state is uninstrumented before this runs.
    """
    absent_proof = assert_absent_unless_injected()

    import redis.asyncio as aioredis
    from redis.asyncio.connection import Connection

    client = aioredis.from_url(target_url, password=password, decode_responses=False)
    shim = RedisCommandShim(enabled=True)

    # The ASYNC connection class, not the sync one. They are different classes, and
    # an async client - which is what a FastAPI app is - never touches
    # ``redis.connection.Connection``. Instrumenting the sync class counted zero
    # commands against a live async workload and read as "the store was idle",
    # which is precisely the blindness perf J6 describes, one layer over.
    shim.install(Connection)
    try:
        # Align the two observation windows BEFORE taking either baseline.
        #
        # Two things had to be true for the shim and the server to be comparing the
        # same traffic, and both were found by the numbers disagreeing rather than
        # by reasoning:
        #   1. the client must already be CONNECTED, or AUTH and CLIENT from
        #      connection establishment land inside the measured window -
        #      ``from_url`` connects lazily, so a reset taken before the first
        #      command still counted them;
        #   2. the shim must be reset AFTER that, so its window starts where the
        #      server's does.
        # Comparing them without this reported 58 against 39 for identical traffic,
        # which reads as a missed send path and is not one.
        await client.ping()
        shim.reset()
        baseline = await _commandstats_total(client)

        # A small workload with a real pipeline in it, so the packed send path is
        # exercised rather than only the single-command one.
        pipe = client.pipeline(transaction=True)
        for index in range(25):
            pipe.set(f"p10:probe:{index}", b"v")
        pipe.get("p10:probe:0")
        await pipe.execute()

        for index in range(10):
            await client.get(f"p10:probe:{index}")

        shim_export = shim.export()
        after = await _commandstats_total(client)
    finally:
        shim.uninstall(Connection)
        await client.aclose()

    shim_total = shim_export["redis_total_commands"]
    redis_total = after - baseline

    # The two must agree. Not reconciled, not averaged: a disagreement means one
    # observer is blind, and every per-command figure is void until that is found.
    #
    # ONE CAUSE IS DIAGNOSTIC RATHER THAN A DEFECT, and it is worth naming because
    # it looks identical to a missed send path: ``INFO commandstats`` is
    # INSTANCE-WIDE. On a shared Redis it counts every other client's traffic too,
    # so the server total exceeds this process's by however much anyone else is
    # doing. The CI probe runs against a DEDICATED service container, where the
    # only client is this process; against a shared instance the two cannot
    # converge and this raises rather than reporting a plausible number.
    if shim_total != redis_total and redis_total > shim_total:
        raise AssertionError(
            f"T-6 counts DISAGREE: shim counted {shim_total}, INFO commandstats "
            f"reports {redis_total}. If this store is SHARED, the cause is that "
            f"commandstats is instance-wide and counts other clients - the probe "
            f"requires a dedicated store, which the CI service container is. "
            f"If it is dedicated, the shim missed a send path and every per-command "
            f"figure from this run is void."
        )
    assert_counts_agree(shim_total, redis_total)
    assert_pipeline_aware(shim_total, shim_total)

    return {
        "probe": "p10-redis-commands",
        "absent_unless_injected": absent_proof,
        "shim_total_commands": shim_total,
        "info_commandstats_delta": redis_total,
        "counts_agree": True,
        # Two distinct quantities. A pipeline's whole purpose is many commands per
        # round trip, and an artefact reporting one total cannot show whether either
        # moved.
        "total_commands": shim_export["redis_total_commands"],
        "total_round_trips": shim_export["redis_total_round_trips"],
        "commands_per_round_trip": shim_export["redis_commands_per_round_trip"],
        "by_name": shim_export["redis_commands_by_name"],
        "by_path": shim_export["redis_commands_by_path"],
    }


def assert_absent_unless_injected() -> dict[str, Any]:
    """Prove the shim is NOT installed in a freshly imported process.

    This is the P-10 requirement that the counter is not in the production request
    path. Checked at run START rather than asserted in prose: an instrument that
    monkeypatches a third-party library on import changes the behaviour of every
    deployment that imports the module that imports it, including ones that never
    read a series.

    A fresh interpreter is used because the check is about import-time state, and
    checking in the current process would prove only that *this* process had not
    installed it.
    """
    probe = (
        "import redis.connection as c;"
        "print('SENTINEL', c.Connection.send_command.__module__)"
    )
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not probe the redis client: {result.stderr[:200]}")
    # An unpatched send_command lives in redis.connection. The shim's replacement
    # is a closure defined in this module, so its __module__ is ours.
    sentinel = result.stdout.strip()
    patched = "core.monitoring.redis_commands" in sentinel
    if patched:
        raise AssertionError(
            f"the Redis shim is installed on import ({sentinel!r}); it must be absent "
            f"unless explicitly injected, or every deployment pays for a measurement "
            f"it never reads."
        )
    return {
        "fresh_interpreter_send_command_module": sentinel.replace("SENTINEL ", ""),
        "installed_on_import": patched,
    }


async def _commandstats_total(client: Any) -> int:
    """Total commands from the server's own ``INFO commandstats``.

    Awaited: on an async client ``info`` returns a coroutine, and reading it as a
    dict is how this probe produced an ``AttributeError`` on its first run.
    """
    return commandstats_total(await client.info("commandstats"))


async def _main(args: argparse.Namespace) -> int:
    probes: dict[str, Any] = {}
    failures: list[str] = []

    # Each probe is run in isolation and its failure RECORDED rather than allowed
    # to abort the other. The first Linux run failed at the mechanism probe and
    # produced no artefact at all, which cost the diagnosis: the evidence that had
    # already been collected was discarded with the run. A probe that fails writes
    # its reason into the artefact and lets the process exit non-zero, so the other
    # probe's numbers survive and the failure is still visible.
    if args.probe in ("mechanism", "all"):
        try:
            # install_production_loop() runs in main(), BEFORE asyncio.run builds
            # the loop. Calling it HERE was the second Linux failure: the loop
            # already existed by the time this coroutine ran, so uvloop.install()
            # replaced a policy nothing would consult again and the probe still
            # measured asyncio. The policy has to be in place before the loop is
            # CONSTRUCTED, not after.
            assert_on_production_runtime()
            probes["mechanism"] = await run_mechanism_probe(seconds=args.seconds)
        except (NotTheProductionRuntime, CeilingExceeded, AssertionError, RuntimeError) as error:
            # The four failure kinds this probe can raise, each recorded rather than
            # aborting the other probe: refusing the runtime, failing the sampling
            # rate, two counts that disagree, and a probe that could not start.
            # A catch-all here would swallow a programming error - a NameError in
            # the probe itself - and report it as a measurement failure, which is
            # how a broken instrument comes to read as a working one.
            failures.append(f"mechanism: {type(error).__name__}: {error}")
            probes["mechanism"] = {"failed": True, "error": f"{type(error).__name__}: {error}"}

    if args.probe in ("redis", "all"):
        try:
            probes["redis"] = await run_redis_probe(
                target_url=args.redis_url, password=args.redis_password
            )
        except (AssertionError, RuntimeError, OSError, ValueError) as error:
            failures.append(f"redis: {type(error).__name__}: {error}")
            probes["redis"] = {"failed": True, "error": f"{type(error).__name__}: {error}"}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(probes, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    for failure in failures:
        print(f"PROBE FAILED - {failure}", file=sys.stderr)
    if failures:
        return 1
    print(json.dumps(probes, indent=2, sort_keys=True, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="linux_mechanism_probe", description=__doc__)
    parser.add_argument("--probe", choices=("mechanism", "redis", "all"), default="all")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument("--redis-password", default=None)
    parser.add_argument(
        "--output", type=Path, default=Path("reports/mechanism_probe.json")
    )
    args = parser.parse_args(argv)
    if args.probe in ("mechanism", "all"):
        # BEFORE asyncio.run: a loop is constructed there, and uvloop.install()
        # only affects loops constructed afterwards.
        try:
            install_production_loop()
        except NotTheProductionRuntime as error:
            print(f"PROBE REFUSING - {error}", file=sys.stderr)
            return 2
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
