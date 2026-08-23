# ADR-0001: Single Dependency Mechanism — FastAPI `Depends` (Service Registry Deleted)

- **Status:** Accepted
- **Date:** 2026
- **Context:** Architecture review Round 1, candidate #2 ("Service Registry: a
  hypothetical seam with string-typed holes").

## Decision

**Delete the string-keyed service registry; standardize on direct construction +
FastAPI `Depends` constructor injection as the single cross-module dependency
graph.**

## Rationale

The registry held only 5 services (`tier`, `rate_limit`, `user`, `api_key`,
`storage_settings`) while hundreds of direct module imports remained — one rule,
two implementations. Its typed helpers were `cast()`-based, and its Protocols
covered only 3 of the 5 services. The deletion test passed: removing it moved
almost no complexity.

FastAPI's dependency system already provides per-request caching, lazy
resolution, override hooks for tests (`app.dependency_overrides`), and a visible
dependency graph in each signature. Duplicating that with a registry added drift
risk without leverage.

## Consequences

- Cross-module service access is now plain construction: e.g.
  `UserService(db)` / `TierService(self.db)` inside services, and
  `Depends(_get_user_service)` factories inside routers.
- `src/app/core/service_registry.py` is **deleted**. Do not reintroduce it;
  new cross-module needs should use explicit constructor injection or, for
  HTTP-entry points, the shared `RequestContext` dependency (see
  [request-context.md](../api/request-context.md)).
- Tests patch the service class at its usage site
  (e.g. `patch("src.app.modules.sso.service.UserService")`) instead of patching
  `get_service`. Note: deferred function-level imports bypass module-attribute
  patches — import at module top level when tests must patch.

## Migration applied

| Old | New |
|---|---|
| `auth/service.py`, `auth/services/*`: `get_service("user", db)` | `UserService(db)` |
| `sso/service.py`: `get_service("user", db)` | `UserService(db)` |
| `users/service.py`: `get_service("tier", self.db)` | `TierService(self.db)` |
| `rate_limits/service.py`: `get_service("tier", self.db)` | `TierService(self.db)` |
| `files/service.py`: `get_service("storage_settings", db)` | `StorageSettingsService(db)` |
| `_register_api_key_service` / `_register_tier_service` | removed (unused) |
