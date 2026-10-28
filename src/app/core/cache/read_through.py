"""Read-through caching: compute-or-read once per key, behind one interface.

THE PROBLEM THIS SOLVES

``CacheClient`` offered primitives only — get/set/claim — so every caller that
needed "return the cached value, or compute it once" composed the choreography
itself. Five call sites did, with different give-up budgets, two jitter
expressions, and one place where the lock's TTL and the waiter's patience were
the same number by accident. The seam had a name for every primitive and no
name for the thing callers actually wanted.

THE FIVE DECISIONS

A unified read-through loses behaviour unless it names all five. They are
parameters here, not folklore:

1. ``claim`` — who is allowed to compute. ``"distributed"`` claims a Redis key
   so one process across the fleet computes; ``"none"`` always computes (used
   when there is no client). Local single-flight (a per-key ``asyncio.Lock``)
   is deliberately NOT implemented here: it exists in
   ``modules/feature_flags/service.py``, and moving it is the subject of
   ADR-0029, which is still a design spike ("no production code is authorised").
   Adding an unused ``"local"`` branch here would be the same zero-caller debt
   this module replaces.

2. ``freshness`` — whether a cache hit is served unconditionally. Only
   ``"ttl"`` is implemented: every production caller wants the TTL to decide.
   Probabilistic early expiry (XFetch) lives in
   ``core/utils/cache/decorator.py``, which is also the producer of the
   ``r1_xfetch_*`` series; moving it is a separate change with its own metric
   contract. The retired ``get_with_stampede_protection`` was its only
   standalone reader and had zero callers.

3. ``give_up_seconds`` — how long a loser waits for the winner's value before
   computing anyway. A duplicated query is cheaper than a stalled request.

4. ``lock_ttl_seconds`` — how long the claim survives a crashed holder. This is
   SEPARATE from ``give_up_seconds``, and that separation is a correctness fix:
   ``get_with_lock`` used one number (``lock_timeout``) for both, so waiters
   gave up at exactly the moment the lock could expire — the instant a second
   process was allowed to claim. A waiter must always give up BEFORE the claim
   it is waiting on becomes claimable by someone else. ``__post_init__``
   enforces ``give_up_seconds < lock_ttl_seconds``.

5. ``should_cache`` — whether the value the winner computed may be published.
   This is ADR-0029's point, generalized: a claimer may have nothing to
   publish (the feature-flag service returns the CALLER's default, and caching
   it would serve one caller's fallback to every tenant for the whole TTL).
   The winner still returns its value; it just does not store it, and the next
   caller computes its own.

KEY SCOPING (the decision that stays outside)

Tenant scoping — including the public namespace used when no tenant is bound —
is ``CacheClient.tenant_key``'s decision, applied by the caller BEFORE calling
here. This module never invents a key, so it cannot accidentally publish one
tenant's data into a public slot.

OWNERSHIP

The claim is released with ``release_lock_if_owned`` (compare-and-delete on a
per-claim token). A plain ``delete`` would let a slow winner whose claim had
already expired delete the NEXT winner's claim, and both would compute.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from redis.exceptions import RedisError

from src.app.core.logger import get_logger

logger = get_logger(__name__)

ClaimScope = Literal["distributed", "none"]
Freshness = Literal["ttl"]


@dataclass(frozen=True, slots=True)
class ReadThroughPolicy:
    """The five read-through decisions, with the safest defaults.

    Defaults match the dashboard's existing single-flight: a distributed claim,
    TTL freshness, a 1 s wait, and a 15 s claim TTL. Call sites override only
    what they must.
    """

    claim: ClaimScope = "distributed"
    freshness: Freshness = "ttl"
    give_up_seconds: float = 1.0
    lock_ttl_seconds: int = 15
    #: Poll interval bounds for a waiter. Exponential from base to max, with
    #: +/- jitter so a released claim does not wake every waiter in lockstep.
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 1.0
    jitter: float = 0.25
    #: Return False to compute-without-publishing. None means "always publish".
    should_cache: Callable[[Any], bool] | None = None

    def __post_init__(self) -> None:
        if self.claim == "distributed" and self.give_up_seconds >= self.lock_ttl_seconds:
            # A waiter that outlives the claim races the next claimer. This is
            # the bug the two numbers exist to prevent, so it fails loudly at
            # construction rather than producing duplicate work at runtime.
            raise ValueError(
                "give_up_seconds must be < lock_ttl_seconds: a waiter that "
                "outlives the claim it waits on will compute alongside the "
                f"next claimer (got {self.give_up_seconds} >= {self.lock_ttl_seconds})"
            )
        if self.give_up_seconds < 0:
            raise ValueError("give_up_seconds must be >= 0")


async def read_through(
    cache: Any,
    key: str,
    fetch: Callable[[], Awaitable[Any]],
    *,
    ttl: int,
    policy: ReadThroughPolicy | None = None,
    lock_key: str | None = None,
) -> Any:
    """Return the cached value at ``key``, computing it once if absent.

    Args:
        cache: A ``CacheClient``. The claim primitives are used only when
            ``policy.claim == "distributed"`` and the backend is available.
        key: Fully-built cache key. Tenant scoping is the caller's job — pass
            the result of ``cache.tenant_key(...)`` when the value is
            tenant-scoped, so this function never chooses a namespace.
        fetch: Async producer for the value. Called at most once per winning
            claim (plus once per caller that gave up waiting).
        ttl: Seconds to store the produced value.
        policy: The five decisions. Defaults to a distributed claim with TTL
            freshness.
        lock_key: Claim key. Defaults to ``f"{key}:refresh"``; override only
            when two different values share a cache key (they should not).

    Returns:
        The cached value, or the value ``fetch`` produced.
    """
    policy = policy or ReadThroughPolicy()
    started = time.perf_counter()

    cached = await cache.get_json(key)
    if cached is not None:
        _record(started, is_hit=True)
        return cached

    if policy.claim == "none" or cache.client is None:
        # No backend, or the caller opted out of claiming: compute and publish.
        # Publishing is still attempted — the old inline choreography stored
        # even when it could not claim, and dropping that would leave a
        # computed value unshared for no benefit.
        value = await fetch()
        await _publish(cache, key, value, ttl, policy)
        _record(started, is_hit=False)
        return value

    claim_key = lock_key or f"{key}:refresh"
    token = uuid4().hex
    claim: bool | None = None
    try:
        verdict = await cache.set_nx(claim_key, token, policy.lock_ttl_seconds)
        # Normalise to the tri-state. A backend that returns a truthy non-bool
        # (a proxy, a fake, an adapter that forwards the raw reply) must not
        # land in the "no verdict" branch and silently skip publishing.
        claim = None if verdict is None else bool(verdict)
    except (RedisError, OSError) as e:
        # "Cannot claim" must be distinguishable from "someone else holds it".
        # ``set_nx`` returns False for both a held key and an unavailable
        # backend, and PROPAGATES a transport error. Treating either as "a peer
        # is computing" turns a Redis blip into a stall or a 500; the pre-seam
        # code simply recomputed. So a failed claim falls straight through.
        logger.warning("read_through_claim_failed", key=key, error=str(e))

    if claim is True:
        try:
            # Double-check: a peer may have published between our miss and our
            # claim. Without this the winner recomputes work that is already
            # stored.
            cached = await cache.get_json(key)
            if cached is not None:
                _record(started, is_hit=True)
                return cached
            value = await fetch()
            await _publish(cache, key, value, ttl, policy)
            _record(started, is_hit=False)
            return value
        finally:
            # Ownership-checked: never delete a claim we no longer hold.
            await cache.release_lock_if_owned(claim_key, token)

    if claim is False:
        return await _wait_for_peer(cache, key, claim_key, fetch, policy, started, ttl)

    # claim is None: no usable verdict, so compute rather than stall — and
    # publish, exactly as the pre-seam code did on a failed claim. A claim that
    # could not be made says nothing about whether the value is worth caching.
    value = await fetch()
    await _publish(cache, key, value, ttl, policy)
    _record(started, is_hit=False)
    return value


async def _publish(
    cache: Any,
    key: str,
    value: Any,
    ttl: int,
    policy: ReadThroughPolicy,
) -> None:
    """Store ``value`` unless the policy forbids publishing it.

    ``should_cache`` is decision 5: a winner may have computed something that
    must not be shared (the feature-flag service returns the CALLER's default,
    and caching it would serve one caller's fallback to every tenant for the
    whole TTL). The value is still returned to the caller that computed it.
    """
    if policy.should_cache is not None and not policy.should_cache(value):
        logger.debug("read_through_not_published", key=key)
        return
    await cache.set_json(key, value, ttl=ttl)


async def _wait_for_peer(
    cache: Any,
    key: str,
    claim_key: str,
    fetch: Callable[[], Awaitable[Any]],
    policy: ReadThroughPolicy,
    started: float,
    ttl: int,
) -> Any:
    """Poll for the winner's value until the give-up budget is spent.

    The budget is deliberately shorter than the claim TTL (enforced by the
    policy), so this loop always ends while the claim it waits on is still
    valid. When it expires anyway, computing is the right answer: a duplicated
    query beats a stalled request.
    """
    deadline = time.monotonic() + policy.give_up_seconds
    delay = policy.base_delay_seconds

    while True:
        await asyncio.sleep(delay)
        cached = await cache.get_json(key)
        if cached is not None:
            _record(started, is_hit=True)
            return cached
        if time.monotonic() >= deadline:
            break
        delay = min(delay * 2, policy.max_delay_seconds)
        # Jitter keeps a released claim from waking every waiter in lockstep.
        delay *= 1 + _random_uniform(-policy.jitter, policy.jitter)

    logger.warning("read_through_give_up", key=key, claim_key=claim_key)
    value = await fetch()
    await _publish(cache, key, value, ttl, policy)
    _record(started, is_hit=False)
    return value


def _random_uniform(low: float, high: float) -> float:
    # Imported lazily so a test can patch the seed without importing random at
    # module scope (matching how the stampede module reached its RNG).
    import random

    return random.uniform(low, high)


def _record(started: float, *, is_hit: bool) -> None:
    """Feed the shared XFetch hit/miss series, exactly as the decorator does.

    Without this, ``/api/metrics``' ``xfetch_hit_rate`` describes only the
    decorator's traffic and a read-through caller looks either infinitely
    effective or entirely idle.
    """
    try:
        from src.app.core.monitoring.r1_unified_metrics import record_xfetch_latency

        record_xfetch_latency(time.perf_counter() - started, is_hit=is_hit)
    except Exception as e:  # noqa: BLE001 - metrics must never break a cache read
        logger.debug("read_through_metric_failed", error=str(e))


__all__ = [
    "ClaimScope",
    "Freshness",
    "ReadThroughPolicy",
    "read_through",
]
