# Middleware Layer - Agent Guidance

## Package Identity

FastAPI middleware components for tenant isolation, rate limiting, CORS, and request processing. Uses Starlette middleware pattern.

## Setup & Run

```bash
# No separate setup - middleware auto-registered in main.py
uv run pytest tests/integration/test_tenant_middleware.py -v
```

## Patterns & Conventions

### DO: Use Starlette BaseHTTPMiddleware

```python
from starlette.middleware.base import BaseHTTPMiddleware

class MyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Pre-processing
        response = await call_next(request)
        # Post-processing
        return response
```

### DON'T: Skip tenant context cleanup

```python
# GOOD - Always clear context
from src.app.core.tenancy.context import clear_tenant_context

finally:
    clear_tenant_context()
```

### DO: Exempt health check paths

```python
EXEMPT_PATHS = frozenset(["/docs", "/redoc", "/openapi.json", "/api/v1/health"])

def is_exempt(path: str) -> bool:
    return path in EXEMPT_PATHS or path.startswith("/api/v1/tenants")
```

### DO: Set proper CORS origins

```python
# In config.py - be restrictive in production
CORS_ORIGINS: list[str] = ["https://trusted-domain.com"]
CORS_ALLOW_CREDENTIALS: bool = True
```

## Module Structure

```
src/app/middleware/
├── __init__.py           # Exports all middleware
├── tenant.py             # Tenant validation middleware
└── rate_limit.py         # Rate limiting middleware
```

## Touch Points

- **Tenant middleware**: `tenant.py::TenantMiddleware`
- **Rate limit middleware**: `rate_limit.py::RateLimitMiddleware`
- **Context handling**: `src.app.core.tenancy.context`
- **CORS config**: `src.app.core.config::settings`

## JIT Index Hints

```bash
# Find middleware classes
rg -n "class.*Middleware" src/app/middleware/

# Find middleware registration
rg -n "add_middleware" src/app/

# Find tenant context usage
rg -n "tenant_context" src/app/

# Find rate limit checks
rg -n "rate_limit" src/app/middleware/
```

## Common Gotchas

- **Order matters**: Middleware executes in registration order (reverse for response)
- **Tenant context**: Must be cleared in finally block to prevent leakage
- **Exempt paths**: Health checks and auth endpoints should be exempt
- **Rate limiting**: Applied per-tenant based on tier configuration
- **CORS preflight**: OPTIONS requests bypass most middleware

## Pre-PR Checks

```bash
uv run pytest tests/integration/test_tenant_middleware.py -v && \
uv run ruff check src/app/middleware/
```
