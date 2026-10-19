"""L-12 (liy7): warm-pool throughput ratios for the attack-pattern scan.

Inline vs thread vs process, at the documented production core count.

The unit of work is AttackDetectionMiddleware._check_for_patterns - the CPU-bound
pattern scan that _scan_request already offloads to an executor when the body
exceeds the in-line limit. This harness measures what that offload is actually
worth, on both sides of the prefilter boundary, because the two regimes differ by
more than an order of magnitude and a single-regime ratio would hide that.

Protocol (Lane E standing rules):
  - WARM POOLS ONLY. The pool is created, a discarded warmup batch runs, and only
    then is anything timed. Linux's default start method is fork, so children
    inherit the already-compiled COMBINED_ATTACK_RE and the prefilter automaton;
    that inheritance is what "warm" means here.
  - Fixed iteration count per repetition, never a time-based ceiling.
  - >= 5 repetitions; BOTH median-of-medians and min-of-medians are published.
  - gc.collect() before each repetition.
  - Report only. Never "new < old".

The IPC cost of a process pool is NOT subtracted out: a process pool must pickle
and ship the payload, and that cost is part of what a process pool costs. It is
reported in the ratio rather than excused from it.

Usage:
  uv run python src/benchmarks/l12_pool_ratios.py
  uv run python src/benchmarks/l12_pool_ratios.py --output reports/l12_result.json

Provenance: this harness was recovered from ``/home/ansible/l12_pool_ratios.py``
on the measurement host, where it ran on 2026-10-16. The measurement it produced
is recorded at ``perf-results/RESULT-20261016-l12/l12_pool_ratios.json`` (the
curated report) and ``perf-results/RESULT-20261016-l12/raw/l12_result.json``
(this harness's own raw output, byte for byte). Before the recovery the recorded
report could say ``"instrument": {"in_repo": false}`` and nothing could check it:
the instrument existed only on one machine's disk, so the Phase 0 provenance gate
would have refused the run. The measurement itself is UNCHANGED - this is the same
code, re-homed - and the recorded artefact keeps the historical ``in_repo: false``
because that was true of the run; see
``perf-results/RESULT-20261016-l12/FINDINGS.md``.
"""

from __future__ import annotations

import argparse
import gc
import json
import multiprocessing as mp
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Every environment mutation lives in bootstrap() rather than at import time:
# src/.env exists, so a module-level load_dotenv(override=True) would replace the
# environment a pytest session runs under, and os.chdir would move that
# session's working directory. Same rule as prof_request_path.bootstrap() and
# driver_count_io.bootstrap().

# --- regime bodies, matching scripts/body_scan_regimes.py -------------------
# The filler deliberately avoids every prefilter literal. An earlier attempt used
# lorem ipsum, which CONTAINS "or" inside "dolor"; the prefilter then admitted it,
# both regimes ran the full regex, and the per-KB ratio came out a fake 1.04.
_MISS_FILLER = b"the quick wiggly daffy numbats puffy kiwis waltzing "
_LITERAL_SENTENCE = b"select all rows from the ledger where id is 1 union "

MISS_BODY_KB = 60
HIT_BODY_KB = 40

REPS = 5
ITERS_PER_REP = 12
WARMUP_BATCH = 12
POOL_SIZES = (1, 2, 4, 8)
PRODUCTION_CORE_COUNT = 4

_ATTACKS: list[dict] = []
_MW: Any = None
_MIDDLEWARE_CLS: Any = None


def bootstrap() -> None:
    """Put the process where the harness needs it, then read the environment.

    Call this BEFORE anything under src.app is imported: the middleware's
    settings are constructed at import time and need a configured environment.
    """
    os.chdir(ROOT)
    load_dotenv(ROOT / "src" / ".env", override=True)


def middleware_cls() -> Any:
    """The production middleware class, imported lazily.

    Importing src.app at module level would construct the settings at import
    time, which needs an environment that only bootstrap() provides.
    """
    global _MIDDLEWARE_CLS
    if _MIDDLEWARE_CLS is None:
        from src.app.middleware.attack_detection import AttackDetectionMiddleware

        _MIDDLEWARE_CLS = AttackDetectionMiddleware
    return _MIDDLEWARE_CLS


def prefilter_hit(payload: bytes) -> bool:
    """True when the prefilter ADMITS the payload, so the full regex runs.

    The production predicate, not a reimplementation of it: the whole point of
    the two regimes is that they sit on opposite sides of this boundary.
    """
    from src.app.middleware.attack_detection import _attack_prefilter_hit

    return bool(_attack_prefilter_hit(payload))


def build_bodies() -> tuple[bytes, bytes]:
    miss = (_MISS_FILLER * ((MISS_BODY_KB * 1024) // len(_MISS_FILLER) + 1))[: MISS_BODY_KB * 1024]
    head = (_LITERAL_SENTENCE * ((HIT_BODY_KB * 1024) // len(_LITERAL_SENTENCE) + 1))[: HIT_BODY_KB * 1024]
    hit = head
    return miss, hit


def _inline_batch(payload: bytes, iters: int) -> None:
    mw = _MW
    assert mw is not None
    for _ in range(iters):
        mw._check_for_patterns(payload, "body", _ATTACKS)  # noqa: SLF001


def _thread_payload(payload: bytes, iters: int) -> None:
    _inline_batch(payload, iters)


def _process_payload(payload: bytes, iters: int) -> None:
    global _MW
    if _MW is None:
        _MW = middleware_cls()(app=None)
    _inline_batch(payload, iters)


def _process_init() -> None:
    """Child-side setup.

    Under fork this re-points the module globals at objects the child already
    inherited, so it is a no-op in practice. It exists so the arm does not depend
    on the fork start method silently.
    """
    global _MW
    if _MW is None:
        _MW = middleware_cls()(app=None)


def _summarise(per_rep: list[list[float]]) -> dict[str, Any]:
    medians = [statistics.median(r) for r in per_rep]
    return {
        "rep_medians_scans_per_sec": [round(m, 3) for m in medians],
        "median_of_medians_scans_per_sec": round(statistics.median(medians), 3),
        "min_of_medians_scans_per_sec": round(min(medians), 3),
        "max_of_medians_scans_per_sec": round(max(medians), 3),
        "all_rep_medians_equal": round(max(medians) - min(medians), 6) == 0.0,
    }


def _one_scan(payload: bytes) -> None:
    _inline_batch(payload, 1)


def measure(name: str, payload: bytes, arm: str, pool: int) -> dict[str, Any]:
    """Throughput in scans/second for one arm at one pool size.

    The offload arms submit ONE SCAN PER TASK, so a pool of size P really does
    run P scans concurrently. The first version of this harness submitted a
    single task per repetition that looped over all iterations internally, which
    meant a pool of 4 received exactly one task and three workers sat idle: the
    arms then measured dispatch overhead against pool size rather than parallel
    throughput, and every arm came out flat. Inline is the sequential equivalent -
    the same number of scans, one at a time, in the calling thread.
    """
    per_rep: list[list[float]] = []

    if arm == "inline":
        executor: Any = None

        def run_one() -> None:
            for _ in range(ITERS_PER_REP):
                _one_scan(payload)

    else:
        target = _thread_payload if arm == "thread" else _process_payload
        if arm == "thread":
            executor = ThreadPoolExecutor(max_workers=pool)
        else:
            executor = ProcessPoolExecutor(
                max_workers=pool,
                mp_context=mp.get_context("fork"),
                initializer=_process_init,
            )
        # Discarded warmup batch, so the timed window measures a warm pool.
        warm = [executor.submit(target, payload, 1) for _ in range(WARMUP_BATCH)]
        [f.result() for f in warm]

        def run_one() -> None:
            futures = [executor.submit(target, payload, 1) for _ in range(ITERS_PER_REP)]
            [f.result() for f in futures]

    try:
        for _ in range(REPS):
            gc.collect()
            t0 = time.perf_counter()
            run_one()
            elapsed = time.perf_counter() - t0
            per_rep.append([ITERS_PER_REP / elapsed])
    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    out = _summarise(per_rep)
    out["arm"] = arm
    out["pool_size"] = 1 if arm == "inline" else pool
    out["concurrency"] = 1 if arm == "inline" else pool
    out["tasks_submitted_per_repetition"] = ITERS_PER_REP
    out["scans_per_task"] = 1
    out["iterations_per_repetition"] = ITERS_PER_REP
    out["repetitions"] = REPS
    return out


def prefilter_properties(miss: bytes, hit: bytes) -> dict[str, Any]:
    """The boundary the two regimes sit on, asserted BEFORE anything is timed."""
    miss_hits = prefilter_hit(miss)
    hit_hits = prefilter_hit(hit)
    return {
        "miss_bytes": len(miss),
        "hit_bytes": len(hit),
        "miss_prefilter_hit": miss_hits,
        "hit_prefilter_hit": hit_hits,
        "miss_asserted_miss": miss_hits is False,
        "hit_asserted_hit": hit_hits is True,
        "both_asserted": (miss_hits is False) and (hit_hits is True),
        "statement": (
            "The 60 KB miss body carries no literal the prefilter can find, so the "
            "skip fires and the full regex never runs. The 40 KB hit body carries a "
            "SQL-shaped literal, so the prefilter admits it and the full regex runs. "
            "Asserted before any timing, because a body that changes class between "
            "the two regimes would make every ratio below a measurement of nothing."
        ),
    }


def build_artefact(results: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    """The raw artefact: every arm, every pool size, no editorialising."""
    return {
        "schema": "perf-l12-pool-ratios/1",
        "item": "L-12",
        "bead": "FastAPI_saas-liy7",
        "machine": {
            "cpu_model": _cpu_model(),
            "core_count": os.cpu_count(),
            "production_core_count": PRODUCTION_CORE_COUNT,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "start_method": "fork",
        },
        "protocol": {
            "warm_pools_only": True,
            "warmup_batch_discarded": WARMUP_BATCH,
            "repetitions": REPS,
            "iterations_per_repetition": ITERS_PER_REP,
            "gc_collect_before_each_rep": True,
            "ceiling": "fixed iteration count, never time-based",
            "statistics": "median-of-medians AND min-of-medians, both published",
            "criterion": "report only; never 'new < old'",
            "pool_sizes": list(POOL_SIZES),
            "ipc_cost": (
                "A process pool must pickle and ship the payload. That cost is NOT "
                "subtracted out: it is part of what a process pool costs, and "
                "removing it would report a number no deployment would see."
            ),
        },
        "windows_reference_not_used_as_before_arm": {
            "inline_ms": 1808,
            "thread_ms": 482,
            "process_ms": 782,
            "inline_scans_per_sec": 4.2,
            "thread_scans_per_sec": 16.6,
            "process_scans_per_sec": 10.2,
            "why_not_used": (
                "Addendum A-5 archives the Windows absolutes as non-representative. "
                "They may not serve as the before arm, so every ratio here is "
                "thread-or-process OVER INLINE MEASURED ON THIS LINUX TARGET, not "
                "over a Windows figure."
            ),
        },
        "regimes": results,
    }


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def main(argv: list[str] | None = None) -> int:
    # FIRST, before anything under src.app is imported: the middleware's
    # settings are constructed at import time and need the environment
    # bootstrap() provides. The original driver on the measurement host had
    # os.chdir(ROOT) at module level and took its environment from the shell;
    # this harness does both inside bootstrap() so that importing it never
    # moves a test session's working directory.
    bootstrap()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "l12_result.json",
        help="where to write the raw artefact (default: reports/l12_result.json, untracked)",
    )
    args = parser.parse_args(argv)

    global _MW
    _MW = middleware_cls()(app=None)

    miss, hit = build_bodies()

    # Assert the prefilter boundary BEFORE timing anything. If these do not hold,
    # both regimes run the same code and every ratio below is meaningless.
    properties = prefilter_properties(miss, hit)
    if not properties["both_asserted"]:
        print("PREFILTER PROPERTIES NOT ASSERTED - refusing to report ratios")
        return 1

    regimes = {"miss_60kb": miss, "hit_40kb": hit}
    results: dict[str, Any] = {}
    for regime_name, payload in regimes.items():
        block: dict[str, Any] = {"properties": properties, "arms": {}}
        inline_ref = measure("inline", payload, "inline", 1)
        block["arms"]["inline"] = inline_ref
        ref = inline_ref["median_of_medians_scans_per_sec"]
        for pool in POOL_SIZES:
            for arm in ("thread", "process"):
                key = f"{arm}_pool{pool}"
                measured = measure(key, payload, arm, pool)
                m = measured["median_of_medians_scans_per_sec"]
                measured["ratio_vs_inline_median_of_medians"] = round(m / ref, 3)
                mn = measured["min_of_medians_scans_per_sec"]
                measured["ratio_vs_inline_min_of_medians"] = round(
                    mn / inline_ref["min_of_medians_scans_per_sec"], 3
                )
                block["arms"][key] = measured
        # The production-core-count figure is the headline: 4 cores.
        block["at_production_core_count"] = {
            "core_count": PRODUCTION_CORE_COUNT,
            "pool_size": PRODUCTION_CORE_COUNT,
            "median_of_medians": {
                "inline_scans_per_sec": block["arms"]["inline"]["median_of_medians_scans_per_sec"],
                "thread_scans_per_sec": block["arms"][f"thread_pool{PRODUCTION_CORE_COUNT}"][
                    "median_of_medians_scans_per_sec"
                ],
                "process_scans_per_sec": block["arms"][f"process_pool{PRODUCTION_CORE_COUNT}"][
                    "median_of_medians_scans_per_sec"
                ],
            },
            "ratios_vs_inline": {
                "thread": block["arms"][f"thread_pool{PRODUCTION_CORE_COUNT}"][
                    "ratio_vs_inline_median_of_medians"
                ],
                "process": block["arms"][f"process_pool{PRODUCTION_CORE_COUNT}"][
                    "ratio_vs_inline_median_of_medians"
                ],
            },
            "thread_over_process": round(
                block["arms"][f"thread_pool{PRODUCTION_CORE_COUNT}"]["median_of_medians_scans_per_sec"]
                / block["arms"][f"process_pool{PRODUCTION_CORE_COUNT}"]["median_of_medians_scans_per_sec"],
                3,
            ),
        }
        results[regime_name] = block
        print(
            f"{regime_name}: inline {ref:.2f}/s | "
            f"thread@4 {block['at_production_core_count']['median_of_medians']['thread_scans_per_sec']:.2f}/s | "
            f"process@4 {block['at_production_core_count']['median_of_medians']['process_scans_per_sec']:.2f}/s"
        )

    artefact = build_artefact(results, properties)
    out = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artefact, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
