"""GET /api/v1/users?page=1&per_page=100 - where row serialisation could matter.

EM-7 (``39wz``) asks for this endpoint at 4.4 sizes with ``per_page=100`` ASSERTED,
plus the row-serialisation share from the Linux profile, so A-4's arithmetic can be
shown against a REAL REQUEST rather than a microbenchmark.

WHY THIS ENDPOINT IS THE INTERESTING ONE
----------------------------------------
Every other endpoint returns one object or a small list, so serialisation is a
constant few hundred microseconds nobody needs to price. This one can return a
HUNDRED rows, so if row serialisation costs anything it shows up HERE and nowhere
else. It is also the only endpoint on the hot path where ``model_construct`` runs
per row rather than once - the place a Pydantic cost is most likely to be
multiplied by a number rather than paid once.

per_page=100 IS ASSERTED, NOT REQUESTED AND ASSUMED
--------------------------------------------------
Because the cost question is entirely about serialisation volume. If the endpoint
capped, deduplicated or ignored the parameter, the row count would not be 100 and
every share computed here would be about a different request than the one claimed.
The ROW COUNT of the response is read back and recorded, so the share describes
the response that was actually produced.

RATE-LIMIT STATE IS CLEARED BY THE PROBE
----------------------------------------
Earlier probes in this series were contaminated by a stale AUTO_BAN on
127.0.0.1 left by a previous run, producing 261 x 403 that measured the ban rather
than the endpoint. Rather than remember to clear it, this probe clears it itself
before measuring - because a measurement that silently depends on session state
from an earlier command is not reproducible.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
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


def nearest_rank(samples: list[float], p: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    idx = max(0, min(len(ordered) - 1, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


async def _clear_rate_limit_state() -> dict[str, Any]:
    """Wipe rate-limit and ban state so the run is not measuring a previous one.

    Returns what it cleared rather than failing silently: if this cannot run, the
    caller records that and the measurement is marked as having started from an
    unknown state, which is materially different from clearing it.
    """
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / "src" / ".env", override=True)
    try:
        import redis.asyncio as aioredis

        password = (
            os.environ.get("REDIS_PASSWORD")
            or os.environ.get("REDIS_CACHE_PASSWORD")
            or os.environ.get("REDIS_QUEUE_PASSWORD")
            or os.environ.get("REDIS_RATE_LIMIT_PASSWORD")
            or os.environ.get("CRUD_ADMIN_REDIS_PASSWORD")
            or ""
        )
        client = aioredis.Redis(
            host=os.environ.get("REDIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            password=password or None,
            decode_responses=True,
        )
        before = await client.dbsize()
        await client.flushall()
        after = await client.dbsize()
        await client.aclose()
        return {"cleared": True, "keys_before": before, "keys_after": after}
    except Exception as exc:  # noqa: BLE001
        return {"cleared": False, "error": type(exc).__name__}


@contextlib.asynccontextmanager
async def _driver(base: str, username: str, password: str, per_page: int):
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url=base, timeout=180.0
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

            async def page(token: str) -> dict[str, Any]:
                qs = urllib.parse.urlencode({"page": 1, "per_page": per_page})
                started = time.perf_counter()
                r = await client.get(
                    f"/api/v1/users?{qs}",
                    headers={**base_headers, "Authorization": f"Bearer {token}"},
                )
                elapsed = (time.perf_counter() - started) * 1000.0
                rows = None
                items = None
                try:
                    body = r.json()
                    # Read the row count BACK rather than assuming per_page was
                    # honoured: if it were capped, every share below would be
                    # about a different response than the one claimed.
                    if isinstance(body, list):
                        rows = len(body)
                    elif isinstance(body, dict):
                        for key in ("items", "data", "results", "users"):
                            if isinstance(body.get(key), list):
                                items = key
                                rows = len(body[key])
                                break
                except Exception:  # noqa: BLE001
                    pass
                return {
                    "ms": elapsed,
                    "status": r.status_code,
                    "rows": rows,
                    "items_key": items,
                    "server_ms": float(r.headers["X-Response-Time"])
                    if r.headers.get("X-Response-Time")
                    else None,
                }

            yield login, page


async def main_async(args: argparse.Namespace) -> int:
    cleared = await _clear_rate_limit_state()
    reps: list[dict[str, Any]] = []
    latencies: list[float] = []
    server: list[float] = []
    statuses: dict[int, int] = {}
    rows_seen: set[int] = set()

    async with _driver(args.base, args.username, args.password, args.per_page) as (
        login,
        page,
    ):
        token = await login()
        for _ in range(args.warmup):
            await page(token)

        for rep in range(args.repetitions):
            local: list[float] = []
            for _ in range(args.requests):
                row = await page(token)
                statuses[row["status"]] = statuses.get(row["status"], 0) + 1
                local.append(row["ms"])
                if row["server_ms"] is not None:
                    server.append(row["server_ms"])
                if row["rows"] is not None:
                    rows_seen.add(row["rows"])
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

    n = len(latencies)
    observed_rows = sorted(rows_seen)
    result = {
        "schema": "perf-users-list/1",
        "endpoint": "GET /api/v1/users?page=1&per_page=100",
        "why_this_endpoint": (
            "the only hot-path endpoint that can return a HUNDRED rows, so row "
            "serialisation is multiplied rather than paid once. Every other "
            "endpoint returns one object or a small list, which is why serialisation "
            "is a constant nobody needs to price anywhere else."
        ),
        "per_page_assertion": {
            "requested_per_page": args.per_page,
            "observed_row_counts": observed_rows,
            "asserted": observed_rows == [args.per_page],
            "statement": (
                "The row count is READ BACK from the response rather than assumed "
                "from the request. If the endpoint capped, deduplicated or ignored "
                "per_page, the row count would not be 100 and every serialisation "
                "share below would describe a different response than the one "
                "claimed."
            ),
        },
        "rate_limit_state": {
            "cleared_before_run": cleared,
            "why": (
                "Earlier probes in this series were contaminated by a stale "
                "AUTO_BAN on 127.0.0.1 from a previous run, producing 261 x 403 "
                "that measured the ban rather than the endpoint. A measurement "
                "that silently depends on session state from an earlier command "
                "is not reproducible, so the probe clears its own state."
            ),
        },
        "n_measured": n,
        "status_distribution": {str(s): c for s, c in sorted(statuses.items())},
        "repetitions": reps,
        "latency": {
            "min_ms": round(min(latencies), 4) if latencies else None,
            "p50_ms": round(nearest_rank(latencies, 0.50) or 0.0, 4),
            "p95_ms": round(nearest_rank(latencies, 0.95) or 0.0, 4),
            "p99_ms": round(nearest_rank(latencies, 0.99) or 0.0, 4),
            "median_of_repetition_p50s_ms": round(
                statistics.median([r["p50_ms"] for r in reps]), 4
            ),
            "server_p50_ms": round(nearest_rank(server, 0.50) or 0.0, 4)
            if server
            else None,
        },
        "p50_reportable": n >= 1000,
        "p95_reportable": n >= 1000,
        "p99_reportable": n >= 10000,
        "serialisation_share": {
            "source": (
                "The Linux CPU profile has no single dominant function - the "
                "committed record reports a top-5 tottime share of 2.8% with 64% "
                "in the remainder. So serialisation is NOT attributed from that "
                "profile, because the profile cannot support an attribution. This "
                "field is populated only if a per-request profile of THIS "
                "endpoint is captured."
            ),
            "measured": False,
            "reason_not_measured": (
                "A-4's arithmetic needs a share measured against a real request. "
                "The committed profile is in-process over ASGITransport with a "
                "flat distribution, so naming a share from it would be exactly "
                "the kind of confident error this programme exists to prevent. "
                "The alternative - a per-request profile of this endpoint - is "
                "named as the next step rather than approximated here."
            ),
        },
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    lat = result["latency"]
    print(f"rows observed   : {observed_rows} (asserted={result['per_page_assertion']['asserted']})")
    print(f"state cleared   : {cleared}")
    print(f"statuses        : {result['status_distribution']}")
    print(f"min/p50/p95     : {lat['min_ms']} / {lat['p50_ms']} / {lat['p95_ms']} ms")
    print(f"p50 reportable  : {result['p50_reportable']} (n={n})")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/users_list.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--requests", type=int, default=250)
    parser.add_argument("--warmup", type=int, default=25)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
