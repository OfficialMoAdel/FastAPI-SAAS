#!/usr/bin/env python
"""Sample the app process's RSS and CPU for a run, and write the L-10 artefact.

WHY THIS FILE EXISTS
--------------------
The ``rss_cpu_series.json`` behind L-10 in RESULT-20261011-190914 was produced by
an ad-hoc command typed by hand, not by anything in the repository. ``grep`` for
its name found it only in the prose. That made the artefact irreproducible: no one
could re-derive the interval, the host identity, or whether tracemalloc was on,
and a measurement nobody can re-run is an assertion, not a measurement.

So the producer is committed, and it takes its configuration from arguments rather
than from whatever the operator felt like typing.

WHAT IT RECORDS, AND WHY EACH FIELD
-----------------------------------
RSS is the concluding instrument; ``tracemalloc`` only attributes. Both are
exported side by side and their disagreement is reported rather than averaged into
a single "memory" figure - averaging them would erase the only thing worth knowing.

The workload is recorded because "RSS was flat" is meaningless without it: flat
across zero requests is a different claim from flat across 61,000. A window that
covered no traffic has not been shown to not leak.

TWO WINDOWS, ONE SERIES
-----------------------
``--window-seconds`` is the RSS window and runs with tracemalloc OFF, because
tracemalloc traces every Python allocation and would perturb the very series it
is meant to cross-check.

``--tracemalloc-window-seconds`` is a separate, shorter companion window with
tracemalloc ON, recorded into ``companion`` so the divergence can be reported
against a number actually measured. Running tracemalloc throughout would have
been the simpler choice and the wrong one: it would have contaminated a 30-minute
RSS series to produce a comparison against that contaminated series.

The companion window is a SECOND process-level observation and its divergence is
reported as indicative, never as the conclusion about the main window.

USAGE
-----
    python scripts/rss_window.py --output reports/rss_cpu_series.json \\
        --window-seconds 2100 --request-count 61020 --concurrency 50
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import tracemalloc
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.core.monitoring.process_series import (  # noqa: E402
    MINIMUM_WINDOW_SECONDS,
    ProcessSeriesSampler,
    assert_tracemalloc_is_attribution_only,
    assert_window_long_enough,
)


def _host_identity() -> str:
    """Identify the host, from the environment where it is already declared.

    Falls back to the socket hostname rather than refusing to run: an unrecorded
    host is recorded as such, which is a weaker claim, and ``export()`` exposes
    that weakness as ``host_recorded: false`` instead of hiding it.
    """
    for var in ("PERF_APP_HOST_REPORT", "PERF_HOST_IDENTITY"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return socket.gethostname()


def _sample_window(
    sampler: ProcessSeriesSampler, *, seconds: float, interval: float, trace: bool
) -> None:
    """Sample for ``seconds``, optionally under tracemalloc."""
    if trace:
        tracemalloc.start()
    sampler.start()
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            sampler.sample()
            time.sleep(interval)
    finally:
        sampler.stop()
        if trace:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            sampler.record_tracemalloc_peak(peak)

def _process_name(pid: int) -> str:
    """Best-effort process name for ``pid``, from /proc where available."""
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return f"pid-{pid}"



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", required=True, help="where to write the artefact")
    parser.add_argument(
        "--window-seconds",
        type=float,
        default=float(MINIMUM_WINDOW_SECONDS) + 300.0,
        help="RSS window length; must reach the minimum",
    )
    parser.add_argument(
        "--tracemalloc-window-seconds",
        type=float,
        default=60.0,
        help="companion window with tracemalloc on, for the divergence",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="sample interval")
    parser.add_argument("--request-count", type=int, default=None,
                        help="requests the window covered")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="concurrency the window ran at")
    parser.add_argument(
        "--pid",
        type=int,
        default=None,
        help="PID of the process to measure. OMIT ONLY when the server itself is "
             "running this sampler: a standalone sampler otherwise measures its "
             "own interpreter and reports it as a server-side window, which is "
             "how a 35-minute run produced a flat curve for a sleeping process.",
    )
    args = parser.parse_args()

    sampler = ProcessSeriesSampler(enabled=True, interval=args.interval)
    sampler.record_host_identity(_host_identity())
    # Name the target before sampling, not after: an unnamed run that reports
    # "no leak" is the most dangerous artefact this script could emit, because a
    # sleeping interpreter is as flat as a healthy server.
    if args.pid is not None:
        target_name = _process_name(args.pid)
        sampler.record_target(args.pid, target_name)
        print(f"target: pid {args.pid} ({target_name})", flush=True)
    else:
        target_name = "self"
        print(
            "WARNING: no --pid given, so this measures THIS process. That is only "
            "a server-side measurement if the server is running this script.",
            flush=True,
        )

    print(
        f"sampling {args.window_seconds:.0f}s RSS window "
        f"(tracemalloc off), then a {args.tracemalloc_window_seconds:.0f}s companion",
        flush=True,
    )
    _sample_window(
        sampler,
        seconds=args.window_seconds,
        interval=args.interval,
        trace=False,
    )

    companion = ProcessSeriesSampler(enabled=True, interval=args.interval)
    companion.record_host_identity(_host_identity())
    if args.pid is not None:
        companion.record_target(args.pid, target_name)
    print("companion window: tracemalloc on", flush=True)
    _sample_window(
        companion,
        seconds=args.tracemalloc_window_seconds,
        interval=args.interval,
        trace=True,
    )

    # The conclusion is the RSS series. The workload is attached to the window
    # that produced it, never to whichever window happened to finish last.
    sampler.record_workload(
        request_count=args.request_count or 0,
        concurrency=args.concurrency or 0,
    )
    # Carry the companion's tracemalloc attribution onto the main export so the
    # divergence block has a number, and say where it came from.
    export = sampler.export()
    companion_export = companion.export()
    # Set the sampler's OWN field, not just the export dict. _tracemalloc_divergence
    # reads self._tracemalloc_peak_bytes, so writing only the export key left the
    # sampler believing tracemalloc had never run and the divergence came out
    # "not-available" while the companion's number sat right there in the artefact.
    sampler.record_tracemalloc_peak(companion_export["tracemalloc_peak_bytes"] or 0)
    export["tracemalloc_peak_bytes"] = companion_export["tracemalloc_peak_bytes"]
    export["tracemalloc_divergence"] = sampler._tracemalloc_divergence()  # noqa: SLF001
    export["tracemalloc_divergence"]["companion"] = {
        "window_seconds": companion_export["window_seconds"],
        "rss_growth_bytes": companion_export["rss_growth_bytes"],
        "tracemalloc_peak_bytes": companion_export["tracemalloc_peak_bytes"],
        "role": "attribution-only; indicative, not the conclusion about the "
                "main window, which ran without tracemalloc so the RSS series "
                "stayed unperturbed",
    }
    export["companion_window_seconds"] = args.tracemalloc_window_seconds

    assert_tracemalloc_is_attribution_only(export)
    assert_window_long_enough(export["window_seconds"])

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(export, indent=2, default=str), encoding="utf-8")

    divergence = export["tracemalloc_divergence"]
    print(f"wrote {output}")
    target = export["target"]
    print(
        f"  target: {target['name']} (pid {target['pid']}, "
        f"self-sampled={target['is_self']})"
    )
    print(
        f"  window {export['window_seconds']:.1f}s "
        f"({export['sample_count']} samples, meets_minimum="
        f"{export['window_meets_minimum']})"
    )
    print(
        f"  RSS {export['rss_first_bytes']} -> {export['rss_last_bytes']} "
        f"({export['rss_growth_bytes']:+.0f} bytes)"
    )
    print(
        f"  workload: {export['workload']['request_count']} requests at "
        f"{export['workload']['concurrency']} concurrency"
    )
    print(f"  tracemalloc divergence: {divergence['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
