# ADR-0011: A-3 logging-delta question

**Status:** Decided — ACCEPT the logging delta (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-ijkm` — "A-3 owner: name security/observability owner as audit-trail custodian; deliver the logging-delta question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-3 changes what the attack scan logs. The audit-trail custodian must accept
the delta before any log line goes silent.

## The question

> Logging is a security control. Adding the wrapper changes (i) **which events reach `SamplingProcessor`**, which draws from the **global** `random` stream per INFO/DEBUG event (`log_sampling.py:61-79`) — so suppressing DEBUG changes the sampling behaviour and the global RNG consumption, not just throughput; and (ii) the content of the audit log. ~10 `capture_logs` test modules (3 e2e) assert `debug` records that will silently disappear. Do you accept a reduced DEBUG record set in the audit trail, and do you accept the `SamplingProcessor` interaction?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-3**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**ACCEPT. Production audit trails must not rely on DEBUG events.**

The `SamplingProcessor` RNG shift is an acceptable implementation
detail: suppressing DEBUG changes the global RNG consumption, but
the emitted events at INFO level and above are unchanged (perf G1
proves the SHA-256-identical captured-event digest at the current
level), so the audit trail at production log levels is intact.

Action required (recorded, not optional): update the ~10
`capture_logs` test modules (3 e2e) to assert at INFO level or
higher, or explicitly configure DEBUG in the test fixtures, so the
test suite does not silently lose the records it currently asserts.

Gate D note: the fail-direction change (reduced DEBUG record set)
is named here explicitly, as §14 requires.

