# ADR-0014: A-6 AuthenticatedUser accept-widening question

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-ydan` — "A-6 owner: name auth owner; deliver the AuthenticatedUser accept-widening question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-6 widens what `AuthenticatedUser` accepts. The auth owner must accept the
widened contract before untrusted shapes can flow through it.

## The question (UNANSWERED)

> `UserAuthInfo` requires `hashed_password`; `AuthenticatedUser` uses `extra='ignore'` and does not. Skipping `model_validate` therefore **widens the accept path**: any cache-write path that omits the field — or a poisoned Redis entry — becomes authenticatable. Is that widening acceptable? Also: `tenant_id` changes type (`UUID`→`str`) and `tier` contents (`datetime`→ISO string); the risk is a downstream `==` against a `UUID`.

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-6**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.
