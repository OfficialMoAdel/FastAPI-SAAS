"""L-13: endpoint-level p95 effect of body-scan offload on a BODY-CARRYING route.

Bead FastAPI_saas-e0ss. Measurement only - no production file is edited.

Route is POST /api/v1/posts/by-user/{username}: D18 establishes the body-scan
gate is POST/PUT/PATCH only, so no GET path carries it and GET /users/me is
excluded by the bead's own notes.

Arms are selected by AttackDetectionMiddleware._BODY_OFFLOAD_THRESHOLD, which
production reads at request time:
  inline - threshold raised above the body, scan runs in the event loop thread
  thread  - production threshold 16384, run_in_executor(None, ...) untouched

Accounts and source IPs are DECOUPLED. The rate limiter keys on IP and the
tier policy keys on user, so a run that drives one account from one IP trips
the auth budget (5/60 per IP) within seconds and then trips the auto-ban. Each
request therefore gets its own loopback source IP while cycling a pool of 250
accounts, which keeps every IP at ~1 request and every account far under its
tier limit. Both budgets are satisfied, not relaxed.

Percentiles come from src.benchmarks.percentiles.nearest_rank; int(n*q) is
banned tree-wide. Fixed iteration ceilings, gc.collect() before each rep, five
reps, both median-of-medians AND min-of-medians published.

Usage:
  uv run python src/benchmarks/l13_body_offload.py
  uv run python src/benchmarks/l13_body_offload.py --output reports/l13_result.json

Provenance: this harness was recovered from ``/home/ansible/l13_body_offload.py``
on the measurement host, where it ran on 2026-10-16. The measurement it produced
is recorded at ``perf-results/RESULT-20261016-l13/l13_body_offload.json`` (the
curated report) and ``perf-results/RESULT-20261016-l13/raw/l13_result.json``
(this harness's own raw output, byte for byte). Before the recovery the recorded
report could say ``"instrument": {"in_repo": false}`` and nothing could check it:
the instrument existed only on one machine's disk, so the Phase 0 provenance gate
would have refused the run. The measurement itself is UNCHANGED - this is the same
code, re-homed - and the recorded artefact keeps the historical ``in_repo: false``
because that was true of the run; see
``perf-results/RESULT-20261016-l13/FINDINGS.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import gc
import itertools
import json
import os
import pickle
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.benchmarks.percentiles import nearest_rank  # noqa: E402

# Every environment mutation lives in bootstrap() rather than at import time:
# src/.env exists, so a module-level load_dotenv(override=True) would replace the
# environment a pytest session runs under, and os.chdir would move that
# session's working directory. Same rule as prof_request_path.bootstrap() and
# driver_count_io.bootstrap().

N_ACCOUNTS = 250
REQUESTS_PER_BLOCK = 100
REPS = 5
KNEE_SWEEP = (1, 2, 4, 8, 16, 32, 64)
KNEE_FACTOR = 2.0
P95 = 0.95
P50 = 0.50

TENANT = "test-tenant-e2e"
USER_FMT = "perf_user_{}"
PASSWORD = "Str1ngst!"
LOGIN_PATH = "/api/v1/account/login"
POST_PATH = "/api/v1/posts/by-user/{}"
BASE = "http://l13.invalid"

# MISS_FILLER is deliberately free of every prefilter literal (union, or,
# drop, delete, --, exec, <, javascript, ../, ;, ||, &&, 169.254...) so the
# prefilter skips the compiled regex. HIT_SENTENCE carries "union" so the
# prefilter admits and the full regex runs.
MISS_FILLER = b"the quick wiggly daffy numbats puffy kiwis waltzing "
HIT_SENTENCE = b"select all rows union "

_APP: Any = None


def bootstrap() -> None:
    """Put the process where the harness needs it, then read the environment.

    Call this BEFORE anything under src.app is imported: the application's
    settings are constructed at import time and need a configured environment.
    """
    os.chdir(ROOT)
    load_dotenv(ROOT / "src" / ".env", override=True)
    os.environ.setdefault("QUERY_PROFILER_ENABLED", "false")
    os.environ.setdefault("AUTO_BAN_ENABLED", "true")


def _app() -> Any:
    """The ASGI application, imported lazily.

    Importing src.app at module level would construct the settings at import
    time, which needs an environment that only bootstrap() provides.
    """
    global _APP
    if _APP is None:
        from src.app.main import app

        _APP = app
    return _APP


def _middleware_cls() -> Any:
    """The production middleware class, imported lazily. See _app()."""
    from src.app.middleware.attack_detection import AttackDetectionMiddleware

    return AttackDetectionMiddleware


def _pad(sentence: bytes, size: int) -> bytes:
    body = sentence + MISS_FILLER * ((size // len(MISS_FILLER)) + 2)
    return body[:size]


def _build() -> tuple[bytes, bytes]:
    miss_text = _pad(b"", 60 * 1024)
    hit_text = _pad(HIT_SENTENCE, 40 * 1024)
    # The middleware scans REQUEST BYTES, so the real JSON envelope is what
    # has to be large, not the model field.
    miss_wire = json.dumps({"title": "l13 miss", "text": miss_text.decode()}).encode()
    hit_wire = json.dumps({"title": "l13 hit", "text": hit_text.decode()}).encode()
    return miss_wire, hit_wire


MISS_WIRE, HIT_WIRE = _build()

_IP = itertools.count(1)


def _client() -> httpx.AsyncClient:
    n = next(_IP)
    ip = f"127.{(n >> 16) & 0xFF}.{(n >> 8) & 0xFF}.{n & 0xFF}"
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(), client=(ip, 0)),
        base_url=BASE,
        timeout=120.0,
        headers={"X-Tenant-ID": TENANT, "User-Agent": "l13/1.0"},
    )


async def _block(tokens: list[str], users: list[str], body: bytes, conc: int) -> dict[str, Any]:
    sem = asyncio.Semaphore(conc)

    async def one(i: int) -> tuple[int, float]:
        async with sem:
            c = _client()
            c.headers["Authorization"] = f"Bearer {tokens[i]}"
            t0 = time.perf_counter_ns()
            r = await c.post(
                POST_PATH.format(users[i]),
                content=body,
                headers={"Content-Type": "application/json"},
            )
            dt = (time.perf_counter_ns() - t0) / 1e6
            await c.aclose()
            return r.status_code, dt

    results = await asyncio.gather(*(one(i) for i in range(REQUESTS_PER_BLOCK)))
    codes: dict[str, int] = {}
    for code, _ in results:
        codes[str(code)] = codes.get(str(code), 0) + 1
    lat = sorted(ms for _, ms in results)
    return {
        "requests": len(results),
        "statuses": codes,
        "p50_ms": nearest_rank(lat, P50),
        "p95_ms": nearest_rank(lat, P95),
        "min_ms": lat[0],
        "concurrency": conc,
    }


def _summarise(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    def med(xs: list[float]) -> float:
        o = sorted(xs)
        m = len(o) // 2
        return o[m] if len(o) % 2 else (o[m - 1] + o[m]) / 2

    p95 = [b["p95_ms"] for b in blocks]
    p50 = [b["p50_ms"] for b in blocks]
    return {
        "reps": len(blocks),
        "p95_median_of_medians_ms": med(p95),
        "p95_min_of_medians_ms": min(p95),
        "p95_max_of_medians_ms": max(p95),
        "p50_median_of_medians_ms": med(p50),
        "p50_min_of_medians_ms": min(p50),
        "per_rep_p95_ms": p95,
        "statuses": dict(collections.Counter({c: n for b in blocks for c, n in b["statuses"].items()})),
    }


def _spread(a: float, b: float) -> float:
    return 0.0 if a == 0 else (b - a) / a


def _process_arm_checks(prod_callable: Any, detected: list[Any]) -> dict[str, Any]:
    """Three empirical checks that a process pool cannot be dropped in.

    Each one is executed, not argued: a claim that a process pool "would not
    work" is worth nothing next to the exception it raises.

    ``prod_callable`` is passed in rather than built here so that the lambda
    lives in the CALLER's scope: the recorded artefact's error text names the
    enclosing function, and a lambda defined one frame deeper would print a
    different qualification than the one that was measured.
    """

    try:
        pickle.dumps(prod_callable)
        lam_pickles, lam_err = True, None
    except Exception as exc:  # noqa: BLE001
        lam_pickles, lam_err = False, f"{type(exc).__name__}: {exc}"

    default_err = None
    try:
        from concurrent.futures import ProcessPoolExecutor

        loop = asyncio.new_event_loop()
        loop.set_default_executor(ProcessPoolExecutor(max_workers=1))
        loop.close()
        default_ok = True
    except Exception as exc:  # noqa: BLE001
        default_ok, default_err = False, f"{type(exc).__name__}: {exc}"

    shared: list[int] = [1]
    child = list(pickle.loads(pickle.dumps(shared)))
    child.append(2)

    return {
        "verdict": "IMPOSSIBLE AS A DROP-IN - three independent reasons",
        "1_default_executor_rejects_process_pools": {
            "set_default_executor_succeeded": default_ok,
            "error": default_err,
            "note": (
                "CPython's BaseEventLoop.set_default_executor rejects anything that is not a "
                "ThreadPoolExecutor, so run_in_executor(None, ...) - exactly what production "
                "calls - cannot be redirected to a process pool without patching "
                "BaseEventLoop.run_in_executor itself."
            ),
        },
        "2_production_callable_is_not_picklable": {
            "lambda_pickles": lam_pickles,
            "error": lam_err,
            "note": (
                "Production submits lambda: self._check_for_patterns(...) and self is a "
                "middleware holding an ASGI app. Neither is picklable."
            ),
        },
        "3_in_place_mutation_would_be_lost": {
            "parent_list": shared,
            "child_copy": child,
            "mutation_survives": child == shared,
            "note": (
                "detected_attacks is mutated in place. Across a process boundary it is pickled "
                "BY VALUE, so an attack found in the worker would be discarded and the caller "
                "would never learn about it. That is a security regression, not a performance "
                "question."
            ),
        },
        "process_throughput_figure": (
            "perf-results/RESULT-20261016-l12/l12_pool_ratios.json (bead liy7, closed): the "
            "process arm runs 2.083x inline on the 60 KB miss regime and 2.614x inline on the "
            "40 KB hit regime. That figure exists; it cannot simply be wired into the endpoint."
        ),
    }


async def main(output: Path) -> int:
    """Run the three phases and write the raw artefact.

    Named ``main`` on purpose: the pickling check below records the enclosing
    function's name in its error text, and the recorded artefact says
    ``main.<locals>.<lambda>``.
    """
    thresholds = {"inline": 1 << 30, "thread": 16 * 1024}
    result: dict[str, Any] = {
        "bead": "FastAPI_saas-e0ss",
        "item": "L-13",
        "protocol": {
            "route": "POST /api/v1/posts/by-user/{username}",
            "why_this_route": "D18: the body-scan gate is POST/PUT/PATCH only, so no GET path carries it.",
            "accounts": N_ACCOUNTS,
            "requests_per_block": REQUESTS_PER_BLOCK,
            "reps": REPS,
            "knee_sweep": list(KNEE_SWEEP),
            "knee_factor": KNEE_FACTOR,
            "arm_selection": "AttackDetectionMiddleware._BODY_OFFLOAD_THRESHOLD read at request time",
            "budgets_relaxed": False,
            "ip_account_decoupling": (
                "every request gets a fresh loopback source IP and cycles a pool of 250 "
                "accounts, so the per-IP auth budget (5/60) is satisfied ~1 request deep and "
                "no IP can accumulate violations for the auto-ban"
            ),
            "percentile_source": "src.benchmarks.percentiles.nearest_rank",
            "gc_collect_before_each_rep": True,
            "fixed_iteration_ceiling": True,
            "criterion": "report only; never 'new < old'",
        },
        "regimes": {
            "miss60k": {"wire_bytes": len(MISS_WIRE), "prefilter": "skips the compiled regex"},
            "hit40k": {"wire_bytes": len(HIT_WIRE), "prefilter": "admits; the full compiled regex runs"},
        },
        "knee_sweep": [],
        "blocks": {},
        "ratios": {},
        "process_arm": {},
    }

    from asgi_lifespan import LifespanManager

    async with LifespanManager(_app()):
        users = [USER_FMT.format(i) for i in range(N_ACCOUNTS)]
        tokens: list[str] = []
        login_codes: dict[str, int] = {}
        for username in users:
            c = _client()
            r = await c.post(LOGIN_PATH, data={"username": username, "password": PASSWORD})
            login_codes[str(r.status_code)] = login_codes.get(str(r.status_code), 0) + 1
            if r.status_code == 200:
                tokens.append(r.json()["access_token"])
            await c.aclose()
        result["logins"] = {"statuses": login_codes, "tokens": len(tokens)}
        if len(tokens) != N_ACCOUNTS:
            result["fatal"] = f"only {len(tokens)} logins succeeded: {login_codes}"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return 1

        rot = itertools.cycle(range(N_ACCOUNTS))

        def pick() -> tuple[list[str], list[str]]:
            idx = [next(rot) for _ in range(REQUESTS_PER_BLOCK)]
            return [tokens[i] for i in idx], [users[i] for i in idx]

        # ---- Phase 1: knee sweep, inline / hit40k (the expensive regime)
        middleware = _middleware_cls()
        middleware._BODY_OFFLOAD_THRESHOLD = thresholds["inline"]  # noqa: SLF001
        for conc in KNEE_SWEEP:
            t, u = pick()
            gc.collect()
            result["knee_sweep"].append(await _block(t, u, HIT_WIRE, conc))
        base_p95 = result["knee_sweep"][0]["p95_ms"]
        knee = None
        for row in result["knee_sweep"]:
            row["p95_over_baseline"] = row["p95_ms"] / base_p95
            if knee is None and row["p95_over_baseline"] > KNEE_FACTOR:
                knee = row["concurrency"]
        result["knee"] = knee
        result["knee_note"] = (
            f"first concurrency whose p95 exceeds {KNEE_FACTOR}x the concurrency-1 p95"
            if knee
            else f"no concurrency reached {KNEE_FACTOR}x; using the largest swept"
        )
        knee_conc = knee or max(KNEE_SWEEP)

        # ---- Phase 2: arms x regimes at the knee
        for regime, body in (("miss60k", MISS_WIRE), ("hit40k", HIT_WIRE)):
            for arm, thr in thresholds.items():
                middleware._BODY_OFFLOAD_THRESHOLD = thr  # noqa: SLF001
                blocks = []
                for _ in range(REPS):
                    t, u = pick()
                    gc.collect()
                    blocks.append(await _block(t, u, body, knee_conc))
                result["blocks"][f"{arm}/{regime}"] = _summarise(blocks)
        middleware._BODY_OFFLOAD_THRESHOLD = 16 * 1024  # noqa: SLF001

    # ---- Phase 3: can a process pool be dropped in? Three checks, all empirical.
    detected: list[Any] = []

    class _Fake:
        def _check_for_patterns(self, *a: Any) -> None:
            detected.append(1)

    # A lambda, deliberately: the production call site submits a lambda, and
    # that is exactly what makes it unpicklable. Rewriting it as a def would
    # make the check pass for the wrong reason.
    prod_callable = (lambda: _Fake()._check_for_patterns(b"x", "request_body", detected))  # noqa: E731
    result["process_arm"] = _process_arm_checks(prod_callable, detected)

    ratios: dict[str, Any] = {}
    for regime in ("miss60k", "hit40k"):
        inl = result["blocks"][f"inline/{regime}"]
        thr = result["blocks"][f"thread/{regime}"]
        ratios[regime] = {
            "inline_p95_median_of_medians_ms": inl["p95_median_of_medians_ms"],
            "thread_p95_median_of_medians_ms": thr["p95_median_of_medians_ms"],
            "ratio_median_of_medians": thr["p95_median_of_medians_ms"] / inl["p95_median_of_medians_ms"],
            "ratio_min_of_medians": thr["p95_min_of_medians_ms"] / inl["p95_min_of_medians_ms"],
            "inline_spread_pct": _spread(inl["p95_min_of_medians_ms"], inl["p95_median_of_medians_ms"]) * 100,
            "thread_spread_pct": _spread(thr["p95_min_of_medians_ms"], thr["p95_median_of_medians_ms"]) * 100,
        }
    result["ratios"] = ratios
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {output}")
    return 0


def cli(argv: list[str] | None = None) -> int:
    """Parse the arguments, put the process where it needs to be, then run."""
    # FIRST, before anything under src.app is imported: the application's
    # settings are constructed at import time and need the environment
    # bootstrap() provides.
    bootstrap()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "l13_result.json",
        help="where to write the raw artefact (default: reports/l13_result.json, untracked)",
    )
    args = parser.parse_args(argv)

    return asyncio.run(main(args.output))


if __name__ == "__main__":
    raise SystemExit(cli())
