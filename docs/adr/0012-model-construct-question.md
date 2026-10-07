# ADR-0012: A-4 model_construct question

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-5r3q` — "A-4 owner: name API contract owner + security reviewer; deliver the model_construct question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-4 touches model construction on the auth path. The API contract owner and
the security reviewer must accept the change before it lands.

## The question (UNANSWERED)

> `model_construct` skips validation. A row that would have raised `ValidationError` now serialises to the client, and field validators and `@model_validator` never run. This is a **response-integrity** change on every list endpoint, not a speed change. Which endpoints return attacker-influenced or potentially corrupt rows, and do you accept unvalidated serialisation there?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-4**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.
