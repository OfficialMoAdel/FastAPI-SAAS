# ADR-0005: Tenant-only RLS boundary; per-user RLS dropped

**Status:** Accepted (2026-08-29) — implements P2-1b of `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_0.md`
**Supersedes:** None
**Decider:** Platform team

## Context

The docstring at `src/app/core/db/database.py:176-178` admits plainly:

> `app.current_user_id` is currently never populated, so user-scoped RLS policies are inert. Do not rely on this dependency for per-user row filtering.

Three earlier plans called this out as either Critical (Round 2 RLS user_id inert) or a "drop or finish" decision (Round 0 P2-1). The migration at `src/migrations/versions/08_enable_rls_policies.py` defines a `tenant_isolation` policy (per-table, keyed on `tenant_id`) and a `bypass_rls` policy (admin escape hatch). Grep for `current_user_id` across the codebase confirms:

- `src/app/core/db/rls.py:27` defines the constant `RLS_USER_VAR = "app.current_user_id"`.
- No code anywhere sets this GUC.
- The migration has no policy that reads it.

So the situation is: the GUC is allocated, the policy slot is empty, and tenancy is enforced solely by `app.current_tenant_id` + the `tenant_isolation` policy. Any per-user filtering happens in the application layer (e.g. `User.tenant_id == current_tenant.id AND User.id == current_user.id`).

## Decision

Adopt the **drop** path from the Round 0 plan: the boundary is **tenant-only** at the database level. No per-user RLS policies will be created; no code will populate `app.current_user_id`.

The reservation of the GUC name and the constant `RLS_USER_VAR` are kept (no harm; future-proofing). A test asserts that no migration creates a policy referencing `current_user_id`, so re-introduction cannot happen accidentally.

Application-layer per-user scoping remains the source of truth: any service that returns user-scoped data filters `WHERE user_id = :user_id` explicitly. The RLS tenant boundary is a defense-in-depth backstop, not a substitute for explicit filters.

## Consequences

- **Operators**: simpler mental model — one tenant boundary, no per-user. Migrations stay small.
- **Developers**: cannot rely on RLS for per-user isolation. Tests that cross user boundaries must use the existing app-layer filters and must not assume RLS will block them.
- **Future work**: if per-user RLS is ever required (e.g., admin tools that touch a single user's data across tenants), this ADR is the place to record why it was dropped. Implementing it will require (a) populating `app.current_user_id` in the `restore_rls_context` path, (b) writing a `FORCE ROW LEVEL SECURITY` integration test that crosses user boundaries within a tenant, (c) updating the `tenant_isolation` policy to also check `user_id`. The cost is real and was previously deferred for that reason.
- **Compliance**: this is a *boundary-narrowing* decision, not a boundary-weakening one — the tenant boundary is the same; per-user scoping continues in the app layer with audit-trail coverage.

## Verification

- `grep -rn "current_user_id" src/migrations/` returns nothing — the policy slot is empty.
- The RLS migration's `upgrade()` only creates `tenant_isolation` and `bypass_rls` policies.
- The codebase's `RLS_USER_VAR` constant is reserved but never set, so no surprise GUC traffic on the DB.
