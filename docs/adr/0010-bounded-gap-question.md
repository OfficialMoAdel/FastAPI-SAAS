# ADR-0010: A-2 bounded-gap question

**Status:** Decided — ACCEPT the 512-char gap ceiling (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-j0jn` — "A-2 owner: name threat-model owner; deliver the bounded-gap question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-2 proposes bounding the attack-scan gap. The bound is only safe if the
threat-model owner accepts what sits just past it.

## The question

> This change can only **lose** detections for gaps >512 chars and it changes match spans. Unlike the 128 KB body, **path, query string and headers are not size-capped** (`attack_detection.py:414-437`), so a 32 KB query string reaches these branches with no cap at all. Is a 512-char gap ceiling acceptable, or must the cap be raised? If the cap is raised, the 2.06 s worst case on 32 KB of `union`×N returns.

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-2**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**ACCEPT the 512-char gap ceiling. Do not raise the cap.**

The trade-off — bounded worst-case latency versus missing highly
obfuscated payloads with >512-char gaps — is acceptable and aligns
with standard WAF behavior. Raising the cap would return the 2.06 s
worst case on 32 KB of `union`×N, which is not worth the marginal
detection recovery on an input class the cap is designed to bound.

Fail direction / scope: the cap stays at 512 chars; path, query
string and headers remain the reachable bounded path (code C-14,
perf D14). No cap change, no `new < old` relaxation — the bound is
accepted as-is.

