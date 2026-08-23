# ADR-0002: Optimization Flags Live in the Settings Pipeline (`core/feature_flags.py` Deleted)

- **Status:** Accepted
- **Date:** 2026
- **Context:** Architecture review Round 1, candidate #3 ("nine import-time
  getenv booleans, untestable without reload hacks").

## Decision

**Delete `src/app/core/feature_flags.py`. All optimization/rollout flags are
owned by the existing Pydantic Settings pipeline** — specifically the nested
sections already present in `core/config.py`:

- `settings.r1_unified` (`R1UnifiedSettings`, env prefix `R1_`) — event-bus Lua
  idempotency/batch enqueue, session pipeline eviction, XFetch, RBAC cache,
  DLQ bounds, passkey selects, circuit breaker, token negative-cache TTL.
- `settings.performance` (`PerformanceSettings`, env prefix `PERFORMANCE_`) —
  contextvar tenant context, streaming uploads, eager loading, etc.

## Rationale

Verification found that `core/feature_flags.py` was **dead code**: zero
production imports remained — every live toggle had already been migrated to
the Settings pipeline during earlier optimization rounds (R1/R2). The module
was a second, stale source of truth for the same flags:

- evaluated once at import time (per-test variation required reload hacks);
- names duplicated a third time in `get_all_flags()`;
- drifted from the real flags used in production code paths.

This is the same "one rule, two implementations" failure mode removed in
[ADR-0001](0001-single-dependency-mechanism-depends.md).

## Consequences

- To toggle an optimization per environment: set the env var
  (e.g. `R1_EVENT_BUS_USE_LUA_IDEMPOTENCY=false`,
  `PERFORMANCE_FEATURE_STREAMING_UPLOADS=false`).
- To toggle per test: construct/override the nested settings object — no
  module reloading.
- Do **not** create new module-level `os.getenv` flag globals; add a field to
  the appropriate settings section instead.
- The unrelated runtime *product* feature `src/app/modules/feature_flags/`
  (tenant-facing feature-flag management) is unaffected.
