"""A failed cache warmer must not poison the caller's AsyncSession.

``warm_all(session)`` and ``warm_specific(session)`` run on a session the
CALLER owns (``warm_caches()`` opens one ``local_session()`` and hands it to
``warm_all``). When a warmer raises, SQLAlchemy leaves the session in a failed
transaction: every later statement on it raises ``PendingRollbackError`` until
the transaction is rolled back.

Without isolation that produced two symptoms from ONE real failure:

* the second warmer failed for an unrelated reason, and the error was reported
  against the wrong warmer;
* the log said two things broke when one had.

A ``SAVEPOINT`` is the narrow remedy — it unwinds only the failed warmer's own
statements and leaves the outer transaction usable, which a bare ``rollback()``
would not (that would also discard whatever the caller staged outside the
warmer).

The fake session below reproduces the real ``AsyncSession`` failure contract so
the regression is observable without a database: once poisoned, ``execute``
raises until a savepoint is rolled back.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

from sqlalchemy.exc import PendingRollbackError

from src.app.core.cache.warmer import CacheWarmer


class _PoisonableSession:
    """Minimal ``AsyncSession`` stand-in with real failed-transaction semantics."""

    def __init__(self) -> None:
        self.poisoned = False
        self.savepoints_opened = 0
        self.rollback_to_savepoint = 0
        self.outer_rollbacks = 0

    @asynccontextmanager
    async def begin_nested(self) -> Any:
        self.savepoints_opened += 1
        try:
            yield self
        except BaseException:
            # ROLLBACK TO SAVEPOINT: only this warmer's work is discarded, so
            # the outer transaction becomes usable again.
            self.rollback_to_savepoint += 1
            self.poisoned = False
            raise

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        if self.poisoned:
            raise PendingRollbackError("session is in a failed transaction; rollback first")
        return None

    async def rollback(self) -> None:
        self.outer_rollbacks += 1
        self.poisoned = False


class TestSharedSessionIsolation:
    async def test_a_failed_warmer_does_not_poison_the_next_warmer(self) -> None:
        warmer = CacheWarmer()
        session = _PoisonableSession()

        @warmer.register("tenant")
        async def poisoned_warmer(_session: Any) -> int:
            # A statement failed: the transaction is now unusable.
            _session.poisoned = True
            raise RuntimeError("tenant warmer blew up")

        @warmer.register("feature_flags")
        async def healthy_warmer(_session: Any) -> int:
            await _session.execute("SELECT 1")
            return 7

        result = await warmer.warm_all(session)  # type: ignore[arg-type]

        assert result.errors is not None
        assert any("tenant warmer blew up" in error for error in result.errors)
        # ONE real failure is reported once, against the warmer that failed.
        assert not any("feature_flags" in error for error in result.errors)
        assert session.savepoints_opened == 2
        assert session.rollback_to_savepoint == 1
        # The caller's outer transaction was never rolled back wholesale.
        assert session.outer_rollbacks == 0

    async def test_warm_all_records_elapsed_duration_ms(self, monkeypatch) -> None:
        warmer = CacheWarmer()
        session = _PoisonableSession()
        timestamps: list[float] = []

        def fake_time() -> float:
            timestamps.append(100.0 + len(timestamps) * 0.05)
            return timestamps[-1]

        monkeypatch.setattr("src.app.core.cache.warmer.time.time", fake_time)

        @warmer.register("tenant")
        async def healthy(_session: Any) -> int:
            return 1

        result = await warmer.warm_all(session)  # type: ignore[arg-type]

        assert abs(result.duration_ms - (timestamps[-1] - timestamps[0]) * 1000) < 1e-9

    async def test_warm_specific_records_elapsed_duration_ms(self, monkeypatch) -> None:
        warmer = CacheWarmer()
        session = _PoisonableSession()
        timestamps: list[float] = []

        def fake_time() -> float:
            timestamps.append(200.0 + len(timestamps) * 0.05)
            return timestamps[-1]

        monkeypatch.setattr("src.app.core.cache.warmer.time.time", fake_time)

        @warmer.register("tenant")
        async def healthy(_session: Any) -> int:
            return 1

        result = await warmer.warm_specific("tenant", session)  # type: ignore[arg-type]

        assert abs(result.duration_ms - (timestamps[-1] - timestamps[0]) * 1000) < 1e-9

    async def test_the_second_warmer_still_produces_its_count(self) -> None:
        warmer = CacheWarmer()
        session = _PoisonableSession()

        @warmer.register("tenant")
        async def poisoned_warmer(_session: Any) -> int:
            _session.poisoned = True
            raise RuntimeError("boom")

        @warmer.register("feature_flags")
        async def healthy_warmer(_session: Any) -> int:
            await _session.execute("SELECT 1")
            return 3

        result = await warmer.warm_all(session)  # type: ignore[arg-type]

        # `tenant` failed, so tenants_cached stays 0 — but the run is not
        # aborted and the healthy warmer is not misreported.
        assert result.tenants_cached == 0
        assert len(result.errors or []) == 1

    async def test_a_timeout_inside_a_warmer_also_releases_the_savepoint(self) -> None:
        import asyncio

        warmer = CacheWarmer()
        session = _PoisonableSession()

        @warmer.register("tenant")
        async def slow_warmer(_session: Any) -> int:
            _session.poisoned = True
            await asyncio.sleep(10)
            return 1

        result = await warmer.warm_all(session, timeout=0.01)  # type: ignore[arg-type]

        assert result.errors is not None
        assert any("Timeout" in error for error in result.errors)
        assert session.rollback_to_savepoint == 1
        assert session.outer_rollbacks == 0

    async def test_warm_specific_uses_a_savepoint_on_the_callers_session(self) -> None:
        warmer = CacheWarmer()
        session = MagicMock()

        @warmer.register("tenant")
        async def boom(_session: Any) -> int:
            raise RuntimeError("nope")

        result = await warmer.warm_specific("tenant", session)

        assert result.errors is not None
        session.begin_nested.assert_called_once()

    async def test_a_caller_supplied_session_still_runs_warmers_sequentially(self) -> None:
        """The savepoint must not turn the shared-session path into a gather."""
        warmer = CacheWarmer()
        session = _PoisonableSession()
        order: list[str] = []

        @warmer.register("tenant")
        async def first(_session: Any) -> int:
            order.append("tenant:start")
            await _session.execute("SELECT 1")
            order.append("tenant:end")
            return 1

        @warmer.register("feature_flags")
        async def second(_session: Any) -> int:
            order.append("flags:start")
            await _session.execute("SELECT 1")
            order.append("flags:end")
            return 2

        await warmer.warm_all(session)  # type: ignore[arg-type]

        assert order == ["tenant:start", "tenant:end", "flags:start", "flags:end"]
