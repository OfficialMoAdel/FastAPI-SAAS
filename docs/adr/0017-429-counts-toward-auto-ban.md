# ADR-0017: A-9 429-counts-toward-auto-ban question

**Status:** Decided — a 429 must NOT count toward auto-ban (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-w90w` — "A-9 owner: name abuse-policy owner; deliver the 429-counts-toward-auto-ban question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-9 decides whether 429 responses count toward auto-ban violations. That turns
a throttling signal into a banning signal; only the abuse-policy owner can
accept that escalation.

## The question

> A rate-limited client accrues a violation toward `AUTO_BAN_THRESHOLD` and can ban **itself** (`request_admission.py:310-313` → `auto_ban.py:723-729`). This is real: it banned the benchmark host at `127.0.0.1` and invalidated 3 of 6 census phases. Should a 429 count toward auto-ban at all? If not, what is the abuse signal that replaces it?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-9**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**A 429 must NOT count toward the auto-ban threshold.**

Rate-limiting and IP-banning are distinct tiers of defense: a client
already being throttled by GCRA is mitigated, so counting its 429
toward `AUTO_BAN_THRESHOLD` lets a throttled client ban itself
(`request_admission.py:310-313` → `auto_ban.py:723-729`) — which is
real: it banned the benchmark host at `127.0.0.1` and invalidated 3
of 6 census phases.

The replacement abuse signal is restricted to **true abuse
indicators**: attack-detection 403s and brute-force 401s — not mere
rate-limit exhaustion. Removing the 429 contribution therefore does
not remove abuse detection; it removes a false positive that was
masquerading as one. The owner names the replacement (attack-detection
403s, brute-force 401s) rather than accepting the loss.

