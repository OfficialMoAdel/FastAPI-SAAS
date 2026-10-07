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
