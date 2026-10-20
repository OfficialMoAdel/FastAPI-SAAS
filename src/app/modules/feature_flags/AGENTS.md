# Feature Flags Module - Agent Guidance

## Package Identity

Feature flag management system for controlled feature rollout per tenant. Uses PostgreSQL JSONB for storage with optional user-level overrides. Supports boolean, string, number, and JSON flag values.

## Setup & Run

```bash
# Test feature flags module
uv run pytest tests/unit/modules/feature_flags/ -v

# Type check module
uv run mypy src/app/modules/feature_flags/

# Lint module
uv run ruff check src/app/modules/feature_flags/
```

## Patterns & Conventions

### Module Structure
```
src/app/modules/feature_flags/
├── __init__.py           # Exports: FeatureFlagService, get_feature_flag_service, models, schemas, CRUD
├── router.py             # FastAPI routes (prefix /features) + get_feature_flag_service factory
├── service.py            # FeatureFlagService business logic (the single flag API)
├── schemas.py            # Pydantic schemas
├── models.py             # SQLAlchemy models (FeatureFlag, FeatureFlagOverride)
└── crud.py               # FastCRUD operations
```

There is **no** `utils.py` — it was deleted along with the legacy JSONB
shims (`is_feature_enabled` / `update_feature_flag` /
`bulk_update_feature_flags`), which had no production callers and whose
Redis cache key collided with the service's. `FeatureFlagService` is the
single flag module; `get_tenant_flags` is the tenant-JSONB read (with
cache).

### B7 Precedence Contract (highest wins)

Flag resolution order — implemented in `service.py::is_enabled` and
`get_flag_value`:

1. **Tenant override** — `FeatureFlagOverride.custom_value` (per-tenant row)
2. **Registry default** — `FeatureFlag.default_value` (global flag row)
3. **Legacy tenant JSONB** — `Tenant.feature_flags` dict
4. **Caller default** — the `default=` argument

`is_enabled()` is boolean-only (no coercion of string/number flags — use
`get_flag_value()` or `get_tenant_flags()` for typed values). Result state
is cached per tenant+flag in Redis; mutations go through
`_invalidate_cache`.

⚠️ **A value that fell through to the caller's `default` is never
cached.** Both accessors cache only values sourced from the registry, an
override, or the tenant JSONB, because the cache key
(`feature:{name}` / `feature_value:{name}`) does not include `default` —
the first caller's fallback would otherwise be served to everyone else for
`CACHE_TTL`. The JSONB tier therefore requires the flag to be *in* the
map (`feature_name in tenant.feature_flags`), not merely that the map is
non-empty; `is_enabled` mirrors `_resolve_flag_value` exactly. This also
bounds the key space of `GET /features/{flag_name}`, whose path param is
unconstrained. See
`tests/unit/modules/feature_flags/test_flag_default_not_cached.py`.

### Tenant-JSONB writes are locked (concurrency contract)

`update_tenant_flag` / `delete_tenant_flag` / `bulk_update_tenant_flags`
read-modify-write the whole `Tenant.feature_flags` column, so they must not
be naive:

- **Row lock** — the Tenant read is `db.get(Tenant, tid, with_for_update=True)`
  (`_get_tenant_for_update`). A plain `db.get` is answered from the
  identity map whenever the row is already loaded (the usual case in a
  request), so it emits no SQL and takes NO lock; `with_for_update` forces
  a real `SELECT ... FOR UPDATE` held until the mutator's own commit.
  Without it, two concurrent writes for different flags on one tenant both
  load the same dict and the second commit discards the first flag, and
  `delete_tenant_flag` loses its check-then-act too.
- **Same singleflight lock as the readers** — commit + invalidation run
  under `_flag_cache_locks[features]` (`_commit_and_invalidate`), the same
  lock `get_tenant_flags`' `_load()` holds across its `db.get` +
  `cache_client.set`. Without it a reader that loaded the PRE-commit JSONB
  writes it back after the writer's eviction — stale and un-evictable for
  `CACHE_TTL`. No second lock table: reuse `_with_flag_lock` /
  `_flag_cache_locks`. Lock order is `features` only, keeping the graph
  acyclic with `get_flag_value`'s `feature_value:{name}` → `features`
  nesting (never the reverse).
- **One invalidation shape** — every write evicts all three key shapes in
  a single varargs delete: `features`, `feature:{name}`, and
  `feature_value:{name}` (F27). Single and bulk paths both go through
  `_invalidate_decision_keys`, so they cannot drift apart again; the bulk
  path previously omitted `feature_value:` and left `get_flag_value`
  serving the pre-bulk value for `CACHE_TTL`.

See `tests/unit/modules/feature_flags/test_tenant_flag_writes.py`.

### Feature Flag Storage
⚠️ The JSONB column is the **fallback tier** of the B7 contract, not the
primary store. Registry rows (`FeatureFlag` / `FeatureFlagOverride`) are
authoritative; this dict is consulted only when no registry row matches.
```python
# src/app/modules/tenants/models.py:43
feature_flags: Mapped[dict] = mapped_column(
    JSONB, nullable=False, default_factory=dict, server_default="{}"
)
```

### Check if Feature is Enabled
✅ DO: Use FeatureFlagService for feature checks
```python
from src.app.modules.feature_flags import FeatureFlagService

service = FeatureFlagService(db)
enabled = await service.is_enabled(tenant_id, "advanced_reporting")
# Typed value (string/number/json flags):
value = await service.get_flag_value(tenant_id, "limits", default=None)
```

❌ DON'T: Query FeatureFlag/Tenant models directly for tenant-level checks —
route through the service so the B7 precedence and cache invalidation apply.

### Get All Feature Flags
✅ DO: Read the merged per-tenant flag map via the service
```python
# src/app/modules/feature_flags/router.py:31 (list endpoint)
@router.get(
    "",
    response_model=FeatureFlagsResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_READ))],
)
async def list_feature_flags(
    ctx: RequestContextDep,
    service: FeatureFlagService = Depends(get_feature_flag_service),
) -> FeatureFlagsResponse:
    flags = await service.get_tenant_flags(ctx.tenant_id)
    return FeatureFlagsResponse(feature_flags=flags)
```
⚠️ The response field is `feature_flags`, **not** `flags` — the Pydantic
model is `FeatureFlagsResponse.feature_flags: dict[str, Any]`. Passing
`flags=` silently drops the payload into Pydantic's ignored-extra bucket.

### Update / Delete / Bulk-Update Flags
✅ DO: Mutate through the service (cache invalidation included)
```python
await service.update_tenant_flag(tenant_id, flag_name, value)
await service.delete_tenant_flag(tenant_id, flag_name)
await service.bulk_update_tenant_flags(tenant_id, {"dark_mode": True, "theme": "dark"})
```

### User-Level Overrides
✅ DO: Use FeatureFlagOverride rows for per-tenant custom values
```python
override = await service.get_override(flag_id, tenant_id)
await service.create_override(FeatureFlagOverrideCreate(...))
```
Overrides resolve at precedence tier 1 (see B7 above); `custom_value` may be
a plain value or `{"value": ...}` / `{"enabled": ...}`.

## Touch Points / Key Files

### Core Service
- `src/app/modules/feature_flags/service.py` — `FeatureFlagService`, the single
  flag API: `is_enabled`, `get_flag_value`, `get_tenant_flags`,
  `update_tenant_flag` / `delete_tenant_flag` / `bulk_update_tenant_flags`,
  the override CRUD, and the module-level `warm_feature_flags(session)` warmer
  (`service.py:746` for the `@warmer.register` decorator, `service.py:747`
  for the `async def`, registered as the `"feature_flags"` warmer).

### Router
- `src/app/modules/feature_flags/router.py` — mounted at
  `/api/v1/features` (router prefix `/features` under the `/api` + `/v1`
  group routers). It is **not** `/api/v1/feature-flags`. The
  `get_feature_flag_service` DI factory also lives here.

### Schemas
- `FeatureFlagsResponse` — `{"feature_flags": {...}}` (field name is
  `feature_flags`, not `flags`)
- `FeatureFlagCheckResponse` — `{"flag_name": str, "enabled": bool, "value": Any | None}`
- `FeatureFlagUpdateRequest` — `{"enabled": bool | None, "value": Any | None}`
- `FeatureFlagBulkUpdateRequest` — `{"feature_flags": {...}}`

### Models
- FeatureFlag: Global feature flag definitions
- FeatureFlagOverride: User-level overrides

## JIT Index Hints

```bash
# Find feature flag checks
rg -n "is_enabled|get_flag_value" src/app/modules/feature_flags/

# Find feature flag routes
rg -n 'prefix="/features"' src/app/modules/feature_flags/

# Find feature flag updates
rg -n "update_tenant_flag|bulk_update_tenant_flags" src/app/modules/feature_flags/

# Find flag usage in code
rg -n "feature_flags\." src/app/modules/

# Find schema definitions (named FeatureFlag*Base/Create/Update/Read, not *Schema)
rg -n "^class FeatureFlag" src/app/modules/feature_flags/schemas.py
```

## Common Gotchas

### Flag Value Types
✅ DO: Flags can be any JSON-serializable type (bool, str, int, list, dict)
```python
# Boolean flag
await service.update_tenant_flag(tenant_id, "dark_mode", True)

# String flag
await service.update_tenant_flag(tenant_id, "theme", "premium")

# Object flag
await service.update_tenant_flag(tenant_id, "limits", {"max_users": 100})
```

### Missing Flags
⚠️ The service **raises**, it does not return a sentinel. `is_enabled`
resolves to the caller's `default` (default `False`) when no flag matches —
but `delete_tenant_flag` on a missing flag raises `ModelNotFoundError`,
which the central `AppException` handler in `src/app/core/errors.py` maps
to **404** via the exception's own `http_status`
(`src/app/shared/exceptions.py:143`).
```python
from src.app.shared.exceptions import ModelNotFoundError
```

### Admin Protection
✅ DONE — RBAC is already enforced per-route; do not re-add it. The
dependency comes from `src.app.core.rbac.dependencies`
(`requires_permission`), **not** `src.app.modules.rbac.dependencies`, and
permissions are `Permission` enum members rather than bare strings:
```python
# src/app/modules/feature_flags/router.py — READ on GET, MANAGE on writes
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission

@router.get(
    "",
    response_model=FeatureFlagsResponse,
    dependencies=[Depends(requires_permission(Permission.FEATURE_FLAGS_READ))],
)
```
❌ DON'T: hand-roll an admin check on a new endpoint — copy the
`requires_permission(Permission.FEATURE_FLAGS_*)` dependency from a sibling
route so the two stay in sync.

### Performance
✅ DO: Cache frequently checked flags in Redis (is_enabled/get_tenant_flags already do)
❌ DON'T: Query database on every `is_enabled()` call in hot paths

## Pre-PR Checks

```bash
# Test feature flag functionality
uv run pytest tests/unit/modules/feature_flags/ -v

# Type check
uv run mypy src/app/modules/feature_flags/

# Lint
uv run ruff check src/app/modules/feature_flags/

# Integration coverage (router + permissions)
uv run pytest tests/integration/modules/feature_flags/ -v
```

❌ There is no root-level `test_feature_flags` or `test_tenant_context`
module under `tests/` — those were the two stale commands above. The root
`tests/` tree holds shared harnesses (`conftest.py`, `pytest_plugin.py`,
`factories/`, `fixtures/`); suites live under `tests/unit/…`,
`tests/integration/…` and `tests/contract/…`. Tenant context lives at
`tests/unit/core/tenancy/test_tenant_context.py`.

## Doc Pointer Integrity

Every rooted `src/…`, `tests/…`, `scripts/…`, `docs/…` and `.github/…`
path in this file is a claim that the path exists, and is enforced:

```bash
uv run python scripts/checks/doc_paths.py
```

If you add a pointer here, the checker runs in CI. If it fails, either fix
the pointer or add a reasoned `ALLOWLIST` entry in the script — never leave
a dead pointer for the next reader to trip over.
