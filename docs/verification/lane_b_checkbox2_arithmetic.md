# GATE B checkbox 2: the 4.09 ms uncontended-floor arithmetic for S-1 to S-5

**Status:** Recorded. The arithmetic GATE B asks for, with every number sourced.
**Date:** 2026-10-20
**Tracked by bead:** `FastAPI_saas-osxx` - "GATE B checkbox 2: the 4.09 ms uncontended-floor arithmetic for S-1, S-2, S-3, S-4 and S-5"
**Gate:** `FastAPI_saas-m5ql` (GATE B), checkbox 2, quoted verbatim from the master register `FastAPI_saas-hcnr`:

> the PR description states, WITH ARITHMETIC, that it moves capacity and not p95 (§4.7 gate (c)), except where a p95 effect is separately demonstrated;

**Companion record:** `docs/verification/lane_b_gate_b_record.md` - the per-item gate texts (labels, oracles, suites). This document supplies the arithmetic that record marks absent or wrong.
**Audited at:** commit `36dfda38` (branch `security/remediate-run3-findings`).
That commit is not an ancestor of this branch's tip - it was squashed into
`cc6e6d17` - so read "Note on the commit identities" at the end of this
document before trying to `git show` it.

## What this document is, and what it is not

GATE B was closed as a **recorded decision, not a pass** (`FastAPI_saas-m5ql`). One
of the reasons was that checkbox 2 was met in only two of eight items, and in five
of them the arithmetic was either absent, measured against the wrong denominator,
or derived from a Windows artefact.

This document supplies that arithmetic for S-1 to S-5. It does **not** re-open
GATE B, does not turn any item into a pass, and does not change the gate's
verdict. In particular:

- The arithmetic is a **documentary record**, not a change to any Lane B body.
  The bodies are history and are not edited here; where a body is wrong, the
  record says so and names the corrected figure.
- No number here was re-derived from a fresh measurement. Every numerator is
  either (a) quoted from a committed artefact in this repository, with the
  artefact named, or (b) **declared not to exist**. Where a figure is an
  estimate, an artefact quote or a Windows measurement, it is labelled as such
  and left unverified.
- No test was weakened and no assertion was relaxed to produce this document.
  No test was changed at all.

## The denominator: what the 4.09 ms uncontended floor is

GATE B names one denominator: **the 4.09 ms uncontended floor**. It is the
post-regression **minimum** response time of `GET /api/v1/users/me`, quoted in
the Round-1 perf pack (perf finding 4 / A7) as

> min 3.65 -> 4.09 ms (+12%)

beside the tail it belongs to:

> p95 19 -> 740 ms (39x), avg 9.56 -> 174.6 ms, throughput 51.94 -> 44.77 rps (-13.8 %)

That pair is quoted verbatim in the beads `FastAPI_saas-cwt8` (R1-OPT plan),
`FastAPI_saas-p90o` (GATE A), `FastAPI_saas-9pu4` (L-3/P-1), `FastAPI_saas-ztcl`
(Step 3), `FastAPI_saas-8rez` (P-2/L-4), `FastAPI_saas-cv94` (EM-1) and
`FastAPI_saas-1six` / `FastAPI_saas-49yx` (A-3).

**The Round-1 locust baseline itself is not in this repository.** The
`baseline_20260922_011954_stats.csv` file the figure came from is absent, so
4.09 ms is a quoted Round-1 number, not a re-derivable one. Two committed
artefacts in this repository corroborate its magnitude:

| Artefact | What it records | Value | Host |
|---|---|---|---|
| `perf-results/RESULT-20261012-sweepB/sweepA_8_stats.csv` | row `GET,/users/me (readonly)`, **Minimum Response Time**, concurrency 8, arm A (production controls, 0 overrides) | **4.09577600294142 ms** | `perf-local`, 2 cores (per `perf-results/STATE.md` section 4c) |
| `perf-results/RESULT-20261012-linuxvm/raw_linux_d.json` | `testclient.get_unwrapped.min` (n=25, full-stack GET phase, `x_query_count {0: 25}`, `sql_per_req 0.0`); `async.cprofile.clean_ms_per_req` 4.233371680020355 ms (n=25) | **4.055988996697124 ms** | Linux, Python 3.13.16, `cwd /home/ansible/FastAPI-Saas` |

The first names its endpoint explicitly; the second does not name its endpoint,
but records a full-stack GET phase with **zero SQL over 25 requests**, which is
the shape of `GET /api/v1/users/me` (EM-1 cross-checked zero SQL over 2 000
requests on the same path). The two independent committed minima are 4.0958 ms
and 4.0560 ms; the gate's 4.09 ms sits between them.

**Fractions in this document are computed against 4 090 us exactly.** Using
either committed corroboration instead moves every fraction by less than
1 % (e.g. S-4's 0.600 % becomes 0.599 % against 4 095.776 us and 0.605 %
against 4 055.989 us).

### Denominators this document does NOT use, and why

| Rejected denominator | Why it is rejected |
|---|---|
| The warm `/users/me` **p50** (the "4 ms" used by S-4 and S-5) | A warm p50 is a contaminated figure: it carries the concurrency and the cache state of the run that produced it. The gate's own design says the arithmetic must be "against the 4.09 ms uncontended floor, not against a contaminated warm number". |
| EM-1's later in-process floor (client min-of-mins 2 797.2 us / server min-of-mins 2 481.0 us, `perf-results/RESULT-20261013-usersme/users_me_repetitions.json`) | A **different instrument** (in-process over ASGITransport, 5 reps x 400 warm authenticated requests, 2026-10-13), not the Round-1 locust measurement. It is quoted in this document only as a *secondary scale*, explicitly labelled, and never as the floor. |
| `tests/benchmarks/baselines/main.json` | **A stub. Never compared** (A-13). No figure in this document comes from it. |
| The Windows locust baselines (0.72-3.10 ms RTT range) | Windows artefacts are non-representative and may never be a `before` arm (A-5). Used below only where the point is to show that the *original* figure was Windows-derived. |

### The rule set applied to every numerator below

1. A numerator whose host is Windows **may not** be quoted as a production
   capacity claim. It is either re-measured on Linux or marked
   `[Unverified - Needs-Linux-measure]` and left unverified.
2. A numerator whose host is **not named** may not be quoted as a production
   capacity claim either. No host is not a Linux host.
3. Where no figure exists, the item says so and names the measurement bead
   that owns it - or records that no such bead exists - rather than inventing
   one.
4. The fraction is against the **uncontended** floor. Where the saving lands on
   a path that is not `GET /users/me`, that is stated, because the fraction is
   then a *scale* comparison and not an endpoint claim.

---

## S-1 - collapse `invalidate_session` to one atomic Lua EVALSHA

**The change.** `invalidate_session` issued `HEXISTS`, then a `pipeline()` that
carried `HDEL` + `ZREM`, as two separate client round trips. After the change
the whole thing is one `EVALSHA`. The before-code, read from the git object
database (`git show a94b1d9f^:src/app/core/security/session_manager.py`):

```python
exists = await client.hexists(hash_key, session_id)
if not exists:
    return False
async with client.pipeline() as pipe:
    pipe.hdel(hash_key, session_id)
    pipe.zrem(zset_key, session_id)
    await pipe.execute()
```

redis-py's transaction pipeline packs `MULTI`, `HDEL`, `ZREM`, `EXEC` into a
**single** write (`Pipeline._execute_transaction` -> one
`send_packed_command`), so the before-arm is **2 wire round trips** and the
after-arm is **1**.

> **The bead record disagrees with itself, and the disagreement is recorded
> here rather than resolved silently.** The bead title says "2 RTT -> 1" and
> the bead's close_reason says "exactly one round trip saved per invalidation";
> the commit body's own table says "hit (session present) 3 -> 1", which counts
> `MULTI` and `EXEC` as two round trips. Reading the before-code settles the
> wire count: **2 -> 1, one round trip saved.** The body's "3" is a miscount of
> the pipeline's single write.

| | |
|---|---|
| **Numerator** | **1 Redis round trip saved per session invalidation, on the hit path only** |
| Round trips before -> after | hit: 2 -> 1; **miss: 1 -> 1, i.e. no saving at all** |
| RTT per round trip | **337.2 - 589.8 us** (Redis at `redis://127.0.0.1:6379/0`, concurrency 1: min 0.3372 ms, p50 0.4322 ms, p95 0.5898 ms, n=40) |
| **Source** | `perf-results/RESULT-20261012-sweepB/store_rtt.json`, schema `perf-store-rtt/1`, `results[0].summary` (Redis, concurrency 1). Host `perf-local`, 2 cores, per `perf-results/STATE.md` section 4c. |
| **Fraction of the 4 090 us floor** | **8.244 % (at the artefact's min) to 14.421 % (at its p95); 10.567 % at its p50** |
| Endpoint the saving lands on | The **logout / session-invalidation path**, not `GET /users/me`. The fraction is therefore a scale comparison, not an endpoint claim. |
| Tier | **Measured, Linux.** The RTT is a store probe, not the invalidation path itself, and n=40 at concurrency 1; it is reported as a range, not a constant, because the artefact's own note says "RTT is reported ONLY as buckets. No single RTT constant and no p95-per-RTT figure appears anywhere in this artefact, by L-7." |

**What is still owed.** Nothing for the count. The S-1 bead recorded that "the
Phase-0 INFO commandstats delta on perf-local confirming exactly one round trip
saved per invalidation against a real Redis is still owed". The round-trip
**count** above was settled by reading the code, and it has since been
**observed against a real Redis on the perf host**, on both arms -
`perf-results/RESULT-20261020-qs06/s1_session_invalidate_roundtrips.json`
(bead `FastAPI_saas-qs06`, harness
`src/benchmarks/s1_session_invalidate_roundtrips.py`).

| Observed on perf-local | hit path | miss path |
|---|---|---|
| **Before** (the box's pre-S-1 code, commit `93750647d`) | the client sent `HEXISTS` + one packed `MULTI`..`EXEC` pipeline = **2 round trips** | the client sent `HEXISTS` = **1 round trip** |
| **After** (commit `dc115291`) | the client sent one `EVALSHA` = **1 round trip** | the client sent one `EVALSHA` = **1 round trip** |

The command delta was read out of the Redis server's own `INFO commandstats`
around one call to `SessionManager.invalidate_session`, and the counts are
asserted as **absolute** values - no `new < old` comparison is made anywhere.
A control window with no invalidation in it was **empty** in both runs, so the
instrument contributed no traffic of its own.

**The observation settles the dispute: 2 -> 1 on the hit path, and the S-1
commit body's "3 -> 1" is a miscount.** The body counted `MULTI` and `EXEC` as
two round trips; redis-py sends a transaction pipeline as ONE write on ONE
connection, so the before-arm paid two trips, not three. The derivation above
was right and the commit body was wrong, and this record now carries the
observation beside the derivation.

**The saving is on the hit path only**, exactly as the row above says: the
miss path cost one trip before (the `HEXISTS` probe) and one trip after (the
`EVALSHA`, whose internal existence check returns early). The two arms ran on
different commits, on the same box and the same Redis, and the artefact records
the file each arm's code was actually imported from.

**The original figure.** The S-1 bead's close_reason carried the arithmetic
against the Windows range ("0.3 ms = 7.3 % of the 4.09 ms floor at co-located
locality, or 0.72 ms = 17.6 % at the pessimistic end of the Windows range").
7.3 % is the co-located *estimate* from perf section 4 and 17.6 % is a
**Windows** measurement. Both are superseded by the committed artefact above;
neither is quoted as a production claim here.

---

## S-2 - drop the redundant outer rollback in the blacklist duplicate path

**The change.** On the duplicate-token path, the session was wrapped in an
outer `ROLLBACK` that is only correct by accident. The change removes it. The
rollback on the **non-duplicate** path remains.

**The numerator exists now.** It did not when this section was written: a search of
the whole bead corpus for a ROLLBACK / transaction-unwind / savepoint figure
returned nothing, and no bead owned that measurement. Bead `FastAPI_saas-gryp`
owns it now, and the measurement is committed:
`perf-results/RESULT-20261020-gryp/s2_transaction_unwind_cost.json`, harness
`src/benchmarks/gryp_transaction_unwind_cost.py`, perf-local (Linux), 2026-10-20.

**How it was measured.** The duplicate path is reproduced in the database, not
sketched: the staged insert is made inside a SAVEPOINT (`begin_nested()`) - what
`blacklist_tokens` does - the flush raises the unique violation on
`token_blacklist.token`, the savepoint rolls ITSELF back so the outer
transaction is open and holds NOTHING, and only then is the outer unwind timed
(`await db.rollback()`). The exception is classified with the production
predicate (`_is_duplicate_blacklist_violation`), so the arm can only ever be the
duplicate branch. The savepoint's own rollback is outside the timed region.

| | | |
|---|---|---|
| **Numerator** | **355.226 us** (min-of-20) - the cost of ONE transaction unwind, as it would have been executed on the duplicate path. |
| **Source** | `perf-results/RESULT-20261020-gryp/s2_transaction_unwind_cost.json` (bead `FastAPI_saas-gryp`), harness `src/benchmarks/gryp_transaction_unwind_cost.py`, perf-local, 2026-10-20. |
| **Fraction of the 4 090 us floor** | **8.6852 %** - as a SCALE comparison. Logout is not `GET /users/me`, so the saving does not land on that path: it lands on a REPEAT logout. |
| The denominator it would be measured against | EM-6 (`FastAPI_saas-ky66`, closed), `perf-results/RESULT-20261013-logout/logout_step.json`: **p50 12 670.7 us**, min 7 498.1 us, p95 33 834.2 us, 1 000 measured requests, success rate 1.0, 2026-10-13. The unwind is **2.8035 %** of that p50. |
| Tier | **Measured, Linux (perf-local), 2026-10-20.** The `[Unverified - Needs-Linux-measure]` tag is REMOVED for this figure: the measurement settles exactly this figure and no other. |

**Two more arms, for scale.** An unwind WITH a flushed insert to undo costs
**350.558 us** (min-of-20) - the unwind's cost is the round trip, not the work
undone. An unwind with nothing to undo and no transaction open - the local floor
of the call - costs **23.783 us**, so the figure is about 15x the floor and is
ABOVE it, not below it.

**Three caveats that must travel with the figure.** (1) EM-6 measured **normal**
logouts; the duplicate path was not exercised by it at all (1 000 requests, no
duplicate recorded), and the 3.17-3.69 s logout step it was commissioned to
explain **did not reproduce** (0 of 1 000). So even a measured unwind cost
would need a duplicate-path frequency to become an endpoint claim, and this
document has neither. (2) **There is no before arm.** The line S-2 removed is
gone from the tree, so this is the cost of the unwind as it would have been
executed, not a measured saving. (3) The measurement ran with **no tenant
context**: the app logged `restore_rls_context: no tenant context at pool
checkout; resetting RLS vars to empty (fail-closed)` on every connection
checkout. The row read was an existing `token_blacklist` row and the staged
insert was a raw insert under empty RLS variables.

---

## S-3 - no-op `trace_span` when no OTel SDK is installed

**The change.** `trace_span` enters the OpenTelemetry machinery even when the
SDK is not installed. The change short-circuits it.

| | |
|---|---|
| **Numerator** | **2.1470 us per invocation** removed (dead `ProxyTracer` arm 3.0440 us -> guarded arm 0.8970 us) |
| **Source** | M-5 (`FastAPI_saas-7zwp`, closed), `perf-results/RESULT-20261018-7zwp/`. **Linux target**, commit 4ea2c2a8a, 4 cores, Python 3.13.16, measured 2026-10-18T12:38:58Z. Best-of-N, batch mins: dead arm 3.0770 / 3.0780 / 3.0440 / 3.0410 / 3.0170; guarded arm 0.8450 / 0.8390 / 0.9260 / 0.9260 / 0.8970. Arms separated: the dead arm's fastest batch min (3.0170) is above the guarded arm's slowest (0.9260). Both under the unchanged 50.0 us ceiling. |
| **Fraction of the 4 090 us floor** | **0.0525 %** (2.1470 / 4 090). The residual production cost, 0.8970 us, is **0.0219 %**. |
| Endpoint the saving lands on | **A request that opens a database session.** |
| **On `GET /users/me` specifically** | **0 us, i.e. 0.0000 % of the floor.** M-5's probe counted **0** `trace_span` constructions on that path (50 x 401, 0 constructions); EM-1 independently recorded 0 SQL over 2 000 requests on it. The floor path opens no database session and therefore never paid the dead span. |
| Tier | **Measured, Linux.** |

**The before-figure is P-19's, and the two are not interchangeable.** P-19
(`FastAPI_saas-9baq`, closed, `perf-results/RESULT-20261013-span/span_cost.json`,
Linux target, 20 000 repeats, best-of-N) measured the dead path at **3.123 us
best / 3.308 us median** per invocation. M-5 measured it at 3.0440 us. M-5
records explicitly that "P-19's 3.123 us and the harness's 19.486 us were
never the same quantity". The defensible numerator is therefore M-5's **paired
delta** (2.1470 us), not a difference between two unrelated harnesses.

**The scale on the endpoint that does pay it.** P-19 measured the affected
request - `GET /api/v1/users?page=1&per_page=100`, which issues SQL - at a
p50 of **14 226.7 us**. Against that, 2.1470 us is **0.0151 %**, and P-19's
own before-figure of 3.123 us was 0.0220 %.

**An upper bound, not a lower one.** The live production cost is at or **below**
0.8970 us, because the guarded arm never enters the machinery. So 2.1470 us is
an upper bound on what S-3 removed, and the true saving is smaller by up to
0.8970 us.

---

## S-4 - two-level copy for the auth payload

**The change.** `copy.deepcopy` of the auth payload was replaced by a
two-level copy. Three call sites in `src/app/modules/users/cache.py`: the L1
auth-info hit read, the Redis fill, and the auth-info write.

| | |
|---|---|
| **Numerator** | **9.187 us saved per read** (9.788 us -> 0.601 us), measured on Linux. |
| **Source** | `perf-results/RESULT-20261020-x0or/s4_s5_numerators.json` (bead `FastAPI_saas-x0or`), harness `src/benchmarks/x0or_s4_s5_numerators.py`, **Linux target (perf-local), 2026-10-20**. The ORIGINAL instrument is **not in the repository** - the old figure came from a hand-rolled Windows script - so the min-of-N protocol was **re-implemented** with the repo's canonical `src/benchmarks/protocol.py::measure`. |
| **Host** | **Linux (perf-local)**, CPython 3.13.16, 2026-10-20. The old figure's own host was Windows, and its commit body said so: "NOTE this figure is a WINDOWS microbenchmark number. It must be re-measured on Linux per H-11 before it is quoted as a production capacity claim." |
| **Fraction of the 4 090 us floor** | **0.2246 %** (9.187 / 4 090). The old 0.600 % was the same shape of claim against the wrong denominator; the Linux measurement moves both the numerator and the fraction. |
| Secondary scale (EM-1's in-process server min-of-mins, 2 481.0 us) | 0.370 % - labelled as a different instrument, not the floor. |
| Endpoint the saving lands on | The authenticated request path that reads the auth-info payload (the auth prefetch: one `EVALSHA(composite_auth_prefetch)` per request in `raw_linux_d.json`), so it lands on `GET /users/me` **and** on every other authenticated request. No endpoint-level measurement of it exists. |
| Tier | **Measured, Linux (perf-local), 2026-10-20.** The `[Unverified - Needs-Linux-measure]` tag is REMOVED for this figure: the re-measurement settles exactly this figure and no other. The old Windows figure (24.55 us) is quoted above for comparison only and is not a production capacity claim. |

---

## S-5 - derive the request ID once per request

**The change.** Three independent callers of `request_id_from_scope(scope)` each
drew their own `uuid4`, so a single request had up to three identities. The
derivation is now memoised per scope. **Two of the three draws leave the
unconditional path**: one draw remains only when no inbound `X-Request-ID` is
present, and none at all when one is.

| | |
|---|---|
| **Numerator** | **9.841 us saved per request** (two `uuid4` draws removed), measured on Linux. |
| **Source** | `perf-results/RESULT-20261020-x0or/s4_s5_numerators.json` (bead `FastAPI_saas-x0or`), harness `src/benchmarks/x0or_s4_s5_numerators.py`, **Linux target (perf-local), 2026-10-20**. The ORIGINAL instrument is **not in the repository**, so the min-of-N protocol was **re-implemented** with the repo's canonical `src/benchmarks/protocol.py::measure`. The old figure's source was the commit body `7dcf9f9f`: "THE ARITHMETIC (33 us / 0.8 %) ..." - quoted for comparison only. |
| **Host** | **Linux (perf-local)**, CPython 3.13.16, 2026-10-20. The old figure named **no host at all** for its numerator - "An unnamed host is not a Linux host" - which is why it carried the tag. |
| **Fraction of the 4 090 us floor** | **0.2406 %** (9.841 / 4 090). The old 0.807 % was the same shape of claim against the wrong denominator; the Linux measurement moves both the numerator and the fraction. |
| Secondary scale (EM-1's in-process server min-of-mins, 2 481.0 us) | 0.396 % - labelled as a different instrument, not the floor. |
| Endpoint the saving lands on | **Every request**, including `GET /users/me` - the derivation sits in `request_id_from_scope`, which the request-id middleware and the outer guards all call. |
| Tier | **Measured, Linux (perf-local), 2026-10-20.** The `[Unverified - Needs-Linux-measure]` tag is REMOVED for this figure: the re-measurement settles exactly this figure and no other. The old 33 us figure is quoted above for comparison only. |

---

## Summary

| Item | Numerator | Host / tier | Fraction of the 4 090 us uncontended floor | On `GET /users/me`? |
|---|---|---|---|---|
| S-1 | **1 Redis round trip** (2 -> 1 wire round trips, hit path only) | measured, Linux (perf-local), store probe, n=40 | **8.244 % - 14.421 %** (10.567 % at p50) | no - logout / invalidation path |
| S-2 | **355.226 us** for one transaction unwind | measured, Linux (perf-local), 2026-10-20 | **8.6852 %** - a SCALE comparison; logout is not `GET /users/me` | no - logout path; duplicate path only |
| S-3 | **2.1470 us** per DB-touching invocation | measured, Linux (M-5) | **0.0525 %** | **0 us - 0.0000 %** (the floor path opens no DB session) |
| S-4 | **9.187 us** per read | measured, Linux (perf-local), 2026-10-20 | **0.2246 %** | yes, and every other authenticated request |
| S-5 | **9.841 us** per request | measured, Linux (perf-local), 2026-10-20 | **0.2406 %** | yes, and every request |

### Figures that remain unverified

**None of this document's own figures.** Every figure it carried as unverified has since been settled by a committed artefact:

| Figure | Settled by | The measurement |
|---|---|---|
| S-1: the round-trip count against a real Redis | `FastAPI_saas-qs06`, `perf-results/RESULT-20261020-qs06/` | 2 -> 1 client round trips on the hit path |
| S-2: the cost of one transaction unwind | `FastAPI_saas-gryp`, `perf-results/RESULT-20261020-gryp/` | 355.226 us |
| S-4: the copy saving per read | `FastAPI_saas-x0or`, `perf-results/RESULT-20261020-x0or/` | 9.187 us |
| S-5: the request-id draw saving | `FastAPI_saas-x0or`, `perf-results/RESULT-20261020-x0or/` | 9.841 us |

What is still unverified is not a figure in this document: it is the **frequency** with which the duplicate logout path is taken (S-2), which no measurement in this programme records - which is why S-2's fraction is a scale comparison and not an endpoint claim.

## What this document does not establish

1. **It does not re-open GATE B.** `FastAPI_saas-m5ql` remains closed as a
   recorded decision, not a pass, and the Lane B epic `FastAPI_saas-ai2v` is
   still blocked on `FastAPI_saas-33d9` and `FastAPI_saas-osxx`.
2. **It does not repair the absent PR descriptions.** The gate's checkbox 2
   asks for the arithmetic *in the change description*. These figures now exist
   as a committed record; they are not in the bodies, because the bodies are
   history and the artefacts they would live in do not exist (see
   `docs/verification/lane_b_gate_b_record.md`).
3. **It does not promote an evidence tier beyond what a measurement
   settles.** The figures that carried the tag when this document was
   written have each been settled by a committed artefact, and the tag has
   been removed from exactly those. What no artefact settles is the
   duplicate-logout FREQUENCY, so S-2's fraction stays a scale comparison.
4. **It quotes no figure from `tests/benchmarks/baselines/main.json`**, and no
   figure from a Windows artefact is quoted as a production capacity claim.
5. **It does not claim a p95 effect for anything.** Every fraction here is a
   capacity statement about the uncontended floor, which is what the label
   "This moves capacity, not p95" asserts.

## Note on the commit identities (2026-10-20)

Three commits named in this document are **not reachable from this branch's
tip**, so a reader who runs `git show <sha>` on this branch finds nothing.
Each is named here with what it is and where its content now lives.

- **`36dfda38`** - the S-6 commit (the batched L1 publish fan-out) that the
  companion record cites. It was squashed into **`cc6e6d17`** ("perf(security):
  batch L1 fan-out, add C-2/C-5/C-6 oracles, offload body scan"), whose tree is
  byte-identical to the pre-squash tip: `git diff backup-before-squash-10c
  cc6e6d17` is empty. Use `cc6e6d17`.
- **`a94b1d9f`** and **`7dcf9f9f`** - the S-1 and S-5 implementation commits.
  They are contained by **no branch or ref** at all, so they survive in this
  repository's object database only - exactly as the companion record
  `docs/verification/lane_b_gate_b_record.md` states ("Every body below was
  recovered verbatim from the git object database"). A **fresh clone will not
  contain them**: what they carried into the tree is in the tree, and the gate
  texts they carried are transcribed in that record rather than merely linked.

Nothing in this document's arithmetic depends on those commits being
reachable. The arithmetic is arithmetic on recorded numbers and their
recorded sources, and the two figures that were derived from an unreachable
commit body - S-1's round-trip count and S-5's 33 us - have since been
OBSERVED on the Linux target (`FastAPI_saas-qs06` and `FastAPI_saas-x0or`).
