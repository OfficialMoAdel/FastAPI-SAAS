# ADR-0016: A-8 fail-direction question (platform half)

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-yrpq` — "A-8 owner: name security owner + platform owner; deliver the fail-direction question (platform half ...)"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-8 decides the fail direction for the security gate; this half is the
platform side of that decision. Both the security owner and the platform
owner must answer in writing.

## The question (UNANSWERED)

> The scan is a pure function of `bytes`, so the transform is isomorphic. What changes is the **error path** of a security control: `BrokenProcessPool`, pickling failure, worker death, and cancellation are new fail directions where today there is only an in-process exception. Do you accept that, and what is the fail direction when the pool is dead? Separately: on this platform a cold 8-worker pool costs **8–14 s of spawn** (Windows; ~0.1 s on Linux, where the cost/benefit flips), process throughput is **0.62×** thread throughput, and the body scan is **not on the `GET /users/me` path** at all (`attack_detection.py:429`, POST/PUT/PATCH only). Is the production platform Linux or Windows?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-8**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## The platform clause is answered; the fail-direction clause is not

The question has two halves. They are not both open, and keeping them in one
unanswered block hides the half that is already settled.

**ANSWERED — "Is the production platform Linux or Windows?" — Linux.** This is
`[Confirmed]`, from three independent facts and not from anyone's recollection:

| Fact | Evidence |
|---|---|
| uvloop is declared for every platform except Windows | `pyproject.toml` `uvloop>=0.19.0; sys_platform != 'win32'` |
| so the measurement host could never have run uvloop | no `.venv/Lib/site-packages/uvloop` |
| and the Dockerfile's CMD passes no `--loop`, so a Linux image resolves `auto` → uvloop | `Dockerfile` CMD is `["uvicorn", "src.app/main:app", "--host", "0.0.0.0", "--port", "8000"]` |

CONSEQUENCE, and it is a deletion rather than a decision: the spawn-cost
objection is WITHDRAWN as a Windows artefact. A cold 8-worker pool costs 8–14 s
on Windows and ~0.1 s on Linux, where the cost/benefit flips, so the "8–14 s"
figures are `[Unverified - Needs-Linux-measure]` and stay that way.

**STILL UNANSWERED — the fail direction.** "Do you accept that, and what is the
fail direction when the pool is dead?" Nothing in the repository answers it, and
it is the ONLY live objection left on A-8: `BrokenProcessPool`, a pickling
failure, a dead worker and a cancellation are all fail directions a security
control does not currently have, because today there is only an in-process
exception. Answering it needs a **security owner**, and naming one is Q-11.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owners respond, would be a Gate D violation. The platform
clause above is the one exception, and it needed no owner: it was a fact about
the build rather than a judgement about risk, so anyone can check it and it is
recorded here as checked, not decided.

## Amendment (2026-10-20): the Owner line was true when written, and the programme has an owner now

The header line above reads:

> **Owner:** _Unassigned - no Owner field exists anywhere in the programme; its absence is Q-11._

That was TRUE when this ADR was written on 2026-10-06, and it is left as
written. An ADR is a record of a moment: replacing the line with the present's
owner would rewrite history to match the present. What follows is the
amendment.

THE ABSENCE WAS DISCHARGED on 2026-10-08

`FastAPI_saas-3yp4` (Q-11) is the bead that closed that absence, and the
twelve tracked ADRs 0009-0020 now carry an Owner line. Re-derived from those
twelve files themselves, not from any summary field:

* `0009-branch-pruning-proof-basis.md` - "OfficialMoAdel <mohamed01023618534@gmail.com> - threat-model owner, A-1"
* `0010-bounded-gap-question.md`, `0011-logging-delta-question.md`,
  `0012-model-construct-question.md`, `0013-api-key-mget-fail-direction.md`,
  `0014-authenticated-user-accept-widening.md`,
  `0015-gcra-double-consume-question.md`, `0017-429-counts-toward-auto-ban.md`,
  `0018-literal-narrowing-max-scan-size.md`,
  `0019-permission-catalog-reconcile-deferral.md` and
  `0020-401-path-error-to-debug.md` - "OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)"

`0021-amxv-linux-harness-deferred.md`, `0022-step4-p99-bar-vs-production-rate-limits.md`
and `0023-admission-owns-api-key-identity.md` carry the same name without the
date.

THE CURRENT OWNER

**OfficialMoAdel <mohamed01023618534@gmail.com>** - recorded on those ADRs
between 2026-10-08 and 2026-10-09, and for ADR-0009 in the role of
threat-model owner for A-1. The security and platform halves of A-8 are both
that individual; the deviation is recorded in
`0016-a8-process-pool-offload-fail-direction.md`.

WHERE THE PLATFORM HALF'S QUESTION WAS ANSWERED

The question below was answered on 2026-10-07, in the sibling ADR
`0016-a8-process-pool-offload-fail-direction.md`: fail-to-current with a
circuit breaker. This ADR's own header and status are left as written for the
same reason its Owner line is - and the implementation landed on 2026-10-20, as
that sibling ADR's own amendment records.

WHAT THIS AMENDMENT DOES NOT CHANGE

No decision, threshold, ceiling or budget in this ADR is changed. The Owner
line above is left exactly as it was written.
