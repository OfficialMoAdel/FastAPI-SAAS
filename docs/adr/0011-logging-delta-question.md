# ADR-0011: A-3 logging-delta question

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-ijkm` — "A-3 owner: name security/observability owner as audit-trail custodian; deliver the logging-delta question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-3 changes what the attack scan logs. The audit-trail custodian must accept
the delta before any log line goes silent.

## The question (UNANSWERED)

> Logging is a security control. Adding the wrapper changes (i) **which events reach `SamplingProcessor`**, which draws from the **global** `random` stream per INFO/DEBUG event (`log_sampling.py:61-79`) — so suppressing DEBUG changes the sampling behaviour and the global RNG consumption, not just throughput; and (ii) the content of the audit log. ~10 `capture_logs` test modules (3 e2e) assert `debug` records that will silently disappear. Do you accept a reduced DEBUG record set in the audit trail, and do you accept the `SamplingProcessor` interaction?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-3**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.
