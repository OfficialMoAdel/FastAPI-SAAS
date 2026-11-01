"""Phase 4.3: the recovery tick claims the whole page in ONE round trip.

The batch exists for the OUTAGE case — a worker outage leaves up to
``WEBHOOK_RECOVERY_BATCH_SIZE`` rows stuck and the per-row claim cost one Redis
round trip each. What must NOT change while batching the claim:

* claim BEFORE enqueue (a running ``deliver()`` holds the same lock);
* release BEFORE enqueue (E6 — otherwise the enqueued job finds the lock held
  and skips the delivery);
* one commit per recovered row;
* a row whose claim is False is IN FLIGHT and is skipped, never enqueued;
* a Redis failure degrades to the per-row claim instead of discarding the page.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.webhooks.delivery import recover_stuck_deliveries


def _delivery(delivery_id: int) -> MagicMock:
    delivery = MagicMock()
    delivery.id = delivery_id
    delivery.tenant_id = f"tenant-{delivery_id}"
    return delivery


def _db(rows: list) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db.execute.return_value = result
    return db


def _cache(verdicts: list[bool] | Exception, *, has_client: bool = True) -> AsyncMock:
    """A cache client whose batched claim answers ``verdicts``."""
    cache = AsyncMock()
    cache.client = MagicMock() if has_client else None
    if isinstance(verdicts, Exception):
        cache.set_nx_many.side_effect = verdicts
    else:
        cache.set_nx_many.return_value = verdicts
    cache.set_nx.return_value = True
    cache.release_lock_if_owned.return_value = True
    return cache


def _patch_recovery(db: AsyncMock):
    mock_ctx = patch("src.app.modules.webhooks.delivery.get_db_context")
    mock_bypass = patch("src.app.modules.webhooks.delivery.bypass_rls")
    return mock_ctx, mock_bypass


async def _run(ctx: dict, db: AsyncMock, cache: AsyncMock) -> int:
    mock_ctx, mock_bypass = _patch_recovery(db)
    with (
        mock_ctx as db_ctx,
        mock_bypass as rls_bypass,
        patch("src.app.core.cache.client.cache_client", cache),
    ):
        cm = AsyncMock()
        cm.__aenter__.return_value = db
        cm.__aexit__.return_value = False
        db_ctx.return_value = cm
        rls_bypass.return_value.__aenter__.return_value = db
        return await recover_stuck_deliveries(ctx)


class TestThePageIsClaimedOnce:
    @pytest.mark.asyncio
    async def test_three_rows_cost_one_claim_round_trip(self) -> None:
        rows = [_delivery(1), _delivery(2), _delivery(3)]
        redis = AsyncMock()
        cache = _cache([True, True, True])

        recovered = await _run({"redis": redis}, _db(rows), cache)

        assert recovered == 3
        cache.set_nx_many.assert_awaited_once()
        items = cache.set_nx_many.await_args.args[0]
        assert cache.set_nx_many.await_args.kwargs["ttl"] == 60
        assert [key for key, _token in items] == [f"webhook:deliver_lock:{row.id}" for row in rows]
        cache.set_nx.assert_not_awaited()  # the per-key claim is the FALLBACK, not the path
        assert redis.enqueue_job.await_count == 3

    @pytest.mark.asyncio
    async def test_each_row_keeps_its_own_commit(self) -> None:
        db = _db([_delivery(1), _delivery(2)])

        recovered = await _run({"redis": AsyncMock()}, db, _cache([True, True]))

        assert recovered == 2
        assert db.commit.await_count == 2, "one commit per recovered row, not one per batch"

    @pytest.mark.asyncio
    async def test_the_release_uses_the_token_that_was_claimed(self) -> None:
        """D8: an ownership token, not a blind delete."""
        cache = _cache([True])

        await _run({"redis": AsyncMock()}, _db([_delivery(9)]), cache)

        claimed_key, claimed_token = cache.set_nx_many.await_args.args[0][0]
        released = [call.args for call in cache.release_lock_if_owned.await_args_list]
        assert released, "the claim must be released"
        assert all(key == claimed_key for key, _token in released)
        assert all(token == claimed_token for _key, token in released)


class TestSkippedRows:
    @pytest.mark.asyncio
    async def test_a_row_in_flight_is_skipped_and_never_released(self) -> None:
        """Claim False means a running deliver() owns that lock."""
        redis = AsyncMock()
        cache = _cache([True, False, True])

        recovered = await _run(
            {"redis": redis}, _db([_delivery(1), _delivery(2), _delivery(3)]), cache
        )

        assert recovered == 2
        enqueued = [call.kwargs["delivery_id"] for call in redis.enqueue_job.await_args_list]
        assert enqueued == ["1", "3"]
        released_keys = {call.args[0] for call in cache.release_lock_if_owned.await_args_list}
        assert "webhook:deliver_lock:2" not in released_keys, "never release a lock we do not own"

    @pytest.mark.asyncio
    async def test_no_redis_client_still_recovers_the_page(self) -> None:
        """The pre-4.3 behaviour: without a client nothing can be claimed or released."""
        redis = AsyncMock()
        cache = _cache([], has_client=False)

        recovered = await _run({"redis": redis}, _db([_delivery(1), _delivery(2)]), cache)

        assert recovered == 2
        cache.set_nx_many.assert_not_awaited()
        cache.release_lock_if_owned.assert_not_awaited()
        assert redis.enqueue_job.await_count == 2


class TestDegradation:
    @pytest.mark.asyncio
    async def test_a_batch_failure_falls_back_to_per_key_claims(self) -> None:
        """One failed batch must not discard the page's progress."""
        redis = AsyncMock()
        cache = _cache(RuntimeError("pipeline down"))

        recovered = await _run({"redis": redis}, _db([_delivery(1), _delivery(2)]), cache)

        assert recovered == 2
        assert cache.set_nx.await_count == 2, "the fallback claims each row individually"
        assert redis.enqueue_job.await_count == 2

    @pytest.mark.asyncio
    async def test_a_short_batch_answer_is_a_failed_batch(self) -> None:
        """Attributing a short answer to the wrong rows is worse than re-claiming."""
        cache = _cache([True])  # two rows claimed, one verdict

        recovered = await _run({"redis": AsyncMock()}, _db([_delivery(1), _delivery(2)]), cache)

        assert recovered == 2
        assert cache.set_nx.await_count == 2, "the unusable batch degraded to per-key claims"

    @pytest.mark.asyncio
    async def test_an_unclaimable_row_is_skipped_alone(self) -> None:
        cache = _cache(RuntimeError("pipeline down"))
        cache.set_nx.side_effect = [True, RuntimeError("row lost"), True]

        recovered = await _run(
            {"redis": AsyncMock()}, _db([_delivery(1), _delivery(2), _delivery(3)]), cache
        )

        assert recovered == 2, "the two claimable rows are still recovered"


class TestOrderingInvariantsSurvive:
    @pytest.mark.asyncio
    async def test_each_release_precedes_its_own_enqueue(self) -> None:
        order: list[tuple[str, str]] = []
        cache = _cache([True, True])
        redis = AsyncMock()

        async def _release(key: str, token: str) -> bool:
            order.append(("release", key))
            return True

        async def _enqueue(*args, **kwargs) -> MagicMock:
            order.append(("enqueue", kwargs["delivery_id"]))
            return MagicMock()

        cache.release_lock_if_owned.side_effect = _release
        redis.enqueue_job.side_effect = _enqueue

        await _run({"redis": redis}, _db([_delivery(1), _delivery(2)]), cache)

        # Row 1: its release is the first thing recorded, before its enqueue.
        assert order[0] == ("release", "webhook:deliver_lock:1")
        assert order.index(("enqueue", "1")) > order.index(("release", "webhook:deliver_lock:1"))
        assert order.index(("enqueue", "2")) > order.index(("release", "webhook:deliver_lock:2"))

    @pytest.mark.asyncio
    async def test_a_failed_enqueue_releases_the_claim_and_continues(self) -> None:
        """Crash-before-enqueue: the claim TTL is not the only release path.

        The invariant is EXACTLY ONE release per row — never zero (a leaked claim
        blocks the next harvest for the whole TTL) and never two (a second
        ``release_lock_if_owned`` is a guaranteed no-op, since the Lua script
        deletes only on token match, so it buys nothing and costs a Redis round
        trip per recovered row in the outage path this batching exists to
        optimise). The ``finally`` is a safety net for the row that never got to
        release, not a second attempt on the row that did.
        """
        redis = AsyncMock()
        redis.enqueue_job.side_effect = [RuntimeError("queue lost"), MagicMock()]
        cache = _cache([True, True])
        db = _db([_delivery(1), _delivery(2)])

        recovered = await _run({"redis": redis}, db, cache)

        assert recovered == 1
        released_keys = [call.args[0] for call in cache.release_lock_if_owned.await_args_list]
        assert released_keys.count("webhook:deliver_lock:1") == 1
        assert released_keys.count("webhook:deliver_lock:2") == 1
        assert len(released_keys) == 2, "one release per claimed row, no more"
        assert db.rollback.await_count == 1
        assert db.commit.await_count == 1


class _ExpiringDelivery:
    """An ORM instance whose mapped attributes are expired by a rollback.

    A root ``Session.rollback()`` runs ``_restore_snapshot``, which expires
    EVERY state in the identity map — including primary keys — even when the
    session was built with ``expire_on_commit=False``. Reading such an
    attribute outside the greenlet raises ``MissingGreenlet``. This double
    raises instead, so a test proves the loop never touches it after a
    rollback.
    """

    def __init__(self, delivery_id: int, tenant_id: str) -> None:
        self._values = {"id": delivery_id, "tenant_id": tenant_id}
        self._expired = False

    def expire(self) -> None:
        self._expired = True

    def __getattr__(self, name: str) -> object:
        if name not in ("id", "tenant_id"):
            raise AttributeError(name)
        if self._expired:
            raise RuntimeError("MissingGreenlet: expired attribute read outside the greenlet")
        return self._values[name]


def _expire_all(rows: list[_ExpiringDelivery]):
    def _expire() -> None:
        for row in rows:
            row.expire()

    return _expire


class TestARollbackDoesNotPoisonTheNextRow:
    """R1-BUG-2: per-row isolation must survive the rollback that isolates it.

    The old loop read ``delivery.id`` for the lock key, the claim lookup and the
    enqueue OUTSIDE the per-row try. After a rollback expired the page, the next
    iteration's first read raised from outside the try, so one bad row aborted
    the whole tick instead of being skipped.
    """

    @pytest.mark.asyncio
    async def test_the_second_row_is_recovered_after_the_first_row_rolls_back(self) -> None:
        rows = [_ExpiringDelivery(1, "tenant-1"), _ExpiringDelivery(2, "tenant-2")]
        db = _db(rows)
        db.rollback.side_effect = _expire_all(rows)
        redis = AsyncMock()
        redis.enqueue_job.side_effect = [RuntimeError("arq down"), MagicMock()]

        recovered = await _run({"redis": redis}, db, _cache([True, True]))

        assert recovered == 1, "row 1 failed and rolled back; row 2 must still be recovered"
        assert db.rollback.await_count == 1
        enqueued = [call.kwargs["delivery_id"] for call in redis.enqueue_job.await_args_list]
        assert enqueued == ["1", "2"], "row 2 was attempted after the rollback expired the page"

    @pytest.mark.asyncio
    async def test_the_row_retry_window_is_written_as_a_statement(self) -> None:
        """The next_retry_at write is a Core UPDATE, not an ORM mutation.

        An ORM mutation after a rollback needs the instance's primary key at
        flush time, which is an implicit IO the coroutine cannot perform.
        """
        rows = [_ExpiringDelivery(1, "tenant-1"), _ExpiringDelivery(2, "tenant-2")]
        db = _db(rows)
        db.rollback.side_effect = _expire_all(rows)
        redis = AsyncMock()
        redis.enqueue_job.side_effect = [RuntimeError("arq down"), MagicMock()]

        await _run({"redis": redis}, db, _cache([True, True]))

        update_calls = [
            call for call in db.execute.await_args_list if call.args and getattr(call.args[0], "is_update", False)
        ]
        assert len(update_calls) == 1, "the retry window is written with an UPDATE statement"


class TestAClaimOnlyMeansSomethingWhileItIsFresh:
    """The claim is a LIVENESS TEST, so its age must be bounded.

    Claiming the whole page up front under a single 60 s TTL meant a later row's
    claim could expire before its turn came: the tick would then enqueue a row
    whose live ``deliver()`` had already re-taken the lock. Claims are therefore
    taken one WINDOW at a time, immediately before the rows they guard, while
    the outage case still collapses from one round trip per row to one per
    window.
    """

    @pytest.mark.asyncio
    async def test_a_page_larger_than_the_window_is_claimed_in_windows(self) -> None:
        from src.app.modules.webhooks.delivery import _RECOVERY_CLAIM_WINDOW

        rows = [_delivery(index) for index in range(1, _RECOVERY_CLAIM_WINDOW * 2 + 1)]
        cache = _cache([])

        async def _claim(items, ttl: int) -> list[bool]:
            # A verdict per claim, like the real pipeline.
            return [True] * len(items)

        cache.set_nx_many.side_effect = _claim

        recovered = await _run({"redis": AsyncMock()}, _db(rows), cache)

        assert recovered == len(rows)
        assert cache.set_nx_many.await_count == 2, "one claim round trip per window, not per page"
        for call in cache.set_nx_many.await_args_list:
            assert call.kwargs["ttl"] == 60, "the TTL stays the crash-leak bound"
            assert len(call.args[0]) <= _RECOVERY_CLAIM_WINDOW
        cache.set_nx.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_window_is_claimed_immediately_before_its_rows(self) -> None:
        """No row may be processed before the claim that guards it."""
        from src.app.modules.webhooks.delivery import _RECOVERY_CLAIM_WINDOW

        rows = [_delivery(index) for index in range(1, _RECOVERY_CLAIM_WINDOW + 2)]
        order: list[str] = []
        cache = _cache([True] * len(rows))
        redis = AsyncMock()

        async def _claim(items, ttl: int) -> list[bool]:
            order.append("claim")
            return [True] * len(items)

        async def _enqueue(*args, **kwargs) -> MagicMock:
            order.append(f"enqueue:{kwargs['delivery_id']}")
            return MagicMock()

        cache.set_nx_many.side_effect = _claim
        redis.enqueue_job.side_effect = _enqueue

        await _run({"redis": redis}, _db(rows), cache)

        # Row 11 is the first row of the SECOND window, so a second claim must
        # precede it.
        second_claim_at = [index for index, event in enumerate(order) if event == "claim"][1]
        assert order.index(f"enqueue:{_RECOVERY_CLAIM_WINDOW + 1}") > second_claim_at


class TestAFailedReleaseIsRetried:
    """``release_lock_if_owned`` swallows Redis errors and returns False.

    Setting the released flag unconditionally therefore skipped the ``finally``
    safety net and left the claim held for the full 60 s TTL — during which the
    job we are about to enqueue finds the lock taken and skips the delivery.
    """

    @pytest.mark.asyncio
    async def test_a_release_that_reports_failure_is_attempted_again(self) -> None:
        cache = _cache([True])
        cache.release_lock_if_owned.side_effect = [False, True]

        await _run({"redis": AsyncMock()}, _db([_delivery(1)]), cache)

        assert cache.release_lock_if_owned.await_count == 2, "the finally net must see the failure"

    @pytest.mark.asyncio
    async def test_a_release_that_succeeds_is_not_attempted_again(self) -> None:
        cache = _cache([True])

        await _run({"redis": AsyncMock()}, _db([_delivery(1)]), cache)

        assert cache.release_lock_if_owned.await_count == 1, "a successful release costs one round trip"
