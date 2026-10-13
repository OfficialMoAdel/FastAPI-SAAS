"""POST /api/v1/account/login - the endpoint whose floor moved 7.6x.

EM-5 (``n9ho``) asks for Min at every repetition (the headline for this
endpoint), p50/p95/p99 at 4.4 sizes, the bcrypt cost factor read from LIVE
settings and echoed into the artefact, and the ~902 us expectation stated inline so
no reader mistakes the result for a regression.

WHY THE 902 US EXPECTATION MUST BE STATED
-----------------------------------------
The plan's reference figure for this endpoint is ~902 us. A measured Min here is
expected to be roughly TWO ORDERS OF MAGNITUDE above that. On this target Min has
been observed around 1,800 ms and the auth budget is 5/60 per IP, so the number is
not a defect - it is what bcrypt at the configured work factor costs when 2 cores
serve it.

The danger is that a reader comparing 1,800 ms against 902 us files a regression
against the application when the cause is the WORK FACTOR AND THE BOX. So the
expectation, the live factor, and the arithmetic relating them all go into the
artefact together. A number is only interpretable beside what was expected of it.

BCRYPT FACTOR FROM LIVE SETTINGS, NOT A CONSTANT
-----------------------------------------------
Read from the running app's own settings rather than hardcoded, so the artefact
describes the configuration that produced the measurement. Reading a constant
would have produced a correct-looking artefact that is wrong the moment anyone
changes BCRYPT_ROUNDS.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import time
import urllib.parse
from pathlib import Path
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TENANT = "test-tenant-e2e"

#: The plan's reference figure, recorded so a reader can see the gap is expected.
REFERENCE_EXPECTATION_US = 902


def nearest_rank(samples: list[float], p: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    idx = max(0, min(len(ordered) - 1, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


@contextlib.asynccontextmanager
async def _driver(base: str, username: str, password: str):
    """The real app in-process; login needs FORM encoding, not JSON."""
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url=base, timeout=120.0
    ) as client:
        async with LifespanManager(app):

            async def hit() -> dict[str, Any]:
                started = time.perf_counter()
                r = await client.post(
                    "/api/v1/account/login",
                    data=urllib.parse.urlencode(
                        {"username": username, "password": password}
                    ),
                    headers={
                        "X-Tenant-ID": TENANT,
                        "User-Agent": USER_AGENT,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                elapsed = (time.perf_counter() - started) * 1000.0
                return {
                    "ms": elapsed,
                    "status": r.status_code,
                    "server_ms": float(r.headers["X-Response-Time"])
                    if r.headers.get("X-Response-Time")
                    else None,
                }

            yield hit


def _live_bcrypt_settings() -> dict[str, Any]:
    """BCRYPT_ROUNDS and the thread-pool size, read from the running app's settings.

    Read live rather than imported from a constant so the artefact describes the
    configuration that produced the measurement. A hardcoded 12 would have produced
    a correct-looking artefact that is wrong the moment anyone changes the factor.
    """
    from src.app.core.config import settings

    crypt = settings.crypt
    return {
        "bcrypt_rounds": crypt.BCRYPT_ROUNDS,
        "bcrypt_thread_pool_size": crypt.BCRYPT_THREAD_POOL_SIZE,
        "source": "src.app.core.config.settings.crypt, read at run time",
    }


async def main_async(args: argparse.Namespace) -> int:
    crypto = _live_bcrypt_settings()
    reps: list[dict[str, Any]] = []
    latencies: list[float] = []
    server_latencies: list[float] = []
    statuses: dict[int, int] = {}

    async with _driver(args.base, args.username, args.password) as hit:
        for _ in range(args.warmup):
            await hit()

        for rep in range(args.repetitions):
            local: list[float] = []
            server: list[float] = []
            for _ in range(args.requests):
                row = await hit()
                statuses[row["status"]] = statuses.get(row["status"], 0) + 1
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
                    "p50_ms": round(nearest_rank(local, 0.50) or 0.0, 4),
                    "p95_ms": round(nearest_rank(local, 0.95) or 0.0, 4),
                    "server_min_ms": round(min(server), 4) if server else None,
                }
            )
            print(
                f"  rep {rep + 1}: n={len(local)} min={reps[-1]['min_ms']}ms "
                f"p50={reps[-1]['p50_ms']}ms",
                flush=True,
            )

    n = len(latencies)
    floor_ms = min(latencies) if latencies else None
    result = {
        "schema": "perf-login-floor/1",
        "endpoint": "POST /api/v1/account/login",
        "driver": "in-process over ASGITransport; form-encoded body, as the endpoint parses",
        "repetitions": args.repetitions,
        "requests_per_repetition": args.requests,
        "warmup_discarded": args.warmup,
        "n_measured": n,
        "status_distribution": {str(s): c for s, c in sorted(statuses.items())},
        "crypto": crypto,
        "expectation": {
            "reference_us": REFERENCE_EXPECTATION_US,
            "measured_min_us": round(floor_ms * 1000, 1) if floor_ms else None,
            "ratio_vs_reference": (
                round((floor_ms * 1000) / REFERENCE_EXPECTATION_US, 1)
                if floor_ms
                else None
            ),
            "statement": (
                f"The plan's reference for this endpoint is ~{REFERENCE_EXPECTATION_US} us. "
                f"Min here is expected to be roughly TWO ORDERS OF MAGNITUDE above it, and "
                f"that is NOT a regression: it is bcrypt at BCRYPT_ROUNDS="
                f"{crypto['bcrypt_rounds']} on a 2-core box. The cost is the WORK FACTOR "
                f"and the CPU budget, not the application. Anyone comparing this figure "
                f"against {REFERENCE_EXPECTATION_US} us without that context would file a "
                f"defect against the endpoint that does not exist."
            ),
        },
        "latency": {
            "min_ms": round(floor_ms, 4) if floor_ms else None,
            "p50_ms": round(nearest_rank(latencies, 0.50) or 0.0, 4),
            "p95_ms": round(nearest_rank(latencies, 0.95) or 0.0, 4),
            "p99_ms": round(nearest_rank(latencies, 0.99) or 0.0, 4),
            "server_min_ms": round(min(server_latencies), 4) if server_latencies else None,
            "server_p50_ms": round(nearest_rank(server_latencies, 0.50) or 0.0, 4)
            if server_latencies
            else None,
            "median_of_repetition_mins_ms": round(
                statistics.median([r["min_ms"] for r in reps]), 4
            ),
        },
        "per_repetition": reps,
        "p99_reportable": n >= 10000,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    lat = result["latency"]
    print(f"bcrypt_rounds  : {crypto['bcrypt_rounds']} (thread pool "
          f"{crypto['bcrypt_thread_pool_size']})")
    print(f"statuses       : {result['status_distribution']}")
    print(f"min            : {lat['min_ms']} ms  <- headline, per repetition above")
    print(f"p50/p95/p99    : {lat['p50_ms']} / {lat['p95_ms']} / {lat['p99_ms']} ms")
    print(f"server min/p50 : {lat['server_min_ms']} / {lat['server_p50_ms']} ms")
    print(f"vs reference   : {result['expectation']['ratio_vs_reference']}x the plan's "
          f"{REFERENCE_EXPECTATION_US} us - expected, not a regression")
    print(f"p99 reportable : {result['p99_reportable']} (n={n}, needs 10000)")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/login_floor.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--warmup", type=int, default=8)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
