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
├── router.py             # FastAPI routes
├── service.py            # FeatureFlagService business logic (the single flag API)
├── schemas.py            # Pydantic schemas
├── models.py             # SQLAlchemy models (FeatureFlag, FeatureFlagOverride)
└── crud.py               # FastCRUD operations
```

B7: the legacy JSONB shims (`is_feature_enabled` / `update_feature_flag` /
`bulk_update_feature_flags`) were REMOVED, and the module's `utils.py` was
deleted with them — it had no production callers and its Redis cache key
collided with the service's. `FeatureFlagService` is the single flag
module; `get_tenant_flags` is the tenant-JSONB read (with cache).

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

### Feature Flag Storage
✅ DO: Store flags as JSONB on tenant model for performance
```python
# src/app/modules/tenants/models.py (lines 30-35)
feature_flags: dict[str, Any] = Column(JSONB, nullable=True, default=dict)
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
# src/app/modules/feature_flags/router.py (list endpoint)
@router.get("", response_model=FeatureFlagsResponse)
async def list_feature_flags(ctx: RequestContextDep) -> FeatureFlagsResponse:
    flags = await ctx.service.get_tenant_flags(ctx.tenant_id)
    return FeatureFlagsResponse(flags=flags)
```

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
- Service: `src/app/modules/feature_flags/service.py` - `FeatureFlagService` (single flag API: is_enabled, get_flag_value, get_tenant_flags, update/delete/bulk_update_tenant_flag(s), overrides, warm_feature_flags)
  (`utils.py` was deleted — no production callers, and its cache key collided with the service's)

### Router
- Router: `src/app/modules/feature_flags/router.py` - API endpoints at `/api/v1/feature-flags`

### Schemas
- Response: `FeatureFlagsResponse` - `{"flags": {...}}`
- Check: `FeatureFlagCheckResponse` - `{"flag_name": str, "enabled": Any}`
- Update: `FeatureFlagUpdateRequest` - `{"value": Any}`

### Models
- FeatureFlag: Global feature flag definitions
- FeatureFlagOverride: User-level overrides

## JIT Index Hints

```bash
# Find feature flag checks
rg -n "is_enabled|get_flag_value" src/app/modules/feature_flags/

# Find feature flag routes
rg -n "feature-flags" src/app/modules/feature_flags/

# Find feature flag updates
rg -n "update_tenant_flag|bulk_update_tenant_flags" src/app/modules/feature_flags/

# Find flag usage in code
rg -n "feature_flags\." src/app/modules/

# Find schema definitions
rg -n "class.*FeatureFlag.*Schema" src/app/modules/feature_flags/
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
✅ DO: Return `False` or `None` for non-existent flags
```python
# The service raises a domain error; the central
# EXCEPTION_HANDLERS table maps it to 404 for DELETE on non-existent flag
from src.app.shared.exceptions import ModelNotFoundError
```

### Admin Protection
✅ DO: Add RBAC check for flag management endpoints
```python
# TODO in router - add admin permission check
# from src.app.modules.rbac.dependencies import require_permission
# require_permission("feature_flags.manage")
```

### Performance
✅ DO: Cache frequently checked flags in Redis (is_enabled/get_tenant_flags already do)
❌ DON'T: Query database on every `is_enabled()` call in hot paths

## Pre-PR Checks

```bash
# Test feature flag functionality
uv run pytest tests/test_feature_flags.py -v

# Type check
uv run mypy src/app/modules/feature_flags/

# Lint
uv run ruff check src/app/modules/feature_flags/

# Test flag checking in context
uv run pytest tests/test_tenant_context.py -v -k "feature"
```
