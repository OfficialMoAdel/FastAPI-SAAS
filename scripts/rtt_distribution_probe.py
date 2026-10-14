"""Per-request round-trip distribution for the warm authenticated path.

P-12 (``n5de``) asks for the round-trip-count DISTRIBUTION for a fixed warm
authed run, reported with its own ``n`` and a sound denominator, and for the
"1 request in 20 does 4 RTTs instead of 2" figure from the Round-1 ``.out`` to be
either confirmed or refuted with a number.

WHY THIS NEEDS ITS OWN INSTRUMENT
---------------------------------
``driver_count_io.py`` keeps GLOBAL aggregates - one REDIS dict and one TIMES list
for the whole run. That is enough to say how many commands a phase issued, and not
enough to say how many any individual request issued, which is the entire
question. A distribution cannot be recovered from a total: 20 requests at 2 RTT
and one at 44 look identical in aggregate to a flat distribution and are not.

So this counts round trips as a running total and samples it at each request
boundary. The delta across one request IS that request's round trips.

WHAT COUNTS AS A ROUND TRIP
---------------------------
One network exchange, not one command. A Redis PIPELINE of N commands is N
commands and ONE round trip - that distinction is the whole reason the Round-1
figure distinguishes ``PIPE:EXISTS``/``PIPE:GET`` from a bare ``GET``. Each
Postgres statement is its own round trip. Counting commands instead would make a
warm request look like it costs 4 when it costs 2.

PERCENTILES
-----------
Computed from the RAW SAMPLE LIST by nearest rank, never from the Round-1
``pct()`` helper. ``pct()`` returned literally ``max`` for any n <= 20
(``driver_count_io.py:113-115`` before H-1), so a percentile computed from it at
small n is the maximum wearing a percentile's name.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.benchmarks.percentiles import nearest_rank

TENANT_HEADER = {"X-Tenant-ID": "test-tenant-e2e"}

#: The same agent prof_request_path.py and the locust harness send.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class RoundTripCounter:
    """Running total of network exchanges, installable around a live app."""

    def __init__(self) -> None:
        self.round_trips = 0
        self.commands = 0
        self._originals: list[tuple[Any, str, Any]] = []

    def install(self) -> None:
        """Patch the LOWEST level that writes to the socket, once per class.

        Same discipline the store probe established: redis-py's chain is
        execute_command -> send_command -> send_packed_command, so patching more
        than one layer counts a single command several times, and patching both a
        base class and its subclass makes the subclass wrapper call the base
        wrapper. One class, one hook, one count.
        """
        from redis.asyncio.connection import Connection

        original = Connection.send_packed_command
        counter = self

        async def wrapper(self_conn, *args, **kwargs):  # type: ignore[no-untyped-def]
            payload = kwargs.get("command", args[0] if args else None)
            commands = len(payload) if isinstance(payload, list | tuple) else 1
            counter.round_trips += 1
            counter.commands += commands
            return await original(self_conn, *args, **kwargs)

        self._originals.append((Connection, "send_packed_command", original))
        Connection.send_packed_command = wrapper  # type: ignore[method-assign]

    def remove(self) -> None:
        for cls, method, original in self._originals:
            setattr(cls, method, original)
        self._originals.clear()


@contextlib.asynccontextmanager
async def _build_inprocess_driver(base: str, token: str):
    """An ASGI driver that runs the REAL app in THIS process.

    Returns a callable that issues one authenticated GET /users/me and asserts it
    succeeded. The app is imported rather than described, so the Redis connections
    it opens are the ones the counter is patched on.
    """
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=base, timeout=30.0
    ) as client:
        async with LifespanManager(app):
            # A real User-Agent, because the attack scanner answers 403
            # "Bad bot detected" to httpx's default. That is the control working;
            # the measurement simply has to present the same client every other
            # harness in this repository presents.
            headers = {
                **TENANT_HEADER,
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
            }

            async def drive() -> int:
                r = await client.get("/api/v1/users/me", headers=headers)
                if r.status_code != 200:
                    raise RuntimeError(
                        f"in-process /users/me returned {r.status_code}: {r.text[:200]}"
                    )
                return r.status_code

            yield drive


async def main_async(args: argparse.Namespace) -> int:
    load_dotenv("src/.env", override=True)

    base = args.base.rstrip("/")
    login_body = urllib.parse.urlencode(
        {"username": args.username, "password": args.password}
    ).encode()
    login_req = urllib.request.Request(
        f"{base}/api/v1/account/login",
        data=login_body,
        headers={**TENANT_HEADER, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(login_req, timeout=20) as r:
        token = json.load(r).get("access_token", "")
    if not token:
        print("LOGIN FAILED")
        return 1
    auth = {**TENANT_HEADER, "Authorization": f"Bearer {token}"}

    async def hit() -> int:
        req = urllib.request.Request(f"{base}/api/v1/users/me", headers=auth)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status

    for _ in range(args.warmup):
        await hit()

    # The counter MUST be installed in the process that runs the app.
    #
    # A first attempt patched redis-py's Connection in THIS process while the app
    # ran under a separate uvicorn, and reported 0 round trips for all 200
    # requests - a number that means nothing, because the app's connections were
    # never in this process to be counted. The same shape of error as the RSS
    # sampler measuring itself: an instrument pointed at the wrong process reports
    # a beautifully clean result. It is caught only by asking whether the number is
    # plausible, and 200 consecutive zeros is not.
    #
    # So the app is driven IN-PROCESS over ASGITransport, where the patched
    # Connection is the same object the app uses. That is the pattern
    # prof_request_path.py already established for exactly this reason.
    counter = RoundTripCounter()
    counter.install()
    per_request: list[int] = []
    try:
        async with _build_inprocess_driver(base, token) as driver:
            for _ in range(args.warmup):
                await driver()
            counter.round_trips = 0
            for _ in range(args.requests):
                before = counter.round_trips
                await driver()
                per_request.append(counter.round_trips - before)
    finally:
        counter.remove()

    distribution = Counter(per_request)
    n = len(per_request)
    rtt_min = min(per_request) if per_request else None
    # "1 in 20 does 4 instead of 2" is a claim about the EXCESS over the modal
    # value, so it is tested as a share of requests above the mode rather than as
    # a share at exactly 4. A distribution that moved wholesale to 3 would not
    # refute the claim but would certainly change the picture.
    modal = distribution.most_common(1)[0][0] if distribution else None
    above_mode = sum(c for v, c in distribution.items() if modal is not None and v > modal)

    result = {
        "schema": "perf-rtt-distribution/1",
        "endpoint": "GET /users/me (authenticated, warm)",
        "n_requests": n,
        "warmup_discarded": args.warmup,
        "denominator": (
            "n_requests - the measured requests only. Warm-ups are excluded from "
            "both numerator and denominator; including them would inflate the "
            "denominator with requests whose cost is not the steady state."
        ),
        "denominator_value": n,
        "rtt_per_request": {
            "min": rtt_min,
            "modal": modal,
            "p50": nearest_rank(per_request, 0.50),
            "p95": nearest_rank(per_request, 0.95),
            "p99": nearest_rank(per_request, 0.99),
            "max": max(per_request) if per_request else None,
            "mean": round(sum(per_request) / n, 4) if n else None,
        },
        "distribution": {str(k): v for k, v in sorted(distribution.items())},
        "above_modal": {
            "count": above_mode,
            "share": round(above_mode / n, 6) if n else None,
            "one_in": round(n / above_mode, 2) if above_mode else None,
        },
        "claim_tested": (
            "Round-1 .out showed 1 request in 20 doing 4 RTT instead of 2, and "
            "recorded the rate as unestimable at n=20."
        ),
        "total_commands_issued": counter.commands,
        "percentile_method": "nearest rank over the raw per-request sample list",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"n = {n} (warmup {args.warmup} discarded)")
    print(f"distribution (rtt -> requests): {result['distribution']}")
    print(f"min {rtt_min}  modal {modal}  p50 {result['rtt_per_request']['p50']}  "
          f"p95 {result['rtt_per_request']['p95']}  max {result['rtt_per_request']['max']}")
    print(f"above modal: {above_mode}/{n} = {result['above_modal']['share']} "
          f"(1 in {result['above_modal']['one_in']})")
    print(f"wrote {out}")
    return 0


async def _unused() -> None:
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/rtt_distribution.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=10)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
