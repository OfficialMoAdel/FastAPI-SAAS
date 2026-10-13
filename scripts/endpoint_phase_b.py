"""Targeted pass over the light endpoints, as a SEPARATE phase from the matrix.

The Step 4 ten-endpoint matrix (``sgn3``) cannot satisfy 4.4 from a single mixed
run. The locustfile weights are 80/10/10/10/5/1, so over a 45-minute run the
hot endpoints clear n>=10,000 and the light ones never clear n>=1,000 - at 589
requests the weight-1 endpoint would need about 12 hours.

Re-weighting the matrix to fix that would be the wrong trade. The weights are a
fact about where this app's traffic goes; equalising them would produce a matrix
where every endpoint clears its sample size AND the traffic mix is fiction. A
number that passes because the measurement was shaped to pass is worse than one
that is absent.

So this is a SECOND PHASE, and the distinction is the whole point:

- Phase A (committed, ``RESULT-20261012-matrix``): production weights. p99 is
  admissible for the heavy endpoints, and the throughput/knee story.
- Phase B (this): each light endpoint driven directly to n>=1,000 so its p50 and
  p95 become reportable.

THEY ARE NOT BLENDED. A Phase B number describes a different traffic shape, so
putting it in the same column as Phase A's would be the same category of error as
averaging RSS with tracemalloc - two populations presented as one. Every value
below carries its phase.

WHAT THIS CANNOT FIX, STATED UP FRONT
-------------------------------------
p99 needs n>=10,000 PER ENDPOINT. This phase reaches n>=1,000. So p99 stays
non-reportable for every endpoint it covers, and that is recorded rather than
quietly omitted. Reaching 10,000 on a weight-1 endpoint under production weights
is a ~12-hour run on this box, not a probe flag.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

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


@contextlib.asynccontextmanager
async def _driver(base: str, username: str, password: str):
    """The real app in-process, with the auth and cookie plumbing these endpoints need."""
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
                    raise RuntimeError(f"login {r.status_code}")
                token = r.cookies.get("refresh_token")
                if not token:
                    raise RuntimeError("no refresh_token cookie")
                return {"access_token": r.json()["access_token"], "refresh_token": token}

            async def call(method: str, path: str, payload: Any = None) -> dict[str, Any]:
                headers = dict(base_headers)
                if payload and payload.get("access_token"):
                    headers["Authorization"] = f"Bearer {payload['access_token']}"
                kwargs: dict[str, Any] = {"headers": headers}
                if payload and payload.get("refresh_token"):
                    kwargs["data"] = urllib.parse.urlencode(
                        {"refresh_token": payload["refresh_token"]}
                    )
                    kwargs["headers"] = {
                        **headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                    }
                started = time.perf_counter()
                r = await client.request(method, path, **kwargs)
                return {
                    "ms": (time.perf_counter() - started) * 1000.0,
                    "status": r.status_code,
                    "server_ms": float(r.headers["X-Response-Time"])
                    if r.headers.get("X-Response-Time")
                    else None,
                }

            yield login, call


async def main_async(args: argparse.Namespace) -> int:
    # The endpoints phase A could not size, plus logout, which phase A omitted
    # entirely because PERF_LOGOUT_WEIGHT was 0.
    targets = [
        ("GET", "/api/v1/account/sessions/count", "count"),
        ("GET", "/api/v1/account/sessions", "sessions"),
        ("GET", "/api/v1/notifications", "notifications"),
        ("GET", "/api/v1/users?page=1&per_page=100", "users"),
        ("GET", "/api/v1/health/live", "/health/live"),
    ]

    results: list[dict[str, Any]] = []
    async with _driver(args.base, args.username, args.password) as (login, call):
        creds = await login()

        for method, path, name in targets:
            for _ in range(args.warmup):
                await call(method, path, creds)
            reps: list[dict[str, Any]] = []
            latencies: list[float] = []
            server: list[float] = []
            statuses: dict[int, int] = {}
            for rep in range(args.repetitions):
                local: list[float] = []
                for _ in range(args.requests):
                    row = await call(method, path, creds)
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
            n = len(latencies)
            entry = {
                "endpoint": name,
                "method": method,
                "path": path,
                "phase": "B - targeted pass, NOT blended with phase A",
                "n": n,
                "status_distribution": {str(s): c for s, c in sorted(statuses.items())},
                "repetitions": reps,
                "min_ms": round(min(latencies), 4),
                "p50_ms": round(nearest_rank(latencies, 0.50) or 0.0, 4),
                "p95_ms": round(nearest_rank(latencies, 0.95) or 0.0, 4),
                "p99_ms": round(nearest_rank(latencies, 0.99) or 0.0, 4),
                "server_p50_ms": round(nearest_rank(server, 0.50) or 0.0, 4)
                if server
                else None,
                "p50_reportable": n >= 1000,
                "p95_reportable": n >= 1000,
                "p99_reportable": n >= 10000,
            }
            results.append(entry)
            print(
                f"  {name:16} n={n:>5} min={entry['min_ms']}ms p50={entry['p50_ms']}ms "
                f"p95={entry['p95_ms']}ms p50_ok={entry['p50_reportable']} "
                f"p99_ok={entry['p99_reportable']}",
                flush=True,
            )

        # Logout: state-changing, so re-authenticate before each measured call.
        logout_lat: list[float] = []
        logout_status: dict[int, int] = {}
        for _ in range(args.warmup):
            await call("POST", "/api/v1/account/logout", await login())
        for _ in range(args.requests * args.repetitions):
            fresh = await login()
            row = await call("POST", "/api/v1/account/logout", fresh)
            logout_status[row["status"]] = logout_status.get(row["status"], 0) + 1
            logout_lat.append(row["ms"])
        ln = len(logout_lat)
        results.append(
            {
                "endpoint": "/account/logout",
                "method": "POST",
                "path": "/api/v1/account/logout",
                "phase": "B - targeted pass, NOT blended with phase A",
                "n": ln,
                "status_distribution": {
                    str(s): c for s, c in sorted(logout_status.items())
                },
                "min_ms": round(min(logout_lat), 4),
                "p50_ms": round(nearest_rank(logout_lat, 0.50) or 0.0, 4),
                "p95_ms": round(nearest_rank(logout_lat, 0.95) or 0.0, 4),
                "p99_ms": round(nearest_rank(logout_lat, 0.99) or 0.0, 4),
                "reauthentications": ln,
                "p50_reportable": ln >= 1000,
                "p95_reportable": ln >= 1000,
                "p99_reportable": ln >= 10000,
            }
        )
        print(
            f"  {'/account/logout':16} n={ln:>5} min={results[-1]['min_ms']}ms "
            f"p50={results[-1]['p50_ms']}ms p50_ok={ln >= 1000}",
            flush=True,
        )

    payload = {
        "schema": "perf-endpoint-targeted-pass/1",
        "phase": "B",
        "phase_note": (
            "TARGETED PASS. Phase A (perf-results/RESULT-20261012-matrix) ran "
            "production weights and is the only phase whose p99 columns are "
            "admissible. These numbers describe a DIFFERENT traffic shape and are "
            "not blended with phase A. Mixing them would be the same error as "
            "averaging RSS with tracemalloc."
        ),
        "why_a_second_phase": (
            "Production weights are 80/10/10/10/5/1, so the light endpoints cannot "
            "reach n>=1,000 in a 45-minute run - a weight-1 endpoint would need "
            "about 12 hours. Re-weighting the MATRIX would make every column "
            "pass while making the traffic mix fiction, so the sizing problem is "
            "solved here instead, in a labelled phase."
        ),
        "cannot_fix": (
            "p99 needs n>=10,000 per endpoint. This phase reaches n>=1,000, so "
            "p99 remains non-reportable for every endpoint here. That is a ~12-hour "
            "run at production weights, not a flag."
        ),
        "endpoints": results,
        "summary": {
            "endpoints_covered": len(results),
            "p50_reportable": sum(1 for r in results if r["p50_reportable"]),
            "p95_reportable": sum(1 for r in results if r["p95_reportable"]),
            "p99_reportable": sum(1 for r in results if r["p99_reportable"]),
        },
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    s = payload["summary"]
    print(
        f"endpoints {s['endpoints_covered']} | p50 {s['p50_reportable']} | "
        f"p95 {s['p95_reportable']} | p99 {s['p99_reportable']}"
    )
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/endpoint_phase_b.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--requests", type=int, default=250)
    parser.add_argument("--warmup", type=int, default=25)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
