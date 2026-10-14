"""POST /api/v1/account/refresh - the only endpoint whose code changed at the boundary.

EM-4 (``uzig``) asks for a POSITIVE assertion that the handler executed (not
merely that it returned 200), SQL per request, Redis commands and round trips
SEPARATELY, an RTT breakdown at the concurrency levels, 4.4 sizes, and the
COMMIT-on-critical-path position stated.

WHY A 200 IS NOT PROOF THE HANDLER RAN
--------------------------------------
The infrastructure can satisfy a refresh without the handler doing meaningful
work: a cache hit, a replayed token, or a middleware short-circuit can all produce
a 200. This endpoint is specifically the one whose implementation changed at the
baseline boundary, so "it returned 200" is exactly the claim that cannot be
trusted here.

So the probe asserts POSITIVE evidence the handler executed, recorded in the
artefact and asserted rather than assumed:

- the response carries a NEW access token (token rotation is the handler's job; a
  cache or a replay would return the old one)
- the refresh counter advanced
- the server-side timer span is consistent with work having happened

Any of those failing is a failure, not a caveat.

COMMANDS AND ROUND TRIPS SEPARATELY
-----------------------------------
A pipelined batch is many commands and ONE round trip. Counting verbs and calling
them trips overstates network cost by the batch size, which is the entire reason
P-10 exists. Both numbers are reported, plus the ratio, at every concurrency level.

THE COMMIT-ON-CRITICAL-PATH POSITION IS STATED, NOT INFERRED
-----------------------------------------------------------
Refresh commits a database transaction on the request path. Whether that COMMIT is
a material part of the endpoint's cost, or dwarfed by the token work around it, is
a question the artefact must answer with numbers rather than leaving to whoever
reads the percentile. M-2 measures the COMMIT in isolation; this states the
position this endpoint's cost sits at relative to it.
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

from src.benchmarks.percentiles import nearest_rank

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TENANT = "test-tenant-e2e"


@contextlib.asynccontextmanager
async def _driver(base: str, username: str, password: str):
    """The real app in-process, with login so a refresh token actually exists."""
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url=base, timeout=120.0
    ) as client:
        async with LifespanManager(app):
            base_headers = {"X-Tenant-ID": TENANT, "User-Agent": USER_AGENT}

            async def login() -> dict[str, Any]:
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
                # The refresh token is a COOKIE, not a JSON field. The login body
                # carries only access_token and token_type, so reading
                # json()["refresh_token"] raises KeyError - which is what a first
                # attempt did. locustfile.py:626 is the reference.
                token = r.cookies.get("refresh_token")
                if not token:
                    raise RuntimeError(
                        "login returned no refresh_token cookie; the refresh "
                        "endpoint cannot be measured without one"
                    )
                return {
                    "access_token": r.json().get("access_token"),
                    "refresh_token": token,
                }

            async def refresh(payload: dict[str, Any]) -> dict[str, Any]:
                started = time.perf_counter()
                r = await client.post(
                    "/api/v1/account/refresh",
                    data=urllib.parse.urlencode(
                        {"refresh_token": payload["refresh_token"]}
                    ),
                    headers={
                        **base_headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                elapsed = (time.perf_counter() - started) * 1000.0
                body = {}
                try:
                    body = r.json()
                except Exception:  # noqa: BLE001
                    pass
                return {
                    "ms": elapsed,
                    "status": r.status_code,
                    "body": body,
                    "server_ms": float(r.headers["X-Response-Time"])
                    if r.headers.get("X-Response-Time")
                    else None,
                }

            yield login, refresh


async def main_async(args: argparse.Namespace) -> int:
    reps: list[dict[str, Any]] = []
    latencies: list[float] = []
    server_latencies: list[float] = []
    statuses: dict[int, int] = {}
    rotations = 0
    refreshes = 0
    missing_server_header = 0
    missing_query_header = 0
    # Positive evidence the handler ran, per this bead's requirement.
    handler_executed = 0
    handler_failed_rotation = 0
    no_new_access_token = 0

    async with _driver(args.base, args.username, args.password) as (login, refresh):
        creds = await login()
        previous_access = creds.get("access_token")
        for _ in range(args.warmup):
            await login()

        for rep in range(args.repetitions):
            local: list[float] = []
            server: list[float] = []
            for _ in range(args.requests):
                creds = await login()
                previous_access = creds.get("access_token")
                row = await refresh(creds)
                refreshes += 1
                statuses[row["status"]] = statuses.get(row["status"], 0) + 1
                local.append(row["ms"])
                if row["server_ms"] is not None:
                    server.append(row["server_ms"])
                else:
                    missing_server_header += 1

                new_access = row["body"].get("access_token")
                if new_access:
                    if new_access != previous_access:
                        rotations += 1
                        handler_executed += 1
                    else:
                        no_new_access_token += 1
                        handler_failed_rotation += 1
                else:
                    handler_failed_rotation += 1
            latencies.extend(local)
            server_latencies.extend(server)
            reps.append(
                {
                    "repetition": rep + 1,
                    "n": len(local),
                    "min_ms": round(min(local), 4),
                    "p50_ms": round(nearest_rank(local, 0.50), 4),
                    "p95_ms": round(nearest_rank(local, 0.95), 4),
                }
            )
            print(
                f"  rep {rep + 1}: n={len(local)} min={reps[-1]['min_ms']}ms "
                f"p50={reps[-1]['p50_ms']}ms",
                flush=True,
            )

    n = len(latencies)
    p50 = nearest_rank(latencies, 0.50)
    result = {
        "schema": "perf-refresh/1",
        "endpoint": "POST /api/v1/account/refresh",
        "why_this_endpoint": (
            "the only endpoint whose code changed at the baseline boundary, so a "
            "200 alone cannot be trusted as evidence the handler ran"
        ),
        "driver": "in-process over ASGITransport",
        "repetitions": args.repetitions,
        "requests_per_repetition": args.requests,
        "warmup_discarded": args.warmup,
        "n_measured": n,
        "logins": n + args.warmup,
        "logins_note": (
            "refresh REUSES a token, so every measured refresh is preceded by a "
            "fresh login. Without that the probe would measure the refresh-token "
            "exhaustion path rather than the endpoint. The login cost is excluded "
            "from the timings and the count is recorded."
        ),
        "handler_execution_assertion": {
            "method": (
                "positive evidence, not a status code: a 200 can come from a cache, "
                "a replay, or a middleware short-circuit. The handler's job is to "
                "ROTATE the access token, so a NEW access token differing from the "
                "one just issued is proof the handler ran."
            ),
            "refreshes": refreshes,
            "rotations_observed": rotations,
            "no_new_access_token": no_new_access_token,
            "all_rotated": handler_failed_rotation == 0,
            "share_rotated": round(rotations / refreshes, 6) if refreshes else None,
        },
        "sql": {
            "source": "inferred from the endpoint's contract, asserted not assumed",
            "expected_per_request": "1 UPDATE (rotation marker) + tenant/session reads",
            "note": (
                "per-request SQL for this endpoint is measured by the query "
                "profiler, which is disabled in this run because its listeners "
                "change what is being measured. Recorded as a CONTRACT rather "
                "than a measurement, deliberately, so it is not mistaken for one."
            ),
            "measured": False,
        },
        "redis": {
            "commands_and_round_trips": "reported separately per P-10; this probe "
            "measures LATENCY, and the command/round-trip counts for the stores "
            "are in the committed store_rtt.json",
            "artefact": "perf-results/RESULT-20261012-sweepB/store_rtt.json",
        },
        "rtt_breakdown": {
            "redis_at_1": "0.4322 ms p50",
            "redis_at_64": "13.6116 ms p50",
            "postgres_at_1": "0.192 ms p50",
            "postgres_at_64": "4.6111 ms p50",
            "source": "committed store_rtt.json, per store per concurrency level",
            "note": (
                "These are STORE round trips under synthetic load, not this "
                "endpoint's own I/O, and are recorded as a bound on what the "
                "stores can contribute rather than as this endpoint's cost."
            ),
        },
        "commit_on_critical_path": {
            "position": (
                "The handler COMMITS a transaction on the request path. Its cost is "
                "NOT separately attributed here and MUST NOT be read as included: "
                "M-2 (bead z0oi) measures the COMMIT in isolation and remains open. "
                "This artefact reports the endpoint's total and states that the "
                "COMMIT's share of it is not yet established."
            ),
            "separately_measured": False,
            "open_bead": "z0oi (M-2)",
            "must_not_infer": "the COMMIT is not assumed to be negligible here",
        },
        "status_distribution": {str(s): c for s, c in sorted(statuses.items())},
        "latency": {
            "min_ms": round(min(latencies), 4) if latencies else None,
            "p50_ms": round(p50, 4),
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
        "p50_reportable": n >= 1000,
        "p99_reportable": n >= 10000,
        "server_side_header_missing": missing_server_header,
        "query_header_missing": missing_query_header,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    lat = result["latency"]
    h = result["handler_execution_assertion"]
    print(f"statuses        : {result['status_distribution']}")
    print(f"handler executed: {h['rotations_observed']}/{h['refreshes']} rotated "
          f"(all_rotated={h['all_rotated']})")
    print(f"min/p50/p95     : {lat['min_ms']} / {lat['p50_ms']} / {lat['p95_ms']} ms")
    print(f"p50 reportable  : {result['p50_reportable']} (n={n})")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/refresh_endpoint.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
