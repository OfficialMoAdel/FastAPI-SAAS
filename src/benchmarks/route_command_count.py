"""Operator harness: one route request against an exact Redis command-count ceiling.

This is the route-scoped user of :mod:`src.benchmarks.command_count`. It logs in
with the repository's existing e2e fixture, warms the route, then measures ONE
authenticated request. The measurement is the server's own ``INFO commandstats``
delta, not a client-side estimate, so Lua-internal commands are included and the
number is a SERVER command count. Use the per-command output to show which wire
round trips the route actually pays for.

``INFO commandstats`` is server-wide. The measurement therefore refuses a dirty
control window; run it against a dedicated Redis instance, or stop other traffic
for the duration of the run.

Usage:
  uv run python src/benchmarks/route_command_count.py --max-commands 4
  uv run python src/benchmarks/route_command_count.py --max-commands 3 \
      --expect evalsha=2 --expect get=1

Exit codes: 0 pass, 1 setup/measurement failure, 2 command-count ceiling failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.benchmarks.protocol import CommandCountCeiling, CommandCountResult, CommandCountVerdict

ROOT = Path(__file__).resolve().parents[2]

TENANT = "test-tenant-e2e"
EMAIL = "e2e_test@example.com"
PASSWORD = "TestPassword123!"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
LOGIN_PATH = "/api/v1/account/login"


def bootstrap() -> None:
    """Load the local environment and relax only the rate limits.

    ``AUTO_BAN_ENABLED`` is deliberately NOT overridden: the authenticated
    request-admission branch only fuses the ban/blacklist/GCRA Lua when auto-ban
    is enabled, and that fused path is the one a production request pays for.
    """
    os.chdir(ROOT)
    load_dotenv(ROOT / "src" / ".env", override=True)
    os.environ.setdefault("ENVIRONMENT", "local")
    os.environ["LOG_LEVEL"] = "WARNING"

    from src.benchmarks.prof_request_path import SECURITY_BUDGET_KEYS

    os.environ["SECURITY_BUDGETS"] = json.dumps({k: [1000000, 60] for k in SECURITY_BUDGET_KEYS})
    os.environ["TIER_RATE_LIMITS"] = json.dumps({k: [1000000, 3600] for k in ("free", "basic", "pro", "enterprise")})
    os.environ["DEFAULT_AUTHENTICATED_LIMIT"] = "[1000000, 3600]"
    os.environ["DEFAULT_ANONYMOUS_LIMIT"] = "[1000000, 3600]"
    os.environ["ANONYMOUS_IP_AGGREGATE_LIMIT"] = "[1000000, 3600]"


def parse_expected(values: list[str]) -> tuple[tuple[str, int], ...]:
    """Parse repeated ``COMMAND=COUNT`` arguments into a normalized tuple."""
    expected: list[tuple[str, int]] = []
    for raw in values:
        name, separator, count_text = raw.partition("=")
        if not separator or not name.strip():
            raise ValueError(f"expected COMMAND=COUNT, got {raw!r}")
        try:
            count = int(count_text)
        except ValueError as exc:
            raise ValueError(f"count for {name!r} is not an integer: {count_text!r}") from exc
        expected.append((name.strip().lower(), count))
    return tuple(expected)


async def measure_route(
    client: Any,
    request: Callable[[], Awaitable[Any]],
    *,
    name: str,
    ceiling: CommandCountCeiling,
) -> tuple[CommandCountResult, CommandCountVerdict]:
    """Measure one request and return its command result and verdict.

    The request is executed exactly once. A non-2xx response fails the
    measurement instead of being described as a command count.
    """
    from src.benchmarks.command_count import measure_command_counts
    from src.benchmarks.protocol import command_count_verdict

    async def operation() -> None:
        response = await request()
        if response.status_code != 200:
            raise RuntimeError(f"{name} returned HTTP {response.status_code}: {response.text[:300]}")

    result = await measure_command_counts(client, operation, name=name)
    return result, command_count_verdict(result, ceiling)


async def _run(args: argparse.Namespace) -> int:
    import httpx
    from asgi_lifespan import LifespanManager

    import src.app.main as app_main
    from src.app.core.redis_provider import get_redis
    from src.benchmarks.protocol import CommandCountCeiling

    ceiling = CommandCountCeiling(
        max_commands=args.max_commands,
        expected=args.expected,
        label=args.name,
    )

    async with LifespanManager(app_main.app, startup_timeout=180, shutdown_timeout=60):
        redis = get_redis()
        if redis is None:
            print("NO_REDIS_CLIENT: the cache client is not installed", file=sys.stderr)
            return 1

        transport = httpx.ASGITransport(app=app_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=60.0) as http:
            response = await http.post(
                LOGIN_PATH,
                data={"username": EMAIL, "password": PASSWORD},
                headers={"X-Tenant-ID": TENANT, "User-Agent": UA},
            )
            if response.status_code != 200:
                print(f"LOGIN FAIL {response.status_code}: {response.text[:300]}", file=sys.stderr)
                return 1

            token = response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": TENANT, "User-Agent": UA}
            for _ in range(args.warmup):
                warm = await http.get(args.route, headers=headers)
                if warm.status_code != 200:
                    print(f"WARMUP FAIL {warm.status_code}: {warm.text[:300]}", file=sys.stderr)
                    return 1

            result, verdict = await measure_route(
                redis,
                lambda: http.get(args.route, headers=headers),
                name=args.name,
                ceiling=ceiling,
            )

    print(verdict.message)
    print(
        json.dumps(
            {
                "passed": verdict.passed,
                "commands": dict(result.commands),
                "measured_commands": verdict.measured_commands,
                "ceiling_commands": verdict.ceiling_commands,
                "missing": dict(verdict.missing),
                "unexpected": dict(verdict.unexpected),
                "excess": dict(verdict.excess),
            },
            indent=2,
        )
    )
    return 0 if verdict.passed else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", default="/api/v1/users/me", help="GET route to measure")
    parser.add_argument("--name", default="GET /api/v1/users/me", help="verdict label")
    parser.add_argument(
        "--max-commands",
        type=int,
        required=True,
        help="maximum total Redis commands for one request",
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="COMMAND=COUNT",
        help="exact per-command count; repeat for each command",
    )
    parser.add_argument("--warmup", type=int, default=30, help="unmeasured requests before the controlled one")
    args = parser.parse_args(argv)
    try:
        args.expected = parse_expected(args.expect)
    except ValueError as exc:
        parser.error(str(exc))

    bootstrap()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
