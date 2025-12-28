# Research: Multi-tenancy Foundation

**Feature**: 001-multitenancy-foundation  
**Date**: 2025-12-28  
**Status**: Complete

## Research Topics

### 1. Python Contextvars for Request-Scoped State

**Decision**: Use `contextvars.ContextVar` for tenant context storage

**Rationale**:
- Native Python 3.7+ feature, no external dependencies
- Async-safe by design - each async task gets isolated context
- FastAPI/Starlette automatically copy context for each request
- Lightweight and performant (<1ms overhead)

**Alternatives Considered**:
- **Thread-local storage**: Rejected - not async-safe, context leaks between coroutines
- **Request state (`request.state`)**: Rejected - requires passing request object everywhere, breaks service layer isolation
- **Global singleton**: Rejected - not thread/async safe, violates constitution's explicit DI principle

**Implementation Pattern**:
```python
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant

_current_tenant: ContextVar["Tenant | None"] = ContextVar("current_tenant", default=None)

def get_tenant_context() -> "Tenant | None":
    return _current_tenant.get()

def set_tenant_context(tenant: "Tenant | None") -> None:
    _current_tenant.set(tenant)
```

### 2. FastAPI Middleware for Header Validation

**Decision**: Use Starlette `BaseHTTPMiddleware` with path whitelist

**Rationale**:
- Full access to request headers before route handlers
- Can short-circuit with error responses (400, 403)
- Path-based whitelisting is simple and explicit
- Integrates cleanly with FastAPI's ASGI architecture

**Alternatives Considered**:
- **FastAPI Dependencies**: Rejected for middleware role - can't intercept before routing
- **Pure ASGI Middleware**: More complex, BaseHTTPMiddleware sufficient for this use case
- **Router-level guards**: Rejected - requires per-route configuration, error-prone

**Whitelist Strategy**:
```python
TENANT_EXEMPT_PATHS = frozenset([
    "/docs",
    "/redoc", 
    "/openapi.json",
    "/api/v1/health",
    "/api/v1/ready",
])

def is_path_exempt(path: str) -> bool:
    return path in TENANT_EXEMPT_PATHS or path.startswith("/api/v1/tenants")
```

**Note**: Tenant management endpoints are also exempt from tenant header requirement since they operate at platform level.

### 3. Safe Alembic Migration with Data Backfill

**Decision**: Single migration file with ordered operations using `op.execute()` for data manipulation

**Rationale**:
- Alembic supports raw SQL execution for data operations
- Single migration ensures atomicity (all-or-nothing)
- Ordered operations prevent FK constraint violations
- Reversible with proper downgrade steps

**Alternatives Considered**:
- **Multiple migrations**: Rejected - risk of partial application, harder to test
- **Application-level backfill script**: Rejected - not part of migration history, manual step
- **Django-style data migrations**: Not applicable to Alembic

**Migration Order** (critical):
1. Create `tenants` table
2. Insert default "System" tenant (slug="host")
3. Add `tenant_id` column to `user` as NULLABLE
4. Backfill all existing users with System tenant ID
5. Alter `tenant_id` to NOT NULL
6. Add FK constraint
7. Drop old unique index on `email`
8. Create composite unique index on `(email, tenant_id)`

**Downgrade Strategy**:
1. Drop composite unique index
2. Recreate simple unique index on `email` (may fail if duplicates exist)
3. Drop FK constraint
4. Drop `tenant_id` column
5. Drop `tenants` table

### 4. Slug Validation and Format

**Decision**: Alphanumeric lowercase with hyphens, 3-50 characters

**Rationale**:
- URL-safe and header-safe characters only
- Lowercase enforces consistency
- Length limits prevent abuse and storage issues
- Matches common SaaS tenant identifier patterns

**Validation Pattern**:
```python
import re
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")

def validate_slug(slug: str) -> bool:
    return bool(SLUG_PATTERN.match(slug))
```

**Reserved Slugs**:
- `host` - Default system tenant
- `admin` - Reserved for future use
- `api` - Reserved to avoid path conflicts

### 5. Composite Unique Constraint Strategy

**Decision**: Database-level composite unique index on `(email, tenant_id)`

**Rationale**:
- Enforced at database level - impossible to bypass
- Efficient lookups for auth (email + tenant)
- PostgreSQL handles race conditions atomically

**Alternatives Considered**:
- **Application-level validation only**: Rejected - race conditions possible
- **Trigger-based enforcement**: Overcomplicated, index is simpler

**Index Definition**:
```sql
CREATE UNIQUE INDEX ix_user_email_tenant ON "user" (email, tenant_id);
```

### 6. Tenant Context Dependency for Services

**Decision**: FastAPI `Depends()` function that retrieves from contextvar

**Rationale**:
- Follows constitution's explicit DI principle
- Testable - can mock the dependency
- Type-safe with proper return type hints
- Raises clear error if accessed without valid context

**Implementation**:
```python
from fastapi import HTTPException, status

async def get_current_tenant() -> Tenant:
    tenant = get_tenant_context()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tenant context not available"
        )
    return tenant
```

## Performance Considerations

| Operation | Target | Approach |
|-----------|--------|----------|
| Tenant lookup by slug | <5ms | Database index on `slug` column |
| Middleware overhead | <2ms | In-memory whitelist check, single DB query |
| Context propagation | <0.1ms | Contextvar is O(1) |

## Security Considerations

- **Header spoofing**: Tenant validated against database on every request
- **Inactive tenant access**: Explicit `is_active` check in middleware
- **Context leakage**: Contextvar isolation + reset after request
- **Slug enumeration**: Consider rate limiting tenant lookups (Phase 2)

## Open Questions Resolved

| Question | Resolution |
|----------|------------|
| Auth for tenant endpoints? | Any authenticated user (Phase 1), RBAC in Phase 2 |
| Tenant reactivation? | Yes, reversible soft-delete |
| Default tenant slug? | "host" per Specify.md |
| Tenant endpoint path exemption? | Yes, `/api/v1/tenants/*` exempt from X-Tenant-ID |
