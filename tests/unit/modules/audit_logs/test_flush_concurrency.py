"""Two concurrent audit flushes must not double-process a record.

`flush_audit_log_buffer` drains the buffer with `LRANGE` + `LTRIM` in one
pipeline, and the audit flush is scheduled every 30s with a 60s timeout — so
an interval shorter than the timeout means a second run CAN start while the
first is still going. Whether that is a correctness bug depends entirely on
whether the drain is atomic:

- `client.pipeline()` defaults to `transaction=True`, i.e. MULTI/EXEC, so the
  read and the trim are one indivisible step. Two flushes then partition the
  buffer: no record is read twice, none is skipped.
- `client.pipeline(transaction=False)` would send two round trips. Another
  flush could trim between this call's LRANGE and LTRIM, and the records this
  call is holding would be trimmed out from under it.

The existing flush tests use `MagicMock` pipelines, which model neither case
— a mock cannot express the hazard. `_FakeRedis` below models both, so this
test fails if the transaction is ever dropped rather than merely asserting
that today's code looks right.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import src.app.modules.ai.models  # noqa: F401  (mapper initialisation)
import src.app.modules.billing.models  # noqa: F401
from src.app.modules.audit_logs import service as audit_service

pytestmark = pytest.mark.asyncio


class _Pipe:
    """A pipeline that honours the `transaction` flag the way Redis does."""

    def __init__(self, redis: _FakeRedis, transaction: bool) -> None:
        self._redis = redis
        self._transaction = transaction
        self._queued: list[tuple[str, tuple[Any, ...]]] = []

    async def __aenter__(self) -> _Pipe:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def lrange(self, key: str, start: int, stop: int) -> None:
        self._queued.append(("lrange", (key, start, stop)))

    def ltrim(self, key: str, start: int, stop: int) -> None:
        self._queued.append(("ltrim", (key, start, stop)))

    def lpush(self, key: str, *values: str) -> None:
        self._queued.append(("lpush", (key, *values)))

    async def execute(self) -> list[Any]:
        if self._transaction:
            # MULTI/EXEC: queued together, applied under the lock, indivisible.
            async with self._redis.lock:
                return [self._redis._apply(name, args) for name, args in self._queued]

        # No MULTI: each command is its own round trip, so another coroutine
        # can slip in between them. This is the hazard, faithfully modelled.
        results = []
        for name, args in self._queued:
            await asyncio.sleep(0)  # the network round trip
            results.append(self._redis._apply(name, args))
        return results


class _FakeRedis:
    """Enough Redis to make the drain's atomicity observable.

    ``drained`` records every payload any caller received from LRANGE, which
    is the property under test: a record appearing there twice has been read
    twice and would be inserted twice.
    """

    def __init__(self, items: list[str]) -> None:
        self.items = list(items)
        self.lock = asyncio.Lock()
        self.drained: list[str] = []

    def pipeline(self, transaction: bool = True) -> _Pipe:
        return _Pipe(self, transaction)

    async def llen(self, key: str) -> int:
        assert key == audit_service.AUDIT_LOG_BUFFER_KEY
        return len(self.items)

    def _apply(self, name: str, args: tuple[Any, ...]) -> Any:
        if name == "lrange":
            key, start, stop = args
            assert key == audit_service.AUDIT_LOG_BUFFER_KEY
            window = list(self.items[start : stop + 1 if stop != -1 else None])
            self.drained.extend(window)
            return window
        if name == "ltrim":
            _, start, stop = args
            self.items = self.items[start:] if stop == -1 else self.items[start : stop + 1]
            return True
        if name == "lpush":
            key, *values = args
            assert key == audit_service.AUDIT_LOG_BUFFER_KEY
            self.items[:0] = list(values)
            return len(self.items)
        raise AssertionError(f"unexpected command {name}")


def _payload(i: int) -> str:
    return json.dumps(
        {
            "tenant_id": str(uuid4()),
            "action": f"test.action.{i}",
            "entity_type": "widget",
            "entity_id": i,
            "user_id": None,
        }
    )


async def _run_two_concurrent_flushes(items: list[str], transaction: bool) -> list[str]:
    """Flush the same buffer twice concurrently; return the rows inserted."""
    redis = _FakeRedis(items)
    db = AsyncMock()

    def _pipeline(*, transaction: bool = True) -> _Pipe:
        return _Pipe(redis, transaction)

    redis.pipeline = _pipeline  # type: ignore[method-assign]

    # One batch per call (batch size 2, budget 2) so the two invocations
    # compete for the same window. The batching LOOP is not what is under
    # test; the drain is.
    with (
        patch.object(audit_service, "redis_cache", MagicMock(client=redis)),
        patch.object(audit_service, "AUDIT_LOG_BATCH_SIZE", 2),
    ):
        await asyncio.gather(
            audit_service.flush_audit_log_buffer(max_flush_per_run=2, db=db),
            audit_service.flush_audit_log_buffer(max_flush_per_run=2, db=db),
        )

    return redis.drained


async def test_concurrent_flushes_do_not_double_process() -> None:
    """THE property: with a transaction-wrapped drain, each record flushes once.

    Falsifiability: run the same scenario against a non-transactional pipeline
    (below) and the double-processing appears. That contrast is what makes
    this an assertion about the code rather than about the scheduler.
    """
    items = [_payload(i) for i in range(4)]

    drained = await _run_two_concurrent_flushes(items, transaction=True)

    assert len(drained) == len(set(drained)), (
        f"a record was drained twice, so it would be inserted twice: {len(drained)} "
        f"drained, {len(set(drained))} distinct"
    )
    assert sorted(drained) == sorted(items), (
        f"the two concurrent flushes did not partition the buffer: {drained}"
    )


async def test_a_non_transactional_drain_would_double_process() -> None:
    """The counter-test that gives the test above its meaning.

    Models `client.pipeline(transaction=False)` — the exact edit that would
    introduce the bug. If this ever stops failing, the fake is no longer
    faithful and the test above is proving nothing.
    """
    items = [_payload(i) for i in range(4)]

    with pytest.raises(AssertionError, match="drained twice"):
        # If the drain were unsafe, the assertion in the test above would
        # fire. Here we reproduce that same assertion against a
        # non-transactional pipeline to prove the fake can express the bug.
        async def _run() -> list[str]:
            redis = _FakeRedis(items)
            db = AsyncMock()

            def _pipeline(*, transaction: bool = False) -> _Pipe:
                return _Pipe(redis, False)

            redis.pipeline = _pipeline  # type: ignore[method-assign]

            with (
                patch.object(audit_service, "redis_cache", MagicMock(client=redis)),
                patch.object(audit_service, "AUDIT_LOG_BATCH_SIZE", 2),
            ):
                await asyncio.gather(
                    audit_service.flush_audit_log_buffer(max_flush_per_run=2, db=db),
                    audit_service.flush_audit_log_buffer(max_flush_per_run=2, db=db),
                )
            return redis.drained

        drained = await _run()
        assert len(drained) == len(set(drained)), (
            f"a record was drained twice: {len(drained)} drained, {len(set(drained))} distinct"
        )
        assert sorted(drained) == sorted(items)


async def test_audit_buffer_drain_uses_a_transaction() -> None:
    """The drain must not opt out of MULTI/EXEC.

    A direct statement of the assumption the whole file rests on. If this
    fails, the concurrency tests above are describing a property the code no
    longer has.
    """
    seen: list[bool] = []

    class _RecordingPipe(_Pipe):
        pass

    redis = _FakeRedis([])
    real_pipeline = redis.pipeline

    def _pipeline(*, transaction: bool = True) -> _Pipe:
        seen.append(transaction)
        return real_pipeline(transaction=transaction)

    redis.pipeline = _pipeline  # type: ignore[method-assign]
    db = AsyncMock()

    with patch.object(audit_service, "redis_cache", MagicMock(client=redis)):
        await audit_service.flush_audit_log_buffer(max_flush_per_run=1, db=db)

    assert seen, "no pipeline was created"
    assert all(seen), (
        f"the audit drain opened a non-transactional pipeline: transaction={seen}. "
        f"LRANGE+LTRIM would then be two round trips and two concurrent flushes "
        f"could read the same records."
    )
