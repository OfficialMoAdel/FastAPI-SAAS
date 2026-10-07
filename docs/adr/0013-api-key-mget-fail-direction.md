# ADR-0013: A-5 API-key MGET fail-direction question

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-w9w3` — "A-5 owner: name auth owner; deliver the API-key MGET fail-direction question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-5 batches API-key cache reads into one MGET. The fail direction (open vs
closed) when the store is down is a policy call only the auth owner can make.

## The question (UNANSWERED)

> Today a Redis outage **raises** out of `RedisCacheAdapter.get` (`core/cache/client.py:89-95`, no try/except); fall-through-to-DB happens on a cache **miss**, not an outage. A swallowing `mget` changes the answer to 'Redis is down, do we authenticate?' from *propagate* to *fail-soft-to-DB*. Pick the semantics deliberately and pin them in a test.

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-5**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.
