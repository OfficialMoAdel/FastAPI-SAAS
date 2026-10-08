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

## capture_logs pre-flight table

The action item above names the test modules that assert `debug`
records and will silently lose them once the wrapper filters at
INFO. Pre-flighted here so the A-3 implementation (bead
`FastAPI_saas-49yx`) cannot land without re-asserting them. 13
modules use `capture_logs`; the ones that assert `debug` records
are marked and must be updated to assert at INFO level or higher,
or explicitly configure DEBUG in the fixture.

| Module | Level asserted | Action |
|---|---|---|
| `tests/unit/core/test_logging_wrapper_oracle.py` | **debug (26 refs)** | Re-assert at INFO+, or configure DEBUG in fixture |
| `tests/unit/middleware/test_attack_scan_method_gate.py` | **debug (13 refs)** | Re-assert at INFO+, or configure DEBUG in fixture |
| `tests/e2e/core/security/test_attack_scan_bounds_e2e.py` | **debug (3 refs)** | Re-assert at INFO+, or configure DEBUG in fixture |
| `tests/unit/core/security/test_pii_and_fail_open_knobs.py` | **debug (2 refs)** | Re-assert at INFO+, or configure DEBUG in fixture |
| `tests/e2e/modules/billing/test_checkout_tenant_authority_e2e.py` | debug (2) + info (2) | Re-assert the debug records at INFO+ |
| `tests/unit/modules/billing/test_checkout_tenant_resolution.py` | debug (2) + info (2) | Re-assert the debug records at INFO+ |
| `tests/unit/core/test_log_sanitizer.py` | debug (1) + info (9) | Re-assert the debug record at INFO+ |
| `tests/unit/core/test_structlog_kwarg_enforcement.py` | debug (1) + info (2) | Re-assert the debug record at INFO+ |
| `tests/unit/core/utils/test_ip_utils_x_real_ip.py` | debug (1) + info (1) | Re-assert the debug record at INFO+ |
| `tests/unit/core/security/test_s01_session_invalidation_oracle.py` | info (12) only | No change — asserts at INFO already |
| `tests/e2e/api/test_monitoring_error_redaction.py` | none | No change — no level assertion |
| `tests/performance/test_rollback_verification.py` | none | No change — no level assertion |
| `tests/unit/modules/storage/test_storage_interface_conformance.py` | info (2) only | No change — asserts at INFO already |

Nine modules assert `debug` records and need the re-assertion; four
are already INFO-only or level-agnostic and need no change.

Gate D note: the fail-direction change (reduced DEBUG record set)
is named here explicitly, as §14 requires.

