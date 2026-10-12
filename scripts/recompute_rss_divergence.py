"""Recompute the tracemalloc divergence from a stored rss_cpu_series artefact.

The 20261012 run shipped `tracemalloc_divergence.status == "not-available"`
while its own companion block carried a tracemalloc peak of 58,066 bytes. The
cause was a wiring bug in ``scripts/rss_window.py``: it wrote the companion's
peak into the export dict but not into the sampler, and
``_tracemalloc_divergence`` reads the sampler's attribute.

Rather than spend another 40 minutes to reproduce a number the artefact
already contains, this recomputes it from the stored companion figures and
reports both the corrected value and what the artefact originally claimed.

It also fixes the recorded workload from the locust CSV, because the run
declared ``request_count: 0`` - the generator's true total was not known until
after the window closed, and a declared zero is worse than an absent field.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

MB = 1048576.0


def recompute_divergence(artefact: dict) -> dict:
    """Rebuild the divergence block from the artefact's own companion figures."""
    companion = artefact.get("tracemalloc_divergence", {}).get("companion", {})
    peak = companion.get("tracemalloc_peak_bytes")
    growth = artefact.get("rss_growth_bytes")
    if peak is None:
        return {"status": "not-available", "reason": "companion carried no peak"}
    accounted = max(0, min(peak, growth)) if growth is not None and growth > 0 else 0
    return {
        "status": "compared",
        "rss_growth_bytes": growth,
        "tracemalloc_peak_bytes": peak,
        "tracemalloc_accounted_bytes": accounted,
        "unattributed_bytes": (growth - accounted) if growth is not None else None,
        "recomputed_from": "companion block of this artefact; see scripts/"
        "recompute_rss_divergence.py",
        "caveat": (
            "tracemalloc was measured in the 120 s COMPANION window, not the "
            "40-minute one, so this compares two different periods. It is "
            "indicative evidence about attribution, NOT a measured divergence "
            "for the RSS window itself."
        ),
    }


def workload_from_csv(path: Path) -> dict:
    """The generator's true total for the run, from its own stats CSV."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("Name") or "").strip() == "Aggregated":
                return {
                    "request_count": int(row["Request Count"]),
                    "failures": int(row.get("Failure Count") or 0),
                    "source": str(path),
                }
    return {}


def main() -> int:
    artefact_path = Path(sys.argv[1])
    csv_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    artefact = json.loads(artefact_path.read_text(encoding="utf-8"))
    original = artefact["tracemalloc_divergence"]["status"]
    corrected = recompute_divergence(artefact)

    artefact["tracemalloc_divergence"] = corrected
    artefact["tracemalloc_divergence"]["originally_reported_as"] = original

    if csv_path:
        observed = workload_from_csv(csv_path)
        if observed:
            artefact["workload"]["observed_request_count"] = observed["request_count"]
            artefact["workload"]["observed_failures"] = observed["failures"]
            artefact["workload"]["declared_request_count"] = artefact["workload"][
                "request_count"
            ]
            artefact["workload"]["request_count"] = observed["request_count"]
            artefact["workload"]["source"] = observed["source"]

    out = artefact_path.with_name(artefact_path.stem + "_corrected.json")
    out.write_text(json.dumps(artefact, indent=2), encoding="utf-8")

    growth = artefact["rss_growth_bytes"] / MB
    div = artefact["tracemalloc_divergence"]
    print(f"artefact     : {artefact_path}")
    print(f"corrected    : {out}")
    print(f"target       : {artefact['target']}")
    print(f"window       : {artefact['window_seconds']:.1f}s "
          f"({artefact['sample_count']} samples, meets_minimum="
          f"{artefact['window_meets_minimum']})")
    print(f"rss growth   : {growth:+.3f} MB")
    print(f"divergence   : {original} -> {div['status']}")
    print(f"  tracemalloc accounted : {div['tracemalloc_accounted_bytes']} bytes")
    print(f"  unattributed          : {div['unattributed_bytes']} bytes")
    if "observed_request_count" in artefact["workload"]:
        w = artefact["workload"]
        print(f"workload     : {w['request_count']} requests "
              f"(was declared {w['declared_request_count']}), "
              f"{w['observed_failures']} failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
