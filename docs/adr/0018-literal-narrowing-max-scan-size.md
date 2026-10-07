# ADR-0018: A-10 literal-narrowing + _MAX_SCAN_SIZE question

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-q8un` — "A-10 owner: name threat-model owner; deliver the literal-narrowing + _MAX_SCAN_SIZE question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-10 narrows the attack-scan literals and caps the scan size. Both change
what the scanner can see; the threat-model owner must accept the new blind
spots before they ship.

## The question (UNANSWERED)

> ADR-0008 reported and did not fix this: narrowing `b"or"` is a security-relevant change to what reaches the regex. Do not settle it in a performance PR. Additionally, `_MAX_SCAN_SIZE = 128 KiB` leaves bodies above the cap unscanned while still allowing them through — is that policy accepted as-is?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-10**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.
