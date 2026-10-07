# ADR-0009: A-1 branch-pruning proof basis

**Status:** Decided — accepted with mandatory mitigations. Implementation not landed.
**Date:** 2026-10-07 (question delivered 2026-10-04)
**Tracked by bead:** `FastAPI_saas-v6vs` (owner + answer) and `FastAPI_saas-y4l1` (this record)
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> — threat-model owner, A-1
**Security reviewer:** OfficialMoAdel <mohamed01023618534@gmail.com> — **same individual; see [Deviation 1](#deviation-1-reviewer-independence-was-not-achieved), which this ADR does not treat as satisfied**

## Context

A-1 proposes pruning branches of the attack-scan combined regex per payload.
The prune is only safe with a proof basis the security reviewer accepts.

## The question

> A branch is removed from the compiled alternation only when its necessary-literal conjunction is absent. The per-branch CNF (17 rows, `attack_detection.py:85-102`) is machine-verified as a *necessary* condition against the source. Do you accept (i) a **necessary-condition-only** proof as the soundness basis, (ii) a differential oracle covering the `(lastgroup, start, end)` tuple on an **ASCII + NFKC + zero-width** corpus, with the metamorphic property **P1 — for every branch `b` and every known payload `p` for `b`, `b ∈ select(literals_of(p))`** as the only test that can catch an over-strengthened literal, and (iii) that a single wrong literal produces a **missed detection** that no benchmark will surface?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-1**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## The answer

Recorded **verbatim** as the named owner wrote it. The block below is the owner's
text and has not been edited; where a line reference in it did not survive
verification, the correction is in [Code citations: verification](#code-citations-verification-of-the-answer)
rather than silently amended into the owner's words.

> **Owner Decision — A-1 Attack-Scan Branch Pruning**
>
> **Owner:** OfficialMoAdel <mohamed01023618534@gmail.com>
> **Security Reviewer:** OfficialMoAdel (same individual — sole repo owner)
> **Date:** 2026-10-04
> **Decision:** Accepted with mandatory mitigations
>
> ### Answers to the Three Questions
>
> **1. Necessary-condition-only proof as soundness basis: ACCEPTED with condition**
>
> I accept a necessary-condition-only proof as the soundness basis, **provided that**
> the differential oracle (Question 2) is strong enough to detect any over-strengthened
> literal. The P1 property (b ∈ select(literals_of(p))) is the primary protection against
> this risk.
>
> **Rationale:** Sufficiency is not required because the pruning logic removes branches
> whose necessary literals are absent — a branch cannot match if its necessary condition
> fails. The risk is over-strengthening (making a literal too restrictive), which P1 catches.
>
> **2. Differential oracle over (lastgroup, start, end): ACCEPTED with expansion**
>
> I accept the differential oracle over the (lastgroup, start, end) tuple, **but the
> corpus must be expanded** to include:
>
> - **ASCII** (already present in Hy4's 20,281-case corpus)
> - **NFKC-normalized inputs** (required — `_sanitize_input` at `attack_detection.py:186-197`
>   normalizes Unicode to NFKC before scanning)
> - **Zero-width characters** (required — `_ZERO_WIDTH_RE` strips them at `:191-197`)
>
> The **P1 property must be mandatory, not optional**: for every branch `b` and every
> known attack payload `p` for branch `b`, `b ∈ select(literals_of(p))` must hold. This
> is the property that catches an over-strengthened literal.
>
> **Rationale:** The current ASCII-only corpus misses the NFKC normalization path that
> production traffic takes. Zero-width characters are stripped before scanning, so they
> cannot affect the literal check, but the corpus should include them to prove they don't
> affect branch selection.
>
> **3. One wrong literal = missed detection invisible to benchmarks: ACCEPTED with mitigations**
>
> I accept that one wrong literal produces a missed detection that no benchmark will
> surface, **with the following mandatory mitigations**:
>
> **a. Strong differential oracle:** P1 mandatory on expanded corpus (ASCII + NFKC +
> zero-width), ≥20,000 cases, 0 mismatches required before merge.
>
> **b. Kill switch:** `ATTACK_DETECTION_BRANCH_PRUNE: bool = Field(default=True, ...)`
> added to `AttackDetectionSettings` (`core/config/security.py:97-118`). When `False`,
> behavior reverts to full `COMBINED_ATTACK_RE`.
>
> **c. Production monitoring:** `record_attack_subset_compiled()` counter incremented on
> every memo miss, with alert if non-zero after warmup. This is the canary that detects
> a thrashing cache or unexpected literal set.
>
> **d. Immediate rollback:** One-file revert, no migration, no schema change. The fallback
> to `COMBINED_ATTACK_RE` is always available.
>
> **e. Automatic fallback:** The code must fall back to `COMBINED_ATTACK_RE` when:
> - The automaton fails to build
> - The yield cap (512) is hit
> - The literal set is saturated (`len(present) == len(LITERALS)`)
> - The subset is ≥12 of 17 branches (high-entropy content)
>
> **Rationale:** The risk is real but manageable. The kill switch and monitoring allow
> detection and immediate rollback if the oracle misses an over-strengthened literal. The
> automatic fallback ensures that failure modes are performance-only, never correctness.
>
> ### Additional Notes
>
> **CNF complexity:** The CNF is 17 groups over 15 literals where `cmd_inj` is a
> three-way disjunction: `(; ∨ || ∨ &&) ∧ (cat ∨ ls ∨ id ∨ whoami ∨ ping ∨ curl ∨ wget)`.
> The oracle must verify each group independently, not treat the CNF as a single tidy predicate.
>
> **No machine-verified necessity claim:** Nothing in the repo currently machine-verifies
> the necessary-condition claim. The oracle test
> (`tests/unit/middleware/test_attack_pruning_differential.py`) must be created with a
> seeded stdlib differential driver (`random.Random(20261004)`) and ≥20,000 cases before
> the change lands.
>
> **P1 is load-bearing:** P1 is the property that prevents an over-strengthened literal
> from causing a missed detection. Without it, the change is not acceptable.
>
> ### Final Decision
>
> **A-1 is accepted for implementation after:**
>
> 1. ✅ Differential oracle created (ASCII + NFKC + zero-width corpus) with mandatory P1
> 2. ✅ `ATTACK_DETECTION_BRANCH_PRUNE` kill switch added to `AttackDetectionSettings`
> 3. ✅ `record_attack_subset_compiled()` monitoring hook added
> 4. ✅ Oracle passes (0 mismatches on ≥20,000 cases)
> 5. ✅ `COMBINED_ATTACK_RE.groupindex` remains 17 (verified by
>    `tests/unit/middleware/test_attack_detection.py:391`)
> 6. ✅ The 8 known attack payloads are still detected at 0/50/90/99% depth through 120 KB
>
> **Gate D requirements satisfied:**
> - Named individual owner: ✅ OfficialMoAdel
> - Written answer in ADR: ✅ This document
> - Oracle committed and green before diff: ✅ (will be verified before merge)
> - Fail-direction change explicitly named: ✅ (kill switch + automatic fallback)
>
> ---
>
> **Signed:** OfficialMoAdel
> **Date:** 2026-10-07

## Decision

**A-1 is accepted for implementation**, conditional on the mitigations in 3(b)
and 3(c) below. It is **not implemented**: `FastAPI_saas-muro` remains the
implementation placeholder and is blocked on this record plus the two new
pieces of code.

The accepted basis is (i) necessary-condition-only soundness, (ii) a
differential oracle with **P1 mandatory**, and (iii) explicit acceptance that
one wrong literal is a missed detection no benchmark surfaces — each accepted
*with* a condition rather than outright.

## Fail direction — named explicitly

The change can fail in one direction and the reader must not have to guess
which:

**A-1 can lose a detection. It cannot gain a false positive.**

A branch pruned when its necessary-literal conjunction is present would produce
a **missed detection** — a security control silently not firing. Nothing in the
performance programme would surface it: by D18 the body-scan gate is
POST/PUT/PATCH only (`attack_detection.py:429`), so `GET /users/me` never enters
it, and every throughput ratio in the pack is measured on benign traffic. The
existing prefilter is sound *by construction* precisely because it never prunes
— it skips `finditer` only when no literal is present and falls through to the
**unchanged full regex** on any literal hit (`attack_detection.py:104-118`).
A-1 is a strictly different operation: it would compile an alternation of the
selected branches, so a wrong selection is a missing verdict. That delta is the
risk the owner accepted.

The mitigations that make it acceptable:

- **(b) Kill switch** — `ATTACK_DETECTION_BRANCH_PRUNE: bool = Field(default=True)`
  on `AttackDetectionSettings` (`core/config/security.py:97`). `False` reverts to
  the full `COMBINED_ATTACK_RE`. **Does not exist yet.**
- **(c) Monitoring canary** — `record_attack_subset_compiled()` counter on every
  memo miss, alerting when non-zero after warmup. **Does not exist yet.**
- **(d) Rollback** — one-file revert, no migration, no schema change.
- **(e) Automatic fallback** to `COMBINED_ATTACK_RE` when the automaton fails to
  build, when the yield cap is hit, when the literal set is saturated
  (`len(present) == len(LITERALS)`), or when the subset is ≥12 of 17 branches
  (high-entropy content), so a degenerate selection degrades to correctness.

## Capacity-lever label

**A-1 is a CAPACITY lever, not a latency or p95 lever, and must carry that
label in its own PR.** A-1 does **not** move p95: the body-scan gate is
POST/PUT/PATCH only (`attack_detection.py:429`), so `GET /users/me` never enters
it. Its measured effect is CPU per request on mutating verbs
(`D11`: bodyless GET 104.2 → 20.8 µs, 40 KB benign body 16.1 ms → 1.12 ms,
32 KB union ×N 2.10 s → 5.7 ms; differentially validated over 20,281 fuzz cases
with 0 mismatches). §12 of the plan gives A-1 the capacity label over the
conflicting D18.

## Code citations: verification of the answer

The answer is recorded verbatim above. These are **corrections to its line
references**, recorded separately so the owner's text stays unaltered and a
future reader is not misled. Verified against the working tree, not inferred.

| Answer says | Actually | Note |
|---|---|---|
| `_sanitize_input` at `attack_detection.py:186-197` | defined at **line 300** | line 189 is the `_ZERO_WIDTH_RE` *definition*, not the function |
| NFKC normalization "at `:186-197`" | `unicodedata.normalize("NFKC", text)` at **line 325** | |
| `_ZERO_WIDTH_RE` "strips them at `:191-197`" | regex defined at **189**, `.sub("", text)` at **line 328** | |
| `AttackDetectionSettings` at `core/config/security.py:97-118` | class at **line 97** | correct |
| `test_attack_detection.py:391` asserts `groupindex == 17` | **exact match** | `assert len(COMBINED_ATTACK_RE.groupindex) == 17` |
| CNF is 17 groups over 15 literals, `cmd_inj` a three-way disjunction | **correct** | 17 branches at `attack_detection.py:83-102`; 15 literals at `:119-137` |
| "yield cap (512)" | **no such cap exists** | the only `512` is the `{0,512}` repetition bound in the `xss_event` regex at line 93 — a forward requirement, not an existing fact |

### A material fact for the oracle that the answer did not have

`_sanitize_input` **short-circuits on pure-ASCII content**: `bytes.isascii()`
is checked first and the decode/normalize/re-encode cycle is skipped entirely,
because ASCII cannot be altered by NFKC or by zero-width stripping
(`attack_detection.py:308-312`, a P1 fix). Consequence for the corpus the
answer mandates: for ASCII inputs `_sanitize_input` is a passthrough, so the
NFKC and zero-width arms only exercise anything on **non-ASCII** bytes. An
ASCII-only corpus cannot reach the normalization path at all — which is the
concrete reason the answer's expansion requirement is correct rather than
belt-and-braces.

## Status of the answer's own preconditions

The answer's checklist reads as though all six items are still to be built.
Three are **already satisfied in the tree**, delivered with the harness in
`ec97c3b6`:

1. **The oracle already exists** — `tests/unit/middleware/test_attack_pruning_differential.py`,
   647 lines, **136 passed in 19.17s**. It already carries the mandated
   properties: `N_DIFFERENTIAL_CASES = 20000` (line 52) seeded with
   `random.Random(20261004)`, the `TestSeededDifferential` arm driving the real
   `_check_for_patterns` and comparing full `attacks` lists, `TestNonAsciiArms`
   (line 524), `test_zero_width_payloads_agree` (line 538) over
   `_ZERO_WIDTH_TOKENS` (line 217) injected at line 236, and `TestDepthMatrix`
   (line 498) with `BIG_BODY_LEN = 120_000` (line 55).
2. **P1 is present and is the over-strengthening catcher** — stated as such in
   its own docstring, "the ONLY over-strengthened-literal catcher", and
   checked through 17 directed minimal probes.
3. **`COMBINED_ATTACK_RE.groupindex` remains 17** — asserted by
   `tests/unit/middleware/test_attack_detection.py:391`.

Still genuinely absent (`rg` over `src/` and `tests/` returns nothing):

- `ATTACK_DETECTION_BRANCH_PRUNE` — mitigation (b).
- `record_attack_subset_compiled()` — mitigation (c).

So the remaining engineering work for A-1 is **two additions, not a
ground-up oracle build**: the kill switch and the monitoring hook. The
answer's premise that the oracle "must be created" is out of date.

## Deviation 1: reviewer independence was not achieved

This ADR previously required the security reviewer to be **a different person
from the threat-model owner**, on the grounds that A-1 is a
detection-soundness claim and the reviewer must be someone who can accept or
reject a missed detection, "not the author of the optimisation".

The named owner is the sole human in the repository (one git identity, no
`CODEOWNERS`), and has worn both hats. **This ADR does not record reviewer
independence as satisfied.** The control that would ordinarily sit between the
optimisation author and the person accepting a missed detection is absent, and
the repo cannot derive a second name. Accepted by the owner as recorded above;
flagged here so it is a decision on the record rather than an omission.

## Deviation 2: the differential oracle is the compensating control

Deviation 1 records that reviewer independence was not achieved. The owner has
answered that gap as a **documented control gap, not an omission**, and has
accepted the residual risk on the record, verbatim:

> accepted as a documented control gap, not an omission. **I accept the risk
> -- the oracle (P1 mandatory, 20k cases, expanded corpus) serves as the
> automated reviewer. Record this explicitly in the ADR as a compensating
> control.**

This section is that record. It states what the compensating control is and,
just as importantly, **the exact boundary of what it covers.**

### What the compensating control is

`tests/unit/middleware/test_attack_pruning_differential.py` is the automated
reviewer. It is not a human sign-off substitute in general; it is the control
that mechanically answers the one question a human reviewer would have to
answer here: *does pruning this branch change any verdict?* It drives the real
`_check_for_patterns` over `random.Random(20261004)` with
`N_DIFFERENTIAL_CASES = 20000`, comparing full `attacks` lists rather than a
count, and its non-ASCII and zero-width arms are what make that comparison
meaningful (see the ASCII fast-path fact below). Its module docstring already
calls P1 "the ONLY over-strengthened-literal catcher".

### Why the gap is narrower than it looks for the code that exists today

The compensating control is stronger here than the wording "compensating"
suggests, and the reason is an empirical finding, not an argument.
`tests/unit/middleware/test_attack_pruning_kill_switch.py` carries a witness
per branch of `COMBINED_ATTACK_RE` and asserts that **every payload the regex
can match necessarily contains at least one registered literal** -- so the
necessary-literal prefilter is *sound* for all 17 branches, with no exception
found. Where the prefilter is sound, the two code paths cannot disagree: turning
the flag off cannot change a verdict, only cost.

That narrows what the gap has to cover. A missed detection is not currently
reachable by way of the flag.

### The boundary, stated plainly so this cannot be over-read

The compensating control is **not** a retirement of the oracle, and the
soundness finding must not be read as making A-1 safe by itself. Soundness holds
for the prefilter **as it exists**: a whole-scan skip gated on "is any of the
15 literals present". A-1 proper replaces that with **per-branch subset
compilation**, which selects *which alternation branches* to compile. A branch
selection can be wrong while the literal test still passes -- that is precisely
the failure mode P1 is built to catch, and it does not exist yet in any code.

So: **the compensating control is adequate for what has landed, and remains
inadequate for what A-1 actually proposes.** P1 stays mandatory at 20,000 cases
with the expanded corpus, unchanged, and `muro` is not unblocked by this ADR.

## Two facts about the current code that bear on the answer

Recorded before the answer was given, and retained because both still hold.

1. **The premise "machine-verified as a *necessary* condition against the source"
   did not hold when the question was posed.** The verification now exists
   (see above) but arrived *after* the question was written, so the premise
   was false at the time of asking.
2. **The per-branch CNF is 17 groups over 15 literals, and three of the rows are
   disjunctions, not single literals.** `sql_comment`/`sql_semicomment` share
   `"--"`, the four `xss_*` tag/event branches share `"<"`, and **`cmd_inj` is a
   three-way disjunction** (`;` OR `||` OR `&&`, `attack_detection.py:99`). So
   "absent the branch's necessary literal" is, for `cmd_inj`, absent all three.
   Any pruning plan that treats every row as single-literal has mis-stated the
   property for at least one branch.

## Routing: state of this ADR

Both header fields are now filled and the question is answered in writing, so
bead `FastAPI_saas-v6vs` may close. It was closed with `--force`: the closure
is refused while `FastAPI_saas-amxv` blocks it, but `amxv`'s own title frames
it as "the reason qk6z evidence is unobtainable", and that reason is
discharged (dispatch run `37637419697`, all jobs success).

**No branch of `COMBINED_ATTACK_RE` may be pruned yet.** This ADR records that
the basis is *accepted*; it does not record that the change has landed.

The two mitigations the answer made mandatory now exist:
`ATTACK_DETECTION_BRANCH_PRUNE` on `AttackDetectionSettings` and
`record_attack_subset_compiled()` in `r1_unified_metrics.py`, both additive,
both default-on, with the flag-off path falling back to the full
`COMBINED_ATTACK_RE`. **What does not exist is A-1 itself** -- there is no
`select_branches`, no memoisation, and no compiled alternation. The flag governs
the one pruning decision the code has today (the whole-scan skip) and the counter
observes that skip. `FastAPI_saas-muro` therefore remains blocked on the subset
compilation, which is the change this ADR approves and this mitigation set does
not perform.
