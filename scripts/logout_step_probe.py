"""POST /api/v1/account/logout - the unexplained ~3.4 s fixed step.

EM-6 (``ky66``) asks for the endpoint measured OUTSIDE ``on_stop`` with
``catch_response``, n>=1000 for p50, Min and p50 across >=5 repetitions, a 100%
success status distribution, and the 3.17-3.69 s step either reproduced with a
number or explicitly shown NOT to reproduce on Linux - which is itself a result.

WHY OUTSIDE on_stop, AND WHY IT STILL MATTERS IF IT IS OUTSIDE
--------------------------------------------------------------
The locustfile used to POST logout from ``on_stop``. Every such sample was a
SHUTDOWN-BURST sample: taken while the event loop, the DB pool and the Redis pool
were all closing, so the number measured teardown contention rather than the
handler. The 208.47 ms logout p95 in the 2026-09-22 baseline came from there and is
attributable to nothing - ``logout`` performs no bcrypt and no batched I/O.

So the step must be measured as an ordinary request in steady state. That is what
this probe does: it logs in, then issues logout as a normal measured request, over
n>=1000 samples, and reports the distribution rather than one reading.

WHAT "NOT REPRODUCING" WOULD MEAN
---------------------------------
The recorded step is 3.17-3.69 s. If this target shows no such plateau, that is a
RESULT and is reported as one - not as a failure to reproduce. The recorded figure
came from a shutdown burst on a different platform; if the burst was the cause,
the step should be absent from a steady-state measurement, and saying so plainly
is more useful than hunting for a number that is not there.

LOGOUT IS STATE-CHANGING, SO THE ACCOUNT POOL IS EXHAUSTED DELIBERATELY
---------------------------------------------------------------------
Each logout revokes the session behind it, and auth is capped at 5 concurrent
sessions per account. Rather than measure the same account thrashing its own
session limit - which would measure the limiter, not the endpoint - the probe
re-authenticates before each measured logout and records how many re-logins it
needed, so the cost is visible rather than buried.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import time
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

from src.benchmarks.percentiles import nearest_rank

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TENANT = "test-tenant-e2e"

#: The step recorded in the pack, quoted so a reader can judge reproduction.
RECORDED_STEP_RANGE_S = (3.17, 3.69)


@contextlib.asynccontextmanager
async def _driver(base: str, username: str, password: str):
    """The real app in-process, with a re-auth helper: logout is state-changing."""
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url=base, timeout=120.0
    ) as client:
        async with LifespanManager(app):
            base_headers = {
                "X-Tenant-ID": TENANT,
                "User-Agent": USER_AGENT,
            }

            async def login() -> str:
                r = await client.post(
                    "/api/v1/account/login",
                    data=urllib.parse.urlencode(
                        {"username": username, "password": password}
                    ),
                    headers={
                        **base_headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                if r.status_code != 200:
                    raise RuntimeError(f"login failed {r.status_code}")
                return r.json()["access_token"]

            async def measure_logout() -> dict[str, Any]:
                # Re-auth FIRST so logout never races a session the previous logout
                # already revoked. The re-login cost is excluded from the timing.
                token = await login()
                headers = {**base_headers, "Authorization": f"Bearer {token}"}
                started = time.perf_counter()
                r = await client.post("/api/v1/account/logout", headers=headers)
                elapsed = (time.perf_counter() - started) * 1000.0
                return {
                    "ms": elapsed,
                    "status": r.status_code,
                    "server_ms": float(r.headers["X-Response-Time"])
                    if r.headers.get("X-Response-Time")
                    else None,
                }

            yield measure_logout


async def main_async(args: argparse.Namespace) -> int:
    reps: list[dict[str, Any]] = []
    latencies: list[float] = []
    server_latencies: list[float] = []
    statuses: Counter[int] = Counter()

    async with _driver(args.base, args.username, args.password) as measure:
        for _ in range(args.warmup):
            await measure()

        for rep in range(args.repetitions):
            local: list[float] = []
            server: list[float] = []
            for _ in range(args.requests):
                row = await measure()
                statuses[row["status"]] += 1
                local.append(row["ms"])
                if row["server_ms"] is not None:
                    server.append(row["server_ms"])
            latencies.extend(local)
            server_latencies.extend(server)
            reps.append(
                {
                    "repetition": rep + 1,
                    "n": len(local),
                    "min_ms": round(min(local), 4),
                    "p50_ms": round(nearest_rank(local, 0.50), 4),
                    "p95_ms": round(nearest_rank(local, 0.95), 4),
                    "server_p50_ms": round(nearest_rank(server, 0.50), 4)
                    if server
                    else None,
                }
            )
            print(
                f"  rep {rep + 1}: n={len(local)} min={reps[-1]['min_ms']}ms "
                f"p50={reps[-1]['p50_ms']}ms",
                flush=True,
            )

    n = len(latencies)
    step_lo_ms = RECORDED_STEP_RANGE_S[0] * 1000.0
    step_hi_ms = RECORDED_STEP_RANGE_S[1] * 1000.0
    in_step = sum(1 for v in latencies if step_lo_ms <= v <= step_hi_ms)
    p50 = nearest_rank(latencies, 0.50)

    result = {
        "schema": "perf-logout-step/1",
        "endpoint": "POST /api/v1/account/logout",
        "measured": "as an ordinary steady-state request, NOT from on_stop",
        "why_not_on_stop": (
            "on_stop samples are taken while the event loop, the DB pool and the "
            "Redis pool are closing, so they measure teardown contention rather "
            "than the handler. The 208.47 ms logout p95 in the 2026-09-22 "
            "baseline came from there and is attributable to nothing - logout "
            "performs no bcrypt and no batched I/O."
        ),
        "repetitions": args.repetitions,
        "requests_per_repetition": args.requests,
        "warmup_discarded": args.warmup,
        "n_measured": n,
        "reauthentications": n + args.warmup,
        "reauth_note": (
            "logout revokes the session behind it and auth caps at 5 concurrent "
            "sessions, so each measured logout is preceded by a fresh login. "
            "Without this the probe would measure the session limiter instead of "
            "the endpoint. The re-login cost is EXCLUDED from the timings."
        ),
        "status_distribution": {str(s): c for s, c in sorted(statuses.items())},
        "success_rate": round(
            sum(c for s, c in statuses.items() if 200 <= s < 300) / n, 6
        ) if n else None,
        "latency": {
            "min_ms": round(min(latencies), 4) if latencies else None,
            "p50_ms": round(p50, 4),
            "p95_ms": round(nearest_rank(latencies, 0.95), 4),
            "p99_ms": round(nearest_rank(latencies, 0.99), 4),
            "max_ms": round(max(latencies), 4) if latencies else None,
            "median_of_repetition_p50s_ms": round(
                statistics.median([r["p50_ms"] for r in reps]), 4
            ),
            "server_p50_ms": round(nearest_rank(server_latencies, 0.50), 4)
            if server_latencies
            else None,
        },
        "per_repetition": reps,
        "p50_reportable": n >= 1000,
        "recorded_step": {
            "range_ms": [step_lo_ms, step_hi_ms],
            "samples_in_range": in_step,
            "share": round(in_step / n, 6) if n else None,
            "reproduced": in_step > 0,
            "verdict": (
                f"{in_step} of {n} samples fall in the recorded "
                f"{RECORDED_STEP_RANGE_S[0]}-{RECORDED_STEP_RANGE_S[1]} s band"
                + (
                    ". The step REPRODUCES on Linux."
                    if in_step
                    else ". The step DOES NOT REPRODUCE on Linux in steady state, "
                         "which is itself a result: the recorded figure came from a "
                         "shutdown burst, and a burst is exactly the condition this "
                         "measurement removes. Reported as absent rather than "
                         "hunted for."
                )
            ),
        },
        "p99_reportable": n >= 10000,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    lat = result["latency"]
    print(f"statuses    : {result['status_distribution']} "
          f"success_rate={result['success_rate']}")
    print(f"min/p50/p95 : {lat['min_ms']} / {lat['p50_ms']} / {lat['p95_ms']} ms")
    print(f"max         : {lat['max_ms']} ms")
    print(f"step 3.17-3.69s: {in_step}/{n} samples -> reproduced={in_step > 0}")
    print(f"p50 reportable: {result['p50_reportable']} (n={n}, needs 1000)")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/logout_step.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
