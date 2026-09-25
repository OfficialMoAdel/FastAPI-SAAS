# FastAPI SaaS Application Layer - Agent Guidance

## Package Identity
Main FastAPI application layer providing web interface, admin panel, middleware, and core application setup. Integrates all modules into a cohesive web application.

## Setup & Run
```bash
# No app-specific setup - uses root project configuration
# Application is automatically configured in main.py

# Run the application
uv run uvicorn src.app.main:app --reload

# Run with specific port
uv run uvicorn src.app.main:app --host 0.0.0.0 --port 8000

# Test application startup
uv run python -c "from src.app.main import app; print('App loaded successfully')"
```

## Patterns & Conventions

### Composition Root Pattern
✅ DO: Keep main.py thin — all wiring is delegated
```python
# src/app/main.py (condensed)
from src.app.composition import create_application, lifespan_with_admin

app = create_application(
    router=get_router(),
    settings=settings,
    lifespan=lifespan_with_admin,
    default_response_class=ORJSONResponse,
)

# Middleware stack (LIFO - last added runs first on request)
app.add_middleware(RequestIDMiddleware)   # Innermost - accurate tracing
app.add_middleware(AttackDetectionMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TenantMiddleware)
# ... GZip outermost; full ordering documented inline in main.py
```
❌ DON'T: Add setup logic (routes, handlers, admin mount) directly to main.py — extend `src/app/composition.py` or a lifespan phase. `core/runtime.py` contains only the reusable phase mechanism.

### Admin Interface Configuration
✅ DO: Use configurable admin panel with proper security
```python
# src/app/admin/initialize.py (condensed)
def create_admin_interface() -> CRUDAdmin | None:
    if not settings.admin.CRUD_ADMIN_ENABLED:
        return None

    admin = CRUDAdmin(
        session=_get_db_session,
        SECRET_KEY=settings.SECRET_KEY.get_secret_value(),
        mount_path=settings.admin.CRUD_ADMIN_MOUNT_PATH,
        session_backend="redis" if settings.admin.CRUD_ADMIN_REDIS_ENABLED else "memory",
        allowed_ips=settings.admin.CRUD_ADMIN_ALLOWED_IPS_LIST or None,
        secure_cookies=settings.admin.SESSION_SECURE_COOKIES,
        enforce_https=settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION,
        initial_admin={...},
    )
    register_admin_views(admin)
    return admin
```
Admin settings live in the `settings.admin` namespace (`src/app/core/config/services.py`); initial superuser creds in `settings.first_user`. The admin DB session bypasses RLS with an audited `bypass_rls` reason. Admin mounting happens inside the lifespan, not at import time.

❌ DON'T: Hardcode admin credentials or security settings

### Middleware Pattern
✅ DO: Write NATIVE ASGI middleware (scope/receive/send) — NOT `BaseHTTPMiddleware`.
The hot path is native ASGI; `BaseHTTPMiddleware.dispatch` pays an anyio
task-group + memory-stream hop that double-buffers request bodies.
Registration order is owned by `src/app/core/guard/ordering.py::GUARD_ORDER`
(pinned by `tests/unit/test_middleware_order.py`) — never add middleware by
editing call sites.

```python
# Template matching RateLimitMiddleware / BodyLimitMiddleware:
from starlette.types import ASGIApp, Receive, Scope, Send

class MyMiddleware:
    """Native ASGI middleware."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # ... guard logic; reject via core.errors.middleware_error_response ...
        await self.app(scope, receive, send)
```

### Schema Organization
✅ DO: Keep schemas in their module — `src/app/modules/{module}/schemas.py`, inheriting base DTOs
```python
# src/app/modules/posts/schemas.py
from src.app.modules._base.schemas import BaseCreate, BaseRead, BaseUpdate

class PostCreate(BaseCreate): ...
class PostRead(BaseRead): ...     # or BaseReadUUID for UUID PKs
class PostUpdate(BaseUpdate): ...
```
Only cross-cutting schemas live at `src/app/schemas/` (currently `job.py`).

### Global Exception Handling
✅ DO: Rely on the central error translation — services raise domain errors, HTTP happens in one place
```python
# Domain errors: src/app/shared/exceptions.py (all derive from AppException)
# HTTP translation table: src/app/core/errors.py (EXCEPTION_HANDLERS + _make_table_handler)
# Registered once in src/app/composition.py::create_application → setup_exception_handlers(app)
```
❌ DON'T: Register per-route `@app.exception_handler` blocks in main.py or raise raw `HTTPException` from services.

## Touch Points / Key Files

### Main Application
- Composition root: `src/app/main.py` — `create_application()` and `lifespan_with_admin` from `src/app/composition.py`, LIFO middleware stack, `setup_custom_openapi`
- Application wiring: `src/app/composition.py` — web phase plan, lifespan factory, app factory, CORS/docs control
- Runtime mechanism: `src/app/core/runtime.py` — reusable `StartupPhase` / `compose_phases`
- Infrastructure hooks: `src/app/core/setup.py` — Redis/table startup and teardown primitives

### Admin Interface
- Admin Init: `src/app/admin/initialize.py` - CRUDAdmin configuration (mounted inside lifespan)
- Admin Views: `src/app/admin/views.py` - Admin panel view registration

### Middleware
- Stack: `src/app/middleware/` → [see src/app/middleware/AGENTS.md](middleware/AGENTS.md)

### Schemas
- Job Schemas: `src/app/schemas/job.py` - Background job schemas (module schemas live in `modules/{m}/schemas.py`)

### Outbound Adapters
- Clients: `src/app/clients/{billing,notifications,storage}/` - Vendor SDK adapters (Stripe, SMTP, Azure Blob); services never import vendor SDKs directly

### Shared
- Domain exceptions: `src/app/shared/exceptions.py` - `AppException` hierarchy
- App-level events: `src/app/events.py` - event bus initialization

### Models Base
- Models Base: `src/app/models/__init__.py` - Alembic model discovery registry

## JIT Index Hints

### Application Structure Searches
```bash
# Find main app configuration
rg -n "FastAPI\(|app\.add_middleware\|app\.include_router" src/app/main.py

# Find admin configuration
rg -n "CRUDAdmin\|create_admin_interface" src/app/admin/

# Find middleware classes
rg -n "class.*Middleware" src/app/middleware/

# Find schema definitions
rg -n "class.*Schema\|class.*Base.*Model" src/app/schemas/
```

### Component Registration
```bash
# Find route registrations
rg -n "include_router\|mount\(" src/app/main.py

# Find middleware additions
rg -n "add_middleware" src/app/main.py

# Find exception handlers
rg -n "exception_handler" src/app/main.py
```

### Configuration Searches
```bash
# Find admin settings
rg -n "CRUD_ADMIN_" src/app/core/config/

# Find CORS settings
rg -n "CORS" src/app/core/config/
```

## Common Gotchas

### Admin Security
✅ DO: Use environment-based admin configuration (`settings.admin`, `settings.first_user`)
❌ DON'T: Hardcode admin credentials

### Middleware Order
✅ DO: Add middleware in correct order
```python
# Order matters - first added = outermost
app.add_middleware(MiddlewareA)  # Outermost
app.add_middleware(MiddlewareB)  # Inner
```

❌ DON'T: Ignore middleware execution order

### Schema Validation
✅ DO: Use proper Pydantic validators
```python
class UserCreate(BaseModel):
    password: str = Field(..., min_length=8)
    email: EmailStr
```

❌ DON'T: Use raw types without validation

### Exception Handling
✅ DO: Handle all custom exceptions globally
❌ DON'T: Let exceptions propagate as 500 errors

## Pre-PR Checks

### Application Testing
```bash
# Test application startup
uv run python -c "from src.app.main import app; print('App loaded successfully')"

# Test admin interface
uv run pytest tests/unit/admin/ -v

# Test middleware
uv run pytest tests/unit/middleware/ -v

# Type check app layer
uv run mypy src/app/

# Lint app layer
uv run ruff check src/app/
```

### Integration Testing
```bash
# Test full application
uv run uvicorn src.app.main:app --host 127.0.0.1 --port 8000 &
sleep 2
curl -f http://127.0.0.1:8000/api/v1/health || exit 1
pkill -f uvicorn
```

### Configuration Validation
```bash
# Test configuration loading
uv run python -c "
from src.app.main import app
print('Configuration loaded successfully')
print(f'App title: {app.title}')
print(f'Admin enabled: {app.routes}')
"
```