# ADR-0018: A-10 literal-narrowing + _MAX_SCAN_SIZE question

**Status:** Decided — ACCEPT _MAX_SCAN_SIZE = 128 KiB as-is; do not narrow b"or" in this PR (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-q8un` — "A-10 owner: name threat-model owner; deliver the literal-narrowing + _MAX_SCAN_SIZE question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-10 narrows the attack-scan literals and caps the scan size. Both change
what the scanner can see; the threat-model owner must accept the new blind
spots before they ship.

## The question

> ADR-0008 reported and did not fix this: narrowing `b"or"` is a security-relevant change to what reaches the regex. Do not settle it in a performance PR. Additionally, `_MAX_SCAN_SIZE = 128 KiB` leaves bodies above the cap unscanned while still allowing them through — is that policy accepted as-is?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-10**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**ACCEPT the `_MAX_SCAN_SIZE = 128 KiB` policy as-is for this
round**, with the explicit understanding that bodies above the cap
bypass the regex scan but are still admitted. This is a necessary
anti-DoS boundary on the event loop.

**Do NOT narrow `b"or"` in this PR** — leave it for a dedicated
threat-model review. Narrowing `b"or"` is a security-relevant change
to what reaches the regex (ADR-0008), and settling it inside a
performance PR is exactly what the plan forbids. The flat 15-literal
prefilter and `b"or"` are settled (§12) and not re-litigated here;
the only open item this ADR closes is the scan-size policy, which is
accepted as-is.

