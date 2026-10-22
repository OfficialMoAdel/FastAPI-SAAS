# Tenants Module - Agent Guidance

## Package Identity

Multi-tenant management with tenant isolation, context management, and whitelist validation. Central to the SaaS architecture.

## Setup & Run

```bash
# Run tenant tests
uv run pytest tests/integration/core/tenancy/test_tenant_api.py -v

# Create first tenant
uv run python scripts/create_first_tenant.py
```

## Patterns & Conventions

### DO: Use tenant context for data isolation

```python
from src.app.core.tenancy.context import get_tenant_context

# Get current tenant ID
context = get_tenant_context()
tenant_id = context.tenant_id
```

### DON'T: Skip tenant validation

```python
# BAD - No tenant check
@router.get("/items")
async def get_items(db: AsyncSession = Depends(async_get_db)):
    return await crud_items.get_multi(db=db)  # Returns all items!
```

### DO: Validate tenant exists and is active

```python
from src.app.middleware.tenant import TenantMiddleware

# Middleware validates X-Tenant-ID header
# Returns 400 if tenant not found or inactive
```

### DO: Use tenant-scoped queries

```python
# Always filter by tenant_id
stmt = select(Item).where(Item.tenant_id == current_tenant.id)
```

## Module Structure

```
src/app/modules/tenants/
├── __init__.py           # Exports tenant_service
├── router.py             # /tenants endpoints
├── service.py            # TenantService class
├── models.py             # Tenant model
├── schemas.py            # Pydantic schemas
└── crud.py               # FastCRUD operations
```

## Touch Points

- **Tenant service**: `modules/tenants/service.py::TenantService`
- **Activate/deactivate**: `modules/tenants/service.py::TenantService._set_active_state` — the ONE implementation behind `deactivate_tenant` and `reactivate_tenant`; both public methods delegate to it with the flag and the log event as the only difference.
- **Models**: `modules/tenants/models.py::Tenant`
- **Middleware**: `src/app/middleware/tenant.py`
- **Context**: `src/app/core/tenancy/context.py`

## JIT Index Hints

```bash
# Find tenant endpoints
rg -n "router\.(get|post|patch|delete)" src/app/modules/tenants/

# Find tenant context usage
rg -n "get_tenant_context\|set_tenant_context" src/app/

# Find tenant middleware
rg -n "TenantMiddleware" src/app/middleware/

# Find tenant ID validation
rg -n "X-Tenant-ID" src/app/
```

## Common Gotchas

- **Tenant header**: All requests require X-Tenant-ID header (except exempt paths)
- **Context leakage**: Always clear context in finally block
- **Inactive tenants**: Requests return 400 for inactive tenants
- **Slug uniqueness**: Tenant slugs must be unique
- **Whitelist validation**: Optional tenant whitelist for access control

## Pre-PR Checks

```bash
uv run pytest tests/integration/core/tenancy/test_tenant_api.py -v && \
uv run pytest tests/contract/test_tenant_middleware.py -v && \
uv run ruff check src/app/modules/tenants/
```
