# ADR-0016: A-8 process-pool offload of the body scan — fail direction

**Status:** Decided — fail-to-current with a circuit breaker. **Implementation not landed.**
**Date:** 2026-10-07 (question delivered 2026-10-04)
**Tracked by bead:** `FastAPI_saas-7iby`
**Owner (security):** OfficialMoAdel <mohamed01023618534@gmail.com>
**Owner (platform):** OfficialMoAdel <mohamed01023618534@gmail.com> — see [Deviation 1](#deviation-1-two-hats-one-individual)

## Context

A-8 proposes offloading the attack-detection body scan to a process pool. The scan
is a pure function of bytes — `COMBINED_ATTACK_RE` is a module-level constant with
no clock, RNG or ambient state — so the transform itself is isomorphic and raises
no correctness question. What changes is the **error path of a security control**:
today a scan failure is an in-process exception, which the middleware already
handles. Under a pool it becomes `BrokenProcessPool`, a pickling failure, worker
death, or cancellation — failure modes that do not exist in the current code path
and that no test in the repository exercises.

This ADR is the Gate D record required by §14: A-8's fail direction is named here
explicitly, not left implicit in an implementation.

## The question (Q-2 — the one question the addendum did not close)

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9.4 via bead
`FastAPI_saas-yrpq`. Its wording is carried verbatim; only the
line wrapping of the source rendering is normalised here. Transcribing it in the
owner's own paraphrase is what this file exists to prevent.

> The scan is a pure function of bytes, so the transform is isomorphic - `re` is
> a module constant with no clock, RNG or ambient state. What changes is the
> **ERROR PATH of a security control**: `BrokenProcessPool`, pickling failure,
> worker death, and cancellation are new fail directions where today there is
> only an in-process exception. **Do you accept that, and WHAT IS THE FAIL
> DIRECTION WHEN THE POOL IS DEAD?**

## The answer (verbatim, unedited)

> Fail direction: fail-to-current with circuit breaker.
>
> If the pool is dead or any pool-side error occurs (`BrokenProcessPool`,
> pickling failure, worker death, cancellation), fall back to the current
> in-process scan on the event loop, AND emit an operational alert via the
> existing `core/monitoring/r1_unified_metrics.py` hook
> (`record_attack_scan_pool_failure()`). No request is ever left unscanned, and
> no request is ever rejected due to a pool failure.
>
> Circuit breaker: if pool failures exceed 5 in any 60-second window, disable the
> offload entirely (set `ATTACK_DETECTION_BRANCH_PRUNE_OFFLOAD=False`) and log at
> ERROR. The offload remains disabled until the next process restart or an
> explicit operator re-enable. This prevents flapping between pool and inline
> scan.
>
> Rationale: The current behavior (pre-A-8) is an in-process scan. Falling back
> to in-process scan on pool failure is therefore a fail-to-current, not a
> fail-open in the security sense. Detection coverage is identical to today. The
> only degradation is latency on the degraded path (the same latency that exists
> today without A-8).

## Decision

**Accepted: fail-to-current, with a circuit breaker.** A-8 may be implemented on
that basis.

- Pool-side error ⇒ scan in-process on the event loop **and** record an
  operational alert. Never reject a request because the pool died; never leave a
  request unscanned.
- >5 pool failures in any 60 s window ⇒ set
  `ATTACK_DETECTION_BRANCH_PRUNE_OFFLOAD=False` and log at ERROR. Stays off until
  process restart or explicit operator re-enable.
- The decision is visibly made on the **security question alone** (addendum A-12),
  because the spawn-cost objection is withdrawn — see below.

## Item 1 (A-11): the start method, confirmed from source — NOT assumed

The bead requires this be read from the installed CPython source or the 3.13
docs, and explicitly forbids assuming it. It was read from the installed
CPython **3.13.7** source.

**`ProcessPoolExecutor` inherits the platform default.** `concurrent/futures/process.py`
lines 668-672:

```python
if mp_context is None:
    if max_tasks_per_child is not None:
        mp_context = mp.get_context("spawn")
    else:
        mp_context = mp.get_context()
```

**And the platform default on Linux is `fork`.** `multiprocessing/context.py`
lines 323-328:

```python
if sys.platform == 'darwin':
    # bpo-33725: running arbitrary code after fork() is no longer reliable
    # on macOS since macOS 10.14 (Mojave). Use spawn by default instead.
    _default_context = DefaultContext(_concrete_contexts['spawn'])
else:
    _default_context = DefaultContext(_concrete_contexts['fork'])
```

So: **production Linux + A-8 as specified ⇒ `fork`.** (macOS is excluded by
bpo-33725; this host is Windows, where `_concrete_contexts` contains only
`spawn`, so the Linux default was read from the source branch, not by running it
here.)

### On-target confirmation (L-11, read on the Linux runner)

The read above was taken from the WINDOWS host's installed CPython
3.13.7. That is sound -- the `else` branch is the Linux default by
source -- but the addendum's L-11 method is to read the installed
source IN THE TARGET IMAGE, and [Inference] may not be promoted to
[Confirmed] without that citation. The perf-harness job now does that
read on `ubuntu-latest` (CI step "L-11 on-target read the Linux
image installed CPython start-method source", tests.yml): it locates
the runner's installed `multiprocessing` and `concurrent.futures`
packages, prints the platform-default branch and the `mp_context`
resolution, and asserts `_concrete_contexts['fork']` is present in
the Linux image's own `context.py`. The step passed, so the fork
default is confirmed in the Linux target's installed CPython, not
merely in the Windows host's copy of the same source.

This discharges the addendum's L-11 method on the target. The
conclusion is unchanged -- production Linux + A-8 as specified still
lands on `fork`, and `max_tasks_per_child` still raises `ValueError`
under it -- but the citation now names the file the Linux image
actually ships, which is what moves the item off [Inference].

### The hazard, and something stronger than a warning

The fork-with-inherited-sockets hazard is real — an inherited asyncpg or Redis
socket in a forked child is shared file-descriptor state. But CPython does not
merely document it. It **tracks** it and **hard-refuses** part of A-8's design:

```python
# https://github.com/python/cpython/issues/90622
self._safe_to_dynamically_spawn_children = (
        self._mp_context.get_start_method(allow_none=False) != "fork")
```
(lines 675-677)

```python
if self._mp_context.get_start_method(allow_none=False) == "fork":
    # https://github.com/python/cpython/issues/90622
    raise ValueError("max_tasks_per_child is incompatible with"
                     " the 'fork' multiprocessing start method;"
                     " supply a different mp_context.")
```
(lines 689-693)

### THE FINDING THAT CHANGES A-8's SHAPE

`max_tasks_per_child` — the bounded-pool-lifetime control a memoised, cached
offload design would reach for — **raises `ValueError` on Linux under `fork`**.
So A-8 on Linux faces a forced trade:

- keep `fork` (the platform default, cheap startup) and **give up
  `max_tasks_per_child`**; or
- force `spawn`/`forkserver` and pay the start-up cost **on Linux**, where the
  8-14 s figure was previously dismissed.

**This partially resurrects the spawn-cost objection.** It was withdrawn as a
*Windows* artefact (item 2). It is not withdrawn as a *Linux* artefact if A-8
adopts `max_tasks_per_child`. Recorded here so the withdrawal is not over-read.

## Item 2 (A-12): the spawn objection is withdrawn; the fail-direction objection is not

The spawn-cost objection (perf E5's 8.2 s, 14 420 ms; unified plan §12's 8-14 s)
is **WITHDRAWN as a Windows artefact** — VOID-WINDOWS. The measurement host is
Windows; production is Linux; those numbers say nothing about Linux start-up.

The fail-direction objection is **UNCHANGED** and remains the only live
objection. It is answered above. The decision is therefore made on the security
question alone, which is what A-12 requires be visible.

## Item 3: the GIL correction — the original rationale is GIL-bound, not platform-bound

A-8's original rationale rests on a misconception worth correcting on the record.
**`re` is pure Python**, so a *thread* pool does not release the GIL on Linux
either — offloading to threads buys nothing against the GIL. The 3.7x (thread)
and 2.3x (process) ratios are **GIL-bound, not platform-bound**, and both depend
on an **undocumented production core count** (unified plan §3.1, Q-3). Until that
core count is recorded — bead `FastAPI_saas-rce9` — neither ratio is interpretable.

## Item 4: the capacity-lever label

**No endpoint measurement of the offload exists anywhere** (perf E7). The
`GET /users/me` hot path is unaffected **by construction**: by D18 the body scan is
POST/PUT/PATCH only — `_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})` at
`attack_detection.py:233`, gated at `:456` by `if method not in _BODY_METHODS:`
— so GET never enters it.

*(An earlier revision of this file cited `attack_detection.py:429` for this, taken
from the bead text without checking it. 429 is inside the query-string scan. The
claim is right; the citation was not, and it is corrected here rather than left
to be re-copied.)*
`LD-L13` supplies the body-carrying-route measurement.

A-8 is therefore a **capacity** lever, explicitly labelled as such, and wins the
§12 conflict log on that basis — establishing that the attack-scan cliff **cannot**
explain the `GET /users/me` regression.

## Two artefacts the answer names that DO NOT YET EXIST

The answer commits to two names. Neither is in the tree today
(`rg` over `src/` returns nothing for either):

| Name | Expected home | State |
|---|---|---|
| `record_attack_scan_pool_failure()` | `src/app/core/monitoring/r1_unified_metrics.py` | **absent** |
| `ATTACK_DETECTION_BRANCH_PRUNE_OFFLOAD` | `AttackDetectionSettings`, `src/app/core/config/security.py` | **absent** |

The counter must be **registered** in the metrics counter dict as well as
declared: `inc_counter` silently no-ops on an unregistered name, so a declared
`record_*` that is not registered would count nothing forever while looking
correct in review.

**Their implementation is NOT this ADR's job and is not done here.** It belongs to
`FastAPI_saas-0a0u`, which is blocked on this ADR plus `LD-L11`, `LD-L12` and
`LD-L13`. This ADR records the decision those artefacts must implement; it does
not record that they exist.

## Deviation 1: two hats, one individual

A-8 requires a security owner and a platform owner. The repository has one
human identity (a single git author, no `CODEOWNERS`), so both are the same
individual and **reviewer independence is not achieved**. Recorded rather than
papered over, on the same reasoning as ADR-0009's Deviation 1.

## Routing

`FastAPI_saas-7iby` is discharged by this file. Bead `FastAPI_saas-0a0u`
(A-8 implementation) stays blocked on this ADR plus `LD-L11`/`LD-L12`/`LD-L13`.

**A-8 is NOT implemented.** No process pool exists in the attack-detection path.
What is decided here is the fail direction it must honour.

## Amendment (2026-10-20): the implementation landed

The status line above says "**Implementation not landed.**" That was true as of
2026-10-07 and it is left as written - an ADR records a moment - so this
amendment records what happened instead.

The implementation landed on 2026-10-20 (commit `dfaa9e14`, bead
`FastAPI_saas-0a0u`): the request-body pattern scan can be offloaded to a
worker process, OFF BY DEFAULT. The setting
`ATTACK_DETECTION_BRANCH_PRUNE_OFFLOAD` is a new field on
`AttackDetectionSettings` and it defaults to `False`, because the process
arm's p95 was never measured on the Linux target. The fail direction recorded
above is implemented exactly as stated: a pool-side failure falls back to the
in-process scan on the event loop and increments
`r1_attack_scan_pool_failure_total`, and more than five failures in any
sixty-second window disable the offload, log at ERROR, and keep it disabled
until a process restart or an explicit operator re-enable.

The decision, the fail direction, the thresholds and the budgets in this ADR
are unchanged by the implementation.

**Commit identity.** The implementation landed in commit `dfaa9e14`, which is
**not an ancestor of this branch's tip**: it was squashed into **`cc6e6d17`**
("perf(security): batch L1 fan-out, add C-2/C-5/C-6 oracles, offload body
scan"), whose tree is byte-identical to the pre-squash tip
(`git diff backup-before-squash-10c cc6e6d17` is empty). The original is
preserved on the local branch `backup-before-squash-10c`. Use `git show
cc6e6d17` to read the implementation. Nothing about the decision, the fail
direction, the thresholds or the budgets changes.
