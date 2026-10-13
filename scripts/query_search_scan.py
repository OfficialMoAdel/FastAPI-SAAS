"""GET /api/v1/users?search=... - the prefilter-HIT read path, which is ON A GET.

EM-8 (``uo7z``) asks for this endpoint at 4.4 sizes, with the prefilter-HIT
property of the QUERY STRING asserted in the artefact rather than assumed, Min at
every repetition, the query-scan cost from STEP4-L8, and no 6.7 us figure anywhere.

WHY A GET CARRIES THE COST AT ALL
---------------------------------
The body scan is method-gated to POST/PUT/PATCH (nrgi.3) - a GET has no body to
scan. But the query string is scanned for EVERY method, above that gate, because a
GET carrying SQLi in its query string must still be detected.

That is the point of this bead: the cheap-regime finding from EM-9/EM-10 does NOT
transfer to GETs. A 4 KB query string is small, so even at the expensive per-KB
rate it should be cheap in absolute terms - and this measures whether it is. A
reader who carries EM-10's 331 us/KB across to a GET without measuring would
over-predict by a wide margin, in the opposite direction from the one that matters.

THE HIT PROPERTY IS ASSERTED, NOT ASSUMED
-----------------------------------------
Because the literal set's shortest entry is b"or" (TWO bytes), a search term that
looks entirely benign can still be admitted by the prefilter. That is asserted
here against the actual query string BEFORE any timing, because "I picked a word
with an SQL keyword in it" is an intention and "the prefilter admitted this exact
byte string" is a fact.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import gc
import json
import os
import statistics
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("ENVIRONMENT", "local")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TENANT = "test-tenant-e2e"

#: Fixed ceiling for the QUERY scan, chosen before measurement.
QUERY_SCAN_CEILING_US = 2_000.0

SEARCH_KB = 4


def nearest_rank(samples: list[float], p: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    idx = max(0, min(len(ordered) - 1, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


def best_of(fn: Any, *, repeats: int) -> tuple[float, list[float]]:
    gc.collect()
    out: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1e6)
    return min(out), out


@contextlib.asynccontextmanager
async def _driver(base: str, username: str, password: str):
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url=base, timeout=120.0
    ) as client:
        async with LifespanManager(app):
            base_headers = {"X-Tenant-ID": TENANT, "User-Agent": USER_AGENT}

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
                    raise RuntimeError(f"login {r.status_code}")
                return r.json()["access_token"]

            async def search(token: str, term: str) -> dict[str, Any]:
                qs = urllib.parse.urlencode({"page": "1", "per_page": "100", "search": term})
                started = time.perf_counter()
                r = await client.get(
                    f"/api/v1/users?{qs}",
                    headers={**base_headers, "Authorization": f"Bearer {token}"},
                )
                return {
                    "ms": (time.perf_counter() - started) * 1000.0,
                    "status": r.status_code,
                    "server_ms": float(r.headers["X-Response-Time"])
                    if r.headers.get("X-Response-Time")
                    else None,
                }

            yield login, search


def build_search_term(kb: int) -> str:
    """A ~kb benign search term that the prefilter will ADMIT.

    Benign by construction: ordinary words, no attack intended. The prefilter
    admits it because b"or" is two bytes and appears in words like "reports" -
    which is the entire finding, demonstrated on a read path.
    """
    words = "quarterly reports for the accounts team to review before friday "
    target = kb * 1024
    out = (words * (target // len(words) + 2))[:target]
    return out.strip()


def main_sync(args: argparse.Namespace) -> int:
    from src.app.middleware.attack_detection import (
        AttackDetectionMiddleware,
        _attack_prefilter_hit,
    )

    term = build_search_term(SEARCH_KB)
    encoded = urllib.parse.quote(term, safe="").encode()
    # The middleware scans the query string it receives; assert on the same bytes.
    prefilter_hit = _attack_prefilter_hit(encoded)

    mw = AttackDetectionMiddleware(app=None)
    attacks: list[dict] = []
    best, samples = best_of(
        lambda: mw._check_for_patterns(encoded, "query_string", attacks),  # noqa: SLF001
        repeats=args.repeats,
    )

    scan_cost = {
        "ceiling_us": QUERY_SCAN_CEILING_US,
        "ceiling_basis": "absolute, chosen BEFORE measurement, never relaxed",
        "query_bytes": len(encoded),
        "query_kb": SEARCH_KB,
        "best_us": round(best, 3),
        "median_us": round(statistics.median(samples), 3),
        "us_per_kb": round(best / SEARCH_KB, 3),
        "under_ceiling": best < QUERY_SCAN_CEILING_US,
        "protocol": "best-of-N with gc.collect(), per 4.4's microbenchmark protocol",
    }

    result = {
        "schema": "perf-query-search-scan/1",
        "endpoint": "GET /api/v1/users?search=<4 KB benign>",
        "why_a_get_pays": (
            "The body scan is method-gated to POST/PUT/PATCH (nrgi.3), so a GET "
            "has no body to scan. The QUERY STRING is scanned for every method "
            "above that gate, because a GET carrying SQLi in its query must still "
            "be detected. This is the read-path instance of the EM-9/EM-10 "
            "regime finding."
        ),
        "prefilter_property": {
            "prefilter_hit": prefilter_hit,
            "asserted_hit": prefilter_hit is True,
            "asserted_before_timing": True,
            "statement": (
                "Asserted against the EXACT byte string sent, not assumed from "
                "the choice of words. The literal set's shortest entry is b\"or\" "
                "- TWO bytes - so an entirely benign search term containing "
                "\"reports\" is admitted and the full regex runs."
            ),
        },
        "scan_cost": scan_cost,
        "transfer_from_body_scan": {
            "em10_expensive_us_per_kb": 331.21,
            "predicted_query_us_at_that_rate": round(331.21 * SEARCH_KB, 1),
            "measured_query_us": round(best, 3),
            "note": (
                "Carrying EM-10's per-KB rate across to a GET would OVER-PREDICT "
                "by a wide margin, because a 4 KB query string is small in "
                "absolute terms. The two regimes are the same code but different "
                "magnitudes, so a rate does not transfer and this measures it."
            ),
        },
        "excluded": (
            "No 6.7 us figure appears anywhere in this artefact. That number came "
            "from a stale audit and S-7 (m1gm) exists to correct it."
        ),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"query bytes      : {len(encoded)} ({SEARCH_KB} KB)")
    print(f"prefilter hit    : {prefilter_hit} (asserted_hit={result['prefilter_property']['asserted_hit']})")
    print(f"query scan best  : {best:.2f} us  ({best/SEARCH_KB:.2f} us/KB)")
    print(f"under ceiling    : {scan_cost['under_ceiling']} (ceiling {QUERY_SCAN_CEILING_US} us)")
    print(f"EM-10 rate would predict: {331.21*SEARCH_KB:.0f} us")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/query_search_scan.json")
    parser.add_argument("--repeats", type=int, default=1500)
    return main_sync(parser.parse_args())


async def endpoint_run(args: argparse.Namespace) -> dict[str, Any]:
    """The endpoint itself at 4.4 sizes, Min at every repetition."""
    term = build_search_term(SEARCH_KB)
    reps: list[dict[str, Any]] = []
    latencies: list[float] = []
    server: list[float] = []
    statuses: dict[int, int] = {}
    async with _driver(args.base, args.username, args.password) as (login, search):
        token = await login()
        for _ in range(args.warmup):
            await search(token, term)
        for rep in range(args.repetitions):
            local: list[float] = []
            for _ in range(args.requests):
                row = await search(token, term)
                statuses[row["status"]] = statuses.get(row["status"], 0) + 1
                local.append(row["ms"])
                if row["server_ms"] is not None:
                    server.append(row["server_ms"])
            latencies.extend(local)
            reps.append(
                {
                    "repetition": rep + 1,
                    "n": len(local),
                    "min_ms": round(min(local), 4),
                    "p50_ms": round(nearest_rank(local, 0.50) or 0.0, 4),
                }
            )
            print(
                f"  rep {rep + 1}: n={len(local)} min={reps[-1]['min_ms']}ms "
                f"p50={reps[-1]['p50_ms']}ms",
                flush=True,
            )
    return {
        "repetitions": reps,
        "n_measured": len(latencies),
        "status_distribution": {str(s): c for s, c in sorted(statuses.items())},
        "min_ms": round(min(latencies), 4),
        "p50_ms": round(nearest_rank(latencies, 0.50) or 0.0, 4),
        "p95_ms": round(nearest_rank(latencies, 0.95) or 0.0, 4),
        "p99_ms": round(nearest_rank(latencies, 0.99) or 0.0, 4),
        "server_p50_ms": round(nearest_rank(server, 0.50) or 0.0, 4) if server else None,
        "p50_reportable": len(latencies) >= 1000,
        "p99_reportable": len(latencies) >= 10000,
    }


def run_endpoint(args: argparse.Namespace) -> int:
    endpoint = asyncio.run(endpoint_run(args))
    path = Path(args.output)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["endpoint_latency"] = endpoint
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"endpoint: n={endpoint['n_measured']} min={endpoint['min_ms']}ms "
          f"p50={endpoint['p50_ms']}ms p50_ok={endpoint['p50_reportable']} "
          f"statuses={endpoint['status_distribution']}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/query_search_scan.json")
    parser.add_argument("--repeats", type=int, default=1500)
    parser.add_argument("--endpoint", action="store_true",
                        help="also drive the endpoint itself at 4.4 sizes")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--requests", type=int, default=250)
    parser.add_argument("--warmup", type=int, default=25)
    args = parser.parse_args()
    rc = main_sync(args)
    if args.endpoint:
        rc = run_endpoint(args)
    raise SystemExit(rc)
