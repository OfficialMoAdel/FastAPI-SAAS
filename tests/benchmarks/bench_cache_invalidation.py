"""Benchmarks for hierarchical and pattern-based cache invalidation.

Both arms are timed through ``src/benchmarks/protocol`` (H-11), so each number
carries its sample counts, its spread, and a full run stamp. Every gate is an
ABSOLUTE FIXED CEILING. The printed speedup ratio between the OLD and NEW arm is
REPORTED and never asserted: ``new < old`` is not a pass criterion - it passes on
noise, and the measured noise floor here is 7.1x run-to-run on identical code
(perf C11). The ceilings are what decide pass or fail.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cachetools import TTLCache

from src.app.core.cache import invalidation_channel as channel_mod
from src.app.core.cache.l1 import L1Cache, RawKeyCodec
from src.app.core.utils import cache
from src.benchmarks import protocol

# Absolute fixed ceilings for the NEW arm, in microseconds.
HIERARCHICAL_CEILING_US = protocol.Ceiling(10_000.0, "invalidate_tenant_cache(tenant=1, prefix=user)")
PATTERN_CEILING_US = protocol.Ceiling(50_000.0, "invalidate_by_pattern(pattern:*)")


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
# ---------------------------------------------------------------------------

#: M keys for the sequence test, in an order that is NEITHER sorted NOR
#: reversed-sorted. 10 keys are enough that an order-destroying change
#: (set-iteration, sort, dedup, reverse) cannot pass by luck — with
#: ``range(10)`` insertion order, a ``sorted()`` in the implementation
#: would still pass, so the shuffled order below is load-bearing.
_S6_M_KEYS: tuple[str, ...] = tuple(f"s6-oracle-user:{i}" for i in (3, 0, 7, 1, 9, 2, 6, 4, 8, 5))

#: A single key, invalidated twice, for the idempotence test.
_S6_DOUBLE_KEY = "s6-oracle-user:double"


class _FakePubSubRedis:
    """The subscriber harness: captures exactly what the wire would carry.

    ``publish`` records (channel, message) in call order, which is what a
    subscriber on a single connection receives — Redis preserves
    per-connection FIFO, so call order here IS arrival order there.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


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
