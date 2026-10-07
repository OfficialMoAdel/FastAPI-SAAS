# ADR-0006: One tenant-scope resolution seam — billing module shadows retired

**Status:** Accepted
**Date:** 2026-09-17
**Context:** Architecture review candidate A1 ("Retire the three residual tenant-scope paths"). Prior rounds (see `docs/adr/0001`) already consolidated billing `_resolve_tenant` onto `core.tenancy.context.resolve_tenant_id`, but three residual paths kept re-deriving the precedence rule:

1. `StripeSettingsService/ProductService/PricingService.current_tenant_id` — a hand-rolled "explicit wins" property that shadowed the seam (a mismatching constructor value silently bypassed the enumeration-safe soft-404).
2. `SubscriptionPlanService/UserSubscriptionService._require_tenant` + `BillingRecordService.create_record/get_record` — ad-hoc `get_current_tenant() is None → ModelNotFoundError` checks duplicating the seam's absent-scope failure.
3. `audit_logs/trail.py::_resolve_tenant_id` — a private twin that silently returned the `SYSTEM_TENANT_ID` sentinel; the *reason* for the fallback lived only at one call site, so every other sentinel-attributed row was unattributable in logs.

## Decision

**Every tenant-id resolution in application code goes through
`core.tenancy.context.resolve_tenant_id`. The hand-rolled precedence and
`_require_tenant` variants are deleted.**

- The seam gains one additive parameter, `fail_closed: bool = True`:
  - `True` (default): absent scope raises `ModelNotFoundError` (unchanged; all enforcement callers).
  - `False`: absent scope returns `None` (for read-only resolution views — service construction values, list filters — that must tolerate a tenant-less worker/system path). A *mismatched* explicit value still raises in both modes: that is an inconsistent scope, not a missing one.
- `current_tenant_id` on the five billing services is now a thin view:
  `resolve_tenant_id(self.tenant_id, ..., fail_closed=False)`. It no longer
  shadows the context.
- `_require_tenant` is deleted from `ProductService`, `PricingService`,
  `SubscriptionPlanService`, `UserSubscriptionService`; reads call
  `_resolve_tenant` (the guarded name) — the unguarded twin is gone, so a
  future caller cannot accidentally pick the weaker variant.
- `audit_logs/trail.py` exports `system_tenant_id(*, reason)` as the ONE named,
  logged constructor for the SYSTEM sentinel. Every sentinel-attributed row
  logs `audit_tenant_fallback_to_system action=<reason>`; reason labels are
  greppable (`audit.resolve_tenant`, `audit.unparseable_tenant_id`,
  `audit.flush_missing_tenant_id`, `audit.event_missing_tenant_id`,
  `audit.login_issued_missing_tenant_id`, `audit.login_issued_fallback`,
  `audit.missing_tenant_id`).

## Rationale

The deletion test: deleting the duplicated paths concentrates no complexity
newly — it removes three copies of a rule that drifted. The "explicit wins"
property was the dangerous one: a service constructed with a stale
`tenant_id` would read another tenant's rows while a tenant context was
bound. Mismatch now raises `ModelNotFoundError` (soft-404) at every read,
write, and property access.

## Consequences

- Tests that patched the billing-module alias
  (`src.app.modules.billing.service.get_current_tenant` /
  `subscription_service.get_current_tenant`) must patch the canonical
  `src.app.core.tenancy.context.get_current_tenant` — the accessor the seam
  reads. The aliases remain as re-exports only for legacy patch targets and
  the `get_settings` is-active gate; do not extend their use.
- New services must not add a `current_tenant_id` property body beyond the
  one-line seam view, and must not name a second resolver.
- `audit` callers that need the sentinel must call `system_tenant_id(reason=...)`
  rather than referencing `SYSTEM_TENANT_ID` silently; the constant remains
  exported for assertions.
- ADR-0005 is untouched: the tenant-only RLS boundary and app-layer filters
  are unchanged; this ADR only removes duplication *above* that boundary.

## Verification

- `uv run pytest tests/unit/modules/billing tests/unit/modules/audit_logs -q`
  → 364 passed.
- `uv run pytest tests/unit/core/tenancy tests/contract/stripe
  tests/unit/test_p2_medium_priority_regressions.py tests/unit/api/deps -q`
  → 538 passed.
- New tests pin the contract:
  `tests/unit/modules/billing/test_billing_tenant_resolution.py`
  (property view semantics, mismatch soft-404, missing-scope fail-closed) and
  `tests/unit/modules/audit_logs/test_audit_trail_interface.py`
  (`system_tenant_id` logs its reason).
