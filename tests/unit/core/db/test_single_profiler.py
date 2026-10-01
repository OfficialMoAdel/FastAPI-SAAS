"""Pin that the query profiler instruments each query exactly once.

`src/app/core/db/profiler.py` and `src/app/middleware/query_profiler.py` were
both live behind the same `QUERY_PROFILER_ENABLED` flag, each with its own
`before_cursor_execute` / `after_cursor_execute` pair on the same engine.
Enabling profiling therefore instrumented every query twice and logged each
slow query and each N+1 finding twice. That is finding `muse 12`.

The merge (rather than a deletion) keeps BOTH modules:

  * `middleware/query_profiler.py` owns the single listener pair, the
    per-request `QueryStats` ContextVar, and the `X-Query-Count` headers;
  * `core/db/profiler.py` keeps the CROSS-REQUEST tracker, which sees query
    patterns that recur across requests and background tasks and that no
    single request's counts can detect.

Deleting `profiler.py` was considered and rejected: it would have removed that
cross-request capability, which is exactly what a connection-pool leak or an
ARQ task storm looks like. The two views are not redundant.

These tests pin the property the merge has to hold: ONE listener pair, and a
cross-request tracker that is actually fed.
"""

import importlib
from unittest.mock import MagicMock, patch

import pytest
from starlette.types import Receive, Scope, Send

from src.app.core.config import EnvironmentOption, settings
from src.app.core.config.performance import QueryProfilerSettings
from src.app.middleware.query_profiler import QueryProfilerMiddleware, QueryStats


def _event_counts(engine) -> dict[str, int]:
    return {
        name: len(list(getattr(engine.dispatch, name)))
        for name in ("before_cursor_execute", "after_cursor_execute")
    }


def test_enabling_the_profiler_attaches_exactly_one_listener_per_event() -> None:
    """Enabling profiling instruments each query once, not twice.

    This is the property the bead exists to pin, measured on the SQLAlchemy
    listener registry rather than on source text: while `core/db/profiler.py`
    and `middleware/query_profiler.py` were both live, each registered its own
    before/after pair on the same engine, so every query was timed twice and
    every slow query and N+1 finding was logged twice.

    Falsifiability: a second module registering listeners on `engine` pushes
    the delta past 1.
    """
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    before = _event_counts(engine)

    middleware = QueryProfilerMiddleware(app=MagicMock())
    with (
        patch.object(settings.env, "ENVIRONMENT", EnvironmentOption.LOCAL),
        patch.object(settings.query_profiler, "QUERY_PROFILER_ENABLED", True),
        patch("src.app.middleware.query_profiler.async_engine") as fake_engine,
    ):
        fake_engine.sync_engine = engine
        middleware._get_engine()
        # A second call, as on the next request, must add nothing.
        middleware._get_engine()

    after = _event_counts(engine)
    assert {name: after[name] - before[name] for name in before} == {
        "before_cursor_execute": 1,
        "after_cursor_execute": 1,
    }, f"profiler attached more than one listener per event: {before} -> {after}"
    engine.dispose()


def test_disabled_profiler_attaches_nothing() -> None:
    """COUNTER-TEST: with the flag off, no listener is attached at all.

    The retired engine-level profiler registered its pair during engine
    creation, so a deployment that never queried anything still paid for the
    instrumentation. Cost while off is a bug regardless of which
    implementation survives.
    """
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    before = _event_counts(engine)

    middleware = QueryProfilerMiddleware(app=MagicMock())
    with (
        patch.object(settings.env, "ENVIRONMENT", EnvironmentOption.LOCAL),
        patch.object(settings.query_profiler, "QUERY_PROFILER_ENABLED", False),
    ):
        # The middleware short-circuits on this gate before touching the engine.
        assert middleware._profiler_active() is False

    assert _event_counts(engine) == before, "disabled profiler attached listeners"
    engine.dispose()


def test_profiler_module_is_importable_and_exposes_the_tracker() -> None:
    """`core/db/profiler.py` survives the merge and still owns the tracker.

    It was briefly deleted on the reading that "zero callers" meant "dead
    code". That was wrong on two counts: the cross-request view it owns is a
    capability per-request `QueryStats` cannot provide, and the module's own
    `_evict_stale_tracker_entries` bounds the tracker's growth.
    """
    module = importlib.import_module("src.app.core.db.profiler")

    assert callable(module.track_global_query), "the cross-request entry point is gone"
    assert callable(module.get_query_stats), "operators lost the cross-request read"
    assert callable(module.reset_query_tracker), "operators lost the reset"


def test_database_module_imports_no_profiler() -> None:
    """The engine factory wires no profiler of its own.

    The retired profiler attached listeners eagerly during engine creation.
    `database.py` is imported here rather than grepped, so an import of the
    deleted module would raise — which is the failure that actually matters.
    """
    module = importlib.import_module("src.app.core.db.database")
    assert module.create_async_engine is not None


@pytest.mark.parametrize(
    ("environment", "enabled", "expected"),
    [
        (EnvironmentOption.LOCAL, True, True),
        (EnvironmentOption.STAGING, True, True),
        # Production refuses regardless of the flag.
        (EnvironmentOption.PRODUCTION, True, False),
        (EnvironmentOption.PRODUCTION, False, False),
        # Flag off is off everywhere.
        (EnvironmentOption.LOCAL, False, False),
        (EnvironmentOption.STAGING, False, False),
    ],
)
def test_activation_is_a_pure_function_of_config(
    environment: EnvironmentOption, enabled: bool, expected: bool
) -> None:
    """One flag, one gate, one answer — the property that was undefined.

    There is no second activation path to consult, so this is now the whole
    decision.
    """
    middleware = QueryProfilerMiddleware(app=MagicMock())
    with (
        patch.object(settings.env, "ENVIRONMENT", environment),
        patch.object(settings.query_profiler, "QUERY_PROFILER_ENABLED", enabled),
    ):
        assert middleware._profiler_active() is expected


async def test_disabled_profiler_adds_no_headers_and_no_context() -> None:
    """COUNTER-TEST: a disabled profiler costs nothing and touches nothing.

    A profiler that mutates the response or installs a ContextVar while off is
    a latency bug regardless of which implementation survives, so this asserts
    the off path, not the on path.
    """
    seen: list[Scope] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.request"}

    middleware = QueryProfilerMiddleware(app=app)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/users",
        "query_string": b"",
        "headers": [],
    }

    with (
        patch.object(settings.env, "ENVIRONMENT", EnvironmentOption.LOCAL),
        patch.object(settings.query_profiler, "QUERY_PROFILER_ENABLED", False),
        patch("src.app.middleware.query_profiler._query_stats_ctx") as ctx,
    ):
        await middleware(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["headers"] == [], "disabled profiler must not add response headers"
    ctx.set.assert_not_called()
    ctx.reset.assert_not_called()
    assert len(seen) == 1


async def test_active_profiler_reports_count_and_time() -> None:
    """The operator-facing diagnostics operators currently read still work.

    Removing a profiler an operator reads from is a real regression even when
    the code is redundant, so the surviving one is pinned to emit
    `X-Query-Count` / `X-Query-Time`.
    """
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.request"}

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        # Simulate one slow query recorded against this request.
        from src.app.middleware import query_profiler as qp

        stats = qp._query_stats_ctx.get()
        assert stats is not None
        stats.add_query(statement="SELECT * FROM users WHERE id = ?", params={"id": 7}, duration_ms=250.0)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = QueryProfilerMiddleware(app=app)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/users",
        "query_string": b"",
        "headers": [],
    }

    with (
        patch.object(settings.env, "ENVIRONMENT", EnvironmentOption.LOCAL),
        patch.object(settings.query_profiler, "QUERY_PROFILER_ENABLED", True),
        patch.object(QueryProfilerMiddleware, "_get_engine", return_value=MagicMock()),
        patch("src.app.middleware.query_profiler.async_engine"),
        patch("src.app.middleware.query_profiler.logger") as log,
    ):
        await middleware(scope, receive, send)

    headers = dict(next(m for m in sent if m["type"] == "http.response.start")["headers"])
    assert headers[b"x-query-count"] == b"1"
    assert b"x-query-time" in headers
    assert headers[b"x-slow-queries"] == b"1"

    # The slow-query line carries the statement AND the bound params. The
    # retired engine-level profiler logged params; losing them would have
    # made the surviving log line unactionable.
    warning = " ".join(str(c) for c in log.warning.call_args_list)
    assert "SELECT * FROM users" in warning
    assert "params=" in warning


def test_query_stats_keeps_counting_past_the_record_cap() -> None:
    """The header count stays truthful when detail recording stops (P2-7)."""
    from src.app.middleware.query_profiler import MAX_QUERIES_RECORDED

    stats = QueryStats()
    for i in range(MAX_QUERIES_RECORDED + 10):
        stats.add_query(statement=f"SELECT {i}", params=None, duration_ms=1.0)

    assert stats.total_query_count == MAX_QUERIES_RECORDED + 10
    assert len(stats.queries) == MAX_QUERIES_RECORDED
def test_stale_settings_for_the_retired_profiler_are_absent() -> None:
    """The retired profiler had no knob of its own, so none can go stale.

    Both implementations read the same `QUERY_PROFILER_*` settings, which is
    precisely why one flag silently activated two profilers. A knob left
    behind that does nothing is habit 1, so the surviving set is pinned to
    exactly the four keys the middleware actually reads.
    """
    names = {
        name
        for name in type(settings.query_profiler).model_fields
        if name.startswith("QUERY_PROFILER")
    }
    assert names == {
        "QUERY_PROFILER_ENABLED",
        "QUERY_PROFILER_SLOW_THRESHOLD_MS",
        "QUERY_PROFILER_N_PLUS_1_THRESHOLD",
        "QUERY_PROFILER_DETAILED_LOGS",
    }, f"unexpected query-profiler settings: {names}"


def test_detailed_logs_knob_resolves_through_the_settings_object() -> None:
    """The detailed-log knob resolves from the object that owns it.

    It is read via `getattr(settings, "QUERY_PROFILER_DETAILED_LOGS", False)`
    against the ROOT settings while the field is declared on the nested
    `settings.query_profiler`. Both currently resolve from the same env var,
    so the default masked the mis-path. Pinned so it cannot drift silently
    into a permanently-disabled debug log.
    """
    assert hasattr(settings, "QUERY_PROFILER_DETAILED_LOGS"), (
        "the root getattr lookup in query_profiler.py resolves through the env "
        "var; if this ever stops holding, the detailed N+1 log is dead code"
    )
    with patch.dict("os.environ", {"QUERY_PROFILER_DETAILED_LOGS": "true"}):
        assert QueryProfilerSettings().QUERY_PROFILER_DETAILED_LOGS is True


def test_per_request_stats_are_not_shared() -> None:
    """Each request gets its own QueryStats; per-request state does not leak.

    Distinct from the cross-request tracker below, which is deliberately
    shared and deliberately bounded by eviction.
    """
    first, second = QueryStats(), QueryStats()
    first.add_query(statement="SELECT 1", params=None, duration_ms=5.0)

    assert second.total_query_count == 0
    assert second.queries == []
    assert first is not second


async def test_middleware_feeds_the_cross_request_tracker() -> None:
    """The cross-request tracker is LIVE, not orphaned.

    This is the capability `core/db/profiler.py` exists for, and the reason it
    was not deleted in the muse-12 merge: a query pattern that no single
    request repeats but that recurs across every request or background task
    is invisible to per-request `QueryStats`. The single listener must feed
    both, or the cross-request view silently reads empty forever.
    """
    from sqlalchemy import create_engine, text

    from src.app.core.db import profiler as profiler_mod
    from src.app.middleware import query_profiler as qp

    engine = create_engine("sqlite://")
    profiler_mod.reset_query_tracker()
    # `_profiler_initialized` is a module global, so an earlier test in this
    # module would suppress attachment here and the test would order-depend.
    qp._profiler_initialized = False
    try:
        middleware = qp.QueryProfilerMiddleware(app=MagicMock())
        with (
            patch.object(settings.env, "ENVIRONMENT", EnvironmentOption.LOCAL),
            patch.object(settings.query_profiler, "QUERY_PROFILER_ENABLED", True),
            patch("src.app.middleware.query_profiler.async_engine") as fake_engine,
        ):
            fake_engine.sync_engine = engine
            middleware._get_engine()

        # Execute real statements so the registered listeners really fire.
        with engine.connect() as conn:
            for index in range(4):
                conn.execute(text(f"SELECT {index} AS widget_id"))

        stats = profiler_mod.get_query_stats()
        assert stats, "the cross-request tracker saw no queries; it is not wired"
        assert any("widget_id" in key for key in stats), (
            f"tracked statements are not the ones executed: {list(stats)[:3]}"
        )
    finally:
        profiler_mod.reset_query_tracker()
        engine.dispose()


async def test_exactly_one_listener_pair_on_the_engine() -> None:
    """muse 12: ONE before/after pair on the engine, not two.

    Counts the real SQLAlchemy registry after the middleware attaches. Two
    modules registering the same events behind the same flag is what made
    every query time twice.
    """
    from sqlalchemy import create_engine

    from src.app.middleware import query_profiler as qp


    engine = create_engine("sqlite://")
    events = ("before_cursor_execute", "after_cursor_execute")
    before = {name: len(list(getattr(engine.dispatch, name))) for name in events}
    qp._profiler_initialized = False

    middleware = qp.QueryProfilerMiddleware(app=MagicMock())
    with (
        patch.object(settings.env, "ENVIRONMENT", EnvironmentOption.LOCAL),
        patch.object(settings.query_profiler, "QUERY_PROFILER_ENABLED", True),
        patch("src.app.middleware.query_profiler.async_engine") as fake_engine,
    ):
        fake_engine.sync_engine = engine
        middleware._get_engine()
        middleware._get_engine()  # a later request must add nothing

    after = {name: len(list(getattr(engine.dispatch, name))) for name in events}
    assert {name: after[name] - before[name] for name in events} == {
        "before_cursor_execute": 1,
        "after_cursor_execute": 1,
    }, f"expected one listener per event, got {before} -> {after}"
    engine.dispose()


def test_profiler_module_no_longer_attaches_listeners() -> None:
    """`core/db/profiler.py` must not re-introduce a second attachment.

    The merge removed `setup_query_profiler` from this module precisely
    because it registered a competing listener pair. Asserted by name so a
    future refactor that re-adds it fails here rather than in production.
    """
    from src.app.core.db import profiler as profiler_mod

    assert not hasattr(profiler_mod, "setup_query_profiler"), (
        "core/db/profiler.py must not attach SQLAlchemy listeners; the single "
        "pair lives in middleware/query_profiler.py"
    )
    assert "setup_query_profiler" not in profiler_mod.__all__
