# ADR-0022: Step 4's p99 bar is unreachable for the IP-keyed endpoint class

**Status:** ACCEPTED — Option A, 2026-10-13
**Date:** 2026-10-13
**Tracked by bead:** `FastAPI_saas-c47b` — "Step 4 cannot measure the IP-keyed endpoints at n>=10000"
**Blocks:** `FastAPI_saas-v8qm` (Step 4 exit gate) — claimed, cannot be closed as written
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com>

## Decision

**Option A. §4.5 stands unchanged; the p99 requirement is scoped to the endpoints
that can actually resolve it.**

Concretely, three rules, all machine-checkable by the Step 4 gate checker:

1. **Where production controls permit n>=10,000, the full §4.4 form applies** —
   p50, p95 and p99 all reported, p99 on the pooled sample. This covers the five
   user-keyed endpoints (EM-1, EM-7, EM-8, EM-9, EM-10), which resolve against
   `TIER_RATE_LIMITS[tier_name]` and are reachable by seeding accounts on a
   declared tier. **For these endpoints nothing is given up.**
2. **Where production controls make n>=10,000 unreachable, the artefact prints
   `unresolvable_quantiles()` — which quantiles are missing and the arithmetic
   that proves they are missing** — alongside the p50 and p95 it did resolve.
   A missing p99 is a stated number, not a silent gap.
3. **The endpoint is still measured and still gates.** An IP-keyed endpoint that
   cannot resolve p99 is not exempted from Step 4; it reports what it can, plus
   the reason it cannot report the rest.

Tail claims for the endpoints in rule 2 rest on **p95 plus the T-1 loop-lag
histogram**, not p99. `unresolvable_quantiles` and `nearest_rank` already exist in
`src/benchmarks/percentiles.py`; the gate checker calls the former, it does not
reimplement it.

**Rejected: Option B** (amend §4.5 to permit a carve-out phase with the eight
settings raised). It would have produced all ten p99 figures, but every one of
them would describe a configuration nobody runs, and a programme whose rule is
"never `new < old`" should not open with "and here is a number from a shape we
made up". The tension that made Option B attractive — real p99s versus honest
settings — is resolved by **publishing the p95 and naming the gap**, which is
strictly more informative than either alone.

**Not an option, and not being traded away:** relaxing a production setting in
order to manufacture a sample size. That is the failure mode this whole
programme exists to prevent.

## Context

`FastAPI_saas-v8qm` requires ten endpoints measured to §4.4, with **p99 computed
at n>=10,000**, with §4.5's void conditions asserted at run start and run end. §4.5
voids any phase in which one of the eight rate/ban settings sat at a load-producing
value, so a reportable Step 4 run must hold **production values** for all eight.

Those two requirements collide, because every rate limit that bounds the campaign
is per **source IP**, and the load host has exactly one.

## The measurements (perf-local, `ansible@192.168.121.147`, Linux 5.14.0-578, 4 CPU)

Every figure below was **observed by running**, not read off the source. Budgets
were read live from the running settings object; the walls were found by issuing
requests until the first 429.

| class | live budget | observed wall | limiter key |
|---|---|---|---|
| anonymous | `DEFAULT_ANONYMOUS_LIMIT = (10, 3600)` | **10 × 401, then 429** | `gcra:rate_limit:anonymous:ip:127.0.0.1:...` |
| authenticated | **tier-dependent** — `TIER_RATE_LIMITS[tier]` | basic 100×200 then 429; pro 1003×200 then 429; no tier 51×200 then 429 | `gcra:tenant:<tenant_id>:rate_limit:user:s4x001:users_me` |
| login | `SECURITY_BUDGETS["auth"] = (5, 60)` | **5 accounts, 6th distinct account 429** | `gcra:rate_limit:user:127.0.0.1:budget:auth` |
| refresh | `SECURITY_BUDGETS["refresh"] = (60, 60)` | not probed to the wall | `gcra:rate_limit:user:127.0.0.1:budget:refresh` |
| IP aggregate | `ANONYMOUS_IP_AGGREGATE_LIMIT = (300, 3600)` | not probed to the wall | `ratelimit:rate_limit:anonymous:ip:...:__ip_aggregate__` |

The login row is the load-bearing one. `src/app/core/security/budgets.py:269`
declares the `auth` leg as `Leg(KeyKind.IP, "auth")` and `:285` declares refresh
the same way, and `src/app/modules/auth/rate_limit.py:33` passes
`Identity(ip=client_ip)`. The observed key confirms it at runtime: the `user:`
slot holds `127.0.0.1`. **Eight distinct accounts were used and the sixth was
refused** — an account pool does not relieve an IP-keyed budget. This was measured
specifically to kill that hypothesis, and it did not die.

## Arithmetic: hours of wall clock to reach n=10,000

| endpoint | class | user-hours needed | note |
|---|---|---|---|
| EM-2 no-auth 401 | anonymous, IP | **1000 h ≈ 41.7 days** | cannot be relieved by seeding |
| EM-5 login | auth budget, IP | **33.3 h** | cannot be relieved by seeding |
| EM-6 logout | no budget of its own | bounded by EM-5 | tokens must first be minted |
| EM-4 refresh | refresh budget, IP | **2.8 h** | long but not impossible |
| EM-1 /users/me | authed, **tier** × path | tier-dependent | see below |
| EM-7 users?per_page=100 | authed, **tier** × path | tier-dependent | same |
| EM-8 query search | authed, **tier** × path | tier-dependent | same |
| EM-9 60 KB body | authed, **tier** × path | tier-dependent | same |
| EM-10 40 KB body | authed, **tier** × path | tier-dependent | same |
| EM-3 /health | not probed | — | §4.6 gives it its own paired arm (`ng1x`, closed) |

**So the accurate claim is narrower than "every endpoint is blocked".** The five
**user-keyed** endpoints resolve against `TIER_RATE_LIMITS[tier_name]`, so their
cost is set by the tier the seeded accounts are put on, and all three tiers are
reachable:

| tier | `TIER_RATE_LIMITS` | user-hours for n=10,000 | 16 seeded users |
|---|---|---|---|
| `free` | (10, 3600) | 1000 | not practical |
| `basic` | (100, 3600) | 100 | 6.25 h |
| `pro` | (1000, 3600) | 10 | **0.63 h** |
| `enterprise` | (10000, 3600) | 1 | 0.06 h |

The genuinely blocked class is the **IP-keyed** one: EM-2 at 41.7 days and EM-5 at
33.3 h are the two that no amount of seeding reaches, EM-6 inherits EM-5's wall,
and EM-4 is expensive but achievable. **Choosing the tier is itself a declared
configuration and must be recorded in the Step 4 artefact** — the same reasoning
Option B applies to relaxing the eight §4.5 settings applies to picking the tier
that makes the number easiest.

## The limiter keys on username, not user id

`gcra:tenant:<tenant_id>:rate_limit:user:s4x001:users_me` — the `user:` slot holds
the username, and the whole key is wrapped in a `tenant:` prefix. Worth recording
because `src/benchmarks/security_state.py`'s `gcra:*` pattern does match it, so the
pre-run purge still reaches these keys; a narrower pattern would silently stop
purging them.

## A retracted finding, recorded because it nearly shipped

An earlier draft of this ADR asserted that `DEFAULT_AUTHENTICATED_LIMIT = (50,
3600)` "is not what is enforced", on the grounds that the measured wall was 100
and 100 is `DEFAULT_RATE_LIMIT_LIMIT`. **That was wrong**, and the retraction is
kept here because the same mistake is easy to repeat.

`src/app/core/security/rate_policy.py:177-255` resolves in four steps: tier-policy
cache → DB `rate_limit` table → `TIER_RATE_LIMITS[tier_name]` → `DEFAULT_AUTHENTICATED_LIMIT`
/ `DEFAULT_ANONYMOUS_LIMIT`. Every account seeded for this investigation was on
`tier='basic'`, so step 3 answered `(100, 3600)` and step 4 was **never reached**.
The `50` is real and enforces exactly 50 — for an account with no tier.

Confirmed by changing the variable rather than by reading the chain, each on a
**fresh username**:

| probe user | tier | observed wall | setting that predicts it |
|---|---|---|---|
| `freshpro01` | pro (id 7) | 1003 | `TIER_RATE_LIMITS["pro"] = (1000, 3600)` |
| `freshnotier1` | none | 51 | `DEFAULT_AUTHENTICATED_LIMIT = (50, 3600)` |
| `s4x001` | basic | 100 | `TIER_RATE_LIMITS["basic"] = (100, 3600)` |

**The measurement trap that produced the error:** a first attempt varied the tier
on a *single reused* account and saw 100 for every tier. That was an artefact.
`clear_security_state()` purges the rate-limit and ban key families but **not** the
auth-info user cache (`src/app/modules/users/cache.py`), so the tier change was
never visible to the limiter. A variation step that mutates server state must use
a fresh identity or explicitly invalidate that cache, or it measures nothing.
(`s4x001` is now left on `tier='basic'`.)

## Options

*Recorded for the audit trail. **A is ACCEPTED**, B is rejected — see Decision.*

**Option A — lower the p99 bar to what production controls permit.** Accept
`unresolvable_percentiles(n, qs)` — the repo's own `src/benchmarks/percentiles.py`
function, already used by `tests/performance/locustfile.py:386` — as the honest
output where n is below 10,000. Step 4 then reports p99 as *unresolvable* for the
IP-keyed class and as *measured* for the user-keyed class.
*Cost:* p99 stops being a Step 4 exit criterion. The plan's tail claims rest on
p95 and on the T-1 loop-lag histogram, which is unaffected.
*Honest only if* the unresolvable p99 is printed as unresolvable and never
interpolated or carried forward as a number.

**Option B — amend §4.5 to carve out a measured phase.** Permit a phase in which
the eight settings are raised, on the grounds that Step 4 is measuring the
**endpoint**, not the limiter. This is what the L-8 harness already does
(`RELAXED_LIMITS` in `src/benchmarks/loop_occupancy.py`).
*Cost:* it weakens the exact guarantee §4.5 exists to provide, and any number
taken this way is not a production number. It is defensible **only** if the
artefact records `limits_relaxed: true` and every figure derived from it is
labelled non-production. `src/benchmarks/loop_occupancy.py` already carries that
labelling; nothing else does.

**Not an option — relaxing silently to manufacture n.** Whatever is chosen must
be recorded in the Step 4 artefact and in the `v8qm` close reason, as an explicit
reading, exactly as §4.4's composition was recorded for the pooled-p99 decision.

## Consequences

- **Option A is in force from 2026-10-13.** `FastAPI_saas-c47b` is updated to
  match; `FastAPI_saas-v8qm` is re-adjudicated against it.
- **The Step 4 gate checker must implement rule 2.** It calls
  `unresolvable_quantiles()` for any endpoint whose pooled n is below 10,000, and
  it must **fail** if such an endpoint publishes a p99 anyway. A missing p99 is
  acceptable; a fabricated one is not.
- **EM-2, EM-5 and EM-6 will land in rule 2** — 41.7 days, 33.3 h and inherited
  respectively. They are measured, they report p50 and p95, and they state the
  arithmetic that makes p99 unreachable. They are **not** exempted from the gate.
- **EM-4 refresh is borderline**: 2.8 h of wall clock for n=10,000. It should be
  attempted under rule 1 and fall back to rule 2 only if it cannot finish.
- The five user-keyed endpoints keep the **full** §4.4 form, including p99. The
  tier they were seeded on is a declared configuration and belongs in the artefact.
- EM-7 additionally needs RBAC seeded: `permissions` holds 26 rows but `roles`,
  `role_permissions` and `user_roles` are all empty, which is why
  `GET /api/v1/users?page=1&per_page=100` returns
  `403 Permission denied: users:read`. That is seedable and is tracked separately
  under `FastAPI_saas-3r4z`.

## What is explicitly NOT being traded away

Filing this ADR does not license publishing a p99 interpolated from an n below the
protocol's floor, dropping the n>=10,000 requirement without saying so, or
satisfying a Step 4 checkbox from a run whose eight settings were not at
production values without the artefact recording that they were not. Each of those
converts a blocked measurement into a false one, which is the failure this
programme's addenda exist to prevent.

## Re-open condition

Reopen if the load host ever gets **more than one source IP**. The entire ADR
exists because the load host has exactly one, and every blocked figure is a
function of that. A second address multiplies the IP-keyed class's budget and
would put EM-2 and EM-5 back under rule 1 without any change to §4.4 or §4.5.

Reopen also if `unresolvable_quantiles()` ever grows a documented interpolation
mode. Rule 2 is honest precisely because it prints a gap; an interpolator would
turn it back into the thing this ADR exists to prevent.