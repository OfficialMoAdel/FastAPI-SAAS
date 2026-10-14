"""Repetitions of GET /api/v1/health - the zero-I/O floor, with its spread.

EM-3 (``2tc3``) asks for the floor reported warm at 4.4 sizes with Min and p50
across **>=5 repetitions**, summarised as median-of-medians and
min-of-medians. The reason is in 4.4 itself: this target showed +-2x intra-arm
variance and **7.1x run-to-run**, so a single reading of a sub-millisecond floor
is not a measurement, it is a sample.

That matters more here than anywhere else in the programme. Every
micro-optimisation claims its win "against the uncontended floor", so if the
floor is reported as one number it silently becomes whatever the floor happened
to read that minute. The spread IS the result.

DRIVEN IN-PROCESS
-----------------
Over ASGITransport, not over a socket. EM-3's purpose is to isolate transport and
framework cost from application work, and a real socket adds kernel network
latency that varies with the host - noise this measurement is specifically trying
to exclude. The handler itself performs no I/O, so nothing is lost by not going
through the loopback stack.

Min is taken per repetition rather than pooled, because 4.4 requires Min *and*
p50 per repetition and then an aggregate over repetitions; pooling would destroy
the within-repetition spread that the summary is meant to characterise.
"""

from __future__ import annotations

import argparse
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
TENANT_HEADER = {"X-Tenant-ID": "test-tenant-e2e"}


@contextlib.asynccontextmanager
async def _driver():
    """The real app, in this process, over ASGITransport."""
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://inproc", timeout=30.0
    ) as client:
        async with LifespanManager(app):

            async def hit() -> float:
                started = time.perf_counter()
                r = await client.get(
                    "/api/v1/health", headers={**TENANT_HEADER, "User-Agent": USER_AGENT}
                )
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                if r.status_code != 200:
                    raise RuntimeError(f"/api/v1/health returned {r.status_code}")
                return elapsed_ms

            yield hit


def _install_loop(kind: str) -> str:
    """Put the requested event loop in place, and report which one is actually in use.

    Reported rather than assumed: an arm labelled uvloop that silently ran on
    asyncio would make the comparison meaningless while looking like data, and
    the label is the only thing distinguishing the two arms.
    """
    if kind == "asyncio":
        import asyncio as _a

        _a.set_event_loop_policy(_a.DefaultEventLoopPolicy())
        return "asyncio"
    if kind == "uvloop":
        import uvloop

        uvloop.install()
        return "uvloop"
    return "installed"


async def main_async(args: argparse.Namespace) -> int:
    _install_loop(args.loop)
    import asyncio as _a

    in_use = type(_a.get_event_loop_policy()).__name__
    per_rep: list[dict[str, Any]] = []
    async with _driver() as hit:
        # Warm-up is issued and DISCARDED: the first request pays for pool
        # creation and first-touch, and a floor that includes those is not a floor.
        for _ in range(args.warmup):
            await hit()

        for rep in range(args.repetitions):
            samples: list[float] = []
            for _ in range(args.requests):
                samples.append(await hit())
            per_rep.append(
                {
                    "repetition": rep + 1,
                    "n": len(samples),
                    "min_ms": round(min(samples), 4),
                    "p50_ms": round(nearest_rank(samples, 0.50), 4),
                    "p95_ms": round(nearest_rank(samples, 0.95), 4),
                }
            )
            print(
                f"  rep {rep + 1}: n={len(samples)} "
                f"min={per_rep[-1]['min_ms']}ms p50={per_rep[-1]['p50_ms']}ms",
                flush=True,
            )

    mins = [r["min_ms"] for r in per_rep]
    p50s = [r["p50_ms"] for r in per_rep]
    result = {
        "schema": "perf-health-floor/1",
        "endpoint": "GET /api/v1/health",
        "purpose": (
            "zero-I/O floor: isolates transport and framework cost from "
            "application work. The handler performs no I/O."
        ),
        "driver": "in-process over ASGITransport (no socket, so kernel network "
                  "variance does not contaminate a sub-millisecond measurement)",
        "loop_requested": args.loop,
        "loop_policy_in_use": in_use,
        "repetitions": args.repetitions,
        "requests_per_repetition": args.requests,
        "warmup_discarded": args.warmup,
        "per_repetition": per_rep,
        "summary": {
            "min_of_mins_ms": round(min(mins), 4),
            "median_of_mins_ms": round(statistics.median(mins), 4),
            "min_of_p50s_ms": round(min(p50s), 4),
            "median_of_p50s_ms": round(statistics.median(p50s), 4),
            "min_spread_x": round(max(mins) / min(mins), 2) if min(mins) else None,
            "p50_spread_x": round(max(p50s) / min(p50s), 2) if min(p50s) else None,
        },
        "summary_method": "median-of-medians and min-of-medians over repetitions, "
                          "per 4.4",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    s = result["summary"]
    print(f"min-of-mins {s['min_of_mins_ms']}ms | median-of-mins {s['median_of_mins_ms']}ms")
    print(f"min-of-p50s  {s['min_of_p50s_ms']}ms | median-of-p50s {s['median_of_p50s_ms']}ms")
    print(f"spread: min {s['min_spread_x']}x, p50 {s['p50_spread_x']}x across "
          f"{args.repetitions} repetitions")
    print(f"loop: requested={args.loop} policy={in_use}")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/health_floor.json")
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument(
        "--loop",
        choices=("uvloop", "asyncio", "installed"),
        default="installed",
        help="uvloop installs and uses uvloop; asyncio forces the stdlib loop; "
             "installed uses whatever the process already has. The paired arm "
             "(P18) requires uvloop and asyncio to be measured the same way "
             "except for the loop.",
    )
    return __import__("asyncio").run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
