"""Row-serialisation share for GET /api/v1/users?per_page=100, measured per request.

A-4's arithmetic needs to know what fraction of this endpoint's cost is row
SERIALISATION rather than everything else, and it must come from a REAL REQUEST
rather than a microbenchmark of a serialiser in isolation.

Why not reuse the committed CPU profile
---------------------------------------
Because that profile has a top-5 tottime share of 2.8% with 64% in the remainder -
it is nearly flat. A flat profile cannot attribute cost to serialisation without
inventing the attribution. Naming a share from it would be exactly the confident
error this programme exists to prevent, so this profiles THIS endpoint and THIS
request shape instead.

METHOD, AND ITS LIMITS STATED
-----------------------------
cProfile around in-process requests over ASGITransport, attributing tottime to
frames by module. Three limits are recorded rather than glossed:

- cProfile inflates everything it measures, so the SHARE is the figure and the
  absolute numbers are not a latency measurement. `distortion_x` is reported so
  the inflation is visible.
- Attribution is by module prefix, so a frame is counted once, to the most
  specific module that owns it. Overlap between pydantic and the app's own
  serializers is resolved by first match in a stated order.
- The share is for THIS response shape: 100 rows. It is not a per-row cost and
  must not be divided out without a second measurement at another row count.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import cProfile
import json
import os
import pstats
import sys
import time
import urllib.parse
from collections import defaultdict
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

#: Attribution buckets, checked in this ORDER so a frame is counted exactly once.
#: Serialisation is split into its own three buckets rather than one, because
#: "serialisation" means different things and A-4's arithmetic needs to know
#: which is which.
BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("serialisation_pydantic", ("pydantic",)),
    ("serialisation_orm", ("sqlalchemy/orm", "sqlalchemy/relationships", "sqlalchemy/engine")),
    ("serialisation_schema", ("fastapi/encoders", "fastapi/routing")),
    ("serialisation_orjson", ("orjson",)),
    ("app_models", ("src/app/modules/users/models", "src/app/core/db/models")),
    ("app_middleware", ("src/app/middleware",)),
    ("app_core", ("src/app/core",)),
    ("framework", ("starlette", "anyio", "fastapi")),
    ("driver", ("httpx",)),
)


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
            headers = {"X-Tenant-ID": TENANT, "User-Agent": USER_AGENT}
            r = await client.post(
                "/api/v1/account/login",
                data=urllib.parse.urlencode(
                    {"username": username, "password": password}
                ),
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                raise RuntimeError(f"login {r.status_code}")
            token = r.json()["access_token"]
            qs = urllib.parse.urlencode({"page": 1, "per_page": per_page})
            auth = {**headers, "Authorization": f"Bearer {token}"}

            async def hit() -> int:
                resp = await client.get(f"/api/v1/users?{qs}", headers=auth)
                return resp.status_code

            yield hit


def attribute(stats: pstats.Stats) -> dict[str, Any]:
    """Bucket tottime by module, counting each frame exactly once."""
    totals: dict[str, float] = defaultdict(float)
    frames: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (filename, lineno, func), (cc, nc, tt, ct, _callers) in stats.stats.items():
        path = filename.replace("\\", "/")
        placed = False
        for bucket, needles in BUCKETS:
            if any(n in path for n in needles):
                totals[bucket] += tt
                frames[bucket].append(
                    {
                        "site": f"{path}:{lineno}({func})",
                        "tottime_s": round(tt, 6),
                        "calls": nc,
                    }
                )
                placed = True
                break
        if not placed:
            totals["other"] += tt
            frames["other"].append(
                {"site": f"{path}:{lineno}({func})", "tottime_s": round(tt, 6), "calls": nc}
            )
    total = sum(totals.values()) or 1.0
    return {
        "total_tottime_s": round(total, 6),
        "buckets": {
            k: {
                "tottime_s": round(v, 6),
                "share": round(v / total, 6),
                "top_frames": sorted(frames[k], key=lambda f: -f["tottime_s"])[:4],
            }
            for k, v in sorted(totals.items(), key=lambda kv: -kv[1])
        },
        "serialisation_share": round(
            sum(v for k, v in totals.items() if k.startswith("serialisation")) / total, 6
        ),
    }


async def main_async(args: argparse.Namespace) -> int:
    async with _driver(args.base, args.username, args.password, args.per_page) as hit:
        for _ in range(args.warmup):
            await hit()

        profiler = cProfile.Profile()
        profiler.enable()
        started = time.perf_counter()
        for _ in range(args.requests):
            status = await hit()
        wall = time.perf_counter() - started
        profiler.disable()

    stats = pstats.Stats(profiler)
    result = attribute(stats)

    # Distortion: how much the profiler inflated this shape, measured by timing
    # the same calls with and without it. The SHARE is the figure; this makes the
    # absolute numbers honest about being inflated.
    async with _driver(args.base, args.username, args.password, args.per_page) as hit:
        for _ in range(args.warmup):
            await hit()
        clean_started = time.perf_counter()
        for _ in range(args.requests):
            await hit()
        clean_wall = time.perf_counter() - clean_started

    payload = {
        "schema": "perf-users-list-serialisation/1",
        "endpoint": f"GET /api/v1/users?page=1&per_page={args.per_page}",
        "requests_profiled": args.requests,
        "per_page": args.per_page,
        "profiler": "cProfile, in-process over ASGITransport",
        "clean_ms_per_request": round(clean_wall / args.requests * 1000, 4),
        "profiled_ms_per_request": round(wall / args.requests * 1000, 4),
        "distortion_x": round(wall / clean_wall, 3) if clean_wall else None,
        "distortion_note": (
            "cProfile inflates everything it measures. The SHARE below is the "
            "figure; the absolute tottimes are not a latency measurement and are "
            "reported only so the inflation is visible."
        ),
        "status": status,
        "attribution": result,
        "method_limits": [
            "buckets are checked in a stated order so each frame is counted once",
            "the share is for a 100-row response and is NOT a per-row cost; "
            "dividing it out would need a second measurement at another row count",
            "in-process ASGI transport, so transport cost is absent from all "
            "buckets - the share is of SERVER work only",
        ],
        "why_not_the_committed_profile": (
            "The committed Linux profile has a top-5 tottime share of 2.8% with "
            "64% in the remainder - it is nearly flat and cannot support an "
            "attribution. This profiles THIS endpoint at THIS row count instead of "
            "naming a share from a profile that does not carry one."
        ),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    a = result
    print(f"distortion      : {payload['distortion_x']}x  "
          f"(clean {payload['clean_ms_per_request']} ms/req)")
    print(f"SERIALISATION SHARE: {a['serialisation_share']:.1%}")
    for bucket, v in a["buckets"].items():
        print(f"  {bucket:26} {v['share']:>7.2%}  {v['tottime_s']:.4f}s")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/users_list_serialisation.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--requests", type=int, default=60)
    parser.add_argument("--warmup", type=int, default=15)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
