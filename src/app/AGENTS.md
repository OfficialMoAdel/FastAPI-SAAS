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
uv run pytest tests/test_app_startup.py -v
```

## Patterns & Conventions

### Application Factory Pattern
✅ DO: Configure all components in main.py
```python
# src/app/main.py (lines 1-50)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import api_router
from .admin import create_admin_interface
from .core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.PROJECT_DESCRIPTION,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Configure middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_HOSTS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(api_router)

# Add admin interface
admin_app = create_admin_interface()
if admin_app:
    app.mount("/admin", admin_app)
```

### Admin Interface Configuration
✅ DO: Use configurable admin panel with proper security
```python
# src/app/admin/initialize.py (lines 10-50)
def create_admin_interface() -> Optional[CRUDAdmin]:
    if not settings.CRUD_ADMIN_ENABLED:
        return None

    admin = CRUDAdmin(
        session=async_get_db,
        SECRET_KEY=settings.SECRET_KEY.get_secret_value(),
        mount_path=settings.CRUD_ADMIN_MOUNT_PATH,
        session_backend="redis" if settings.CRUD_ADMIN_REDIS_ENABLED else "memory",
        allowed_ips=settings.CRUD_ADMIN_ALLOWED_IPS_LIST,
        secure_cookies=settings.SESSION_SECURE_COOKIES,
        enforce_https=settings.ENVIRONMENT == EnvironmentOption.PRODUCTION,
        initial_admin={
            "username": settings.ADMIN_USERNAME,
            "password": settings.ADMIN_PASSWORD,
        }
    )
    
    register_admin_views(admin)
    return admin
```

❌ DON'T: Hardcode admin credentials or security settings

### Middleware Pattern
✅ DO: Use Starlette middleware for cross-cutting concerns
```python
# src/app/middleware/client_cache_middleware.py (lines 5-57)
class ClientCacheMiddleware(BaseHTTPMiddleware):
    """Middleware to set Cache-Control header for client-side caching."""
    
    def __init__(self, app: FastAPI, max_age: int = 60) -> None:
        super().__init__(app)
        self.max_age = max_age

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response: Response = await call_next(request)
        response.headers["Cache-Control"] = f"public, max-age={self.max_age}"
        return response
```

### Schema Organization
✅ DO: Organize schemas by domain
```python
# src/app/schemas/user.py (lines 1-30)
from pydantic import BaseModel, EmailStr
from typing import Optional

class UserBase(BaseModel):
    email: EmailStr
    username: str

class UserCreate(UserBase):
    password: str

class UserRead(UserBase):
    id: int
    is_active: bool
    created_at: datetime

class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
```

### Global Exception Handling
✅ DO: Handle all exceptions in main.py
```python
# src/app/main.py (lines 25-40)
@app.exception_handler(DuplicateValueException)
async def duplicate_value_exception_handler(request: Request, exc: DuplicateValueException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )

@app.exception_handler(NotFoundException)
async def not_found_exception_handler(request: Request, exc: NotFoundException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
```

## Touch Points / Key Files

### Main Application
- Main App: `src/app/main.py` - FastAPI application factory, route registration, middleware setup
- App Init: `src/app/__init__.py` - Application initialization

### Admin Interface
- Admin Init: `src/app/admin/initialize.py` - CRUDAdmin configuration
- Admin Views: `src/app/admin/views.py` - Admin panel view registration

### Middleware
- Cache Middleware: `src/app/middleware/client_cache_middleware.py` - Client-side caching headers

### Schemas
- User Schemas: `src/app/schemas/user.py` - User-related Pydantic schemas
- Job Schemas: `src/app/schemas/job.py` - Background job schemas

### CRUD Base
- CRUD Base: `src/app/crud/__init__.py` - Base CRUD operations and utilities

### Models Base
- Models Base: `src/app/models/__init__.py` - Base model definitions

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
rg -n "CRUD_ADMIN_" src/app/core/config.py

# Find CORS settings
rg -n "ALLOWED_HOSTS|CORS" src/app/core/config.py
```

## Common Gotchas

### Admin Security
✅ DO: Use environment-based admin configuration
```python
initial_admin={
    "username": settings.ADMIN_USERNAME,
    "password": settings.ADMIN_PASSWORD,
}
```

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
uv run pytest tests/test_admin.py -v

# Test middleware
uv run pytest tests/test_middleware.py -v

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