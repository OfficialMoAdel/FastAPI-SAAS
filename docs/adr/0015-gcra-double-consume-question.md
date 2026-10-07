# ADR-0015: A-7 GCRA double-consume question

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-hnma` — "A-7 owner: name rate-limiting owner; deliver the GCRA double-consume question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-7 merges GCRA checks into one script. A fallback that re-consumes the
budget after the script already advanced it double-charges the caller; only
the rate-limiting owner can accept that risk.

## The question (UNANSWERED)

> On aggregate reject, `is_limited=True`, `remaining=0`, `retry_after=aggregate[2]` (`request_admission.py:235-237`); the aggregate leg runs **only** when the per-path leg did not reject (`:228`), and `_degraded` from the aggregate is discarded (`:269`). If a merged `EVALSHA` raises on a **timeout**, the server may still have executed it — falling back to the sequential path would **double-consume** the per-path budget for that request. Do you accept a merge whose error path can rate-limit a legitimate caller twice, and if not, what idempotency guard do you require?" Oracle: pin "aggregate bucket **not** consumed when per-path rejects

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-7**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.
