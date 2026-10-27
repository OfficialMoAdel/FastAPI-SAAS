# ADR-0029: Distributed single-flight for cold feature-flag keys

**Status:** Proposed — **design spike only. No production code is authorised or
changed by this document.**
**Date:** 2026-10-25
**Source:** `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_VERIFIED.md` §4.2 (P3, "design spike only")

## Context

`modules/feature_flags/service.py` protects a cold cache key with a
process-local `asyncio.Lock` per key (`_with_flag_lock`, called from four
decision paths: `is_enabled`, `get_flag_value`, the tenant listing, and the
`_run()` bulk path at `:684`). The lock table is capped at 4096 entries with
FIFO eviction of the oldest 10%, which is safe because losing a live lock only
loses batching for one miss.

What it does not do is span processes. With N workers, a cold key means up to N
concurrent DB loads — the classic stampede the lock was added to prevent, just
moved one level up. Decision keys are per tenant (`tenant:{tid}:feature*`) with
`CACHE_TTL = 300`, so a cold key is rare but coincident when it happens (deploy,
invalidation on a registry write, TTL expiry under load).

Available primitives, none of them a drop-in:

* `core/utils/lua_manager.py::xfetch_get_or_claim` — atomic
  ``[value, ttl, claimed]``; claiming happens in the SAME script as the read,
  which is what makes it single-flight (a Python-side GET-then-SET-NX can be
  interleaved).
* `cache_client.set_nx` / `release_lock_if_owned` (ownership-token release).
* `modules/dashboard/service.py:427-460` — the repo's existing distributed
  claim pattern: a **tri-state** claim (`True` mine / `False` peer / `None` no
  usable verdict), a bounded poll for the peer's value, then
  fall-through-to-recompute.

## Why `xfetch_get_or_claim` is not a drop-in

1. **A claimer may have nothing to write.** `_resolve_flag_value` returns
   `(value, cacheable)`; `cacheable=False` when the answer is the CALLER's
   `default` (caching it would serve one caller's fallback to everyone for
   300s). `xfetch` assumes the claimer publishes a value. A claimer that
   correctly publishes nothing leaves every waiter with `['', -2, 0]` and no
   signal distinguishing "peer is working" from "peer finished with nothing".
2. **No stale-while-revalidate.** `xfetch` returns a value OR a claim; it has
   no notion of "serve the last known value while someone recomputes".
3. **Two different policies are fused.** The lock TTL (how long a crashed
   leader blocks progress) and the waiter budget (how long a request may wait)
   are separate decisions, and `xfetch` only models the first.
4. **Release is a blind `DEL`** unless composed with `lock_release_if_owned`.
   A leader whose TTL expired must not delete the lock a NEW leader acquired.
5. **Feature flags have no L1.** Unlike the bitmask/auth-info caches, decisions
   live only in Redis, so "serve stale" must be an explicit cache read, not an
   in-process fallback.

## Design (recommended, for approval — not implemented)

### Who owns recomputation

Exactly one holder per `(key, window)`: the coroutine/worker whose `set_nx`
claim on `flagref:{cache_key}` returns `True`. Everyone else is a waiter. This
matches the dashboard pattern and keeps the in-process lock as a cheap
FIRST-layer filter (a local hit under a local lock never touches Redis).

### Lock TTL

**5 seconds** for the recompute claim (the dashboard uses a comparable value
for its metrics recompute). Rationale: a cold flag load is a single indexed
registry read plus, for `get_flag_value`, one override read — p99 well under
1s. 5s bounds a crashed leader to one cold window while never being long enough
to stall a deploy. The TTL is measured, not guessed, before implementation.

### Waiter behaviour

Bounded wait, then **fall through to recompute locally**:

* poll the decision key every 25 ms for a **250 ms** budget;
* if the value appears → return it (cache hit, no DB work);
* if the budget expires, or the value never appears (`cacheable=False`),
  compute locally WITHOUT taking the lock. This is the property that makes the
  feature safe: a distributed lock failure can never turn a cache miss into an
  error or an unbounded stall — it degrades to today's behaviour (N callers
  compute) for at most one window.

### Failure behaviour (Redis down / claim errored)

Claim is tri-state, exactly as the dashboard path:

| claim | meaning | action |
|---|---|---|
| `True` | mine | compute, publish, release in `finally` |
| `False` | peer holds it | poll ≤ 250 ms, then compute locally |
| `None` | no client, or the claim raised | compute locally immediately |

An outage therefore produces the pre-optimisation behaviour (each caller
computes), never a 500 and never a "deny" verdict. **A Redis failure must not
change a flag decision**, only its cost.

### Stale value policy

Serve stale **only inside the waiter budget**, and only from the decision key
itself (there is no L1). Concretely: the waiter's poll returns the peer's
freshly written value, which is indistinguishable from a plain cache hit. No
background refresh, no stale-while-revalidate: a flag is a policy input, and a
value older than the waiter budget is indistinguishable from "the leader has
not published yet", so the honest answer is to compute it.

### Release

Ownership token, released with `release_lock_if_owned` in `finally` — the
first release before publishing would let a third caller claim and duplicate
the work, so the order is: claim → compute → publish → release.

## Risks

* **Distributed lock failure turning a miss into an error** — closed by the
  `None` claim arm and the fall-through (there is no error path).
* **TTL too short duplicates work; too long stalls** — the waiter budget is
  what bounds the request; the TTL only bounds a CRASHED leader. They are
  therefore tuned independently, and the 5s/250ms pair must be measured.
* **Cross-process eviction and invalidation** — `_purge_registry_caches` SCANs
  the raw client (the correctness source of truth) and `_invalidate_decision_keys`
  drops specific keys. Invalidation is INDEPENDENT of single-flight: a purge
  during a leader's recompute can make the leader publish a value computed
  before the registry write. That is the existing 300s freshness contract, not
  a new race, but the design MUST NOT claim otherwise.
* **The in-process lock table** (4096 cap, FIFO eviction) stays as is: it is
  the first layer, it is bounded, and evicting a live lock is safe.

## Verification plan (when implementation is approved)

Each item below becomes a test; the first four are the plan's own list.

1. **Two workers, one cold key** — one recomputes, the other polls and takes
   the published value; assert exactly ONE DB load.
2. **Leader crashes after claim** — the claim expires after the TTL; the next
   caller becomes leader; no waiter exceeds its budget.
3. **Redis outage** — claim is `None`, every caller computes locally, no
   raised error, decisions identical to the pre-change code.
4. **Lock map eviction under pressure** — register 5000 keys, evict, assert the
   cache-miss path still resolves (correctness) and only batching is lost.
5. **`cacheable=False`** — the caller-default path must not publish and must
   not leave waiters waiting longer than their budget.
6. **Ownership** — an expired leader cannot release a new leader's lock.

## Non-goals

* No change to `CACHE_TTL` (300s) or to the invalidation seams.
* No new cache layer (no L1 for flags).
* No behaviour change on the Redis-down path.

## Consequences if approved

Four call sites move from `_with_flag_lock(...)` to a shared
`single_flight_or_compute(key, loader)` helper that owns the tri-state claim,
the waiter budget and the release; `_with_flag_lock` remains as the in-process
first layer. Until that lands, this ADR changes nothing.
