"""The L1 cache seam: grammar parity and invalidation ordering.

Every case in ``GRAMMAR_CASES`` was read off the hand-rolled handler this module
replaces, and asserts the **existing** behaviour. That is the point: a mistake in
the seam should fail here, in one table, rather than surface later as a cache
that is stale in production or a broadcast that flushes more than it should.

The two fail-* policies are deliberately opposite and both load-bearing:

* ``IntKeyCodec`` (tier policy) fails **open** — a malformed tier id is ignored,
  because a garbage payload must not flush the whole fleet's policy cache.
* ``PairKeyCodec`` (bitmask) fails **safe** — an unparseable payload clears
  everything, because a garbage payload must not leave a revoked permission
  cached.

Both are asserted below so that "tidying them up" into one policy breaks a test.
"""

from __future__ import annotations

import pytest
from cachetools import TTLCache

import src.app.core.cache.l1 as l1_module
from src.app.core.cache.l1 import (
    BROADCAST,
    IGNORE,
    IntKeyCodec,
    KeyCodec,
    L1Cache,
    PairKeyCodec,
    PrefixedKeyCodec,
    RawKeyCodec,
)


@pytest.fixture(autouse=True)
def _no_registry_pollution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop these unit tests leaking fake channels into the global registry.

    ``L1Cache`` self-registers on construction. The registry contract test
    asserts the registered set equals exactly the five real channels, so a
    ``test_l1`` left behind here would fail an unrelated test.
    """
    monkeypatch.setattr(l1_module, "register_l1_channel", lambda name, channel: channel)


def _make(codec: KeyCodec, keys: tuple = ()) -> tuple[L1Cache, TTLCache]:
    """Build an L1Cache over one real layer, seeded with ``keys``."""
    layer: TTLCache = TTLCache(maxsize=16, ttl=60)
    for key in keys:
        layer[key] = "v"
    cache = L1Cache(name="test_l1", channel="test:channel", codec=codec, caches=[layer])
    return cache, layer


# (label, codec, seeded keys, payload, expected keys afterwards)
GRAMMAR_CASES = [
    # tenant_l1 — core/tenancy/cache.py. "" is a KEY here, not a broadcast.
    ("tenant/broadcast", RawKeyCodec(), ("a", "b"), "*", ()),
    ("tenant/empty-is-a-key", RawKeyCodec(), ("", "a"), "", ("a",)),
    ("tenant/key", RawKeyCodec(), ("a", "b"), "a", ("b",)),
    # ban_l1 — core/security/auto_ban.py. Wire carries the bare IP; the cache
    # key carries a prefix. This is the only grammar needing a transform.
    ("ban/broadcast", PrefixedKeyCodec(prefix="ban:"), ("ban:1.1.1.1",), "*", ()),
    ("ban/empty-pops-the-bare-prefix", PrefixedKeyCodec(prefix="ban:"), ("ban:", "ban:1.1.1.1"), "", ("ban:1.1.1.1",)),
    ("ban/bare-ip", PrefixedKeyCodec(prefix="ban:"), ("ban:1.1.1.1", "ban:2.2.2.2"), "1.1.1.1", ("ban:2.2.2.2",)),
    # rate_limit_policy_l1 — core/security/rate_limit_cache.py. Fail-OPEN.
    ("policy/broadcast", IntKeyCodec(), (1, 2), "*", ()),
    ("policy/malformed-is-ignored-fail-open", IntKeyCodec(), (1, 2), "not-a-tier", (1, 2)),
    ("policy/empty-is-ignored", IntKeyCodec(), (1, 2), "", (1, 2)),
    ("policy/key", IntKeyCodec(), (1, 2), "1", (2,)),
    # bitmask_l1 — core/rbac/dependencies.py. Fail-SAFE, and "" broadcasts.
    ("bitmask/broadcast", PairKeyCodec(empty_is_broadcast=True), ((1, "t"),), "*", ()),
    ("bitmask/empty-broadcasts", PairKeyCodec(empty_is_broadcast=True), ((1, "t"),), "", ()),
    ("bitmask/malformed-clears-fail-safe", PairKeyCodec(empty_is_broadcast=True), ((1, "t"),), "abc:t", ()),
    ("bitmask/key", PairKeyCodec(empty_is_broadcast=True), ((1, "t"), (2, "t")), "1:t", ((2, "t"),)),
    # A bare digit has no separator, so it is malformed and broadcasts
    # fail-safe (PairKeyCodec docstring): partition() never fails, so the
    # separator check — not ValueError — is what closes the revoked-bitmask
    # hole. Asymmetric with IntKeyCodec (fail-open) by design.
    ("bitmask/bare-digit-clears-fail-safe", PairKeyCodec(empty_is_broadcast=True), ((1, "t"),), "5", ()),
    # auth_info_l1 — modules/users/cache.py. "" broadcasts; payload IS the key.
    ("auth/broadcast", RawKeyCodec(empty_is_broadcast=True), ("k1", "k2"), "*", ()),
    ("auth/empty-broadcasts", RawKeyCodec(empty_is_broadcast=True), ("k1",), "", ()),
    ("auth/key", RawKeyCodec(empty_is_broadcast=True), ("k1", "k2"), "k1", ("k2",)),
]


@pytest.mark.parametrize(
    ("codec", "seeded", "payload", "expected"),
    [case[1:] for case in GRAMMAR_CASES],
    ids=[case[0] for case in GRAMMAR_CASES],
)
def test_handle_matches_the_hand_rolled_grammar(codec: KeyCodec, seeded: tuple, payload: str, expected: tuple) -> None:
    """``handle`` reproduces each owner's original ``on_message`` exactly."""
    cache, layer = _make(codec, seeded)
    cache.handle(payload)
    assert set(layer) == set(expected)


def test_codecs_declare_which_payloads_are_not_theirs() -> None:
    """``parse`` returns the sentinels, so ``handle`` can branch on identity."""
    assert IntKeyCodec().parse("nope") is IGNORE
    assert PairKeyCodec().parse("nope:") is BROADCAST
    assert RawKeyCodec().parse("") == ""
    # Sentinels must not be strings, or they could collide with a real key.
    assert not isinstance(BROADCAST, str)
    assert not isinstance(IGNORE, str)


def test_every_layer_is_dropped_in_lockstep() -> None:
    """A companion cache cannot outlive the cache it was derived from.

    This is the tenant parsed-object memo and the compiled rate-limit path map:
    both were previously kept in step by a comment and a code review.
    """
    primary: TTLCache = TTLCache(maxsize=8, ttl=60)
    derived: TTLCache = TTLCache(maxsize=8, ttl=60)
    for layer in (primary, derived):
        layer["a"] = "v"
        layer["b"] = "v"
    cache = L1Cache(
        name="test_l1",
        channel="test:channel",
        codec=RawKeyCodec(),
        caches=[primary, derived],
    )

    cache.drop("a")
    assert set(primary) == {"b"}
    assert set(derived) == {"b"}

    cache.clear()
    assert not primary
    assert not derived


async def test_invalidate_drops_before_it_publishes() -> None:
    """The ordering is the whole point: pub/sub does not echo to the publisher.

    Publishing first would leave this process serving the entry it just
    invalidated. The recorder captures the layer's contents *at publish time*.
    """
    cache, layer = _make(RawKeyCodec(empty_is_broadcast=True), ("k",))
    seen: list[dict] = []

    class _Recorder:
        def start(self) -> None: ...

        async def stop(self) -> None: ...

        async def publish(self, payload: str | None = None) -> None:
            seen.append({"payload": payload, "still_cached": sorted(layer)})

    cache._channel = _Recorder()  # type: ignore[assignment]
    await cache.invalidate("k")

    assert seen == [{"payload": "k", "still_cached": []}]


async def test_invalidate_with_no_key_clears_and_broadcasts() -> None:
    """``None`` means "everything", locally and on the wire."""
    cache, layer = _make(RawKeyCodec(), ("a", "b"))
    seen: list[str | None] = []

    class _Recorder:
        def start(self) -> None: ...

        async def stop(self) -> None: ...

        async def publish(self, payload: str | None = None) -> None:
            seen.append(payload)

    cache._channel = _Recorder()  # type: ignore[assignment]
    await cache.invalidate(None)

    assert not layer
    # None on the wire becomes "*" inside InvalidationChannel.
    assert seen == [None]


def test_a_cache_needs_at_least_one_layer() -> None:
    """An L1Cache with no layers would silently drop nothing."""
    with pytest.raises(ValueError, match="at least one cache"):
        L1Cache(name="test_l1", channel="test:channel", codec=RawKeyCodec(), caches=[])
