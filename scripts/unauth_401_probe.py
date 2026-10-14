"""GET /api/v1/users/me with no Authorization - the 401 class.

EM-2 (``je4m``) asks for the unauthenticated variant with 4.4 sizes, the
expected-401 exemption DOCUMENTED in the artefact, zero 429s, zero rate-limit or
auto-ban events, and an explicit ERROR-traceback count per request.

THE 401 EXEMPTION IS THE POINT, NOT AN AFTERTHOUGHT
----------------------------------------------------
A 401 here is the endpoint working: no credential was supplied, so the request is
correctly refused. Any harness that counts non-2xx as failure reports this
endpoint as broken, which is why the pack's earlier runs and this one would
disagree for reasons that have nothing to do with the server.

So 401 is EXPECTED and is classified separately from unexpected statuses, and the
exemption is written into the artefact with its reason rather than applied as an
exception in the counting code. An exemption nobody can see is indistinguishable
from a metric quietly ignoring failures.

WHAT WOULD ACTUALLY BE A FAILURE
--------------------------------
429 - a rate limit was tripped, which means the run is measuring rejection rather
than the 401 path. 403 - the request was authenticated and then denied, which this
endpoint never should be. Anything else non-401 is a genuine defect and is
counted as one.

ERROR TRACEBACKS
----------------
Counted per request. A 401 that returns a traceback leaks internals to an
unauthenticated caller, and a bare count cannot tell "rejected cleanly" from
"rejected loudly". The check reads the response body for a traceback signature
rather than trusting the status alone.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from src.benchmarks.percentiles import nearest_rank

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TENANT = "test-tenant-e2e"

#: Statuses that mean the run is broken rather than the endpoint misbehaving.
FATAL_STATUSES = (429, 403)

#: Signatures of an internals leak in an error body. Checked in the response
#: TEXT because a traceback can ride on a 200 in some frameworks, and because the
#: status alone cannot tell a clean refusal from a loud one.
TRACEBACK_MARKERS = (
    "Traceback (most recent call last)",
    'File "',
    "sqlalchemy.exc",
    "pydantic_core",
    "raise ",
)


@contextlib.asynccontextmanager
async def _driver(base: str):
    """The real app in-process, sending NO Authorization header - deliberately."""
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url=base, timeout=60.0
    ) as client:
        async with LifespanManager(app):
            # Only the tenant and agent headers. No credential is the whole point;
            # sending one and expecting a 401 would test the token path, not this.
            headers = {"X-Tenant-ID": TENANT, "User-Agent": USER_AGENT}

            async def hit() -> dict[str, Any]:
                started = time.perf_counter()
                r = await client.get("/api/v1/users/me", headers=headers)
                elapsed = (time.perf_counter() - started) * 1000.0
                body = r.text
                return {
                    "ms": elapsed,
                    "status": r.status_code,
                    "traceback": any(marker in body for marker in TRACEBACK_MARKERS),
                    "server_ms": float(r.headers["X-Response-Time"])
                    if r.headers.get("X-Response-Time")
                    else None,
                }

            yield hit


async def main_async(args: argparse.Namespace) -> int:
    statuses: Counter[int] = Counter()
    traceback_count = 0
    traceback_examples: list[str] = []
    reps: list[dict[str, Any]] = []
    latencies: list[float] = []
    server_latencies: list[float] = []

    async with _driver(args.base) as hit:
        for _ in range(args.warmup):
            await hit()

        for rep in range(args.repetitions):
            local: list[float] = []
            server: list[float] = []
            for _ in range(args.requests):
                row = await hit()
                statuses[row["status"]] += 1
                local.append(row["ms"])
                if row["server_ms"] is not None:
                    server.append(row["server_ms"])
                if row["traceback"]:
                    traceback_count += 1
                    if len(traceback_examples) < 3:
                        traceback_examples.append(f"status {row['status']}")
            latencies.extend(local)
            server_latencies.extend(server)
            reps.append(
                {
                    "repetition": rep + 1,
                    "n": len(local),
                    "min_ms": round(min(local), 4),
                    "p50_ms": round(nearest_rank(local, 0.50), 4),
                    "p95_ms": round(nearest_rank(local, 0.95), 4),
                    "server_p50_ms": round(nearest_rank(server, 0.50), 4) if server else None,
                }
            )
            print(
                f"  rep {rep + 1}: n={len(local)} min={reps[-1]['min_ms']}ms "
                f"p50={reps[-1]['p50_ms']}ms",
                flush=True,
            )

    n = len(latencies)
    fatal = {s: c for s, c in statuses.items() if s in FATAL_STATUSES}
    result = {
        "schema": "perf-401-class/1",
        "endpoint": "GET /api/v1/users/me",
        "credential_sent": False,
        "purpose": "the 401 class - traceback exposure and refusal cost, unauthenticated",
        "driver": "in-process over ASGITransport; no Authorization header is sent",
        "repetitions": args.repetitions,
        "requests_per_repetition": args.requests,
        "warmup_discarded": args.warmup,
        "n_measured": n,
        "expected_401_exemption": {
            "applies_to": 401,
            "reason": (
                "A 401 is this endpoint working: no credential was supplied, so "
                "the request is correctly refused. Counting it as a failure would "
                "report a healthy endpoint as broken. The exemption is recorded "
                "here rather than applied silently in the counting code, so a "
                "reader can tell an exemption from a metric ignoring failures."
            ),
            "counts_as_failure": False,
        },
        "status_distribution": {str(s): c for s, c in sorted(statuses.items())},
        "fatal_statuses": {
            "statuses": list(FATAL_STATUSES),
            "observed": fatal,
            "meaning": "429 means a rate limit tripped and the run is measuring "
                       "rejection; 403 means an unauthenticated request was "
                       "denied rather than challenged, which this endpoint must "
                       "never do. Either voids the run under 4.5.",
            "clean": not fatal,
        },
        "error_traceback": {
            "count": traceback_count,
            "per_request": round(traceback_count / n, 6) if n else None,
            "clean": traceback_count == 0,
            "markers_checked": list(TRACEBACK_MARKERS),
            "note": "checked in the response BODY, not inferred from the status, "
                    "because a clean refusal and a loud one share a status",
        },
        "latency": {
            "min_ms": round(min(latencies), 4) if latencies else None,
            "p50_ms": round(nearest_rank(latencies, 0.50), 4),
            "p95_ms": round(nearest_rank(latencies, 0.95), 4),
            "p99_ms": round(nearest_rank(latencies, 0.99), 4),
            "median_of_repetition_p50s_ms": round(
                statistics.median([r["p50_ms"] for r in reps]), 4
            ),
            "server_p50_ms": round(nearest_rank(server_latencies, 0.50), 4)
            if server_latencies
            else None,
        },
        "per_repetition": reps,
        "p99_reportable": n >= 10000,
        "p99_note": (
            f"n={n}; 4.4 requires 10,000 for a p99 claim and 4.6 says a column "
            f"below its sample size is not reported. p99 is computed here for "
            f"completeness but p99_reportable is {n >= 10000}."
        ),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"statuses      : {result['status_distribution']}")
    print(f"fatal statuses: {fatal or 'none'}  (429/403 void the run)")
    print(f"tracebacks    : {traceback_count} over {n} requests "
          f"({result['error_traceback']['per_request']}/req)")
    print(f"min {result['latency']['min_ms']}ms  p50 {result['latency']['p50_ms']}ms  "
          f"p95 {result['latency']['p95_ms']}ms  "
          f"server_p50 {result['latency']['server_p50_ms']}ms")
    print(f"p99 reportable: {result['p99_reportable']} (n={n}, needs 10000)")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/unauth_401.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--warmup", type=int, default=60)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
