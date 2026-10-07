# ADR-0015: A-7 GCRA double-consume question

**Status:** Decided — REJECT the naive double-consume fallback (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-hnma` — "A-7 owner: name rate-limiting owner; deliver the GCRA double-consume question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-7 merges GCRA checks into one script. A fallback that re-consumes the
budget after the script already advanced it double-charges the caller; only
the rate-limiting owner can accept that risk.

## The question

> On aggregate reject, `is_limited=True`, `remaining=0`, `retry_after=aggregate[2]` (`request_admission.py:235-237`); the aggregate leg runs **only** when the per-path leg did not reject (`:228`), and `_degraded` from the aggregate is discarded (`:269`). If a merged `EVALSHA` raises on a **timeout**, the server may still have executed it — falling back to the sequential path would **double-consume** the per-path budget for that request. Do you accept a merge whose error path can rate-limit a legitimate caller twice, and if not, what idempotency guard do you require?" Oracle: pin "aggregate bucket **not** consumed when per-path rejects

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-7**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**REJECT the naive double-consume fallback.**

Rate-limiting a legitimate caller twice for a single request is not
acceptable. On a merged `EVALSHA` timeout, the request must either
**fail-closed (return 429)** or use a **strict idempotency guard**
— e.g. checking the current TAT before consuming — so the per-path
budget is consumed at most once per request. The merged-Lua GCRA
fallback as written is permanently rejected (§11.3); only the
version with an idempotency guard is in scope.

Oracle, restated: the aggregate bucket is **not** consumed when the
per-path leg rejects. The owner accepts a merge only with the
idempotency guard in place.

Gate D note: the fail direction (429 on timeout, or guarded
single-consume) is named here explicitly, as §14 requires for A-7.

