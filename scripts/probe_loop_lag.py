"""100Hz event-loop-lag probe for the microbenchmark arm (u71j).

Samples asyncio event-loop lag: await a sleep cadence and record
actual-vs-commanded wakeup error per tick. Emits JSON stats to stdout or a
file. Stdlib only (asyncio/time/json/argparse/statistics/sys) so it runs
anywhere, including the CI microbenchmark job with no app imports.

The ARM contract (not this script's opinion): the microbenchmark job runs
this probe in the background during pytest; the artefact records the lag
stats alongside a disclaimer that shared-runner numbers satisfy no Gate A
checkbox. Thresholds below are validation plumbing with documented,
generous bounds — they detect a wedged loop, not a slow machine.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import statistics
import sys
import time

DEFAULT_RATE_HZ = 100.0
DEFAULT_DURATION_S = 5.0
# A tick whose error exceeds one full interval missed its slot entirely.
# Counts as an overrun (scheduling stall), reported not asserted.
OVERRUN_MULTIPLE = 1.0


#: Set by the SIGTERM handler below; sample_lag() checks it every tick and
#: stops early. Module-level (not a parameter) so the early-exit path is
#: unit-testable without delivering a real signal (unreliable on Windows,
#: where os.kill(SIGTERM) terminates instead of dispatching).
_term_state: dict[str, bool] = {"terminated": False}


def _install_term_handler() -> bool:
    """Write partial stats on SIGTERM: CI kills the background probe when
    pytest ends, and a killed probe with no output would void the lag
    validation the microbenchmark job exists to carry.

    Returns True when the handler is installed, False on non-POSIX dispatch
    (where the probe runs to --duration instead)."""

    def _on_term(signum: int, frame: object) -> None:
        _term_state["terminated"] = True

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except (OSError, ValueError):
        return False
    return True


async def sample_lag(rate_hz: float, duration_s: float) -> tuple[list[float], float, float]:
    """Per-tick wakeup errors in seconds (actual minus commanded).

    Positive error = the loop woke us late (lag). Errors near zero at idle;
    large positive spikes under load. Uses the running loop's clock so the
    measurement is in loop time, not wall time. NOTE on absolute error size:
    on Windows, asyncio.sleep quantizes to the ~15.6ms system timer, so
    mean_abs_error sits near ~8ms at idle there — that is sleep granularity,
    not loop lag. The probe's contract is RATE agreement (measured vs
    commanded), which holds regardless; error SIZE is only comparable across
    runs on the same platform (Linux CI). Also returns the first and
    last tick timestamps (loop time) so the measured rate is computed from
    real elapsed time, never from error values.
    """
    if rate_hz <= 0:
        raise ValueError("rate_hz must be positive")
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    loop = asyncio.get_running_loop()
    interval = 1.0 / rate_hz
    errors: list[float] = []
    actuals: list[float] = []
    start = loop.time()
    tick = 0
    _term_state["terminated"] = False
    while True:
        if _term_state["terminated"]:
            break
        commanded = start + tick * interval
        delay = commanded - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        actual = loop.time()
        errors.append(actual - commanded)
        actuals.append(actual)
        tick += 1
        if actual - start >= duration_s:
            break
    return errors, actuals[0], actuals[-1]


def summarize(
    errors: list[float], rate_hz: float, t_first: float = 0.0, t_last: float = 0.0
) -> dict[str, float]:
    """Aggregate tick errors into a JSON-serializable stats mapping."""
    if not errors:
        raise ValueError("no samples to summarize")
    abs_err = [abs(e) for e in errors]
    interval = 1.0 / rate_hz
    overruns = sum(1 for e in abs_err if e > interval * OVERRUN_MULTIPLE)
    span = t_last - t_first if t_last > t_first else 0.0
    measured_rate = (len(errors) - 1) / span if span > 0 else 0.0
    return {
        "commanded_rate_hz": rate_hz,
        "samples": float(len(errors)),
        "measured_rate_hz": measured_rate,
        "mean_abs_error_us": statistics.mean(abs_err) * 1e6,
        "max_abs_error_us": max(abs_err) * 1e6,
        "overruns": float(overruns),
    }


def _verify_once(rate_hz: float, duration_s: float, tol: float) -> dict[str, float]:
    """Run one self-check rate; raise SystemExit with detail on failure."""
    errors, t_first, t_last = asyncio.run(sample_lag(rate_hz, duration_s))
    stats = summarize(errors, rate_hz, t_first, t_last)
    measured = stats["measured_rate_hz"]
    rel = abs(measured - rate_hz) / rate_hz
    if rel > tol:
        raise SystemExit(f"VERIFY FAIL: rate {rate_hz}Hz measured {measured:.2f}Hz (rel err {rel:.2%} > {tol:.0%})")
    return stats


def verify() -> int:
    """Self-check: commanded-vs-measured rate agreement at 3 cadences.

    Generous tolerances on purpose — this proves the probe measures the
    loop instead of itself, not that the machine is fast. A wedged loop
    (errors of whole seconds) still fails loudly.
    """
    for rate, duration, tol in ((10.0, 1.0, 0.20), (100.0, 1.0, 0.10), (1000.0, 0.5, 0.10)):
        stats = _verify_once(rate, duration, tol)
        print(
            f"verify rate={rate:.0f}Hz measured={stats['measured_rate_hz']:.2f}Hz "
            f"mean_abs_err={stats['mean_abs_error_us']:.1f}us "
            f"max_abs_err={stats['max_abs_error_us']:.1f}us"
        )
    # Reload check: stats must round-trip through JSON losslessly.
    stats = _verify_once(100.0, 0.5, 0.10)
    text = json.dumps(stats, sort_keys=True)
    assert json.loads(text) == stats, "stats JSON round-trip must be exact"
    print("verify JSON round-trip: exact")
    print("VERIFY OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sample asyncio event-loop lag.")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE_HZ, help="commanded tick rate in Hz")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_S, help="sampling window in seconds")
    parser.add_argument("--output", type=str, default="", help="write stats JSON to this path (default: stdout)")
    parser.add_argument("--verify", action="store_true", help="run the self-check and exit")
    args = parser.parse_args(argv)
    if args.verify:
        return verify()
    _install_term_handler()
    started_wall = time.time()
    errors, t_first, t_last = asyncio.run(sample_lag(args.rate, args.duration))
    stats = summarize(errors, args.rate, t_first, t_last)
    stats["wall_s"] = time.time() - started_wall
    stats["partial"] = bool(_term_state["terminated"])
    text = json.dumps(stats, indent=2, sort_keys=True) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
