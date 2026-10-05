"""Request-path profiler driver: CPU, allocation, peak memory, per-middleware cost.

Methodology requirement B of PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_bunny_pi.md,
following the structure of scripts/BASELINE.md (per-middleware timing,
X-Query-Count via QueryProfilerMiddleware, pool/Redis census).

Design constraints:
  * READ + MEASURE. Writes nothing under src/. The per-middleware timing
    wrapper is a dynamic subclass installed by THIS script on
    ``app.user_middleware[i].cls`` -- no production file is edited.
  * The middleware stack is built lazily by Starlette and cached on
    ``app.middleware_stack``. To measure the stack UNWRAPPED first (true
    baseline latency, no instrumentation overhead) and wrapped second, we
    clear ``app.middleware_stack`` in between so it is rebuilt.
  * cProfile is THREAD-LOCAL. Starlette's TestClient drives the app on a
    blocking-portal thread, so a profile taken from the main thread under
    TestClient sees nothing. The CPU/allocation phases therefore use an
    in-process asyncio driver (httpx ASGITransport + asgi_lifespan), which
    keeps the app on the SAME thread as the profiler. The TestClient phase
    is still run, and is the phase that yields latency, X-Query-Count and
    per-middleware numbers -- the same in-process approach ADR-0008 used for
    its end-to-end table, so the numbers are comparable.
  * Fixture: the existing e2e tenant/user (created earlier by the repo's own
    e2e suite). Real login, so the session-validity leg has a real session.
    bcrypt is paid once, outside every measured window.
  * refresh rotates tokens, so the POST loop chains the returned
    refresh_token. Runs last in its phase.

Usage:
  uv run python src/benchmarks/prof_request_path.py
  uv run python src/benchmarks/prof_request_path.py --n 400 --pyinstrument

Provenance: this harness was promoted out of the gitignored scratch dir
``reports/perf_review_2026-10-04/``. An instrument that cannot be recovered
from git can never be checked, and this one had already had a report cite its
output while it was untracked. See ``src/benchmarks/run_manifest.py`` for the
manifest a run of this harness should be recorded alongside.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import gc
import io
import json
import os
import pstats
import sys
import threading
import time
import tracemalloc
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.benchmarks.percentiles import nearest_rank  # noqa: E402

# Every environment mutation below lives in bootstrap() rather than at import
# time. src/.env EXISTS, so a module-level load_dotenv(override=True) would
# replace the environment a pytest session runs under, os.chdir would move the
# session's working directory, and QUERY_PROFILER_ENABLED / LOG_LEVEL /
# AUTO_BAN_ENABLED / the relaxed budgets would leak into every test collected
# after an import of this module. Same rule as driver_count_io.bootstrap().

# Output goes to the repo's report directory, NOT to this file's own directory:
# src/benchmarks/ holds source, and a profile artefact written beside it would
# be mistaken for a checked-in baseline.
OUT_DIR = ROOT / "reports"


def bootstrap() -> None:
    """Put the process where the profiler needs it, then relax what it trips.

    Call this before importing anything under src.app, because the budget
    relaxations below are read at settings-construction time.
    """
    os.chdir(ROOT)
    load_dotenv(ROOT / "src" / ".env", override=True)
    os.environ.setdefault("ENVIRONMENT", "local")

    # --- probes the runbook asks for -------------------------------------
    os.environ["QUERY_PROFILER_ENABLED"] = "true"
    os.environ["LOG_LEVEL"] = "WARNING"

    # Same relaxations the repo's own baseline harness uses: without them the
    # free-tier fixture caps at 10 req/hr and warmup alone trips 429. A run
    # with these in force is NOT a capacity measurement, which is why
    # run_manifest.py's labels mark a relaxed run non-representative.
    os.environ["AUTO_BAN_ENABLED"] = "false"
    os.environ["SECURITY_BUDGETS"] = json.dumps(
        {
            k: [1000000, 60]
            for k in (
                "auth",
                "mfa_verify",
                "mfa_verify_global",
                "mfa_mutate",
                "magic_link_send",
                "passkey_login",
                "refresh",
                "ai_chat",
            )
        }
    )
    os.environ["TIER_RATE_LIMITS"] = json.dumps({k: [1000000, 3600] for k in ("free", "basic", "pro", "enterprise")})
    os.environ["DEFAULT_AUTHENTICATED_LIMIT"] = "[1000000, 3600]"
    os.environ["DEFAULT_ANONYMOUS_LIMIT"] = "[1000000, 3600]"
    os.environ["ANONYMOUS_IP_AGGREGATE_LIMIT"] = "[1000000, 3600]"


TENANT = "test-tenant-e2e"
EMAIL = "e2e_test@example.com"
PASSWORD = "TestPassword123!"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


# --------------------------------------------------------------------------
# per-middleware timing (runbook probe #1)
# --------------------------------------------------------------------------
MW_US: list[tuple[str, float]] = []  # (name, microseconds to response.start)
MW_LOCK = threading.Lock()


def _record(name: str, us: float) -> None:
    with MW_LOCK:
        MW_US.append((name, us))


def _make_timed(cls, name: str):
    """Functionally transparent subclass that times each request to the point
    the downstream response head is emitted. Semantics unchanged."""

    class Timed(cls):  # type: ignore[misc, valid-type]
        async def __call__(self, scope, receive, send):  # noqa: ANN001
            if scope.get("type") != "http":
                await super().__call__(scope, receive, send)
                return
            t0 = time.perf_counter_ns()
            done = False

            async def timed_send(message):  # noqa: ANN001
                nonlocal done
                if message.get("type") == "http.response.start" and not done:
                    done = True
                    _record(name, (time.perf_counter_ns() - t0) / 1000.0)
                await send(message)

            await super().__call__(scope, receive, timed_send)

    Timed.__name__ = f"Timed{name}"
    return Timed


def install_middleware_timers(app) -> list[str]:
    """Swap each registered guard class for its timing subclass and force a
    middleware-stack rebuild. Returns outer->inner middleware names."""
    names: list[str] = []
    for mw in app.user_middleware:
        base = mw.cls
        name = base.__name__
        # app.user_middleware is innermost-registered first; execution is LIFO.
        mw.cls = _make_timed(base, name)
        names.append(name)
    app.middleware_stack = None  # force rebuild with the timing subclasses
    # NOTE: Starlette's add_middleware INSERTS at position 0, so
    # app.user_middleware is ALREADY in execution order (index 0 == outermost
    # == last registered == CORSMiddleware). Do not reverse. (Verified against
    # the measured nesting: CORS's inclusive window is the whole request.)
    return names


# --------------------------------------------------------------------------
# Redis / SQL round-trip census (tripwire; counts only, never alters behaviour)
# --------------------------------------------------------------------------
SHA_TO_NAME: dict[str, str] = {}


def index_lua_shas() -> None:
    from src.app.core.utils.lua_manager import get_lua_manager

    mgr = get_lua_manager()
    for name in mgr.get_script_names():
        SHA_TO_NAME[mgr.get_script(name).sha1] = name


class Tripwire:
    def __init__(self) -> None:
        self.sql: Counter[str] = Counter()
        self.redis: Counter[str] = Counter()
        self.pool_checkouts: Counter[str] = Counter()
        self.enabled = True

    def reset(self) -> None:
        self.sql.clear()
        self.redis.clear()
        self.pool_checkouts.clear()


def install_tripwire(tw: Tripwire) -> None:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):  # noqa: ANN001, ARG001
        if tw.enabled:
            tw.sql[" ".join(statement.split())[:110]] += 1

    @event.listens_for(Engine, "checkout")
    def _checkout(dbapi_conn, conn_record, connection_proxy):  # noqa: ANN001, ARG001
        if tw.enabled:
            tw.pool_checkouts["checkout"] += 1

    from redis.asyncio.client import Redis as AsyncRedis

    orig = AsyncRedis.execute_command

    async def patched(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if tw.enabled and args:
            label = str(args[0])
            if label.upper() in {"EVALSHA", "EVAL"} and len(args) > 1:
                label = f"{label}({SHA_TO_NAME.get(str(args[1]), str(args[1])[:8])})"
            tw.redis[label] += 1
        return await orig(self, *args, **kwargs)

    AsyncRedis.execute_command = patched  # type: ignore[method-assign]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def pct(vals: list[float], q: float) -> float:
    """``vals[round(q * (n - 1))]``, now the shared nearest-rank estimator.

    This was the seventh implementation in the harness tree and the reason
    ``tests/unit/benchmarks/test_percentiles.py``'s name-based rule exists: it
    was found by that scan, not by reading each file for arithmetic. The old
    body indexed with ``round(q * (n - 1))`` and, unlike the shared estimator,
    returned ``nan`` for an empty sample rather than raising - which is what a
    profiled window with no samples would silently print.

    ``nan`` here meant "there was nothing to summarise"; ``ValueError`` means
    the same thing and cannot be mistaken for a measurement.
    """
    return nearest_rank(vals, q)


def rss_mb() -> float:
    import psutil

    rss: int = psutil.Process().memory_info().rss
    return rss / (1024 * 1024)


def frame_stats(st: pstats.Stats) -> dict[tuple[str, int, str], tuple[int, int, float, float, Any]]:
    """``st.stats`` with its type stated.

    cProfile builds this mapping in ``pstats.Stats.__init__`` and does not
    declare it, so every read of it is an ``attr-defined`` error under mypy
    even though the attribute is always present on a constructed Stats. Naming
    it once here keeps that a single documented fact rather than four
    suppressions.
    """
    return cast("dict[tuple[str, int, str], tuple[int, int, float, float, Any]]", st.stats)  # type: ignore[attr-defined]


class RssSampler(threading.Thread):
    """Samples RSS in a background thread so the GIL-bound main loop cannot
    hide the true peak."""

    def __init__(self, interval: float = 0.004) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.peak = 0.0
        self.samples = 0
        self._stop = threading.Event()

    def run(self) -> None:  # noqa: D102
        while not self._stop.is_set():
            try:
                self.peak = max(self.peak, rss_mb())
            except Exception:  # noqa: BLE001, S110
                pass
            self.samples += 1
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


def exclusive_table(n_req: int, mw_order: list[str]) -> tuple[dict, dict]:
    """Convert nested (inclusive) middleware timings to exclusive cost.

    Middleware nest like an onion and each wrapper's window is closed by the
    SAME http.response.start event, so layer N's window is a strict superset
    of layer N+1's. Exclusive cost of layer N == incl[N] - incl[N+1].
    The innermost layer's exclusive cost IS the router handler.
    """
    tot: dict[str, float] = defaultdict(float)
    for name, us in MW_US:
        tot[name] += us
    incl = {n: tot.get(n, 0.0) / n_req for n in mw_order}
    excl: dict[str, float] = {}
    for i, n in enumerate(mw_order):
        excl[n] = incl[n] - (incl[mw_order[i + 1]] if i + 1 < len(mw_order) else 0.0)
    return incl, excl


def print_middleware_table(n_req: int, mw_order: list[str], label: str) -> None:
    incl, excl = exclusive_table(n_req, mw_order)
    total = incl.get(mw_order[0], 0.0)
    print(f"\n--- per-middleware cost, {label} (n={n_req}) ---")
    print(f"{'layer (outer->inner)':32} {'incl us/req':>12} {'excl us/req':>12} {'%of total':>10}")
    for n in mw_order:
        pct_of = (incl[n] / total * 100.0) if total else 0.0
        print(f"{n:32} {incl[n]:12.1f} {excl[n]:12.1f} {pct_of:9.1f}%")
    handler = excl.get(mw_order[-1], 0.0)
    print(f"{'== ROUTER HANDLER (exclusive of innermost layer)':32} {'':12} {handler:12.1f}")
    guard_excl = sum(excl[n] for n in mw_order[:-1])
    print(
        f"{'== GUARD STACK TOTAL (sum of all 9 layers excl)':32} {'':12} {guard_excl:12.1f}"
        f"   ({guard_excl / total * 100 if total else 0:.1f}% of request)"
    )
    print(
        "[Unverified] 'incl' is measured to http.response.start, so any body "
        "streaming/compression after the head is outside these windows."
    )


# --------------------------------------------------------------------------
# Phase A -- TestClient (sync, portal thread): latency, X-Query-Count, mw cost
# --------------------------------------------------------------------------
def phase_testclient(tw: Tripwire, n_get: int, n_post: int) -> dict:
    from starlette.testclient import TestClient

    import src.app.main as m

    app = m.app

    print("\n=== registered middleware (execution order, outer->inner) ===")
    reg = [w.cls.__name__ for w in app.user_middleware]
    for i, nm in enumerate(reg):
        print(f"  {i}: {nm}")

    out: dict = {"registered": reg}
    with TestClient(app) as tc:
        base_rss = rss_mb()
        print(f"\nRSS after boot/lifespan: {base_rss:.1f} MB")

        r = tc.post(
            "/api/v1/account/login",
            data={"username": EMAIL, "password": PASSWORD},
            headers={"X-Tenant-ID": TENANT, "User-Agent": UA},
        )
        if r.status_code != 200:
            print("LOGIN FAIL", r.status_code, r.text[:300])
            raise SystemExit(1)
        tok = r.json()["access_token"]
        refresh = r.json().get("refresh_token", "")
        H = {
            "Authorization": f"Bearer {tok}",
            "X-Tenant-ID": TENANT,
            "User-Agent": UA,
            "Accept-Encoding": "gzip",
        }

        for _ in range(30):
            assert tc.get("/api/v1/users/me", headers=H).status_code == 200

        # --- A1: UNWRAPPED baseline latency (no timing instrumentation) ---
        s: list[float] = []
        tw.reset()
        qc: Counter[int] = Counter()
        for _ in range(n_get):
            t0 = time.perf_counter()
            resp = tc.get("/api/v1/users/me", headers=H)
            s.append((time.perf_counter() - t0) * 1e3)
            qc[int(resp.headers.get("X-Query-Count", -1))] += 1
        s.sort()
        sql_ops = dict(tw.sql.most_common(10))
        redis_ops = dict(tw.redis.most_common(10))
        checkouts = tw.pool_checkouts["checkout"]
        print(f"\n=== A1 GET /api/v1/users/me UNWRAPPED (n={n_get}) ===")
        print(
            f"  min={s[0]:.3f} p50={pct(s, 0.5):.3f} p95={pct(s, 0.95):.3f} "
            f"p99={pct(s, 0.99):.3f} max={s[-1]:.3f} ms   mean={sum(s) / len(s):.3f}"
        )
        print(f"  X-Query-Count histogram: {dict(sorted(qc.items()))}")
        print(f"  SQL round-trips/req: {sum(tw.sql.values()) / n_get:.2f}")
        for k, v in sql_ops.items():
            print(f"    [{v / n_get:.2f}/req] {k}")
        print(f"  Redis round-trips/req: {sum(tw.redis.values()) / n_get:.2f}")
        for k, v in redis_ops.items():
            print(f"    [{v / n_get:.2f}/req] {k}")
        print(f"  pool checkouts/req: {checkouts / n_get:.2f}")
        out["get_unwrapped"] = {
            "n": n_get,
            "p50": pct(s, 0.5),
            "p95": pct(s, 0.95),
            "p99": pct(s, 0.99),
            "min": s[0],
            "max": s[-1],
            "mean": sum(s) / len(s),
        }
        out["x_query_count"] = dict(qc)
        out["sql_per_req"] = sum(tw.sql.values()) / n_get
        out["redis_per_req"] = sum(tw.redis.values()) / n_get
        out["pool_checkouts_per_req"] = checkouts / n_get
        out["sql_ops"] = sql_ops
        out["redis_ops"] = redis_ops
        out["rss_after_boot"] = base_rss

        # --- install timing wrappers, rebuild stack ---
        mw_order = install_middleware_timers(app)
        out["mw_order_timed"] = mw_order
        assert tc.get("/api/v1/users/me", headers=H).status_code == 200, "wrapped stack broken"

        # --- A2: WRAPPED, per-middleware cost ---
        MW_US.clear()
        gc.collect()
        tw.reset()
        for _ in range(n_get):
            assert tc.get("/api/v1/users/me", headers=H).status_code == 200
        print_middleware_table(n_get, mw_order, "GET /users/me")
        post_sql = sum(tw.sql.values()) / n_get
        print(f"  SQL/req under timing wrapper: {post_sql:.2f}")
        out["sql_per_req_wrapped"] = post_sql
        out["rss_after_get_phase"] = rss_mb()

        # --- A3: POST with a body (refresh chains the rotating token) ---
        MW_US.clear()
        sp: list[float] = []
        tw.reset()
        pqc: Counter[int] = Counter()
        fails = 0
        for _ in range(n_post):
            t0 = time.perf_counter()
            resp = tc.post("/api/v1/account/refresh", headers=H, json={"refresh_token": refresh})
            sp.append((time.perf_counter() - t0) * 1e3)
            if resp.status_code == 200:
                refresh = resp.json().get("refresh_token", refresh)
                tok = resp.json().get("access_token", tok)
                H["Authorization"] = f"Bearer {tok}"
                pqc[int(resp.headers.get("X-Query-Count", -1))] += 1
            else:
                fails += 1
        sp.sort()
        print(f"\n=== A3 POST /api/v1/account/refresh (n={n_post}, fails={fails}) ===")
        print(
            f"  min={sp[0]:.3f} p50={pct(sp, 0.5):.3f} p95={pct(sp, 0.95):.3f} "
            f"p99={pct(sp, 0.99):.3f} max={sp[-1]:.3f} ms   mean={sum(sp) / len(sp):.3f}"
        )
        print(f"  X-Query-Count histogram: {dict(sorted(pqc.items()))}")
        print(f"  SQL/req: {sum(tw.sql.values()) / n_post:.2f}  Redis/req: {sum(tw.redis.values()) / n_post:.2f}")
        print_middleware_table(n_post, mw_order, "POST /account/refresh")
        out["post"] = {
            "n": n_post,
            "fails": fails,
            "p50": pct(sp, 0.5),
            "p95": pct(sp, 0.95),
            "p99": pct(sp, 0.99),
            "mean": sum(sp) / len(sp),
            "x_query_count": dict(pqc),
            "sql_per_req": sum(tw.sql.values()) / n_post,
            "redis_per_req": sum(tw.redis.values()) / n_post,
        }
        out["rss_after_post_phase"] = rss_mb()
    return out


# --------------------------------------------------------------------------
# Phase B/C/D -- in-thread asyncio driver: CPU profile, allocations, peak RSS
# --------------------------------------------------------------------------
async def phase_async(tw: Tripwire, n_get: int, use_pyinstrument: bool) -> dict:  # noqa: C901
    import httpx
    from asgi_lifespan import LifespanManager

    import src.app.main as m

    app = m.app
    out: dict = {}

    async with LifespanManager(app, startup_timeout=180, shutdown_timeout=60):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=60.0) as c:
            r = await c.post(
                "/api/v1/account/login",
                data={"username": EMAIL, "password": PASSWORD},
                headers={"X-Tenant-ID": TENANT, "User-Agent": UA},
            )
            if r.status_code != 200:
                print("LOGIN FAIL", r.status_code, r.text[:300])
                raise SystemExit(1)
            tok = r.json()["access_token"]
            refresh = r.json().get("refresh_token", "")
            H = {"Authorization": f"Bearer {tok}", "X-Tenant-ID": TENANT, "User-Agent": UA}
            for _ in range(30):
                assert (await c.get("/api/v1/users/me", headers=H)).status_code == 200

            boot_rss = rss_mb()
            print(f"\nRSS at start of async phase (post-boot): {boot_rss:.1f} MB")

            # ---------- B0: async-driver baseline, NO profiler ----------
            # Required for honesty: cProfile inflates this path heavily, so the
            # unprofiled in-thread loop is what "% of request budget" refers to.
            gc.collect()
            tw.enabled = False
            t0 = time.perf_counter()
            for _ in range(n_get):
                assert (await c.get("/api/v1/users/me", headers=H)).status_code == 200
            clean = time.perf_counter() - t0
            print(
                f"\n=== B0 async driver, NO profiler: {n_get} req in {clean:.3f}s "
                f"= {clean / n_get * 1e3:.3f} ms/req (the unprofiled budget) ==="
            )

            # ---------- B: cProfile ----------
            tw.enabled = False
            gc.collect()
            pr = cProfile.Profile()
            pr.enable()
            t0 = time.perf_counter()
            for _ in range(n_get):
                assert (await c.get("/api/v1/users/me", headers=H)).status_code == 200
            pr.disable()
            wall = time.perf_counter() - t0
            print(f"\n=== B cProfile: {n_get}x GET /users/me, wall {wall:.3f}s ({wall / n_get * 1e3:.3f} ms/req) ===")

            st = pstats.Stats(pr)
            tot_cpu = sum(t[2] for t in frame_stats(st).values())  # tottime sum
            st.sort_stats("tottime")
            st.print_stats(40)
            print(
                f"[cProfile total tottime over window: {tot_cpu:.4f}s; "
                f"wall {wall:.4f}s; profiler distortion vs B0 = "
                f"{wall / clean:.2f}x -> per-frame costs below are INFLATED]"
            )

            # collapse to src.app frames for the hotspot table
            src_rows: list[tuple[str, float, float]] = []
            for (fn, ln, name), (_cc, nc, tt, ct, _c) in frame_stats(st).items():
                if "src\\app" in fn or "/src/app" in fn.replace("\\", "/"):
                    src_rows.append((f"{fn}:{ln}({name})", tt, ct))
            src_rows.sort(key=lambda x: -x[1])
            IDLE_BUILTINS_PRE = {
                "GetQueuedCompletionStatus",
                "WSASend",
                "recv",
                "select",
                "poll",
                "connect",
                "send",
                "WriteFile",
                "ReadFile",
            }
            idle = sum(
                tt
                for (fn, _ln, name), (_a, _b, tt, _c, _d) in frame_stats(st).items()
                if fn.startswith("{") and (name in IDLE_BUILTINS_PRE or "overlapped" in name)
            )
            cpu_total = tot_cpu - idle
            print("\n--- top src.app frames by tottime (% of CPU, idle excluded) ---")
            for name, tt, ct in src_rows[:30]:
                print(f"  {tt * 1e3:9.2f}ms tot {ct * 1e3:9.2f}ms cum  {tt / cpu_total * 100:5.2f}% CPU  {name}")

            # bucket tottime by owner so we can say how much of the CPU is
            # even in src.app at all (vs stdlib / httpx / asyncio driver).
            #
            # CRITICAL: cProfile tottime on an async app INCLUDES TIME SPENT
            # BLOCKED in the event loop. The Windows selector parks in
            # _overlapped.GetQueuedCompletionStatus; on this box that single
            # frame is the largest single entry in the whole profile and it is
            # IDLE, not CPU. It is bucketed separately as "I/O wait (idle)" and
            # excluded from every CPU percentage quoted in the report.
            IDLE_BUILTINS = {
                "GetQueuedCompletionStatus",
                "WSASend",
                "recv",
                "select",
                "poll",
                "connect",
                "send",
                "WriteFile",
                "ReadFile",
            }
            buckets: dict[str, float] = defaultdict(float)
            for (fn, _ln, name), (_cc, _nc, tt, _ct, _c) in frame_stats(st).items():
                f = fn.replace("\\", "/")
                if fn.startswith("{"):
                    if name in IDLE_BUILTINS or "overlapped" in name:
                        buckets["I/O wait (idle, C builtin)"] += tt
                    else:
                        buckets["C builtin"] += tt
                elif "FastAPI_saas/src/app/" in f:
                    rel = f.split("FastAPI_saas/src/app/")[1]
                    buckets["src.app/" + rel.split("/")[0]] += tt
                elif "FastAPI_saas/reports/" in f:
                    buckets["<harness (measurement overhead)>"] += tt
                elif "/site-packages/" in f:
                    buckets["site-packages/" + f.split("/site-packages/")[1].split("/")[0]] += tt
                elif "/Lib/" in f:
                    if "windows_events.py" in f or "proactor_events.py" in f:
                        buckets["stdlib asyncio (windows proactor)"] += tt
                    else:
                        buckets["stdlib"] += tt
                elif f.startswith("<"):
                    buckets["builtin/C"] += tt
                else:
                    buckets["other"] += tt
            idle = buckets.get("I/O wait (idle, C builtin)", 0.0)
            print("\n--- tottime by owner (% of profiled window; CPU% excludes idle) ---")
            for k, v in sorted(buckets.items(), key=lambda x: -x[1])[:22]:
                print(f"  {v * 1e3:9.2f}ms {v / cpu_total * 100:5.2f}% CPU  {k}")
            print(
                f"  [I/O wait (idle, C builtin)] {idle * 1e3:.2f}ms = "
                f"{idle / tot_cpu * 100:.2f}% of the profiled window"
            )
            print(
                f"  CPU denominator = {cpu_total * 1e3:.2f}ms "
                f"(= {tot_cpu * 1e3:.1f}ms profiled - {idle * 1e3:.1f}ms idle)"
            )
            print("[owner buckets captured in raw_prof_request_path.json]")

            out["cprofile"] = {
                "n": n_get,
                "clean_wall_s": clean,
                "clean_ms_per_req": clean / n_get * 1e3,
                "profiled_wall_s": wall,
                "distortion_x": wall / clean,
                "total_tottime_s": tot_cpu,
            }
            buf = io.StringIO()
            out["cprofile"]["top_src"] = [
                {"site": s, "tottime_s": tt, "cumtime_s": ct, "pct": tt / tot_cpu * 100 if tot_cpu else 0.0}
                for s, tt, ct in src_rows[:40]
            ]
            out["cprofile"]["buckets"] = dict(buckets)
            pstats.Stats(pr, stream=buf).sort_stats("tottime").print_stats(120)
            (OUT_DIR / "raw_cprofile_get_users_me.txt").write_text(buf.getvalue(), encoding="utf-8")
            buf2 = io.StringIO()
            pstats.Stats(pr, stream=buf2).sort_stats("cumulative").print_stats(120)
            (OUT_DIR / "raw_cprofile_get_users_me_cum.txt").write_text(buf2.getvalue(), encoding="utf-8")

            # ---------- C: allocation profile ----------
            gc.collect()
            tracemalloc.start(30)
            for _ in range(n_get):
                assert (await c.get("/api/v1/users/me", headers=H)).status_code == 200
            snap = tracemalloc.take_snapshot()
            cur, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            print(f"\n=== C tracemalloc: {n_get}x GET /users/me ===")
            print(f"  traced current after window: {cur / 1e6:.2f} MB")
            print(f"  traced PEAK during window:    {peak / 1e6:.2f} MB ({peak / n_get / 1e3:.1f} KB/req transient)")
            print("\n--- top allocation sites by size (traced-current) ---")
            for s in snap.statistics("lineno")[:30]:
                print(f"  {s.size / 1e6:8.3f} MB  {s.count / n_get:9.1f} blk/req  {s.traceback[0]}")
            out["tracemalloc"] = {
                "n": n_get,
                "peak_bytes": peak,
                "current_bytes": cur,
                "top": [
                    {
                        "site": str(s.traceback[0]),
                        "size_bytes": s.size,
                        "count": s.count,
                        "size_per_req": s.size / n_get,
                        "count_per_req": s.count / n_get,
                    }
                    for s in snap.statistics("lineno")[:30]
                ],
            }
            (OUT_DIR / "raw_tracemalloc_top.txt").write_text(
                "\n".join(f"{s.size:>12} {s.count:>10}  {s.traceback[0]}" for s in snap.statistics("lineno")[:80]),
                encoding="utf-8",
            )

            # ---------- D: peak RSS over the whole async window ----------
            sampler = RssSampler()
            sampler.start()
            tw.enabled = True
            tw.reset()
            t0 = time.perf_counter()
            for _ in range(n_get):
                assert (await c.get("/api/v1/users/me", headers=H)).status_code == 200
            for _ in range(max(n_get // 3, 20)):
                rr = await c.post("/api/v1/account/refresh", headers=H, json={"refresh_token": refresh})
                if rr.status_code == 200:
                    refresh = rr.json().get("refresh_token", refresh)
            dwall = time.perf_counter() - t0
            sampler.stop()
            sampler.join(timeout=2)
            end_rss = rss_mb()
            print(f"\n=== D peak RSS (psutil, 4ms sampling, {sampler.samples} samples) ===")
            print(f"  RSS at window start : {boot_rss:.1f} MB")
            print(f"  RSS peak in window  : {sampler.peak:.1f} MB")
            print(f"  RSS after window    : {end_rss:.1f} MB")
            print(f"  peak - start         : {sampler.peak - boot_rss:+.1f} MB")
            print(f"  window wall time    : {dwall:.2f}s ({n_get + max(n_get // 3, 20)} requests)")
            out["rss"] = {
                "boot_rss_mb": boot_rss,
                "peak_rss_mb": sampler.peak,
                "end_rss_mb": end_rss,
                "delta_mb": sampler.peak - boot_rss,
                "samples": sampler.samples,
                "window_s": dwall,
            }

            # ---------- optional: pyinstrument wall-clock profile ----------
            if use_pyinstrument:
                from pyinstrument import Profiler

                p = Profiler(interval=0.0005)
                p.start()
                for _ in range(n_get):
                    await c.get("/api/v1/users/me", headers=H)
                p.stop()
                txt = p.output_text(unicode=True, color=False, show_all=True)
                (OUT_DIR / "raw_pyinstrument_get_users_me.txt").write_text(txt, encoding="utf-8")
                # The console is cp1252 on this box; pyinstrument's box-drawing
                # chars are not encodable, so print an ASCII-safe excerpt and
                # keep the full unicode report in the file above.
                safe = txt[:12000].encode("ascii", "replace").decode("ascii")
                print("\n=== pyinstrument (wall-clock, exclusive; ascii-safe excerpt) ===")
                print(safe)
                out["pyinstrument"] = True
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300, help="GET requests per phase")
    ap.add_argument("--n-post", type=int, default=100)
    ap.add_argument("--skip-testclient", action="store_true")
    ap.add_argument("--pyinstrument", action="store_true")
    ap.add_argument("--out", default="raw_prof_request_path.json")
    a = ap.parse_args()

    # Before any src.app import: the relaxations are read when Settings is
    # constructed, and os.chdir must happen before the relative paths the
    # phases resolve.
    bootstrap()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tw = Tripwire()
    install_tripwire(tw)
    index_lua_shas()

    result: dict = {"env": {"python": sys.version, "cwd": str(ROOT)}}
    if not a.skip_testclient:
        result["testclient"] = phase_testclient(tw, a.n, a.n_post)
    result["async"] = asyncio.run(phase_async(tw, a.n, a.pyinstrument))

    (OUT_DIR / a.out).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {OUT_DIR / a.out}")


if __name__ == "__main__":
    main()
