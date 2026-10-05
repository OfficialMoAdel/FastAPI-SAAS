#!/usr/bin/env python
"""Attribute latency in ``POST /api/v1/account/refresh`` — measurement only.

Why this exists
---------------
The 2026-09-21 Locust baseline put ``/account/refresh`` at p50 22 ms / p95 43 ms
against a <10 ms target. Contention and network were already ruled out
(single-user warm p50 27.6 ms ~= under-load p50 22 ms; Redis RTT 0.30 ms,
Postgres ``SELECT 1`` 0.50 ms, ~8 hops ~= 4 ms), leaving ~24 ms unexplained.

The handoff's stated next step is to **instrument query/commit timing before
changing code**, because the rotation security invariants (reuse detection,
rotation markers, family revocation) are easy to break. So: this script changes
nothing in ``src/``. It boots the app in-process and attaches timing probes.

What is instrumented
--------------------
* ``AsyncSession.execute`` / ``commit`` / ``flush`` — DB round trips, and
  crucially the number of *separate commits* (each one pays a remote WAL
  fsync to the VM at 192.168.121.147).
* Redis command execution — one probe at the driver level, so every client
  (cache_client, session_manager, jwt_service) is covered.
* Named phases of the refresh path, so milliseconds can be attributed rather
  than guessed at.

Requests are driven over ASGI (in-process), so no TCP stack, no proxy and no
host network latency pollutes the numbers — what remains is app + DB + Redis.

Usage
-----
    python scripts/profile_refresh.py                  # 50 refreshes, 5 warmup
    python scripts/profile_refresh.py -n 100 --warm 10
    python scripts/profile_refresh.py --json reports/refresh_profile.json

Rate limits are relaxed via process env only (``src/.env`` is never touched),
same overrides as ``scripts/run_perf_baseline.sh`` — otherwise the auth budget
of 5 req/min stops the run after five refreshes.
"""

from __future__ import annotations

# --- env overrides BEFORE any src.app import (config is read at import time) --
import os

os.environ.setdefault("AUTO_BAN_ENABLED", "false")
os.environ.setdefault(
    "SECURITY_BUDGETS",
    '{"auth":[10000,60],"mfa_verify":[5,60],"mfa_verify_global":[15,60],'
    '"mfa_mutate":[5,60],"magic_link_send":[3,60],"passkey_login":[10,60],'
    '"refresh":[10000,60],"ai_chat":[10000,60]}',
)
os.environ.setdefault("DEFAULT_ANONYMOUS_LIMIT", "[100000,3600]")
os.environ.setdefault("DEFAULT_AUTHENTICATED_LIMIT", "[100000,3600]")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import statistics  # noqa: E402
import sys  # noqa: E402
from collections import defaultdict  # noqa: E402
from contextvars import ContextVar  # noqa: E402
from pathlib import Path  # noqa: E402
from time import perf_counter  # noqa: E402
from typing import Any  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.benchmarks.percentiles import nearest_rank  # noqa: E402

API_V1 = "/api/v1"
DEFAULT_EMAIL = os.getenv("PERF_TEST_EMAIL", "perf_user_0@example.com")
DEFAULT_PASSWORD = os.getenv("PERF_TEST_PASSWORD", "Str1ngst!")
DEFAULT_TENANT = os.getenv("PERF_TEST_TENANT", "test-tenant-e2e")

# The IP-filter middleware blocks the default python-httpx UA as a "bad bot"
# (403 Forbidden - Bad bot detected), which has nothing to do with what we are
# measuring. Present a browser UA so the request reaches the route handler.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _headers() -> dict[str, str]:
    return {"X-Tenant-ID": DEFAULT_TENANT, "User-Agent": BROWSER_UA}


# --------------------------------------------------------------------------- #
# Recorder
# --------------------------------------------------------------------------- #
_current: ContextVar[dict[str, Any] | None] = ContextVar("refresh_profile_current", default=None)
# Phase stack, so a DB/Redis event can be attributed to the phase that caused
# it (probes are nested, so the innermost active phase owns the event).
_phase_stack: ContextVar[tuple[str, ...]] = ContextVar("refresh_profile_phase", default=())


def _record(kind: str, seconds: float, label: str = "") -> None:
    bucket = _current.get()
    if bucket is None:
        return
    stack = _phase_stack.get()
    bucket["events"].append((kind, seconds, label, stack[-1] if stack else "outside"))


class _Timer:
    """Context manager recording one span into the current request bucket."""

    def __init__(self, kind: str, label: str = "") -> None:
        self.kind = kind
        self.label = label
        self._t0 = 0.0

    def __enter__(self) -> _Timer:
        self._t0 = perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        _record(self.kind, perf_counter() - self._t0, self.label)


# --------------------------------------------------------------------------- #
# Probes
# --------------------------------------------------------------------------- #
def _statement_label(statement: Any) -> str:
    """Short, stable label for a SQLAlchemy statement."""
    try:
        text = str(statement)
    except Exception:  # pragma: no cover - defensive
        return "?"
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "?")
    verb = first.split(" ", 1)[0].upper()
    return verb if len(verb) <= 12 else verb[:12]


def install_db_probes() -> None:
    """Time every DB execute/commit/flush issued through AsyncSession."""
    from sqlalchemy.ext.asyncio import AsyncSession

    orig_execute = AsyncSession.execute
    orig_commit = AsyncSession.commit
    orig_flush = AsyncSession.flush

    async def execute(self: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = perf_counter()
        try:
            return await orig_execute(self, *args, **kwargs)
        finally:
            _record("db.execute", perf_counter() - t0, _statement_label(args[0] if args else None))

    async def commit(self: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = perf_counter()
        try:
            return await orig_commit(self, *args, **kwargs)
        finally:
            _record("db.commit", perf_counter() - t0)

    async def flush(self: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = perf_counter()
        try:
            return await orig_flush(self, *args, **kwargs)
        finally:
            _record("db.flush", perf_counter() - t0)

    AsyncSession.execute = execute  # type: ignore[method-assign]
    AsyncSession.commit = commit  # type: ignore[method-assign]
    AsyncSession.flush = flush  # type: ignore[method-assign]


def install_sql_probes() -> None:
    """Time every statement at the driver level, flush INSERTs included.

    ``AsyncSession.execute`` alone is not enough: ORM writes go through
    ``session.add()`` + ``commit()``, so the INSERTs (and the COMMIT itself)
    never pass through ``execute`` and would be invisible. Cursor events see
    all of them.
    """
    from sqlalchemy import event

    from src.app.core.db.database import get_async_engine

    sync_engine = get_async_engine().sync_engine
    if getattr(sync_engine, "_profiled", False):
        return
    sync_engine._profiled = True  # type: ignore[attr-defined]

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn: Any, cursor: Any, statement: Any, params: Any, context: Any, executemany: Any) -> None:
        conn.info["prof_t0"] = perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn: Any, cursor: Any, statement: Any, params: Any, context: Any, executemany: Any) -> None:
        t0 = conn.info.pop("prof_t0", None)
        if t0 is None:
            return
        _record("sql", perf_counter() - t0, _statement_label(statement))


def install_redis_probes() -> None:
    """Time Redis commands at the driver level (covers every client)."""
    try:
        from redis.asyncio import Redis
    except Exception:  # pragma: no cover
        return

    orig = Redis.execute_command

    async def execute_command(self: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = perf_counter()
        try:
            return await orig(self, *args, **kwargs)
        finally:
            label = str(args[0]).upper() if args else "?"
            _record("redis", perf_counter() - t0, label)

    Redis.execute_command = execute_command  # type: ignore[method-assign]


def _wrap_phase(module: Any, attr: str, label: str) -> bool:
    """Wrap an async function *in the module namespace that uses it*.

    Patching the defining module alone is not enough: ``auth/service.py``
    imports ``verify_token`` / ``blacklist_token`` by name, so it holds its own
    reference. Wrapping where the call site looks the name up is reliable.
    """
    orig = getattr(module, attr, None)
    if orig is None or getattr(orig, "_profiled", False):
        return False

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _phase_stack.set(_phase_stack.get() + (label,))
        t0 = perf_counter()
        try:
            return await orig(*args, **kwargs)
        finally:
            _record(f"phase:{label}", perf_counter() - t0)
            _phase_stack.reset(token)

    wrapper._profiled = True  # type: ignore[attr-defined]
    wrapper.__name__ = getattr(orig, "__name__", attr)
    setattr(module, attr, wrapper)
    return True


def _wrap_method(cls: Any, attr: str, label: str) -> bool:
    orig = getattr(cls, attr, None)
    if orig is None or getattr(orig, "_profiled", False):
        return False

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _phase_stack.set(_phase_stack.get() + (label,))
        t0 = perf_counter()
        try:
            return await orig(*args, **kwargs)
        finally:
            _record(f"phase:{label}", perf_counter() - t0)
            _phase_stack.reset(token)

    wrapper._profiled = True  # type: ignore[attr-defined]
    setattr(cls, attr, wrapper)
    return True


def install_phase_probes() -> list[str]:
    """Wrap the named stages of the refresh path. Returns what got wrapped."""
    wrapped: list[str] = []

    from src.app.core.security import jwt_service as jwt_mod
    from src.app.modules.auth import service as auth_service_mod
    from src.app.modules.auth.services import tokens as tokens_mod

    # Call-site namespaces (see _wrap_phase docstring).
    for attr, label in (("verify_token", "verify_token"), ("blacklist_token", "blacklist_token")):
        if _wrap_phase(auth_service_mod, attr, label):
            wrapped.append(f"auth.service.{attr}")
        if _wrap_phase(jwt_mod, attr, label):
            wrapped.append(f"jwt_service.{attr}")

    if _wrap_method(auth_service_mod.AuthService, "refresh_access_token", "refresh_total"):
        wrapped.append("AuthService.refresh_access_token")
    if _wrap_method(auth_service_mod.AuthService, "_reject_reused_refresh_token", "reuse_check"):
        wrapped.append("AuthService._reject_reused_refresh_token")
    if _wrap_method(auth_service_mod.AuthService, "_resolve_user_auth_info", "resolve_user"):
        wrapped.append("AuthService._resolve_user_auth_info")
    if _wrap_method(tokens_mod.TokenIssuer, "rotate_token_pair", "rotate_pair"):
        wrapped.append("TokenIssuer.rotate_token_pair")

    # Both are imported *inside* the refresh body, so patching the defining
    # module is enough — the late import picks up the wrapper.
    try:
        from src.app.core.security import login_helpers
        from src.app.core.security import session_manager as sm_mod

        if _wrap_phase(login_helpers, "register_login_session", "register_session"):
            wrapped.append("login_helpers.register_login_session")
        if _wrap_method(sm_mod.SessionManager, "invalidate_session", "invalidate_session"):
            wrapped.append("SessionManager.invalidate_session")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[warn] could not instrument session/login helpers: {exc}")

    # blacklist_token internals — it is the dominant phase, so break it down.
    if _wrap_phase(jwt_mod, "_resolve_tenant_id_for_token", "bl.resolve_tenant"):
        wrapped.append("jwt_service._resolve_tenant_id_for_token")
    if _wrap_phase(jwt_mod, "add_token_to_blacklist", "bl.redis_set"):
        wrapped.append("jwt_service.add_token_to_blacklist")
    try:
        from src.app.core.db.crud_token_blacklist import crud_token_blacklist

        if _wrap_method(type(crud_token_blacklist), "create", "bl.db_create"):
            wrapped.append("crud_token_blacklist.create")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[warn] could not instrument crud_token_blacklist: {exc}")

    return wrapped


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
async def _login(client: Any) -> bool:
    resp = await client.post(
        f"{API_V1}/account/login",
        data={"username": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
        headers=_headers(),
    )
    if resp.status_code != 200:
        print(f"[error] login failed HTTP {resp.status_code}: {resp.text[:400]}")
        return False
    if "refresh_token" not in resp.cookies:
        print(f"[error] login response carried no refresh_token cookie: {sorted(resp.cookies)}")
        return False
    return True


async def _one_refresh(client: Any) -> dict[str, Any]:
    bucket: dict[str, Any] = {"events": []}
    token = _current.set(bucket)
    t0 = perf_counter()
    try:
        resp = await client.post(
            f"{API_V1}/account/refresh",
            headers=_headers(),
        )
    finally:
        total = perf_counter() - t0
        _current.reset(token)

    if resp.status_code != 200:
        print(f"[error] refresh failed HTTP {resp.status_code}: {resp.text[:400]}")
    return {"status": resp.status_code, "total": total, "events": bucket["events"]}


def _pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile. ``q`` is a fraction.

    Delegates to ``src.benchmarks.percentiles.nearest_rank`` so there is one
    percentile implementation in the harness tree. The rank convention CHANGED:
    this used ``sorted(v)[round(q * (n - 1))]``, which at n=20 put p0.95 and
    p0.99 both at index 19 and so reported the same value for both; nearest
    rank puts p0.95 at index 18. p50 shifts by one sample for the same reason.
    At n=1000 the two conventions agree to within one index, so large-sample
    figures are unaffected in practice.

    Unlike the old version this raises on an empty sample rather than
    returning 0.0, so callers cannot report a 0.0 ms percentile for a
    measurement that was never taken.
    """
    return nearest_rank(values, q)


def _ms(values: list[float]) -> tuple[float, float, float, int]:
    """(mean, p50, p95, count) in milliseconds."""
    if not values:
        return (0.0, 0.0, 0.0, 0)
    scaled = [v * 1000 for v in values]
    return (statistics.fmean(scaled), _pct(scaled, 0.50), _pct(scaled, 0.95), len(scaled))


def summarise(runs: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [r["total"] for r in runs]

    per_phase: dict[str, list[float]] = defaultdict(list)
    per_kind_count: dict[str, list[int]] = defaultdict(list)
    per_kind_time: dict[str, list[float]] = defaultdict(list)
    statement_counts: dict[str, list[int]] = defaultdict(list)
    redis_labels: dict[str, list[int]] = defaultdict(list)

    # phase -> kind -> [count, seconds]
    res: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))

    for run in runs:
        counts: dict[str, int] = defaultdict(int)
        times: dict[str, float] = defaultdict(float)
        for kind, seconds, label, phase in run["events"]:
            counts[kind] += 1
            times[kind] += seconds
            if kind in ("redis", "sql", "db.commit"):
                cell = res[phase][kind]
                cell[0] += 1
                cell[1] += seconds
            if kind == "phase:refresh_total":
                # counted separately below; skip to avoid double attribution
                continue
            if kind.startswith("phase:"):
                per_phase[kind.removeprefix("phase:")].append(seconds)
            elif kind == "db.execute" and label:
                statement_counts[label].append(1)
            elif kind == "redis" and label:
                redis_labels[label].append(1)
        for kind, n in counts.items():
            per_kind_count[kind].append(n)
            per_kind_time[kind].append(times[kind])

    return {
        "runs": len(runs),
        "total": _ms(totals),
        "phases": {name: _ms(vals) for name, vals in sorted(per_phase.items())},
        "kinds": {
            kind: {
                "count_per_req": statistics.fmean(per_kind_count[kind]),
                "ms_mean": statistics.fmean([v * 1000 for v in per_kind_time[kind]]),
                "ms_p95": _pct([v * 1000 for v in per_kind_time[kind]], 0.95),
            }
            for kind in sorted(per_kind_count)
        },
        "statements_per_req": {
            label: statistics.fmean([1.0] * len(items)) * (len(items) / max(len(runs), 1))
            for label, items in statement_counts.items()
        },
        "redis_per_req": {
            label: len(items) / max(len(runs), 1) for label, items in redis_labels.items()
        },
        "resources_by_phase": {
            phase: {
                kind: {
                    "count": vals[0] / max(len(runs), 1),
                    "ms": vals[1] * 1000 / max(len(runs), 1),
                }
                for kind, vals in kinds.items()
            }
            for phase, kinds in res.items()
        },
    }


def render(summary: dict[str, Any], wrapped: list[str]) -> str:
    lines: list[str] = []
    mean, p50, p95, n = summary["total"]
    lines.append("=" * 72)
    lines.append(f" POST {API_V1}/account/refresh — in-process profile ({n} requests)")
    lines.append(f" total per request: mean {mean:.2f} ms | p50 {p50:.2f} ms | p95 {p95:.2f} ms")
    lines.append("=" * 72)

    lines.append("")
    lines.append("Phases (mean / p50 / p95 ms, calls per request)")
    lines.append("-" * 72)
    for name, (m, p5, p9, count) in summary["phases"].items():
        lines.append(f"  {name:<24} {m:8.2f} {p5:8.2f} {p9:8.2f}   x{count / n:.2f}")

    lines.append("")
    lines.append("DB / Redis per request")
    lines.append("-" * 72)
    for kind, stats in summary["kinds"].items():
        lines.append(
            f"  {kind:<20} x{stats['count_per_req']:5.2f}   "
            f"total mean {stats['ms_mean']:7.2f} ms  p95 {stats['ms_p95']:7.2f} ms"
        )

    # Attribution. Phase probes are nested (bl.* lives inside blacklist_token,
    # and every phase contains some sql/redis), so they are NOT additive — only
    # the service total and the top-level phases are.
    kinds = summary["kinds"]
    service = kinds.get("phase:refresh_total", {}).get("ms_mean", 0.0)
    top_level = [
        "verify_token",
        "reuse_check",
        "resolve_user",
        "rotate_pair",
        "blacklist_token",
        "invalidate_session",
        "register_session",
    ]
    phases = {k.removeprefix("phase:"): v for k, v in kinds.items() if k.startswith("phase:")}
    top_sum = sum(phases.get(name, {}).get("ms_mean", 0.0) for name in top_level)

    lines.append("")
    lines.append("-" * 72)
    lines.append(f"  total request              : {mean:7.2f} ms")
    lines.append(f"  inside AuthService.refresh : {service:7.2f} ms")
    lines.append(f"  outside the service        : {mean - service:7.2f} ms  (middleware, routing)")
    lines.append(f"  top-level phases summed    : {top_sum:7.2f} ms")
    lines.append(f"  unphased service work      : {service - top_sum:7.2f} ms")
    lines.append("")
    lines.append("  (sql / db.commit / redis totals below overlap the phases above —")
    lines.append("   they are the same milliseconds viewed by resource, not additive.)")
    if summary["statements_per_req"]:
        lines.append("")
        lines.append("Statement verbs per request (top 10)")
        for label, cnt in sorted(
            summary["statements_per_req"].items(), key=lambda kv: -kv[1]
        )[:10]:
            lines.append(f"  {label:<16} {cnt:6.2f}")
    if summary["redis_per_req"]:
        lines.append("")
        lines.append("Redis commands per request (top 10)")
        for label, cnt in sorted(summary["redis_per_req"].items(), key=lambda kv: -kv[1])[:10]:
            lines.append(f"  {label:<16} {cnt:6.2f}")

    lines.append("")
    lines.append("DB / Redis cost, attributed to the phase that caused it")
    lines.append("-" * 72)
    for phase, kinds in sorted(
        summary["resources_by_phase"].items(),
        key=lambda kv: -sum(v["ms"] for v in kv[1].values()),
    ):
        total_phase = sum(v["ms"] for v in kinds.values())
        parts = ", ".join(f"{k} x{v['count']:.1f} = {v['ms']:.2f} ms" for k, v in sorted(kinds.items()))
        lines.append(f"  {phase:<22} {total_phase:6.2f} ms   {parts}")
    lines.append("")
    lines.append(f"probes installed: {', '.join(wrapped)}")
    return "\n".join(lines)


async def main() -> int:
    global DEFAULT_EMAIL, DEFAULT_PASSWORD, DEFAULT_TENANT

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--requests", type=int, default=50, help="measured refreshes (default 50)")
    parser.add_argument("--warm", type=int, default=5, help="warmup refreshes, discarded (default 5)")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--json", dest="json_path", default=None, help="write raw summary as JSON")
    args = parser.parse_args()

    DEFAULT_EMAIL, DEFAULT_PASSWORD, DEFAULT_TENANT = args.email, args.password, args.tenant

    install_db_probes()
    install_redis_probes()

    from src.app.main import app  # imported late: probes must exist first

    wrapped = install_phase_probes()
    install_sql_probes()

    import httpx

    runs: list[dict[str, Any]] = []
    failures = 0

    # Run the real lifespan so Redis/DB pools initialise exactly as in prod.
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://profile.invalid",
            timeout=30.0,
        ) as client:
            if not await _login(client):
                return 1

            for i in range(args.warm + args.requests):
                run = await _one_refresh(client)
                if run["status"] != 200:
                    failures += 1
                    if failures > 3:
                        print("[error] too many failed refreshes, aborting")
                        return 1
                    continue
                if i >= args.warm:
                    runs.append(run)

    if not runs:
        print("[error] no successful refreshes measured")
        return 1

    summary = summarise(runs)
    report = render(summary, wrapped)
    print(report)

    if args.json_path:
        path = REPO_ROOT / args.json_path if not Path(args.json_path).is_absolute() else Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nJSON summary -> {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
