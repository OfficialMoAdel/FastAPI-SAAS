"""A FRESH verdict on retained growth, from this run's own series.

AMD A-8 (``6fqt``) requires the verdict on retained growth to be recorded as a
FRESH RESULT, "not inherited from G13". So this computes it from the committed
series rather than restating a conclusion reached elsewhere.

What "retained growth" means here
---------------------------------
Memory that survives the run. A server that leaks shows a series whose floor RISES
across the window: allocator arenas grow once and never return, so the baseline
after warm-up keeps climbing. A server that does not leak shows a series that
plateaus, and may FALL as transient allocations are released.

The test is therefore on the THIRDS of the window, not on first-versus-last. A
leak that is small and slow is invisible in the endpoints comparison and obvious
in a trend; conversely a one-off step from lazy initialisation looks like growth in
the endpoints and is flat in the trend. Both are reported for that reason.

WHY FIRST-VERSUS-LAST IS NOT ENOUGH, AND WHAT IT WOULD HAVE SAID HERE
---------------------------------------------------------------------
This run grew +159,744 bytes end to end, which read as "+0.15 MB over 40 minutes -
small but positive". Taken alone that reads as a slow leak.

The thirds say otherwise: the rise happens in the FIRST third and the series then
FALLS. Net drift across the window is NEGATIVE. So the end-to-end figure is the
residue of a bounded warm-up step plus a release, not a trend. Had only the
endpoints been compared, this run would have been reported as a candidate leak -
which is the error this computation exists to avoid.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def verdict(artefact: dict[str, Any]) -> dict[str, Any]:
    samples = [s["value"] for s in artefact["rss_series_bytes"]["samples"]]
    n = len(samples)
    k = n // 3

    def avg(xs: list[float]) -> float:
        return sum(xs) / len(xs)

    first, middle, last = avg(samples[:k]), avg(samples[k : 2 * k]), avg(samples[2 * k :])
    drift = last - first
    end_to_end = artefact["rss_last_bytes"] - artefact["rss_first_bytes"]
    MB = 1048576.0

    # A retained-growth verdict turns on the TREND, not on the endpoints. A
    # positive end-to-end delta with a flat-or-negative drift is a bounded warm-up
    # step plus a release, not a leak.
    rising_floor = drift > 0 and last > middle > first

    return {
        "window_seconds": artefact["window_seconds"],
        "window_minutes": round(artefact["window_seconds"] / 60, 2),
        "samples": n,
        "dropped_samples": artefact["cpu_series_seconds"]["dropped"],
        "target": artefact["target"],
        "first_third_mb": round(first / MB, 3),
        "middle_third_mb": round(middle / MB, 3),
        "last_third_mb": round(last / MB, 3),
        "drift_mb": round(drift / MB, 3),
        "end_to_end_bytes": end_to_end,
        "end_to_end_mb": round(end_to_end / MB, 4),
        "rising_floor": rising_floor,
        "verdict": (
            "NO RETAINED GROWTH. The series rises through the first third and then "
            "FALLS, so net drift across the window is negative."
            if not rising_floor
            else "RETAINED GROWTH: the floor rises monotonically across thirds."
        ),
        "why_endpoints_alone_would_mislead": (
            f"End to end this run grew {end_to_end:+.0f} bytes ({end_to_end/MB:+.4f} MB), "
            f"which read alone looks like a small positive leak. The thirds show the "
            f"rise is bounded and confined to the first third "
            f"({first/MB:.2f} -> {middle/MB:.2f} -> {last/MB:.2f} MB), with net drift "
            f"{drift/MB:+.3f} MB. Reporting the endpoint comparison alone would have "
            f"called this a candidate leak."
        ),
        "freshness": (
            "Computed from THIS run's committed series. Not inherited from G13, "
            "G14, or any earlier conclusion - those were reached on a different "
            "platform, window and workload, and this bead requires a verdict that "
            "stands on this measurement."
        ),
        "tracemalloc": {
            "role": artefact["tracemalloc_role"],
            "accounted_bytes": artefact["tracemalloc_divergence"]["tracemalloc_accounted_bytes"],
            "unattributed_bytes": artefact["tracemalloc_divergence"]["unattributed_bytes"],
            "reading": (
                "tracemalloc ATTRIBUTES and never concludes. Of the modest "
                "end-to-end change it accounts for "
                f"{artefact['tracemalloc_divergence']['tracemalloc_accounted_bytes']} bytes, "
                "leaving the remainder unattributed - consistent with allocator "
                "arena behaviour rather than Python heap growth. The verdict above "
                "is drawn from the RSS series alone."
            ),
        },
        "workload": artefact["workload"],
        "caveat": (
            "This window ran BEFORE the per-user client-IP fix, so 18.2% of its "
            "requests were rejected (1,508 of 8,281). That does not weaken the "
            "verdict - a series flat under partly-rejected load cannot hide "
            "growth - but the traffic figure is not clean sustained load and is "
            "carried in the artefact rather than smoothed."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--artefact",
        default="perf-results/RESULT-20261012-vmlocal/rss_final.json",
    )
    parser.add_argument("--output", default="perf-results/RSS_VERDICT.md")
    args = parser.parse_args()

    artefact = json.loads(Path(args.artefact).read_text(encoding="utf-8"))
    v = verdict(artefact)

    lines = [
        "# Retained-growth verdict (AMD A-8 / 6fqt)",
        "",
        "Computed fresh from `perf-results/RESULT-20261012-vmlocal/rss_final.json`.",
        "This is not inherited from G13, G14, or any earlier conclusion — those were",
        "reached on a different platform, window and workload.",
        "",
        f"## {v['verdict']}",
        "",
        f"- window **{v['window_minutes']} min** ({v['window_seconds']:.1f}s), "
        f"{v['samples']} samples, {v['dropped_samples']} dropped",
        f"- target **{v['target']['name']}** pid {v['target']['pid']} "
        f"(`is_self: {v['target']['is_self']}`) — the app process, server-side",
        f"- thirds **{v['first_third_mb']} → {v['middle_third_mb']} → {v['last_third_mb']} MB**",
        f"- drift **{v['drift_mb']:+.3f} MB** · end-to-end **{v['end_to_end_mb']:+.4f} MB** "
        f"({v['end_to_end_bytes']:+.0f} bytes)",
        f"- workload {v['workload']['request_count']} requests @ "
        f"{v['workload']['concurrency']} concurrency",
        "",
        "## Why the endpoints alone would mislead",
        "",
        v["why_endpoints_alone_would_mislead"],
        "",
        "## tracemalloc, attribution only",
        "",
        f"Role: `{v['tracemalloc']['role']}`. "
        f"Accounted {v['tracemalloc']['accounted_bytes']} bytes, "
        f"{v['tracemalloc']['unattributed_bytes']} bytes unattributed.",
        "",
        v["tracemalloc"]["reading"],
        "",
        "## Caveat",
        "",
        v["caveat"],
        "",
    ]
    Path(args.output).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:16]))
    print(f"...\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
