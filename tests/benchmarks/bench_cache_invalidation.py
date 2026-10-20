"""Benchmarks for hierarchical and pattern-based cache invalidation.

Both arms are timed through ``src/benchmarks/protocol`` (H-11), so each number
carries its sample counts, its spread, and a full run stamp. Every gate is an
ABSOLUTE FIXED CEILING. The printed speedup ratio between the OLD and NEW arm is
REPORTED and never asserted: ``new < old`` is not a pass criterion - it passes on
noise, and the measured noise floor here is 7.1x run-to-run on identical code
(perf C11). The ceilings are what decide pass or fail.
"""

from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cachetools import TTLCache

from src.app.core.cache import invalidation_channel as channel_mod
from src.app.core.cache import l1 as l1_module
from src.app.core.cache.l1 import L1Cache, RawKeyCodec
from src.app.core.utils import cache
from src.app.modules.users import cache as users_cache
from src.benchmarks import protocol

# Absolute fixed ceilings for the NEW arm, in microseconds.
HIERARCHICAL_CEILING_US = protocol.Ceiling(10_000.0, "invalidate_tenant_cache(tenant=1, prefix=user)")
PATTERN_CEILING_US = protocol.Ceiling(50_000.0, "invalidate_by_pattern(pattern:*)")


@pytest.fixture(autouse=True)
def _keep_the_l1_registry_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop this file's THROWAWAY seams from entering the global L1 registry.

    ``L1Cache.__init__`` registers itself in the process-global L1 registry,
    and ``tests/unit/core/cache/test_l1_registry_contract.py`` holds that
    registry to a CLOSED world: an unlisted channel fails the contract test.
    These seams are oracle fixtures, not owners - nothing here starts a
    listener or expects a lifespan to start one - so registration is stubbed
    for the duration of each test, the same guard
    ``tests/unit/core/cache/test_l1.py`` uses.

    WITHOUT this guard the registry contract test fails whenever it runs after
    this file, because the oracle's own seams are then registered channels it
    does not know about. Nothing is weakened by the guard: the registry is not
    what these tests measure - they assert on the WIRE and on the local cache
    state - and the contract test keeps its closed world untouched.
    """
    monkeypatch.setattr(l1_module, "register_l1_channel", lambda name, channel: channel)


@pytest.mark.asyncio
async def test_benchmark_hierarchical_invalidation():
    """Benchmark Hierarchical Index: 100K keys, invalidate 100 by prefix."""
    print("\n--- Benchmark: Hierarchical Invalidation ---")

    # Setup Mock
    mock_client = AsyncMock()
    mock_pipeline = MagicMock()
    mock_pipeline.__aenter__.return_value = mock_pipeline
    mock_pipeline.__aexit__.return_value = None
    mock_pipeline.execute = AsyncMock(return_value=[1, 1])
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    # Scenario: 100k keys total. Target prefix has 100 keys.
    total_keys = 100_000
    target_count = 100

    # 1. Simulate OLD approach (Global Index + Filter)
    # The full set is not sent over a wire here - the point is to measure the
    # filtering CPU cost, which is what makes the global index expensive.
    all_keys = {f"tenant:1:other:{i}".encode() for i in range(total_keys - target_count)}
    target_keys = {f"tenant:1:user:{i}".encode() for i in range(target_count)}
    all_keys.update(target_keys)

    mock_client.smembers.return_value = all_keys

    async def old_arm() -> None:
        fetched = await mock_client.smembers("tenant_index:1")
        filter_prefix = b"tenant:1:user:"
        filtered = {k for k in fetched if k.startswith(filter_prefix)}
        async with mock_client.pipeline() as pipe:
            pipe.delete(*filtered)
            await pipe.execute()

    old_result = await protocol.measure_async(old_arm, name="invalid_old_global_index", iterations=1, warmup=1)

    # 2. Simulate NEW approach (Sharded Index)
    mock_client.smembers.return_value = target_keys

    async def new_arm() -> None:
        # Logic is inside cache.invalidate_tenant_cache
        with patch("src.app.core.utils.cache.client", mock_client):
            await cache.invalidate_tenant_cache(1, "user")

    new_result = await protocol.measure_async(new_arm, name="invalid_new_sharded_index", iterations=1, warmup=1)

    print(f"OLD (Global Index): {protocol.render(old_result)}")
    print(f"NEW (Sharded Index): {protocol.render(new_result)}")
    print(f"Speedup (REPORTED, never asserted): {old_result.best_us / new_result.best_us:.1f}x")

    protocol.verdict(new_result, HIERARCHICAL_CEILING_US).assert_passed()


@pytest.mark.asyncio
async def test_benchmark_pattern_invalidation():
    """Benchmark Pattern Invalidation: 1M keys, 1K matches."""
    print("\n--- Benchmark: Pattern Invalidation ---")

    # Setup Mock
    mock_client = AsyncMock()
    mock_pipeline = MagicMock()
    mock_pipeline.__aenter__.return_value = mock_pipeline
    mock_pipeline.__aexit__.return_value = None
    mock_pipeline.execute = AsyncMock(return_value=[1, 1])
    mock_client.pipeline = MagicMock(return_value=mock_pipeline)

    # Scenario: 1M keys. 1k matches.
    match_count = 1_000

    # 1. Simulate SCAN (O(N)). A SCAN over 1M keys needs 1000 roundtrips at
    # count=1000. The keyspace is not materialised: what is being measured is
    # the roundtrip overhead of that loop, which is the whole cost of the
    # pattern-matching approach at this scale.
    scan_calls_needed = 1000
    scan_batches: list[tuple[int, list[str]]] = [
        (i + 1 if i < scan_calls_needed - 1 else 0, []) for i in range(scan_calls_needed)
    ]

    async def scan_arm() -> None:
        # Re-arm the side_effect list on every call. The protocol runs warmup
        # plus repetitions x iterations calls against a shared mock, and a list
        # side_effect raises StopIteration once exhausted, which would abort
        # the run rather than measure it. Re-arming is a single attribute
        # assignment and sits inside the timed window; it is ~100ns against a
        # 1000-roundtrip loop, so it cannot move the number.
        mock_client.scan.side_effect = scan_batches
        with patch("src.app.core.utils.cache.client", mock_client):
            await cache._delete_keys_by_pattern("pattern:*")

    scan_result = await protocol.measure_async(scan_arm, name="invalid_old_scan_1m", iterations=1, warmup=1)

    # 2. Simulate SET (O(1))
    # smembers returns 1000 keys.
    target_keys = {f"pattern:{i}".encode() for i in range(match_count)}
    mock_client.smembers = AsyncMock(return_value=target_keys)
    mock_client.scan.side_effect = None  # Reset

    async def set_arm() -> None:
        with patch("src.app.core.utils.cache.client", mock_client):
            await cache.invalidate_by_pattern("pattern:*")

    set_result = await protocol.measure_async(set_arm, name="invalid_new_index_set", iterations=1, warmup=1)

    print(f"OLD (SCAN 1M keys): {protocol.render(scan_result)} (simulated {scan_calls_needed} roundtrips)")
    print(f"NEW (Index Set):    {protocol.render(set_result)}")
    print(f"Speedup (REPORTED, never asserted): {scan_result.best_us / set_result.best_us:.1f}x")

    protocol.verdict(set_result, PATTERN_CEILING_US).assert_passed()


# ---------------------------------------------------------------------------
# S-6 oracle (FastAPI_saas-t6r8): ordered payload sequence + double-invalidate
# idempotence. Landed BEFORE the diff; the S-6 change (batch N publishes into
# one) must satisfy these, not the other way round.
#
# WHY ORDER, NOT SET: batching is only safe because Redis executes a
# pipeline in submission order on a single connection, so per-subscriber FIFO
# is preserved. That is a property of Redis, not of this code. Asserting only
# the SET of payloads would pass while the safety argument silently broke,
# so the sequence comparison below is list equality, in order.
#
# WHAT THE DIFF MUST SHOW: the same two tests run against the batched arm
# and the published sequence is IDENTICAL (same payloads, same order) and
# the local end state is IDENTICAL (all drops first, then the publish —
# l1.py's DROP LOCALLY, THEN PUBLISH invariant). The local drop order is
# load-bearing too: pub/sub does not echo to the publisher, so publishing
# before dropping would leave this process serving the entry it just
# invalidated.
#
# ACCEPTED DELTA (quoted from the bead): "one warning per BATCH instead of
# per MESSAGE on the error path - accept and document". A batched publish
# that fails names the batch, not each key; that is coarser logging, not a
# behaviour change, and is accepted here in advance so the diff is not
# judged against a log shape it was told to change.
#
# WHAT WAS MISSING (FastAPI_saas-49ui): the block above pinned the SERIAL
# arm only, and it exercised a LOCAL helper rather than the production code.
# It was therefore green PRECISELY because the batched arm did not exist —
# a gate that cannot fail cannot pass. The arms added below are the ones
# that can fail: the batched arm of the PRODUCTION seam, the PRODUCTION call
# site that actually pays the N round trips, and the redis-py fidelity the
# batch depends on.
# ---------------------------------------------------------------------------

#: M keys for the sequence test, in an order that is NEITHER sorted NOR
#: reversed-sorted. 10 keys are enough that an order-destroying change
#: (set-iteration, sort, dedup, reverse) cannot pass by luck — with
#: ``range(10)`` insertion order, a ``sorted()`` in the implementation
#: would still pass, so the shuffled order below is load-bearing.
_S6_M_KEYS: tuple[str, ...] = tuple(f"s6-oracle-user:{i}" for i in (3, 0, 7, 1, 9, 2, 6, 4, 8, 5))

#: A single key, invalidated twice, for the idempotence test.
_S6_DOUBLE_KEY = "s6-oracle-user:double"


class _FakePipeline:
    """A redis-py-shaped pipeline: commands queue, ``execute()`` sends them.

    Two redis-py 5.3.1 behaviours are modelled, and both are load-bearing:

    * ``Pipeline.__aexit__`` awaits ``reset()``, which CLEARS the command
      stack. So a trailing ``execute()`` that was dedented out of the
      ``async with`` runs against an empty stack, sends nothing, and
      invalidates NOTHING while logging nothing.
    * ``execute()`` short-circuits on an empty stack: no command, no round
      trip. So the discarded batch is not even a wasted round trip — it is
      silence.
    """

    def __init__(self, wire: "_FakePubSubRedis") -> None:
        self._wire = wire
        self._queue: list[tuple[str, tuple[object, ...]]] = []

    def publish(self, channel: str, message: str) -> None:
        self._queue.append(("publish", (channel, message)))

    def delete(self, *keys: str) -> None:
        self._queue.append(("delete", tuple(keys)))

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._queue.append(("set", (key, value)))

    async def execute(self) -> list[int]:
        queued, self._queue = list(self._queue), []
        if not queued:
            return []
        self._wire.round_trips += 1
        for kind, args in queued:
            if kind == "publish":
                channel, message = args
                self._wire.record(str(channel), str(message))
            elif kind == "delete":
                self._wire.deleted.extend(str(key) for key in args)
        return [1] * len(queued)

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self._queue.clear()
        return False


class _FakePubSubRedis:
    """The subscriber harness: captures exactly what the wire would carry.

    ``publish`` records (channel, message) in call order, which is what a
    subscriber on a single connection receives — Redis preserves
    per-connection FIFO, so call order here IS arrival order there.

    ``round_trips`` counts the wire operations that carried traffic: one per
    direct ``publish`` call and one per pipeline ``execute`` that had
    commands. That is the DISCRIMINATOR between the serial arm (N round
    trips) and the batched arm (1).

    ``observer`` is an optional hook, called with each payload as it is
    recorded. The production-call-site oracle uses it to snapshot the LOCAL
    cache at the moment of the publish, which is what proves that every local
    drop happened BEFORE the first publish.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.round_trips = 0
        self.observer: Callable[[str], None] | None = None

    def record(self, channel: str, message: str) -> None:
        if self.observer is not None:
            self.observer(message)
        self.published.append((channel, message))

    async def publish(self, channel: str, message: str) -> int:
        self.round_trips += 1
        self.record(channel, message)
        return 1

    def pipeline(self, transaction: bool = False) -> _FakePipeline:
        return _FakePipeline(self)


def _fresh_s6_seam(name: str) -> tuple[L1Cache, TTLCache[str, object], _FakePubSubRedis]:
    """A publisher/subscriber seam with its own cache, channel and wire tap.

    Each seam gets a UNIQUE channel name so ``register_l1_channel`` never
    sees a re-registration (which logs loudly and replaces). The caller
    patches ``channel_mod.get_redis`` to return the fake before invalidating.
    """
    local = TTLCache[str, object](maxsize=100, ttl=60.0)
    wire = _FakePubSubRedis()
    seam = L1Cache(
        name=f"s6-oracle-{name}",
        channel=f"s6-oracle-channel-{name}",
        codec=RawKeyCodec(),
        caches=[local],
    )
    return seam, local, wire


def _payloads_of(wire: _FakePubSubRedis) -> list[str]:
    return [message for _, message in wire.published]


async def _serial_invalidate(seam: L1Cache, keys: tuple[str, ...]) -> None:
    """The OLD arm: one drop-then-publish per key, in submission order."""
    for key in keys:
        await seam.invalidate(key)


def test_ordered_payload_sequence_for_m_keys() -> None:
    """The subscriber receives the IDENTICAL ORDERED sequence for M keys."""
    import asyncio

    assert len(_S6_M_KEYS) >= 2, "an order test over fewer than 2 keys cannot fail"
    publisher, pub_local, pub_wire = _fresh_s6_seam("publisher")
    subscriber, sub_local, _ = _fresh_s6_seam("subscriber")
    for key in _S6_M_KEYS:
        pub_local[key] = f"entry-{key}"
        sub_local[key] = f"entry-{key}"

    expected = [str(key) for key in _S6_M_KEYS]  # RawKeyCodec.encode is str(key)
    with patch.object(channel_mod, "get_redis", return_value=pub_wire):
        asyncio.run(_serial_invalidate(publisher, _S6_M_KEYS))

    published = _payloads_of(pub_wire)
    assert published, "the wire tap saw nothing - the harness is broken, not the code"
    assert published == expected, "ORDERED sequence, not just the same set"
    assert dict(pub_local) == {}, "every locally dropped key must be gone on the publisher"

    # Subscriber side: feed the captured wire sequence through handle(), in
    # order, exactly as Redis would deliver it on one connection.
    for payload in published:
        subscriber.handle(payload)
    assert dict(sub_local) == {}, "subscriber ends with nothing cached"
    assert dict(sub_local) == dict(pub_local), "local L1 state is identical on both sides"


def test_double_invalidate_is_idempotent() -> None:
    """Invalidating the same key twice changes nothing the second time."""
    import asyncio

    publisher, pub_local, pub_wire = _fresh_s6_seam("double-publisher")
    subscriber, sub_local, _ = _fresh_s6_seam("double-subscriber")
    pub_local[_S6_DOUBLE_KEY] = "entry-double"
    sub_local[_S6_DOUBLE_KEY] = "entry-double"

    async def _twice() -> None:
        await publisher.invalidate(_S6_DOUBLE_KEY)
        after_first = dict(pub_local)
        await publisher.invalidate(_S6_DOUBLE_KEY)
        after_second = dict(pub_local)
        assert after_first == after_second == {}, "second invalidate must be a no-op on state"

    with patch.object(channel_mod, "get_redis", return_value=pub_wire):
        asyncio.run(_twice())

    published = _payloads_of(pub_wire)
    assert published == [_S6_DOUBLE_KEY, _S6_DOUBLE_KEY], "both invalidations publish; neither raises"

    for payload in published:
        subscriber.handle(payload)
    assert dict(sub_local) == dict(pub_local) == {}, "subscriber tolerates the repeated message too"


def test_the_oracle_names_the_sequence_it_pins() -> None:
    """Anti-vacuity: the golden sequence is M distinct payloads whose order
    a sort, a reverse, or a set-iteration would all destroy."""
    expected = [str(key) for key in _S6_M_KEYS]
    assert len(expected) == len(set(expected)) == 10
    assert expected != sorted(expected), "a sorted() in the code must fail this oracle"
    assert expected != sorted(expected, reverse=True), "a reversal must fail it too"
    assert _S6_DOUBLE_KEY not in _S6_M_KEYS, "the double key must stand alone, not alias the sequence"


# ---------------------------------------------------------------------------
# The arms that can FAIL (FastAPI_saas-49ui).
# ---------------------------------------------------------------------------


def test_the_batched_arm_publishes_the_identical_ordered_sequence_in_one_round_trip() -> None:
    """The batched arm: same payloads, same order, same local state, ONE round trip.

    This is the comparison the S-6 comment block above asked for and never
    had. It runs BOTH arms of the PRODUCTION seam — the serial
    ``invalidate`` per key and the batched ``invalidate_many`` — and
    compares them on all three properties the batch has to preserve.
    """
    import asyncio

    serial_pub, serial_local, serial_wire = _fresh_s6_seam("batch-serial-publisher")
    batch_pub, batch_local, batch_wire = _fresh_s6_seam("batch-batched-publisher")
    serial_sub, serial_sub_local, _ = _fresh_s6_seam("batch-serial-subscriber")
    batch_sub, batch_sub_local, _ = _fresh_s6_seam("batch-batched-subscriber")

    for key in _S6_M_KEYS:
        for local in (serial_local, batch_local, serial_sub_local, batch_sub_local):
            local[key] = f"entry-{key}"

    with patch.object(channel_mod, "get_redis", return_value=serial_wire):
        asyncio.run(_serial_invalidate(serial_pub, _S6_M_KEYS))
    with patch.object(channel_mod, "get_redis", return_value=batch_wire):
        asyncio.run(batch_pub.invalidate_many(list(_S6_M_KEYS)))

    serial_payloads = _payloads_of(serial_wire)
    batch_payloads = _payloads_of(batch_wire)

    assert serial_payloads == [str(key) for key in _S6_M_KEYS], "the serial arm publishes each key, in order"
    assert batch_payloads == serial_payloads, "the batched arm must publish the IDENTICAL ORDERED sequence"
    assert dict(batch_local) == dict(serial_local) == {}, "identical local end state on the publisher"
    assert serial_wire.round_trips == len(_S6_M_KEYS), "the serial arm is one round trip per key"
    assert batch_wire.round_trips == 1, "the batched arm is ONE round trip for the whole batch"

    for payload in serial_payloads:
        serial_sub.handle(payload)
    for payload in batch_payloads:
        batch_sub.handle(payload)
    assert dict(serial_sub_local) == dict(batch_sub_local) == {}, "both subscribers end empty"


def test_the_production_batch_call_site_publishes_once_for_many_users() -> None:
    """The REAL call site must batch: N users -> ONE publish round trip.

    Without this arm the oracle would cover only the new seam primitive, and
    the production call site could stay serial — paying N round trips — with
    the oracle still green. That is the exact failure this bead was filed
    for, so the call site itself is pinned here.
    """
    import asyncio

    users = [
        {"id": 1, "email": "alice@example.com", "username": "Alice", "tenant_id": 42, "token_version": 0},
        {"id": 2, "email": "bob@example.com", "username": "Bob", "tenant_id": 42, "token_version": 0},
    ]
    # The auth-info subset of the delete keys, in the order the key family
    # builds them: tenant-scoped email, global email, tenant-scoped
    # username, global username — per user, users in submission order.
    expected_payloads = [
        "user:auth:42:alice@example.com",
        "user:auth:global:alice@example.com",
        "user:auth:42:alice",
        "user:auth:global:alice",
        "user:auth:42:bob@example.com",
        "user:auth:global:bob@example.com",
        "user:auth:42:bob",
        "user:auth:global:bob",
    ]

    l1_wire = _FakePubSubRedis()
    redis_wire = _FakePubSubRedis()
    snapshots: list[dict[str, object]] = []
    l1_wire.observer = lambda _payload: snapshots.append(dict(users_cache._auth_info_l1))

    local = users_cache._auth_info_l1
    local.clear()
    for payload in expected_payloads:
        local[payload] = {"id": payload}

    try:
        with (
            patch.object(channel_mod, "get_redis", return_value=l1_wire),
            patch.object(users_cache, "get_redis", return_value=redis_wire),
        ):
            asyncio.run(users_cache.UserCacheInvalidator.invalidate_user_cache_batch(users))
    finally:
        local.clear()

    published = _payloads_of(l1_wire)
    assert published == expected_payloads, "the auth-info payloads, in the order the delete keys were built"
    assert l1_wire.round_trips == 1, "N users -> ONE publish round trip for the L1 fan-out"
    assert redis_wire.round_trips == 1, "the Redis delete pipeline is ONE round trip"
    assert set(expected_payloads) <= set(redis_wire.deleted), (
        "the L1 fan-out covers the auth-info subset of what was deleted"
    )
    assert snapshots, "the observer saw no publish - the harness is broken, not the code"
    assert all(snapshot == {} for snapshot in snapshots), "every local drop must happen BEFORE the first publish"
    assert dict(local) == {}, "the local auth-info cache ends empty"


def test_publish_many_sends_every_payload_in_one_round_trip() -> None:
    """The production seam's batched publish: N payloads, ONE round trip, all delivered.

    This is the arm that catches the redis-py trap: if the trailing
    ``execute()`` were dedented out of the ``async with``, the stack would be
    empty by then, nothing would be sent, and this assertion would fail.
    """
    import asyncio

    wire = _FakePubSubRedis()
    channel = channel_mod.InvalidationChannel(channel="s6-publish-many", on_message=lambda _payload: None)

    with patch.object(channel_mod, "get_redis", return_value=wire):
        asyncio.run(channel.publish_many(["a", "b", "c"]))

    assert _payloads_of(wire) == ["a", "b", "c"], "every payload, in order"
    assert wire.round_trips == 1, "ONE round trip for the whole batch"


def test_the_fake_discards_queued_commands_on_exit_like_redis_py() -> None:
    """redis-py 5.3.1: ``Pipeline.__aexit__`` -> ``reset()`` clears the stack.

    The batched publish depends on this. A trailing ``execute()`` dedented
    out of the ``async with`` would run against an EMPTY stack, send
    nothing, and invalidate NOTHING while logging nothing. The fake has to
    model that, or the oracle could not see the bug it exists to catch.
    """
    import asyncio

    wire = _FakePubSubRedis()

    async def _execute_outside_the_with_block() -> None:
        async with wire.pipeline(transaction=False) as pipe:
            pipe.publish("s6-fidelity", "payload")
        # THE BUG SHAPE: the execute() is OUTSIDE the `async with`.
        await pipe.execute()

    asyncio.run(_execute_outside_the_with_block())

    assert wire.published == [], "the queued publish must be discarded, exactly as redis-py discards it"
    assert wire.round_trips == 0, (
        "an empty execute sends nothing at all, exactly as redis-py's execute() short-circuits"
    )


def test_the_parallel_s6_oracle_file_does_not_exist() -> None:
    """AUDIT_CODE_SECURITY.md §6: extend the home file, do NOT create a parallel one.

    A parallel oracle duplicates the gate, and a duplicate that is not run by
    the same command is a gate nobody watches. The S-6 implementation landed
    against such a file; it was never on HEAD, and the file it did land
    against could not discriminate the change.
    """
    forbidden = (
        Path(__file__).resolve().parents[2] / "tests" / "unit" / "core" / "cache" / "test_s6_batched_publish_oracle.py"
    )
    assert not forbidden.exists(), (
        "S-6's oracle belongs in tests/benchmarks/bench_cache_invalidation.py; "
        "a parallel file is the defect FastAPI_saas-49ui was filed for"
    )
