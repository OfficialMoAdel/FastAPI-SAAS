# ADR-0028: D.6 — the tenant negative cache is its own cache

**Status:** ACCEPTED — 2026-10-25
**Date:** 2026-10-25
**Tracked by bead:** (to be filed — closes register item D.6 / OQ‑4 of `REPORT 1-PLAN FOR ADVANCED OPTIMIZATIONS.md`)
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com>
**Measured by:** `tests/unit/core/tenancy/test_negative_cache_isolation.py`

## The question

`_L1_TENANT_CACHE` was `TTLCache[str, dict | None]` (`maxsize=1000, ttl=60`)
and stored a **negative** result as a `None` **value** in the same dict as real
tenants. A slug space is unbounded and attacker-controlled, so ~1 000 probes of
nonexistent slugs evicted every live tenant from the hot path; each subsequent
request then paid an L2 RTT plus an audited L3 `bypass_rls` DB query.

Is that acceptable, and if not, what replaces it?

## Decision

**Not acceptable — attacker-controlled eviction of production cache is a
capacity defect, not a tuning question.** The negative marker moves to its own
cache:

```python
_L1_TENANT_CACHE:    TTLCache[str, dict[str, Any]] = TTLCache(maxsize=1000, ttl=60.0)
_L1_TENANT_NEGATIVE: TTLCache[str, bool]          = TTLCache(maxsize=2000, ttl=60.0)
```

Both are registered as layers of the **same** `L1Cache` key-space
(`caches=[positive, negative, parsed]`), so one invalidation — local, remote, or
broadcast — still drops all three together. That is what keeps the split from
introducing a new staleness class.

Three mechanical consequences:

1. **Reads** consult the negative cache first (it is the cheap answer), then
   the positive one. A hit on either still short-circuits before L2/DB.
2. **Writes** are mutually exclusive: a fill that found a tenant pops the
   negative marker, and a fill that found none pops the positive entry. A stale
   payload can never shadow a marker, or vice versa.
3. **Capacity is asymmetric on purpose** — 2000 negatives vs 1000 positives. A
   negative entry is one boolean; a positive entry is a tenant payload. Trading
   capacity toward the cheap side is safe, and both stay bounded, so the memory
   a scanner can pin is bounded too.

## Why not a Bloom filter

The earlier review proposed a Bloom layer. It is **not** included, and the
reason is the one that made D.6 look like a savings optimisation in the first
place: tenants are created at runtime and each worker holds its own structure,
so a Bloom without a boot seed + create-path update + invalidation turns a
*newly created* tenant into a **permanent miss**. The separate cache needs none
of that — the existing invalidation seam already covers it. A Bloom layer
remains profile-gated (register D.6, "Bloom: profile-gated").

## The invariant

> **After creating a tenant, the next request for its slug resolves without a
> negative-cache hit.**

It holds because `create_tenant` drops the L2 negative key **and** both L1
layers (bead B7), and it is asserted by test rather than assumed — a resolver-
only test would pass even if that drop were removed, since the resolver would
simply re-fill from the DB.

## Verification

- **8 tests** in `test_negative_cache_isolation.py`: eviction isolation in both
  directions, boundedness, invalidation across all three layers, remote-payload
  reach, the create-then-resolve invariant, and that a negative hit still
  short-circuits before the DB.
- The eviction-isolation case was **shown to fail on the pre-D.6 design** by
  simulating the shared-dict layout (tenant evicted at 1050 probes), so it is
  not a vacuous test.
- `tests/unit/core/tenancy` + `tests/unit/middleware` + `tests/e2e/core/tenancy`:
  **799 passed, 6 skipped**.

## Re-open condition

Re-open if a tenant's slug is ever resolvable without passing through
`create_tenant` (a migration, a bulk import, a seed script) — that path would
need the same drop, and the invariant test only covers the service method.
