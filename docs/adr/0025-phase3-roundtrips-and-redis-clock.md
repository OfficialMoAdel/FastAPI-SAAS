# ADR-0025: Phase 3 — one Redis round-trip per anonymous request, and Redis's clock

**Status:** ACCEPTED — 2026-10-23
**Date:** 2026-10-23
**Tracked by bead:** (to be filed — this ADR records Phase 3 of `REPORT 1-PLAN FOR ADVANCED OPTIMIZATIONS.md`)
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com>
**Measured by:** `tests/unit/core/security/test_phase3_roundtrips.py`

## The questions

1. **F‑10.** Anonymous traffic pays **two** sequential GCRA round-trips: the
   per-path/ban composite (`ban_and_gcra_check`) and then
   `_check_anonymous_aggregate` → a second `consume(...)`. The aggregate key is
   a **constant token** (`AGGREGATE_PATH_TOKEN`), so there is no data
   dependency between the two. May they be one EVALSHA?
2. **F‑11.** `check_rate_limit()` stamps `now_ms` in the application process
   and ships it to Redis. That stamp is (a) **stale** by one round-trip by the
   time the script runs, and (b) **per-worker**, so workers that drift compute
   verdicts against different timelines. May the script read Redis's clock?
3. **F‑12.** `get_violation_stats` issues one `GET` per SCAN-page key. May the
   page be one `MGET`?

## Decision

**ACCEPT all three.**

### F‑10 — the aggregate folds into the existing composite

`ban_and_gcra_check` gains a third key (the aggregate bucket) and its own
emission/burst/period arguments. The Python caller passes them only when the
request will be metered as anonymous; the script consumes the aggregate **only
if the per-path leg allowed**, which is ADR‑0015's stated invariant expressed
as an in-script branch rather than a post-hoc check.

Five constraints, each mechanical:

1. **No sequential fallback inside the script** (ADR‑0015 §2.7).
2. **Not fired when the ceiling is disabled**: `ARGV[6] == '0'` means the
   aggregate key is never read.
3. **The fixed-window operator keeps its separate ban RTT** — the fold lives
   only in the GCRA branch (`uses_gcra()`), so the algorithm fence survives.
4. **Consumption order preserved**: per-path first, aggregate second, and the
   aggregate is skipped when the per-path leg rejected.
5. **`record_fail_open` counters unchanged**: the fold introduces no new
   failure path; a script error still falls through to the pre-existing
   sequential fallback (which is §8.3's open item, not touched here).

The fold also lets a warm ban negative cache skip the ban `GET` **inside** the
same round-trip (`ARGV[11] == '0'`), so the cache no longer costs a second
call on the anonymous path.

### F‑11 — Redis owns the GCRA clock

All three variants (`rate_limit_gcra`, `ban_and_gcra_check`,
`ban_blacklist_gcra_check`) read `redis.call('TIME')` and drop `now_ms` from
ARGV. GCRA is translation-invariant in time, so the verdict is unchanged
except within one tick of a boundary — where today's behaviour is *already*
nondeterministic (it depends on which worker handled the request).

**Precondition checked before landing:** no test asserts a verdict at an exact
GCRA boundary. The GCRA suites mock `evalsha` or emulate a counter; none pins
wall-clock arithmetic.

### F‑12 — one MGET per SCAN page

Independent reads of the same keys, with the F19 dedupe untouched. The result
stays approximate only by the same documented mid-scan races.

## Verification

- **Runtime execution of the real Lua** (Lua 5.1, the version Redis embeds),
  against a mock Redis: 21/21 scripts parse; the aggregate is consumed when
  both legs allow, **not** consumed when the per-path leg rejects, and **not**
  read when disabled; a banned IP consumes nothing; the TAT is seeded from the
  mock `TIME`.
- `tests/unit/core/security/test_phase3_roundtrips.py` — 13 tests covering the
  fold, the ADR‑0015 invariant, the disabled ceiling, the ban short-circuit,
  the admission wiring (anonymous folds / authenticated does not), the Redis
  clock in all three scripts, and the MGET batching.
- Existing suites: `tests/unit/core`, `tests/performance/test_rate_limit_behavior.py`,
  `tests/unit/middleware/test_rate_limit_middleware.py` green.

## Not in this ADR

- **§8.3's sequential fallback.** The composite's `except` still re-runs the
  legs sequentially on a timeout, which ADR‑0015 rejected. That is Phase 5's
  item; the fold above deliberately does not add a second instance of it.
- **F‑7 (D.1).** Merging the admission composite with the auth prefetch
  rewrites this same `lua_manager` family. Land it as one rewrite with this
  change, not as a second pass.

## Re-open condition

Re-open if (a) the aggregate and per-path buckets ever become data-dependent
(the constant-token premise fails), (b) a test starts asserting exact
boundary verdicts, or (c) an operator reports that the Redis clock and the
application clock disagree by more than the configured period.
