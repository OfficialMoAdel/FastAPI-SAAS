"""Round-trip census under PRODUCTION DEFAULTS + allocation profile.

Run:
    uv run python src/benchmarks/census.py

Importing this module is side-effect free. ``bootstrap()`` performs the chdir,
the dotenv load and the environment relaxation, and is called by ``main()``. A
test can therefore import ``hook``/``reset``/``unhook`` without booting the
application or rewriting the environment the pytest session runs under.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import sys
import time
import traceback
import tracemalloc
from collections.abc import Awaitable, Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redis.asyncio.client import Pipeline as AsyncPipeline
from redis.asyncio.client import Redis as AsyncRedis
from sqlalchemy import event
from sqlalchemy.engine import Engine

from src.benchmarks import protocol
from src.benchmarks.percentiles import nearest_rank

ROOT = Path(__file__).resolve().parents[2]

#: Commands whose second argument is a script SHA rather than a real argument, so
#: the SHA has to be resolved back to the script's name or the census reports
#: every Lua call as an opaque 40-character hash.
SCRIPT_COMMANDS: frozenset[str] = frozenset({"EVALSHA", "EVAL"})

sql: collections.Counter[str] = collections.Counter()
red: collections.Counter[str] = collections.Counter()

#: Commands that arrived inside a pipeline, counted separately. redis-py packs a
#: pipeline into ONE wire write, so "Redis round trips/request" and "Redis
#: commands/request" are different numbers and only the second one says anything
#: about what the application asked Redis to do.
pipelined: collections.Counter[str] = collections.Counter()

#: Command name -> "file.py:line" of the first application frame that issued it.
stacks: dict[str, str] = {}

#: Commands counted by the name Redis actually saw on the wire, before the
#: EVALSHA->script-name substitution. ``red`` is what a reader wants to look at;
#: ``wire`` is what has to be compared against ``INFO commandstats``, which
#: reports wire names and has never heard of our Lua script registry.
wire: collections.Counter[str] = collections.Counter()

#: script SHA -> script name, populated by ``bootstrap()``. When it is empty the
#: census falls back to the truncated SHA, which still identifies the script.
SHA: dict[str, str] = {}

_stack_attribution_enabled = True
_sql_listener: Callable[..., None] | None = None

#: Set by :func:`bootstrap`. Declared here so :func:`go` can read them without
#: importing ``src.app`` at module scope.
_app_main: Any = None
_settings: Any = None

_original_execute_command = AsyncRedis.execute_command
_original_pipeline_execute = AsyncPipeline.execute


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------


def set_stack_attribution(enabled: bool) -> bool:
    """Turn first-sight stack attribution on or off. Returns the previous value."""
    global _stack_attribution_enabled
    previous = _stack_attribution_enabled
    _stack_attribution_enabled = enabled
    return previous


@contextmanager
def stack_attribution(enabled: bool = True):
    """Scope stack attribution, restoring the previous setting on the way out."""
    previous = set_stack_attribution(enabled)
    try:
        yield
    finally:
        set_stack_attribution(previous)


def _attribute_site(command: str) -> str:
    """Attribute ``command`` to the first application frame that issued it.

    Returns ``""`` when attribution is disabled, or when the stack held no
    application frame.

    Extracting a stack is by a wide margin the most expensive thing this harness
    does - 142 us/call measured on this host - and the value it produced was
    already first-wins, because the caller stored it with ``dict.setdefault``. So
    re-extracting on every repeat of an already-attributed command bought nothing
    and charged the application for it on every request. Attributing once per
    distinct command name produces an identical census and removes the
    per-command cost from inside the timed window.

    That cost cannot be taken out of the window entirely: the hook is called BY
    the request, so whatever it does is inside ``t0..elapsed``. What can be done
    is make it amortise to ~0, and report what is left as a separate figure
    rather than folding it into the latency the census prints.
    """
    if not _stack_attribution_enabled:
        return ""
    seen = stacks.get(command)
    if seen is not None:
        return seen
    frames = [f for f in traceback.extract_stack()[:-1] if "site-packages" not in f.filename]
    if not frames:
        return ""
    site = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}"
    stacks[command] = site
    return site


def _command_name(args: Sequence[Any]) -> str:
    name = str(args[0]) if args else "?"
    if name.upper() in SCRIPT_COMMANDS and len(args) > 1:
        return SHA.get(str(args[1]), str(args[1])[:8])
    return name


def _record_command(args: Sequence[Any]) -> str:
    wire[str(args[0]) if args else "?"] += 1
    name = _command_name(args)
    red[name] += 1
    _attribute_site(name)
    return name


def _record_stacked_commands(pipeline: object) -> int:
    """Attribute every command a pipeline has staged. Returns how many.

    A pipeline is the reason ``Redis/req`` used to under-report. redis-py's
    ``Pipeline.execute_command`` only appends to ``command_stack``; nothing calls
    ``Redis.execute_command`` until the pipeline is flushed, and the flush sends
    the whole stack as ONE packed write. So a pipelined command never passed
    through the counter at all - 10 pipelined commands counted 0.
    """
    staged = getattr(pipeline, "command_stack", None)
    if not staged:
        return 0
    recorded = 0
    for entry in list(staged):
        args = entry[0] if isinstance(entry, tuple) and entry else ()
        _record_command(args)
        pipelined[_command_name(args)] += 1
        recorded += 1
    return recorded


async def _redis_execute_command(self: AsyncRedis, *args: Any, **kwargs: Any) -> Any:
    _record_command(args)
    return await _original_execute_command(self, *args, **kwargs)


async def _pipeline_execute(self: AsyncPipeline, raise_on_error: bool = True) -> Any:
    _record_stacked_commands(self)
    return await _original_pipeline_execute(self, raise_on_error=raise_on_error)


def _sql_before_cursor_execute(c: Any, cu: Any, s: str, p: Any, ctx: Any, em: Any) -> None:
    statement = " ".join(s.split())[:100]
    sql[statement] += 1
    _attribute_site(" ".join(s.split())[:60])


def hook() -> None:
    """Install the census hooks. Idempotent; pair with :func:`unhook`."""
    global _sql_listener
    if _sql_listener is not None:
        return
    _sql_listener = _sql_before_cursor_execute
    event.listen(Engine, "before_cursor_execute", _sql_listener)
    AsyncRedis.execute_command = _redis_execute_command  # type: ignore[method-assign]
    AsyncPipeline.execute = _pipeline_execute  # type: ignore[method-assign]


def unhook() -> None:
    """Remove the census hooks and restore the patched methods.

    A test that installs these must remove them: they patch redis-py and
    SQLAlchemy classes, so leaving them installed would tax every later test in
    the session.
    """
    global _sql_listener
    if _sql_listener is not None:
        event.remove(Engine, "before_cursor_execute", _sql_listener)
        _sql_listener = None
    AsyncRedis.execute_command = _original_execute_command  # type: ignore[method-assign]
    AsyncPipeline.execute = _original_pipeline_execute  # type: ignore[method-assign]


def reset() -> None:
    sql.clear()
    red.clear()
    pipelined.clear()
    stacks.clear()
    wire.clear()


async def commandstats_snapshot(client: Any) -> dict[str, int]:
    """Redis's own per-command call counts, as a plain dict."""
    return parse_commandstats(await client.info("commandstats"))


async def verify_phase_against_server(client: Any, before: dict[str, int]) -> None:
    """Compare the census accumulated since ``before`` with what Redis saw.

    Raises :class:`CensusDisagreement`, which fails the run. This is the check
    that makes "zero rate-limit commands" mean something: a census that
    over-counts proves nothing, and one that cannot be reconciled with the server
    is not evidence of anything at all.
    """
    after = await commandstats_snapshot(client)
    verify_counts_against_server(dict(wire), commandstats_delta(before, after))


# ---------------------------------------------------------------------------
# instrumentation overhead, reported separately and never folded in
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttributionOverhead:
    """The same call timed with stack attribution on, and with it off."""

    with_attribution: protocol.MicroResult
    without_attribution: protocol.MicroResult

    @property
    def overhead_us(self) -> float:
        return self.with_attribution.best_us - self.without_attribution.best_us


async def measure_attribution_overhead(
    call: Callable[[], Awaitable[Any]],
    *,
    iterations: int = 50,
    warmup: int = 10,
    repetitions: int = 5,
) -> AttributionOverhead:
    """Time ``call`` twice, once attributed and once not, and keep both figures.

    ``stacks`` is cleared before each side so each measurement pays the same
    first-sight attribution cost; this therefore measures the AMORTISED cost of
    the census as it actually runs, which is the number that belongs in a report.
    A single-command measurement would instead report the one-off cost of
    building the attribution table.

    The delta is instrumentation overhead. It is returned, never applied: the
    latency this harness prints must stay the latency the instrumented
    application actually took, or the census starts reporting a number about the
    harness instead of about the app.
    """
    with stack_attribution(False):
        stacks.clear()
        without = await protocol.measure_async(
            call,
            name="request_without_stack_attribution",
            iterations=iterations,
            warmup=warmup,
            repetitions=repetitions,
        )
    with stack_attribution(True):
        stacks.clear()
        with_attribution = await protocol.measure_async(
            call,
            name="request_with_stack_attribution",
            iterations=iterations,
            warmup=warmup,
            repetitions=repetitions,
        )
    return AttributionOverhead(with_attribution=with_attribution, without_attribution=without)


# ---------------------------------------------------------------------------
# agreeing with the server's own count
# ---------------------------------------------------------------------------


class CensusDisagreement(RuntimeError):
    """The patched counter and Redis disagree about how much work was done."""


def parse_commandstats(raw: str) -> dict[str, int]:
    """Parse the ``cmdstat_*`` section of ``INFO commandstats`` into call counts.

    Parsed from the raw string rather than via ``parse_info`` so the result does
    not depend on redis-py's nested-info representation staying the same.
    """
    counts: dict[str, int] = {}
    in_commandstats = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_commandstats = stripped.startswith("# Commandstats")
            continue
        if not in_commandstats or not stripped.startswith("cmdstat_"):
            continue
        name, _, rest = stripped.partition(":")
        for field in rest.split(","):
            key, _, value = field.partition("=")
            if key.strip() == "calls":
                counts[name.removeprefix("cmdstat_")] = int(value)
    return counts


def commandstats_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Per-command call-count increase between two ``commandstats`` snapshots."""
    return {name: after.get(name, 0) - seen for name, seen in before.items() if after.get(name, 0) - seen > 0}


def verify_counts_against_server(patched: dict[str, int], delta: dict[str, int]) -> None:
    """Fail the run when the census claims work the server did not see.

    The check is deliberately one-directional. Redis ``INFO commandstats`` is
    server-wide, so on a shared instance the server legitimately reports MORE
    calls of a command than this process issued - another client used it too. It
    can never legitimately report FEWER. So a count that outruns the server is a
    bug in the census (a double count, or a command attributed that was never
    sent) and a count the server outruns is expected and is left alone.

    Making it symmetric would need ``CONFIG RESETSTAT``, which is a
    server-wide destructive operation and is exactly what this harness must never
    do to an instance it does not own.
    """
    for name, counted in sorted(patched.items()):
        seen = delta.get(name, 0)
        if seen < counted:
            raise CensusDisagreement(
                f"census counted {counted} {name} command(s) but Redis reports {seen} "
                f"on this instance since the snapshot. A census that over-counts cannot be "
                f"used to prove the absence of an operation, which is the only thing this "
                f"harness is used for."
            )


# ---------------------------------------------------------------------------
# percentiles
# ---------------------------------------------------------------------------


def pct(s, q):
    """Percentile of ``s`` at quantile ``q``. Delegates to the shared estimator.

    This body used to be ``s[min(len(s) - 1, int(round(q * (len(s) - 1))))]``,
    a different rank convention from the other harness percentiles, so the same
    sample could be reported as two different numbers depending on which script
    produced it. See ``src/benchmarks/percentiles.py``.
    """
    return nearest_rank(s, q)


def _report_overhead(overhead: AttributionOverhead, label: str) -> None:
    print(f"  INSTRUMENTATION OVERHEAD for {label}: {overhead.overhead_us:.2f}us per request")
    print("    REPORTED SEPARATELY - not added to or subtracted from the latency above")
    print(f"    with attribution:    {overhead.with_attribution.best_us:.2f}us")
    print(f"      {protocol.render(overhead.with_attribution)}")
    print(f"    without attribution: {overhead.without_attribution.best_us:.2f}us")
    print(f"      {protocol.render(overhead.without_attribution)}")


def _print_census(phase: str, n: int, samples: list[float]) -> None:
    print(f"\n=== {phase} (n={n}) ===")
    print(f"  p50={pct(samples, 0.5):.2f}ms p95={pct(samples, 0.95):.2f}ms p99={pct(samples, 0.99):.2f}ms")
    print(f"  SQL/req={sum(sql.values()) / n:.2f}  Redis commands/req={sum(red.values()) / n:.2f}")
    if pipelined:
        print(f"  of which pipelined: {sum(pipelined.values()) / n:.2f}/req")
    for k, v in red.most_common():
        print(f"    [{v / n:.2f}/req] {k:32s} {stacks.get(k, '')}")
    for k, v in sql.most_common(6):
        print(f"    [{v / n:.2f}/req] SQL {k[:70]}")


def bootstrap() -> None:
    """Chdir, load the environment, relax the rate-limit ceilings, boot the app.

    Split out of module scope so importing this module costs nothing. Everything
    here must happen before ``src.app`` is imported, which is why the app import
    lives in this function rather than at the top of the file.
    """
    global SHA, _app_main, _settings
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / "src" / ".env", override=True)
    os.environ.setdefault("ENVIRONMENT", "local")
    # ONLY the rate-limit ceilings are relaxed (so warmup does not 429).
    # AUTO_BAN_ENABLED is left at its production default on purpose.
    os.environ["TIER_RATE_LIMITS"] = json.dumps({k: [100000, 3600] for k in ("free", "basic", "pro", "enterprise")})
    os.environ["SECURITY_BUDGETS"] = json.dumps(
        {
            k: [5000, 60]
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
    os.environ["DEFAULT_AUTHENTICATED_LIMIT"] = "[100000,3600]"
    os.environ["DEFAULT_ANONYMOUS_LIMIT"] = "[100000,3600]"
    os.environ["ANONYMOUS_IP_AGGREGATE_LIMIT"] = "[100000,3600]"

    import src.app.main as app_main
    from src.app.core.config import settings
    from src.app.core.utils.lua_manager import get_lua_manager

    print("AUTO_BAN_ENABLED =", settings.security.AUTO_BAN_ENABLED, "(production default)")
    manager = get_lua_manager()
    SHA = {manager.get_script(n).sha1: n for n in manager.get_script_names()}
    _app_main = app_main
    _settings = settings


async def go() -> None:
    bootstrap()

    import httpx
    from asgi_lifespan import LifespanManager

    from src.app.core.redis_provider import get_redis_pool

    hook()
    try:
        probe_redis = AsyncRedis(connection_pool=await get_redis_pool())
        async with LifespanManager(_app_main.app, startup_timeout=180, shutdown_timeout=60):
            transport = httpx.ASGITransport(app=_app_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=60) as c:
                r = await c.post(
                    "/api/v1/account/login",
                    data={"username": "e2e_test@example.com", "password": "TestPassword123!"},
                    headers={"X-Tenant-ID": "test-tenant-e2e", "User-Agent": UA},
                )
                tok = r.json()["access_token"]
                rt = r.json().get("refresh_token", "")
                h = {"Authorization": f"Bearer {tok}", "X-Tenant-ID": "test-tenant-e2e", "User-Agent": UA}
                for _ in range(20):
                    await c.get("/api/v1/users/me", headers=h)
                for _ in range(20):
                    await c.post("/api/v1/account/refresh", headers=h, json={"refresh_token": rt})

                n = 300
                reset()
                server_before = await commandstats_snapshot(probe_redis)
                samples = []
                for _ in range(n):
                    t0 = time.perf_counter()
                    await c.get("/api/v1/users/me", headers=h)
                    samples.append((time.perf_counter() - t0) * 1e3)
                _print_census(f"GET /users/me (AUTO_BAN_ENABLED={_settings.security.AUTO_BAN_ENABLED})", n, samples)
                await verify_phase_against_server(probe_redis, server_before)

                reset()
                server_before = await commandstats_snapshot(probe_redis)
                samples = []
                for _ in range(n):
                    t0 = time.perf_counter()
                    await c.post("/api/v1/account/refresh", headers=h, json={"refresh_token": rt})
                    samples.append((time.perf_counter() - t0) * 1e3)
                _print_census("POST /account/refresh", n, samples)
                await verify_phase_against_server(probe_redis, server_before)

                overhead = await measure_attribution_overhead(
                    lambda: c.get("/api/v1/users/me", headers=h),
                )
                _report_overhead(overhead, "GET /users/me")

                # ---- allocation profile of the hot path ----
                for _ in range(5):
                    await c.get("/api/v1/users/me", headers=h)
                tracemalloc.start(25)
                b0 = tracemalloc.take_snapshot()
                for _ in range(200):
                    await c.get("/api/v1/users/me", headers=h)
                b1 = tracemalloc.take_snapshot()
                tracemalloc.stop()
                st = b1.compare_to(b0, "lineno")
                print("\n=== ALLOCATION: 200x GET /users/me, top retained-by-site ===")
                tot = 0
                for d in st[:22]:
                    tot += d.size_diff
                    per_req_kib = d.size_diff / 200 / 1024
                    print(f"  {per_req_kib:9.2f} KiB/req  {d.count_diff / 200:7.1f} objs/req  {d.traceback[0]}")
                print(f"  net retained total: {tot / 200 / 1024:.1f} KiB/req")
    finally:
        unhook()


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def main() -> None:
    asyncio.run(go())


if __name__ == "__main__":
    main()
