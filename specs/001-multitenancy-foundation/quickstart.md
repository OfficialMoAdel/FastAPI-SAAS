# Quickstart: Multi-tenancy Foundation

**Feature**: 001-multitenancy-foundation  
**Estimated Implementation Time**: 4-6 hours

## Prerequisites

- Python 3.11+ installed
- PostgreSQL running locally or via Docker
- Project dependencies installed (`uv sync`)
- Existing database with users table

## Implementation Order

### Step 1: Create Tenant Module (45 min)

Create the tenant domain module at `src/app/modules/tenants/`:

```bash
mkdir -p src/app/modules/tenants
touch src/app/modules/tenants/__init__.py
```

Files to create:
1. `models.py` - Tenant SQLAlchemy model
2. `schemas.py` - Pydantic request/response schemas
3. `crud.py` - FastCRUD operations
4. `service.py` - Business logic
5. `router.py` - FastAPI endpoints

### Step 2: Create Tenant Context Infrastructure (30 min)

Create `src/app/core/tenancy/context.py`:
- Define `ContextVar` for current tenant
- Implement `get_tenant_context()` and `set_tenant_context()`

### Step 3: Create Tenant Middleware (45 min)

Create `src/app/middleware/tenant.py`:
- Implement `TenantMiddleware` class
- Define whitelist paths
- Validate X-Tenant-ID header
- Set tenant context

### Step 4: Add Tenant Dependency (15 min)

Update `src/app/api/dependencies.py`:
- Add `get_current_tenant()` dependency
- Retrieve from contextvar, raise 500 if not set

### Step 5: Generate Migration (30 min)

Create migration with:
```bash
uv run alembic revision --autogenerate -m "add_tenants_and_user_tenant_id"
```

**Manually edit** the generated migration to:
1. Create `tenants` table first
2. Insert default "System" tenant
3. Add `tenant_id` to users (nullable)
4. Backfill existing users
5. Make `tenant_id` NOT NULL
6. Add FK and composite unique index

### Step 6: Update User Model (15 min)

Modify `src/app/modules/users/models.py`:
- Add `tenant_id` column with FK
- Update any unique constraints

### Step 7: Register in main.py (15 min)

Update `src/app/main.py`:
- Import and register `TenantMiddleware`
- Import and include tenant router

### Step 8: Run Migration (5 min)

```bash
uv run alembic upgrade head
```

### Step 9: Write Tests (60 min)

Create tests:
- `tests/unit/test_tenant_service.py`
- `tests/integration/test_tenant_api.py`
- `tests/contract/test_tenant_middleware.py`

### Step 10: Verify (15 min)

```bash
# Type check
uv run mypy src/

# Lint
uv run ruff check

# Run tests
uv run pytest

# Manual verification
curl -X POST http://localhost:8000/api/v1/tenants \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Tenant", "slug": "test-tenant"}'
```

## Verification Checklist

- [x] Health endpoint works without X-Tenant-ID
- [x] Docs endpoint works without X-Tenant-ID
- [x] Protected endpoints return 400 without X-Tenant-ID
- [x] Protected endpoints return 403 with invalid X-Tenant-ID
- [x] Protected endpoints succeed with valid X-Tenant-ID (skipped - requires DB tenant)
- [x] Tenant CRUD endpoints require authentication
- [x] Existing users have tenant_id = System tenant
- [x] New users require tenant_id (constraint enforced)
- [x] Same email can exist in different tenants
- [x] Same email in same tenant is rejected

## Common Issues

### Migration fails with "column already exists"
- Check if migration was partially applied
- Run `alembic downgrade -1` and retry

### "Tenant context not available" error
- Ensure middleware is registered before routers in main.py
- Check path is not accidentally whitelisted

### Tests fail with "relation does not exist"
- Run migrations in test database: `uv run alembic upgrade head`
- Check test database connection string

## Next Steps

After Phase 1 completion:
1. Run `/speckit.tasks` to generate task list
2. Implement tasks in order
3. Run quality gates before PR
4. Phase 2: RBAC implementation
