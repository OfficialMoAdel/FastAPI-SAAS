# ADR-0010: A-2 bounded-gap question

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-j0jn` — "A-2 owner: name threat-model owner; deliver the bounded-gap question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-2 proposes bounding the attack-scan gap. The bound is only safe if the
threat-model owner accepts what sits just past it.

## The question (UNANSWERED)

> This change can only **lose** detections for gaps >512 chars and it changes match spans. Unlike the 128 KB body, **path, query string and headers are not size-capped** (`attack_detection.py:414-437`), so a 32 KB query string reaches these branches with no cap at all. Is a 512-char gap ceiling acceptable, or must the cap be raised? If the cap is raised, the 2.06 s worst case on 32 KB of `union`×N returns.

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-2**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.
