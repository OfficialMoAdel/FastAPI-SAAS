# ADR-0003: Primary Keys — UUIDv7 for New Tables, Freeze Existing `int` PKs

- **Status:** Accepted
- **Date:** 2026-08-28
- **Context:** Architecture review R3-03. Verified PK split: `users.id` and most tables use `Mapped[int]` autoincrement; `tenants.id`, `audit_logs.id` use `Mapped[UUID]` uuid7. Joins mix types (`users.id int` ↔ `tenants.id UUID` via `tenant_id`). Untyped `Any` density and `type: ignore` hide the resulting coercions.

## Decision

**New tables use `UUID` (uuid7) as primary key. Existing `int` PKs are frozen.**

- New models: `id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, insert_default=uuid7, init=False)` (see `_base/models.py` exemplar).
- Existing tables (`users`, `billing/*`, `posts`, `api_keys`, `auth/*`, `rbac/*`, `tiers`, etc.): keep `int` autoincrement. Do not migrate them to UUID — migration risk outweighs benefit.
- Cross-type joins (`users.id int` ↔ `tenant_id UUID`) go through the typed helper `src/app/core/db/pk.py` (`cast_tenant_id`, `cast_user_id`) instead of ad-hoc `UUID(str(x))` / bare `int`.

## Rationale

- uuid7 is time-ordered → index-friendly, globally unique without a central sequence, safe for multi-region and offline ID generation.
- Freezing `int` PKs avoids a high-risk, high-churn migration of the hottest tables (`users`, `billing`, `posts`) for no functional gain.
- A single cast helper makes int↔UUID coercions visible and grep-able instead of scattered `type: ignore` on join filters.

## Consequences

- New table PRs must use the UUID template; reviewers reject new `Mapped[int]` PKs.
- Existing `int` PKs stay `int`. No backfill migration will be issued for them.
- New join/filter code that mixes `int` and `UUID` must call `pk.cast_*` instead of inline casts.
- `docs/user-guide/database/models.md` documents the mixed-PK state and points to `pk.py`.
