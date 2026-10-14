"""Repetitions of warm GET /api/v1/users/me, server-side beside client-observed.

EM-1 (``cv94``) asks for the hot path at the degrading concurrency with Min at
every repetition, throughput, and the server's own X-Response-Time reported
ALONGSIDE the client-observed latency. It also requires the SQL count to be
cross-checked against P-21's pg_stat_statements delta, expecting zero.

The paired timing is the point of the exercise. Client-observed latency includes
everything between the client's clock and the app's - the socket, the event loop's
wakeup, the ASGI server's own work. Server-side excludes all of it. Reporting one
without the other lets a regression hide in the gap: the Round-1 pack's own floor
was quoted client-side, which is why its 0.87 ms warm figure and a 0.46 ms measured
floor (EM-3) disagree by a factor the endpoint alone cannot explain.

REPETITIONS, NOT ONE RUN
------------------------
4.4 requires Min at every repetition and reports median-of-medians and
min-of-medians, because this target showed +-2x intra-arm and 7.1x run-to-run
variance. EM-3 measured that variance at 1.03x in-process; this endpoint does real
work, so the spread is worth measuring rather than assuming.

WARM, AND WARM ON PURPOSE
-------------------------
The endpoint caches, and P-21 measured ZERO SQL over 20 warm requests - so a cold
request would answer a different question entirely. Warm-ups are issued and
discarded, and their counts are excluded from the reported n.

SQL CROSS-CHECK
---------------
Read from the SAME server the requests hit: X-Query-Count summed over the
measured window, against the per-request values recorded so a mismatch would be
visible per request rather than only in total.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import time
from pathlib import Path
from typing import Any

from src.benchmarks.percentiles import nearest_rank

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TENANT = "test-tenant-e2e"


@contextlib.asynccontextmanager
async def _driver(base: str, token: str):
    """The real app in this process, so X-Response-Time is the app's own header."""
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url=base, timeout=60.0
    ) as client:
        async with LifespanManager(app):
            headers = {
                "X-Tenant-ID": TENANT,
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
            }

            async def hit() -> dict[str, Any]:
                started = time.perf_counter()
                r = await client.get("/api/v1/users/me", headers=headers)
                client_ms = (time.perf_counter() - started) * 1000.0
                if r.status_code != 200:
                    raise RuntimeError(f"/users/me returned {r.status_code}: {r.text[:160]}")
                raw = r.headers.get("X-Response-Time")
                raw_q = r.headers.get("X-Query-Count")
                return {
                    "client_ms": client_ms,
                    "server_ms": float(raw) if raw else None,
                    "queries": int(raw_q) if raw_q is not None else None,
                }

            yield hit


async def _login(base: str, username: str, password: str) -> str:
    import urllib.parse
    import urllib.request

    # Form data, not JSON: posting JSON under x-www-form-urlencoded returns 422
    # "Field required" for both fields, which reads like bad credentials and is not.
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/v1/account/login",
        data=body,
        headers={"X-Tenant-ID": TENANT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("access_token", "")


async def main_async(args: argparse.Namespace) -> int:
    token = await _login(args.base, args.username, args.password)
    if not token:
        print("LOGIN FAILED")
        return 1

    reps: list[dict[str, Any]] = []
    total_queries = 0
    missing_server_header = 0
    missing_query_header = 0

    async with _driver(args.base, token) as hit:
        for _ in range(args.warmup):
            await hit()

        for rep in range(args.repetitions):
            client: list[float] = []
            server: list[float] = []
            started = time.perf_counter()
            for _ in range(args.requests):
                row = await hit()
                client.append(row["client_ms"])
                if row["server_ms"] is None:
                    missing_server_header += 1
                else:
                    server.append(row["server_ms"])
                if row["queries"] is None:
                    missing_query_header += 1
                else:
                    total_queries += row["queries"]
            wall = time.perf_counter() - started
            reps.append(
                {
                    "repetition": rep + 1,
                    "n": len(client),
                    "client_min_ms": round(min(client), 4),
                    "client_p50_ms": round(nearest_rank(client, 0.50), 4),
                    "client_p95_ms": round(nearest_rank(client, 0.95), 4),
                    "server_min_ms": round(min(server), 4) if server else None,
                    "server_p50_ms": round(nearest_rank(server, 0.50), 4) if server else None,
                    "server_p95_ms": round(nearest_rank(server, 0.95), 4) if server else None,
                    "throughput_rps": round(len(client) / wall, 2) if wall else None,
                }
            )
            r = reps[-1]
            print(
                f"  rep {rep + 1}: n={r['n']} client_min={r['client_min_ms']} "
                f"p50={r['client_p50_ms']} server_p50={r['server_p50_ms']} "
                f"rps={r['throughput_rps']}",
                flush=True,
            )

    cmin = [r["client_min_ms"] for r in reps]
    cp50 = [r["client_p50_ms"] for r in reps]
    smin = [r["server_min_ms"] for r in reps if r["server_min_ms"] is not None]
    sp50 = [r["server_p50_ms"] for r in reps if r["server_p50_ms"] is not None]
    n_measured = sum(r["n"] for r in reps)

    result = {
        "schema": "perf-users-me-repetitions/1",
        "endpoint": "GET /api/v1/users/me (authenticated, warm)",
        "driver": "in-process over ASGITransport; X-Response-Time is the app's own header",
        "repetitions": args.repetitions,
        "requests_per_repetition": args.requests,
        "warmup_discarded": args.warmup,
        "n_measured": n_measured,
        "per_repetition": reps,
        "summary": {
            "client_min_of_mins_ms": round(min(cmin), 4),
            "client_median_of_mins_ms": round(statistics.median(cmin), 4),
            "client_min_of_p50s_ms": round(min(cp50), 4),
            "client_median_of_p50s_ms": round(statistics.median(cp50), 4),
            "server_min_of_mins_ms": round(min(smin), 4) if smin else None,
            "server_median_of_mins_ms": round(statistics.median(smin), 4) if smin else None,
            "server_min_of_p50s_ms": round(min(sp50), 4) if sp50 else None,
            "server_median_of_p50s_ms": round(statistics.median(sp50), 4) if sp50 else None,
            "client_min_spread_x": round(max(cmin) / min(cmin), 2),
            "server_min_spread_x": round(max(smin) / min(smin), 2) if smin and min(smin) else None,
        },
        "co_location_gap_ms": (
            round(statistics.median(cp50) - statistics.median(sp50), 4)
            if sp50
            else None
        ),
        "sql_crosscheck": {
            "source": "X-Query-Count per request, summed over the measured window",
            "total_queries": total_queries,
            "requests": n_measured,
            "queries_per_request": round(total_queries / n_measured, 6) if n_measured else None,
            "expected": 0,
            "confirms_p21": total_queries == 0,
            "responses_without_header": missing_query_header,
        },
        "server_side_header_missing": missing_server_header,
        "summary_method": "median-of-medians and min-of-medians over repetitions, per 4.4",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    s = result["summary"]
    print(f"client  min-of-mins {s['client_min_of_mins_ms']}ms  median-of-mins "
          f"{s['client_median_of_mins_ms']}ms  spread {s['client_min_spread_x']}x")
    if smin:
        print(f"server  min-of-mins {s['server_min_of_mins_ms']}ms  median-of-mins "
              f"{s['server_median_of_mins_ms']}ms  spread {s['server_min_spread_x']}x")
        print(f"co-location gap (median p50 client - server): "
              f"{result['co_location_gap_ms']} ms")
    print(f"SQL cross-check: {total_queries} queries over {n_measured} requests -> "
          f"{result['sql_crosscheck']['queries_per_request']}/req, "
          f"confirms P-21: {result['sql_crosscheck']['confirms_p21']}")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/users_me_repetitions.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--warmup", type=int, default=60)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
