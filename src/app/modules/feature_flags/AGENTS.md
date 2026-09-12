# Feature Flags Module - Agent Guidance

## Package Identity

Feature flag management system for controlled feature rollout per tenant. Uses PostgreSQL JSONB for storage with optional user-level overrides. Supports boolean, string, number, and JSON flag values.

## Setup & Run

```bash
# Test feature flags module
uv run pytest tests/test_feature_flags.py -v

# Type check module
uv run mypy src/app/modules/feature_flags/

# Lint module
uv run ruff check src/app/modules/feature_flags/
```

## Patterns & Conventions

### Module Structure
```
src/app/modules/feature_flags/
├── __init__.py           # Exports: is_feature_enabled, get_feature_flags, update_feature_flag
├── router.py             # FastAPI routes
├── service.py            # FeatureFlagsService business logic
├── schemas.py            # Pydantic schemas
├── models.py             # SQLAlchemy models (FeatureFlag, FeatureFlagOverride)
├── crud.py               # FastCRUD operations
└── utils.py              # Convenience functions
```

### Feature Flag Storage
✅ DO: Store flags as JSONB on tenant model for performance
```python
# src/app/modules/tenants/models.py (lines 30-35)
feature_flags: dict[str, Any] = Column(JSONB, nullable=True, default=dict)
```

### Check if Feature is Enabled
✅ DO: Use async utility function for feature checks
```python
# src/app/modules/feature_flags/utils.py (lines 1-30)
async def is_feature_enabled(db: AsyncSession, tenant_id: str, flag_name: str) -> bool:
    """Check if a feature flag is enabled for a tenant."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant or not tenant.feature_flags:
        return False
    return tenant.feature_flags.get(flag_name, False)
```

❌ DON'T: Query FeatureFlag model directly for tenant-level checks - use tenant.feature_flags JSONB

### Get All Feature Flags
✅ DO: Return entire feature_flags JSONB object
```python
# src/app/modules/feature_flags/router.py (lines 26-40)
@router.get("", response_model=FeatureFlagsResponse)
async def list_feature_flags(
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
) -> FeatureFlagsResponse:
    tenant_id = current_tenant.id if hasattr(current_tenant, "id") else current_tenant
    flags = await get_feature_flags(db, tenant_id)
    return FeatureFlagsResponse(flags=flags)
```

### Update Feature Flag
✅ DO: Update tenant.feature_flags JSONB and commit
```python
# src/app/modules/feature_flags/router.py (lines 65-106)
@router.patch("/{flag_name}", response_model=FeatureFlagCheckResponse)
async def update_feature_flag(
    flag_name: str,
    request: FeatureFlagUpdateRequest,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
) -> FeatureFlagCheckResponse:
    tenant_id = current_tenant.id if hasattr(current_tenant, "id") else current_tenant
    query = select(Tenant).where(Tenant.id == tenant_id)
    result = await db.execute(query)
    tenant = result.scalar_one()

    if tenant.feature_flags is None:
        tenant.feature_flags = {}

    flags = dict(tenant.feature_flags)
    flags[flag_name] = request.value
    tenant.feature_flags = flags

    await db.commit()
    await db.refresh(tenant)

    return FeatureFlagCheckResponse(flag_name=flag_name, enabled=request.value)
```

### User-Level Overrides
✅ DO: Use FeatureFlagOverride model for per-user flag values
```python
# src/app/modules/feature_flags/service.py (lines 47-78)
async def is_feature_enabled(
    self, db: AsyncSession, flag_name: str, user_id: Optional[int], tenant_id: int
) -> bool:
    flag = await self.get_flag(db, flag_name, tenant_id)
    if not flag:
        return False
    if not flag.is_enabled:
        return False
    if not user_id:
        return True
    user_override = await self.get_override(db, flag_name, user_id, tenant_id)
    if user_override:
        return user_override.is_enabled
    return True
```

## Touch Points / Key Files

### Core Functions
- Utils: `src/app/modules/feature_flags/utils.py` - `is_feature_enabled()`, `get_feature_flags()`, `update_feature_flag()`
- Service: `src/app/modules/feature_flags/service.py` - `FeatureFlagsService` class

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
rg -n "is_feature_enabled" src/app/

# Find feature flag routes
rg -n "feature-flags" src/app/modules/feature_flags/

# Find feature flag updates
rg -n "update_feature_flag|bulk_update" src/app/modules/feature_flags/

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
await update_feature_flag(db, tenant_id, "dark_mode", True)

# String flag
await update_feature_flag(db, tenant_id, "theme", "premium")

# Object flag
await update_feature_flag(db, tenant_id, "limits", {"max_users": 100})
```

### Missing Flags
✅ DO: Return `False` or `None` for non-existent flags
```python
# In router - the service raises a domain error; the central
# EXCEPTION_HANDLERS table maps it to 404 for DELETE on non-existent flag
from src.app.shared.exceptions import ModelNotFoundError

if tenant.feature_flags is None or flag_name not in tenant.feature_flags:
    raise ModelNotFoundError("FeatureFlag", flag_name)
```

### Admin Protection
✅ DO: Add RBAC check for flag management endpoints
```python
# TODO in router - add admin permission check
# from src.app.modules.rbac.dependencies import require_permission
# require_permission("feature_flags.manage")
```

### Performance
✅ DO: Cache frequently checked flags in Redis
❌ DON'T: Query database on every `is_feature_enabled()` call in hot paths

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
