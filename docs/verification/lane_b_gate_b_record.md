# Lane B gate record: the eight S-* items against GATE B

**Status:** Recorded — not a pass
**Date:** 2026-10-20
**Tracked by bead:** `FastAPI_saas-33d9` — "Lane B merge artefacts: commit the per-item gate record and obtain a CI run for the Lane B commit"
**Gate:** `FastAPI_saas-m5ql` (GATE B), quoted verbatim from the master register `FastAPI_saas-hcnr`
**Audited at:** HEAD = `32c3844d` (branch `security/remediate-run3-findings`)

> **Amendment, 2026-10-20, later the same day.** This file is a point-in-time
> audit of the eight bodies and is left as written. Three things changed
> afterwards and are recorded here rather than rewritten into the sections
> below, which continue to describe the state at `32c3844d`:
>
> - The uncontended-floor arithmetic that the S-1 to S-5 sections mark absent
>   is now supplied in `docs/verification/lane_b_checkbox2_arithmetic.md`
>   (commit `b2469605`, bead `FastAPI_saas-osxx`).
> - S-6's implementation, which the S-6 section records as absent from HEAD,
>   landed in commit `36dfda38` (bead `FastAPI_saas-49ui`), together with the
>   §6 oracle extended in place to assert the batched arm.
> - The push that a CI run for the Lane B merge would require was requested and
>   **declined by the owner**. See "CI at the Lane B merge" below.

This file is the record GATE B's checkboxes 2, 3, 4 and 5 refer to as "the PR
description". **No PR description exists for any Lane B item.** The repository
contains exactly three pull requests (#7, #8, #9), all closed, all unrelated to
Lane B, none merged. The Lane B work landed as direct commits and was then
squashed into `489ce4a7` ("perf: Lane B/C implementations - span no-op, payload
copy, scope path"), which is an ancestor of HEAD; that squash's body is a
bullet list of what landed and carries none of the three texts GATE B requires.
This file therefore **substitutes** for the absent artefact. It does not make
the gate pass, and nothing in it may be read as a pass.

Every body below was recovered verbatim from the git object database with
`git show -s --format=%B <sha>`. Where a required text is missing, it is marked
**ABSENT**. Nothing here paraphrases a missing text into compliance, and no
number has been re-derived, re-measured or adjusted.

## Provenance of the eight bodies

| Item | Commit | Subject | Reachable from a branch? |
|------|--------|---------|--------------------------|
| S-1 | `a94b1d9f` | perf(auth): collapse session invalidation into one atomic Lua EVALSHA | no |
| S-2 | `3e2888c5` | perf(auth): drop the redundant outer rollback in the blacklist duplicate path | no |
| S-3 | `3c8e4e72` | perf(observability): no-op trace_span when no OTel SDK is installed | no |
| S-4 | `acc40828` | perf(users): two-level copy for the auth payload | no |
| S-5 | `7dcf9f9f` | fix(middleware): derive the request ID once per request, not once per guard | no |
| S-6 | `3096afc5` | perf(cache): batch the serial PUBLISH fan-out into one round trip (S-6) | no |
| S-7 | `e647e0d8` | docs(middleware): correct the stale attack-scan comments and the 6.7 us figure | no |
| S-8 | `dd7faad5` | perf(logging): count the attack-scan method gate's debug events per level | `backup-before-squash-10b` (local only) |

Two further commits carry gate evidence and are likewise unreachable:
`fc53e4a1` (the S-1 oracle, committed alone) and `a5118320` (the S-2
discriminator repair). `git branch -a --contains <sha>` returns empty for all
nine; they survive as prose in the beads' close_reasons and in this file.

## The gate's five checkboxes

1. the mandatory test from `AUDIT_CODE_SECURITY.md` §6 is green AGAINST HEAD, not just after the change;
2. the PR description states, WITH ARITHMETIC, that it moves capacity and not p95 (§4.7 gate (c)), except where a p95 effect is separately demonstrated;
3. the global green suites still pass — `tests/unit/test_middleware_order.py`, `tests/benchmarks/test_nrgi3_scan_cost.py`, `tests/benchmarks/test_p2_micros.py`, `tests/performance/test_equivalence.py`, `tests/unit/scripts/test_doc_gate_wiring.py`;
4. S-2 only: C-32 discharged and the result recorded in the PR;
5. S-8 only: A-3 landed and the test asserts the real predicate at both levels.

> No test was weakened to accommodate the change; no assertion was relaxed to make a number pass.

## Summary

| Item | §6 oracle green against HEAD | Label | Arithmetic vs the 4.09 ms uncontended floor | No-test-weakening sentence | Oracle committed before the diff |
|------|---|---|---|---|---|
| S-1 | yes | present | **announced, not present** | **ABSENT** (a different sentence present) | **yes** (`fc53e4a1` alone first) |
| S-2 | yes | present | **ABSENT** | **ABSENT** | **yes** (`a5118320` then the one-line diff) |
| S-3 | yes | present | **ABSENT** | **ABSENT** (a different sentence present) | **no** (oracle repair and source change in one commit) |
| S-4 | yes | present | **present, but against a warm 4 ms p50 floor, Windows-derived** | **ABSENT** | **no** (the two new guards landed with the change) |
| S-5 | yes | present | **present, but against a warm 4 ms p50 floor** | **ABSENT** | **no** (the three new tests landed with the change) |
| S-6 | §6 home green, but it pins the serial arm only | present | **present, conditional; N explicitly not measured** | **ABSENT** | **no, by deliberate decision** (recorded in the body) |
| S-7 | yes | present (capitalised) | **present as 0 us, not expressed against the floor** | **ABSENT** | n/a (no oracle change; pre-check bead closed) |
| S-8 | yes | present | **present** | **ABSENT** | partly (§6 A-3 home pre-existing; its own counter oracle landed with the change) |

The label is present in eight of eight bodies. The arithmetic against the
**uncontended** floor is present, in substance, in **two** of eight (S-8, and S-6
in conditional form). The gate's no-test-weakening sentence is present in
**zero** of eight. The oracle-before-the-diff condition is met in form for
**three** of eight (S-1, S-2, S-7), and S-6's non-compliance is recorded in the
body itself.

The table above describes the **bodies**. The arithmetic that S-1 to S-5 do not
carry is supplied separately in `docs/verification/lane_b_checkbox2_arithmetic.md`.

---

## S-1 — `a94b1d9f`

**Label (verbatim):**

>     This moves capacity, not p95.

**Arithmetic vs the 4.09 ms uncontended floor: ANNOUNCED, NOT PRESENT.**
The body opens "Bead FastAPI_saas-10iv (S-1). Carries the 4.09 ms
uncontended-floor arithmetic. Section 4.7 gate (c) label, verbatim:" and the
arithmetic it does carry is a round-trip count:

```
  client round trips per invalidation
    hit (session present)   3 -> 1
    miss (session absent)   1 -> 1
```

No microsecond figure and no fraction of 4.09 ms appear. The body says
"no p95 effect is claimed and no evidence tier is promoted".

**Supplied separately:** `docs/verification/lane_b_checkbox2_arithmetic.md` gives
the arithmetic this body does not carry, and records that the body's "3 -> 1"
round-trip count is a miscount: reading the before-code
(`git show a94b1d9f^:src/app/core/security/session_manager.py`) shows one
`HEXISTS` trip plus one packed `pipeline()` trip, so the wire count is 2 -> 1.

**No-test-weakening sentence: ABSENT.** The closest statement is:

> Two tests pinned the old mechanism BY NAME and were replaced, not weakened or deleted: test_session_manager_v2.py's uses_hdel_and_zrem and test_session_manager_zset.py's test_invalidate_removes_from_both. Each now asserts the mechanism that exists, and each records why it was replaced.

**Oracle before the diff: YES.**

> THE ORACLE LANDED ALONE FIRST
> fc53e4a1 committed the oracle with no source change, so it could certify
> the diff rather than be written to fit it.

A second oracle already existed and was missed by a grep:
`tests/unit/core/security/test_s01_session_invalidation_oracle.py`.

**Suites recorded in the body:** `uv run pytest tests/unit/core/security/ -q` — 585 passed; ruff clean on the five touched files; mypy at its 4-error pre-existing baseline.

---

## S-2 — `3e2888c5`

**Label (verbatim):**

> This moves capacity, not p95.

**Arithmetic vs the 4.09 ms uncontended floor: ABSENT.**

> No p95 arithmetic is offered yet. EM-6 is the evidence for a logout p50 under
> load and it is not in this bead.

**Supplied separately:** `docs/verification/lane_b_checkbox2_arithmetic.md` - the
arithmetic for this item is a **non-existent numerator**: the cost of one
transaction unwind is not measured anywhere in this programme, and no bead owns
that measurement.

**No-test-weakening sentence: ABSENT.** No sentence in the body attests to it.

**Checkbox 4 — C-32 discharged and recorded: PRESENT.**

> Bead C-32 (FastAPI_saas-m3r3, closed) was the precondition and answered
> PERMITTED. It enumerated both production callers -- logout at
> auth/service.py:633 and refresh rotation at auth/services/tokens.py:205 -- and
> found neither holds pending work at that point: grepping db.add/execute/merge/
> flush/commit/delete returns zero hits in either module, so `blacklist_tokens` is
> the only writer on that session. The outer rollback was therefore correct only
> by the accident that both call sites happen to be clean, never by construction.

**Oracle before the diff: YES.**

> The oracle existed before the diff and was RED. tests/performance/
> test_rollback_verification.py counts bare ROLLBACKs against a fixed ceiling and
> was failing 8 tests on HEAD -- not because of this line, but because the
> duplicate-token discriminator was PostgreSQL-only (see a5118320). That was fixed
> separately and deliberately, because 1kzk's acceptance requires a one-line diff
> and folding a repair into it would have violated the discipline the bead
> enforces. The oracle is green before this deletion, which is what makes the
> deletion defensible rather than merely plausible.

**Suites recorded in the body:** 608 passed (`tests/performance/test_rollback_verification.py`, `tests/unit/core/db/test_token_blacklist_column_width.py`, `tests/unit/core/security`); ruff clean; mypy at its 4-error pre-existing baseline.

---

## S-3 — `3c8e4e72`

**Label (verbatim):**

> "This moves capacity, not p95."

**Arithmetic vs the 4.09 ms uncontended floor: ABSENT.**

> No microsecond figure is offered here. P-19 is the measurement, and it is not in this change.

**Supplied separately:** `docs/verification/lane_b_checkbox2_arithmetic.md` - the
figure is M-5's paired delta, **2.1470 us**, and it is **0 us on
`GET /users/me`** because that path opens no database session.

**No-test-weakening sentence: ABSENT.** The closest statement is:

> No test was weakened — one assertion that could not be true was
> deleted rather than repaired, and one that only made sense for one
> branch now says which branch it is on.

**Oracle before the diff: NO.** The body records why a new oracle was needed
and that it arrived with the change:

> THE ORACLE COULD NOT SEE THIS CHANGE, and that is why it needed a new
> one. tests/unit/core/observability/test_trace_span_noop.py was already
> green before the diff and stayed green after it: every test in it
> asserts what a CALLER observes, and a caller cannot tell the difference
> between the ProxyTracer path and the no-op. A suite that passes
> identically with and without the change is not an oracle for the change.

The body also records that the same commit repaired a stale line-number pin
(`EXPECTED_CALL_SITES` 202/241 → 203/250) that had been failing fifteen tests.

---

## S-4 — `acc40828`

**Label (verbatim):**

> This moves capacity, not p95.

**Arithmetic: PRESENT, but not against the uncontended floor, and the numerator is a Windows artefact.**

> Arithmetic: 24.55 us saved per read against a 4 ms /users/me p50 floor, or
> 0.6 percent. NOTE this figure is a WINDOWS microbenchmark number. It must be
> re-measured on Linux per H-11 before it is quoted as a production capacity
> claim. No evidence-tier promotion is claimed and no p95 effect is asserted.

Two deviations from what the gate asks for, both recorded above verbatim: the
floor is the **warm** `/users/me` p50 (4 ms), not the 4.09 ms uncontended
floor; and the numerator is explicitly a Windows microbenchmark figure, which
the standing rules forbid quoting as a production capacity claim (A-5).

**Corrected:** `docs/verification/lane_b_checkbox2_arithmetic.md` - the numerator
is unchanged (24.55 us, Windows) and the fraction against the 4 090 us
uncontended floor is **0.600 %**, not "0.6 percent" against a 4 000 us warm p50.

**No-test-weakening sentence: ABSENT.** The body carries a mutation proof
instead:

> Two new tests close that: one asserts zero
> copy.deepcopy CALL nodes remain in cache.py, the other that exactly three
> _two_level_copy call sites exist and each takes one positional argument. Both
> were proven to fail with one call site reverted, then restored and verified
> by byte count.

**Oracle before the diff: NO, in form.** The oracle file pre-existed and was
green before and after — it "cannot tell whether the conversion happened" —
and the two guards that can were added in the same commit as the change.

---

## S-5 — `7dcf9f9f`

**Label (verbatim):**

> This moves capacity, not p95.

**Arithmetic: PRESENT, but not against the uncontended floor.**

> THE ARITHMETIC (33 us / 0.8 %)
> 33 us per request is 0.8% of the 4 ms /users/me p50 floor EM-1 recorded. Two
> of the three draws are removed from the unconditional path, leaving one draw
> only when no inbound header is present and none at all when one is. The saving
> is real but small, which is why the honest label is capacity: this does not
> move p95, and claiming otherwise would be the same mistake this programme has
> already made once about a ceiling.

The floor is the **warm** `/users/me` p50 (4 ms, EM-1), not the 4.09 ms
uncontended floor. The body names the harness for the floor (EM-1) but **does
not name a harness or host for the 33 us numerator**; that gap is left as it
stands and is not filled in here.

**Corrected:** `docs/verification/lane_b_checkbox2_arithmetic.md` - the numerator
is unchanged (33 us, no host named) and the fraction against the 4 090 us
uncontended floor is **0.807 %**, not "0.8 %" against a 4 000 us warm p50.

**No-test-weakening sentence: ABSENT.** The body carries a proof by removal
instead ("Removing the memoisation makes the suite fail (1 error), and
restoring it makes it pass").

**Oracle before the diff: NO, in form.** The three new tests were added to the
**existing** `tests/unit/middleware/test_request_id_propagation.py` in the same
commit as the change.

**Suites recorded in the body:** `tests/unit/middleware/ + tests/unit/test_middleware_order.py` — 558 passed, 2 xfailed; ruff clean; mypy at its 4-error pre-existing baseline.

---

## S-6 — `3096afc5`

**Label (verbatim):**

> This moves capacity, not p95.

**Arithmetic vs the 4.09 ms uncontended floor: PRESENT, CONDITIONAL, with N explicitly not measured.**

> This moves capacity, not p95. The saving is (N-1) x RTT: the N-1 round trips
> disappear, and the work that remains — the local drops, and one publish — is
> the same work a single-key invalidation already did.
>
> N IS NOT YET MEASURED. EM-7 (FastAPI_saas-39wz) and EM-8 (FastAPI_saas-uo7z)
> own the fan-out width. Until they land, no figure may be quoted, because the
> product is meaningless without N — (N-1) x RTT against the 4.09 ms uncontended
> floor is 7.3% x (N-1) only if the co-located 0.3 ms estimate holds. The bead's
> acceptance clause asking this PR to "state the measured fan-out width N" is
> therefore recorded as NOT YET SATISFIED rather than papered over with a guess.

The bead's own close_reason later states N = 4B for the `users/cache.py` sites
and leaves the `rbac` site unmeasured. The `rbac` site figure is not stated in
the commit body.

**No-test-weakening sentence: ABSENT.** The body carries a twelve-mutation
proof instead ("twelve mutations, all twelve caught, all six files restored and
SHA-256-verified byte-identical after every run"), and records a bug its own
oracle caught — the dedented `await pipe.execute()` that redis-py 5.3.1's
`Pipeline.__aexit__` would have turned into an empty batch, silently dropping
every cross-worker invalidation.

**Oracle before the diff: NO, BY DELIBERATE DECISION, and the body says so.**

> The oracle and the diff land together, unlike the LC-C3-OR oracle which landed
> alone. LB-S6-T is deliberately RED on the pre-diff tree (27 failed, 1 passed)
> and green after, so committing it first would have landed a red suite.

**Suites recorded in the body:** LB-S6-T 30 passed (before the diff: 27 failed, 1 passed); `tests/unit/core/cache/` 188 passed; `+ tests/unit/modules/rbac/` 254 passed; `tests/unit/modules/users/` 206 passed, 1 skipped; `tests/unit/core/security/` 585 passed; ruff `--no-fix` at the 9-error pre-existing baseline; mypy at its 4-error pre-existing baseline.

---

## S-7 — `e647e0d8`

**Label (verbatim, capitalised in the body):**

> THIS MOVES CAPACITY, NOT p95.

**Arithmetic: PRESENT as 0 us, but not expressed against the uncontended floor.**

> THIS MOVES CAPACITY, NOT p95. The arithmetic is 0 us: this diff touches
> comments and one markdown table row and nothing else. No runtime code path
> changes, no benchmark is re-measured, and no performance claim is made.

**No-test-weakening sentence: ABSENT.** The body does not attest to it.

**Oracle before the diff: n/a — no oracle change was required.** The pre-check bead was closed and named the files whose assertions had to stay put:

> Pre-check bead LB-S7-T (FastAPI_saas-9aw8) is closed, as the bead requires.
> It named tests/benchmarks/test_nrgi3_scan_cost.py:10-18,:214 and
> test_p2_micros.py:139,147; both still assert what they always did.

The body also records that the replacement figures are quoted with their harness
and tagged `[Unverified - Needs-Linux-measure]`, and that ADR-0008 was
annotated, not contradicted.

**Suites recorded in the body:** ruff clean; mypy at its 4-error pre-existing baseline; 205 passed across `tests/unit/middleware/test_attack_detection.py` and `test_attack_pruning_differential.py`; `scripts/check_doc_paths.py` exits 0.

---

## S-8 — `dd7faad5`

**Label (verbatim):**

> This moves capacity, not p95.

**Arithmetic vs the 4.09 ms uncontended floor: PRESENT.**

> This moves capacity, not p95. The guard and the event it suppresses are 0 us
> - 0.00 % of the 4.09 ms uncontended floor and 0.00 % of the 740 ms p95 - so
> there is no duration to report, and a microbenchmark asked to confirm that
> would have produced a number about the harness rather than about the call
> site. What can be substantiated is a count.

**No-test-weakening sentence: ABSENT.** The body does not attest to it.

**Checkbox 5 — A-3 landed, recorded in the PR: the SUBSTANCE is present, the RECORDING is ABSENT.** The string "A-3" does not occur anywhere in this body. What the body records is the predicate itself:

> Also closes the half of S-8's acceptance criterion that was not asserted:
> the real predicate is now asserted True at DEBUG as well as False at INFO.
> The DEBUG direction is what distinguishes a delegating predicate from a
> frozen-minimum one - make_filtering_bound_logger(INFO) returns a
> BoundLoggerFilteringAtInfo whose is_enabled_for(DEBUG) is also False.

**Oracle before the diff: partly.** The §6 home for A-3,
`tests/unit/core/test_logging_wrapper_oracle.py`, pre-existed the change. The
item's own counter oracle, `tests/unit/benchmarks/test_s8_logger_call_counter.py`
(25 tests), landed in the same commit as the change.

**Artefact recorded in the body:** `perf-results/RESULT-20261019-uo44/` (with FINDINGS.md).

---

## Global green suites — the per-item evidence

The five suites GATE B checkbox 3 names, as each item's body recorded them:

| Item | Suites named in the body |
|------|--------------------------|
| S-1 | `tests/unit/core/security/` — 585 passed |
| S-2 | `tests/performance/test_rollback_verification.py`, `tests/unit/core/db/test_token_blacklist_column_width.py`, `tests/unit/core/security` — 608 passed |
| S-3 | no suite list in the body; the bead's close_reason records 155 passed over `tests/unit/core/observability/` and `tests/unit/core/events/` |
| S-4 | no suite list in the body; the bead's close_reason records `tests/unit/modules/users/` — 206 passed, 1 skipped |
| S-5 | `tests/unit/middleware/` + `tests/unit/test_middleware_order.py` — 558 passed, 2 xfailed |
| S-6 | LB-S6-T 30 passed; `tests/unit/core/cache/` 188 passed; `+ tests/unit/modules/rbac/` 254 passed; `tests/unit/modules/users/` 206 passed, 1 skipped; `tests/unit/core/security/` 585 passed |
| S-7 | 205 passed across `tests/unit/middleware/test_attack_detection.py` and `test_attack_pruning_differential.py` |
| S-8 | no suite list in the body; the body names the 25-test counter oracle and the artefact |

None of the five checkbox-3 suites is named in any of the eight bodies. They
were run separately, under `FastAPI_saas-d1gh` (GLOBAL GREEN SUITES, closed).

## CI at the Lane B merge

There is no CI run for the Lane B merge. `.github/workflows/tests.yml`
("Backend CI") has bare `push:` and `pull_request:` triggers with no path or
branch filter, so it would have run on every push to this branch. It did not:

- `gh run list --branch security/remediate-run3-findings` — the newest Backend
  CI run is 2026-10-11 (a failure, sha `93750647`); nothing since.
- `gh run list --commit 489ce4a7` — empty.
- `gh run list --commit 32c3844d` — empty.

At the time of this record HEAD is **23 commits ahead of**
`origin/security/remediate-run3-findings` (`b2469605` against `93750647`), and
the Lane B squash `489ce4a7` is among the unpushed commits, which is why no run
exists for it. Obtaining one requires a push, which is an owner action.

**The push was requested and was declined.** On 2026-10-20 the owner was asked
directly whether the branch may be pushed to origin so that Backend CI could
run against the commit carrying the Lane B work, and the answer was no: the
branch stays local. The absence of a CI run for the Lane B merge is therefore
a **recorded owner decision**, not an oversight. The five checkbox-3 suites
were run locally at HEAD and are green (54 passed in 103.07 s), but a local run
is not a CI run and is not presented as one.

## What this record does not do

It does not close GATE B. GATE B (`FastAPI_saas-m5ql`) closed as a recorded
decision, not as a pass, and the reasons are set out in that bead's
close_reason. This file supplies the per-item evidence GATE B's checkboxes
refer to; it does not supply the PR descriptions or the CI run, neither of
which exists. The uncontended-floor arithmetic is supplied separately in
`docs/verification/lane_b_checkbox2_arithmetic.md`, and the implementation of
S-6 landed separately in commit `36dfda38`. What remains open is the CI run
itself, now tracked as `FastAPI_saas-0xrf`; the PR-description decision was
taken by the owner on 2026-10-20 and is recorded in the amendment below.

No test was weakened to accommodate this record; no assertion was relaxed to
make a number pass. No number in this file was re-derived, re-measured or
adjusted: every figure is quoted from the recovered commit body it came from,
and every absence is marked ABSENT rather than filled in.

## Amendment (2026-10-20): the owner accepts this record as the Lane B artefact of record

**The decision.** The Lane B epic (`FastAPI_saas-ai2v`) cannot close on its own
acceptance criteria, which require each of the eight S-* PR descriptions to
carry the literal sentence "This moves capacity, not p95" plus arithmetic
against the 4.09 ms uncontended floor. There is no Lane B PR: the work landed
as eight commits and then as the squash `489ce4a7`, the repository's only three
PRs (#7, #8, #9) are unrelated and closed, and nine of the ten commits that
carry the gate's required texts are contained by no branch or ref — they
survive as git objects and as prose in the beads' close_reasons.

The owner was asked directly, and on 2026-10-20 chose: **accept the committed
record as the substitute.** This file, together with
`docs/verification/lane_b_checkbox2_arithmetic.md`, is the artefact of record
for Lane B. The Lane B epic closes on their strength, with the deviation
recorded rather than passed over.

**What the substitute carries, and what it does not.**

It carries, for all eight items: the capacity label, the arithmetic against the
uncontended floor, the oracle-before-the-diff evidence, and the per-item suite
evidence. It does **not** carry the artefact the gate names — a PR
description. The no-test-weakening sentence is still absent from all eight
original bodies; it is present only in the commit messages of the two records
themselves.

**This is a recorded decision, not a pass.** GATE B (`FastAPI_saas-m5ql`)
closed as a recorded decision and is not re-opened by this amendment. Nothing
here upgrades a gate that was not met: the eight PR descriptions never existed,
and this amendment does not pretend that a committed document is one.

**Still open, and not covered by this decision.** The checkbox-3 CI run for the
Lane B commit is a separate item, tracked as `FastAPI_saas-0xrf`, and it is
blocked on a push the owner has declined.

No test was weakened to accommodate this decision; no assertion was relaxed to
make a number pass.
