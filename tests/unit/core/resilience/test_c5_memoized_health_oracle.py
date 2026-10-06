"""C-5 oracle: memoized ``get_health_summary`` equals an uncached reference.

The future C-5 diff memoizes ``CircuitBreakerRegistry.get_health_summary``.
Today NO memoization exists anywhere in ``core/resilience`` (verified by
grep: no lru_cache/memoize/TTL there; the only caller of
``get_health_summary`` is the admin route). So this oracle cannot pin an
existing cache — it SPECIFIES the contract the diff must satisfy, using an
in-test memoizing harness around a REAL registry. The diff satisfies this
file by exhibiting the same invalidation discipline, not by importing it.

The discipline under test (the whole point of C-5): invalidation on ALL
FIVE mutating entry points — ``record_success``, ``record_failure``,
``reset``, ``register``, ``unregister`` — PLUS TTL expiry. Alpha itself
lists 'caching without invalidation on every mutation path' as NOT SAFE,
and §11.3 records un-invalidated memoization among the proposals rejected
on principle. A memoization missing one path is a REJECTED proposal.

Why random sequences, not a fixed one: a fixed sequence passes while a
mutation path is missed. Every sequence is ``random.Random(seed)`` with a
FIXED seed (deterministic, no hypothesis dependency), and each sequence is
forced to contain every op kind at least once — with the coverage asserted,
not assumed (``test_sequences_cover_all_entry_points``).

Why equality is asserted after EVERY operation: asserting only at the end
hides a stale-read window — a cache that serves one stale read per
mutation and then catches up would pass an end-only assertion.

TTL uses a controllable ``_Clock`` (not wall time): TTL is the easiest
path to forget precisely because no real-time test forces it.

Scope notes:

- The harness routes every mutation THROUGH ITSELF. A breaker handle
  mutated directly (bypassing the registry) is outside the contract — the
  future production memoization must live where every mutation path
  passes through it, which is exactly the hazard this file pins.
- ``summary()`` returns a shallow copy of the cached dict (the summary is
  flat ints/floats). Caller-side mutation of a returned dict is outside
  the bead's scope; the copy keeps such mutation from corrupting the
  cache and making a later equality check fail for the wrong reason.
- Breakers use ``recovery_timeout=60.0`` (the default) so no
  OPEN→HALF_OPEN wall-clock transition can fire mid-test: ``state`` reads
  ``time.time()``. State changes in these tests come only from the ops.
- ``asyncio_mode = auto`` (pyproject), so async test functions just run.
- Fresh ``CircuitBreakerRegistry()`` per test — never the module-global
  singleton (which would leak state across tests in the session).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

from src.app.core.resilience.registry import CircuitBreakerRegistry
from src.app.core.utils.circuit_breaker import CircuitBreaker

# The five mutating entry points the bead names, plus TTL. ``create`` and
# ``reset_all`` also mutate and are exercised, but the bead's list is the
# contract — the coverage test pins exactly these five.
MUTATING_ENTRY_POINTS: tuple[str, ...] = (
    "record_success",
    "record_failure",
    "reset",
    "register",
    "unregister",
)

# Fixed seeds: deterministic without a hypothesis dependency. Five seeds
# keep the runtime small while giving five independent op orders.
SEEDS: tuple[int, ...] = (11, 23, 37, 101, 2026)

OPS_PER_SEQUENCE = 40

# TTL for the harness cache. Small enough that the TTL test advances past
# it instantly with the fake clock; large enough vs wall time that no
# real-time expiry can fire mid-sequence (sequences run in ms).
HARNESS_TTL = 300.0


class _Clock:
    """Controllable clock for the harness TTL."""

    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class MemoizedHealth:
    """In-test memoizing harness around a REAL registry.

    This is the contract specimen for C-5: cache ``get_health_summary``,
    invalidate on every mutating entry point plus TTL expiry. ``recomputes``
    counts real recomputations so tests can prove the cache both caches
    (stays 1 across pure reads) and invalidates (ticks on every mutation).
    """

    def __init__(
        self,
        registry: CircuitBreakerRegistry,
        ttl: float = HARNESS_TTL,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._registry = registry
        self._ttl = ttl
        self._clock = clock or time.time
        self._cached: dict[str, Any] | None = None
        self._cached_at: float | None = None
        self.recomputes = 0

    def summary(self) -> dict[str, Any]:
        if (
            self._cached is not None
            and self._cached_at is not None
            and self._clock() - self._cached_at < self._ttl
        ):
            return dict(self._cached)
        fresh = self._registry.get_health_summary()
        self.recomputes += 1
        self._cached = fresh
        self._cached_at = self._clock()
        return dict(fresh)

    def _invalidate(self) -> None:
        self._cached = None
        self._cached_at = None

    def register(self, name: str) -> CircuitBreaker:
        try:
            breaker = self._registry.create(name, recovery_timeout=60.0)
        finally:
            # Even a duplicate-name ValueError must not leave a stale
            # summary cached: the registry is unchanged, but the call
            # attempted a mutation, so the safe action is to drop.
            self._invalidate()
        return breaker

    def unregister(self, name: str) -> bool:
        try:
            return self._registry.unregister(name)
        finally:
            self._invalidate()

    async def reset(self, name: str) -> bool:
        try:
            return await self._registry.reset(name)
        finally:
            self._invalidate()

    async def reset_all(self) -> int:
        try:
            return await self._registry.reset_all()
        finally:
            self._invalidate()

    async def record_success(self, name: str) -> None:
        breaker = self._registry.get(name)
        if breaker is not None:
            await breaker.record_success()
        self._invalidate()

    async def record_failure(self, name: str) -> None:
        breaker = self._registry.get(name)
        if breaker is not None:
            await breaker.record_failure()
        self._invalidate()


def _fresh_pair() -> tuple[CircuitBreakerRegistry, MemoizedHealth, _Clock]:
    """A real registry plus the harness around it, sharing one fake clock."""
    clock = _Clock()
    registry = CircuitBreakerRegistry()
    return registry, MemoizedHealth(registry, clock=clock), clock


_OP_KINDS: tuple[str, ...] = (
    "register",
    "record_success",
    "record_failure",
    "reset",
    "unregister",
)


def _random_sequence(rng: random.Random, length: int = OPS_PER_SEQUENCE) -> list[tuple[str, str]]:
    """One op sequence over a small name pool, covering every op kind.

    Names cycle through ``svc-0..svc-4`` so unregister/register churn the
    same keys (a pool of unique names would never collide and never test
    the re-registration path). Every op kind is forced in at least once —
    coverage is then ASSERTED by ``test_sequences_cover_all_entry_points``,
    not assumed from randomness.
    """
    pool = [f"svc-{i}" for i in range(5)]
    ops = [(rng.choice(_OP_KINDS), rng.choice(pool)) for _ in range(length)]
    for kind in _OP_KINDS:
        ops.append((kind, rng.choice(pool)))
    rng.shuffle(ops)
    return ops


async def _apply(op: tuple[str, str], harness: MemoizedHealth) -> None:
    """Drive one op through the harness (which owns the registry)."""
    kind, name = op
    if kind == "register":
        try:
            harness.register(name)
        except ValueError:
            pass  # duplicate name: registry unchanged, harness invalidated
    elif kind == "record_success":
        await harness.record_success(name)
    elif kind == "record_failure":
        await harness.record_failure(name)
    elif kind == "reset":
        await harness.reset(name)
    elif kind == "unregister":
        harness.unregister(name)


class TestMemoizedEqualsReferenceAfterEveryOp:
    async def test_empty_registry(self) -> None:
        registry, harness, _ = _fresh_pair()
        assert harness.summary() == registry.get_health_summary() == {
            "total": 0,
            "closed": 0,
            "open": 0,
            "half_open": 0,
            "health_percentage": 100.0,
            "at_risk": 0,
        }

    async def test_random_sequences(self) -> None:
        for seed in SEEDS:
            rng = random.Random(seed)
            registry, harness, _ = _fresh_pair()
            for op in _random_sequence(rng):
                await _apply(op, harness)
                # The bead's core demand: equality after EVERY op, so a
                # cache that serves one stale read per mutation fails here.
                assert harness.summary() == registry.get_health_summary(), (
                    f"seed={seed} op={op}: memoized diverged from reference"
                )

    def test_sequences_cover_all_entry_points(self) -> None:
        for seed in SEEDS:
            kinds = {kind for kind, _ in _random_sequence(random.Random(seed))}
            assert set(_OP_KINDS) <= kinds, (
                f"seed={seed}: sequence misses {set(_OP_KINDS) - kinds}; "
                "a sequence that never exercises a path cannot pin it"
            )


class TestEveryMutatingEntryPointInvalidates:
    async def _warmed(self) -> tuple[CircuitBreakerRegistry, MemoizedHealth, _Clock]:
        registry, harness, clock = _fresh_pair()
        harness.register("svc-0")
        assert harness.summary() == registry.get_health_summary()
        assert harness.recomputes == 1
        return registry, harness, clock

    async def test_record_success_invalidates(self) -> None:
        registry, harness, _ = await self._warmed()
        await harness.record_success("svc-0")
        assert harness.summary() == registry.get_health_summary()
        assert harness.recomputes == 2

    async def test_record_failure_invalidates(self) -> None:
        registry, harness, _ = await self._warmed()
        await harness.record_failure("svc-0")
        assert harness.summary() == registry.get_health_summary()
        assert harness.recomputes == 2

    async def test_reset_invalidates(self) -> None:
        registry, harness, _ = await self._warmed()
        await harness.reset("svc-0")
        assert harness.summary() == registry.get_health_summary()
        assert harness.recomputes == 2

    async def test_register_invalidates(self) -> None:
        registry, harness, _ = await self._warmed()
        harness.register("svc-1")
        assert harness.summary() == registry.get_health_summary()
        assert harness.recomputes == 2

    async def test_unregister_invalidates(self) -> None:
        registry, harness, _ = await self._warmed()
        harness.unregister("svc-0")
        assert harness.summary() == registry.get_health_summary()
        assert harness.recomputes == 2


class TestTtlExpiryInvalidates:
    async def test_stale_cache_is_recomputed_after_ttl(self) -> None:
        registry, harness, clock = _fresh_pair()
        harness.register("svc-0")
        assert harness.summary() == registry.get_health_summary()
        assert harness.recomputes == 1
        # Pure reads inside TTL must NOT recompute (the cache caches).
        assert harness.summary() == registry.get_health_summary()
        assert harness.recomputes == 1
        clock.advance(HARNESS_TTL + 1.0)
        assert harness.summary() == registry.get_health_summary()
        assert harness.recomputes == 2


class TestTheCacheActuallyCaches:
    async def test_repeated_reads_do_not_recompute(self) -> None:
        _, harness, _ = _fresh_pair()
        harness.register("svc-0")
        for _ in range(10):
            harness.summary()
        # 1 for the first fill; 10 pure reads add nothing. Without this,
        # every equality test above would pass against a pass-through.
        assert harness.recomputes == 1

