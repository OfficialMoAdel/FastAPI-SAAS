"""Per-request I/O round-trip inventory + phase timing — REAL stack, REAL I/O.

Boots src.app.main:app with its real 10-layer middleware stack against the
real remote Postgres (192.168.121.147) and Redis, drives each measured
endpoint in-process over ASGI, and records for every request:

  * every Redis command, by name AND by Lua script name (shim on
    redis.asyncio.Redis.execute_command / Pipeline.execute, plus a shim on
    LuaManager.execute so EVALSHA is attributed to a script)
  * every SQL statement (independent before_cursor_execute listener)
  * wall time distribution, and the app's own QueryProfilerMiddleware
    total_time_ms / db_time_pct log line

Nothing is mocked. Run:
  set PYTHONIOENCODING=utf-8
  uv run python src/benchmarks/driver_count_io.py
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.benchmarks.percentiles import PERCENTILES, nearest_rank, unresolvable_quantiles
from src.benchmarks.security_state import clear_security_state

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def bootstrap() -> None:
    """Side effects needed to drive the real stack. Deliberately NOT at import.

    ``os.chdir`` would move an importing test session's working directory,
    ``load_dotenv(override=True)`` would clobber the environment
    ``tests/conftest.py`` sets, and the forced ``QUERY_PROFILER_ENABLED`` would
    leak into every other test. Keeping them here is what makes this module
    importable from ``tests/unit/benchmarks/test_driver_count_io_math.py``.
    """
    from dotenv import load_dotenv

    os.chdir(ROOT)
    load_dotenv(ROOT / "src" / ".env", override=True)
    os.environ.setdefault("ENVIRONMENT", "local")
    os.environ["QUERY_PROFILER_ENABLED"] = "1"


REDIS: Counter[str] = Counter()
SQL: Counter[str] = Counter()
TIMES: list[tuple[str, float]] = []


def install_counters() -> None:
    from redis.asyncio import Redis
    from redis.asyncio.client import Pipeline

    orig_cmd = Redis.execute_command

    async def execute_command(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        name = args[0] if args else "?"
        REDIS[str(name)] += 1
        t0 = time.perf_counter()
        try:
            return await orig_cmd(self, *args, **kwargs)
        finally:
            TIMES.append((str(name), time.perf_counter() - t0))

    Redis.execute_command = execute_command  # type: ignore[method-assign]

    orig_pipe = Pipeline.execute

    async def pipeline_execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        stack = list(getattr(self, "command_stack", []) or [])
        for cmd in stack:
            REDIS["PIPE:" + str(cmd[0][0] if cmd and cmd[0] else "?")] += 1
        t0 = time.perf_counter()
        try:
            return await orig_pipe(self, *args, **kwargs)
        finally:
            TIMES.append((f"PIPELINE[{len(stack)}]", time.perf_counter() - t0))

    Pipeline.execute = pipeline_execute  # type: ignore[method-assign]

    # Attribute EVALSHA to the Lua script that issued it.
    from src.app.core.utils.lua_manager import get_lua_manager

    lm = get_lua_manager()
    orig_lua = lm.execute

    async def lua_execute(client, script_name, keys=None, args=None, **kw):  # type: ignore[no-untyped-def]
        REDIS["LUA:" + str(script_name)] += 1
        t0 = time.perf_counter()
        try:
            return await orig_lua(client, script_name, keys=keys, args=args, **kw)
        finally:
            TIMES.append(("LUA:" + str(script_name), time.perf_counter() - t0))

    lm.execute = lua_execute  # type: ignore[assignment, method-assign]


def install_sql_counter() -> None:
    from sqlalchemy import event

    from src.app.core.db.database import get_async_engine

    engine = get_async_engine()

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        SQL[" ".join(str(statement).split())[:130]] += 1


def reset() -> None:
    REDIS.clear()
    SQL.clear()
    TIMES.clear()


def pct(v: list[float], p: float) -> float:
    """Percentile of ``v`` at quantile ``p``. Delegates to the shared estimator.

    The name and signature are kept because this harness's callers and its
    tests use them. The BODY is not here: it lives in
    ``src.benchmarks.percentiles`` so there is exactly one percentile
    implementation in the harness tree. See that module for the estimator and
    for the ``s[min(int(n*p), n-1)]`` defect that used to live here, where
    ``int(20 * .95) == int(20 * .99) == 19`` made both columns a maximum.
    """
    return nearest_rank(v, p)


def unresolved_percentiles(n: int, ps: tuple[float, ...] = PERCENTILES) -> list[float]:
    """Percentiles the sample size cannot resolve, so they equal ``max()``.

    Nearest-rank is exact arithmetic, not a bug: with 20 samples the nearest
    rank to p99 IS the 20th sample. The defect is reporting it as if it were
    an estimate, so the reader is told which numbers the sample cannot support.
    """
    return unresolvable_quantiles(n, ps)


def report(label: str, n: int, walls: list[float], extra: str = "") -> None:
    print(f"\n{'=' * 96}\n{label}   n={n} {extra}\n{'=' * 96}")
    print(
        f"  wall ms: min={min(walls):.2f} p50={pct(walls, 0.5):.2f} p95={pct(walls, 0.95):.2f} "
        f"p99={pct(walls, 0.99):.2f} max={max(walls):.2f} mean={statistics.mean(walls):.2f}"
    )
    unres = unresolved_percentiles(n)
    if unres:
        print(
            f"    NOTE: n={n} cannot resolve {', '.join(f'p{int(p * 100)}' for p in unres)} — "
            f"those equal max(). Raise n before quoting them."
        )
    # LUA:* are ATTRIBUTIONS of EVALSHA calls (LuaManager.execute calls
    # Redis.execute_command), not extra round trips. Count them once.
    neval = REDIS.get("EVALSHA", 0)
    nother = sum(v for k, v in REDIS.items() if not k.startswith("LUA:")) - neval
    print(f"  REDIS round trips/request: {nother + neval}  ({neval / n:.2f} EVALSHA, {nother / n:.2f} other)")
    for k, v in sorted(REDIS.items(), key=lambda kv: -kv[1]):
        if k.startswith("LUA:"):
            continue
        print(f"      {v / n:6.2f}  {k}")
    print("  attribution of the EVALSHA round trips:")
    for k, v in sorted(((k, v) for k, v in REDIS.items() if k.startswith("LUA:")), key=lambda kv: -kv[1]):
        print(f"      {v / n:6.2f}  {k}")
    agg = aggregate_timings(TIMES)
    per_call = [d for ds in agg.values() for d in ds]
    if per_call:
        print(
            f"  measured Redis wait/request: {sum(per_call) / n * 1000:.2f} ms "
            f"({100 * sum(per_call) / (sum(walls) / 1000):.1f}% of wall)"
        )
        for name, ds in sorted(agg.items(), key=lambda kv: -sum(kv[1])):
            print(
                f"      {name:36s} calls/req={len(ds) / n:5.2f} "
                f"p50={pct(ds, 0.5) * 1000:7.2f}ms p99={pct(ds, 0.99) * 1000:7.2f}ms "
                f"tot/req={sum(ds) / n * 1000:6.2f}ms"
            )
    print(f"  SQL statements/request: {sum(SQL.values()) / n:.2f}")
    for k, v in sorted(SQL.items(), key=lambda kv: -kv[1]):
        print(f"      {v / n:6.2f}  {k}")


def aggregate_timings(times: list[tuple[str, float]]) -> dict[str, list[float]]:
    """Bucket command timings by name, counting each round trip exactly once.

    The ``LuaManager.execute`` shim times a window that CONTAINS its own
    ``EVALSHA`` (it awaits ``Redis.execute_command`` internally), and that inner
    call is appended to ``TIMES`` first. So every ``EVALSHA`` sample is already
    inside exactly one ``LUA:*`` sample. The old code summed ``TIMES``
    directly for the "measured Redis wait/request" line while only
    de-duplicating ``EVALSHA`` in the per-name breakdown, so that one line
    double-counted every Lua round trip.

    Exactly ``len(LUA:* samples)`` EVALSHA samples are therefore dropped —
    the surplus, if any, is a standalone EVALSHA the application issued
    without the Lua manager and is kept. Which surplus samples are dropped
    does not change the sum, so the leading slice is as good as any other.
    """
    agg: dict[str, list[float]] = {}
    for name, d in times:
        agg.setdefault(name, []).append(d)
    n_lua = sum(len(ds) for k, ds in agg.items() if k.startswith("LUA:"))
    if n_lua and "EVALSHA" in agg:
        agg["EVALSHA"] = agg["EVALSHA"][n_lua:]
        if not agg["EVALSHA"]:
            del agg["EVALSHA"]
    return agg


async def main() -> None:
    install_counters()
    import src.app.main as main_mod

    app = main_mod.app
    install_sql_counter()

    import httpx

    async with app.router.lifespan_context(app):
        from sqlalchemy import text

        from src.app.core.db.database import local_session
        from src.app.core.security.jwt_service import create_access_token, create_refresh_token
        from src.app.core.security.session_manager import session_manager

        async with local_session() as s:
            row = (await s.execute(text("select id, username, token_version from users where id = 1"))).first()
        if row is None:
            raise RuntimeError("users id=1 is missing; run scripts/setup_locust_test_data.py first")
        tv = int(row.token_version or 0)

        def base_claims() -> dict:
            return {
                "sub": "e2etestuser",
                "username_or_email": "e2e_test@example.com",
                "user_id": 1,
                "tenant_id": "7cb3d0ab-f39c-47c7-9d95-95d22fe65648",
                "is_superuser": True,
                "token_version": tv,
                "iat": int(time.time()),
            }

        sid = uuid.uuid4().hex
        await session_manager.register_session(1, sid, device_info="perf-review driver")
        at = await create_access_token(
            {**base_claims(), "sid": sid, "jti": uuid.uuid4().hex, "exp": int(time.time()) + 3600}
        )
        await create_refresh_token(
            {**base_claims(), "sid": sid, "jti": uuid.uuid4().hex, "exp": int(time.time()) + 86400}
        )
        print("minted access+refresh for user 1, sid", sid)

        hdr = {
            "Authorization": f"Bearer {at}",
            "X-Tenant-ID": "test-tenant-e2e",
            "accept-encoding": "gzip",
            "user-agent": UA,
        }
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=120) as c:
            r = await c.get("/api/v1/users/me", headers=hdr)
            print("warm status:", r.status_code)

            async def bench(label, fn, n):
                await clear_security_state()
                reset()
                walls = []
                last: httpx.Response | None = None
                for _ in range(n):
                    t0 = time.perf_counter()
                    r = await fn()
                    walls.append((time.perf_counter() - t0) * 1000)
                    last = r
                if last is None:
                    raise ValueError(f"bench({label!r}) needs n >= 1")
                report(label, n, walls, extra=f"status={last.status_code} bodylen={len(last.content)}")
                return last

            await bench(
                "GET /api/v1/users/me  (authenticated, cache warm)",
                lambda: c.get("/api/v1/users/me", headers=hdr),
                20,
            )

            await bench(
                "GET /api/v1/users/me  (NO Authorization header)",
                lambda: c.get(
                    "/api/v1/users/me",
                    headers={"X-Tenant-ID": "test-tenant-e2e", "user-agent": UA, "accept-encoding": "gzip"},
                ),
                8,
            )

            await bench(
                "GET /api/v1/health  (tenant-exempt, no auth)",
                lambda: c.get("/api/v1/health", headers={"user-agent": UA, "accept-encoding": "gzip"}),
                20,
            )

            # --- refresh: mint a fresh refresh token each iteration
            async def do_refresh():
                tok = await create_refresh_token(
                    {**base_claims(), "sid": sid, "jti": uuid.uuid4().hex, "exp": int(time.time()) + 86400}
                )
                return await c.post(
                    "/api/v1/account/refresh",
                    json={"refresh_token": tok},
                    headers={"X-Tenant-ID": "test-tenant-e2e", "user-agent": UA, "accept-encoding": "gzip"},
                )

            await bench("POST /api/v1/account/refresh", do_refresh, 20)

            # --- login: wrong password exercises the full bcrypt path
            async def do_login():
                return await c.post(
                    "/api/v1/account/login",
                    json={"username": "e2e_test@example.com", "password": "definitely-not-the-password"},
                    headers={"X-Tenant-ID": "test-tenant-e2e", "user-agent": UA, "accept-encoding": "gzip"},
                )

            await bench("POST /api/v1/account/login  (wrong password -> full bcrypt)", do_login, 10)

            # --- logout
            async def do_logout():
                return await c.post(
                    "/api/v1/account/logout",
                    headers=hdr,
                )

            await bench("POST /api/v1/account/logout", do_logout, 10)


if __name__ == "__main__":
    bootstrap()
    asyncio.run(main())
