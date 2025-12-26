# FastAPI SaaS API Layer - Agent Guidance

## Package Identity
API routing and dependency injection layer for the FastAPI SaaS application. Handles request routing, authentication dependencies, and API versioning.

## Setup & Run
```bash
# No API-specific setup - integrated with main application
# API routes are auto-registered via main.py

# Test API endpoints
uv run pytest tests/test_api/ -v

# Type check API layer
uv run mypy src/app/api/
```

## Patterns & Conventions

### API Versioning
✅ DO: Use versioned API structure
```python
# src/app/main.py (lines 15-20)
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
```

❌ DON'T: Mix API versions in same router

### Dependency Injection
✅ DO: Use FastAPI dependency injection for common functionality
```python
# src/app/api/dependencies.py (lines 1-30)
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Get current authenticated user from JWT token."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        token_data = TokenData(**payload)
    except JWTError:
        raise InvalidTokenException("Invalid token")
    
    user = await crud_users.get(db=db, id=token_data.sub, schema_to_select=UserRead)
    if not user:
        raise InvalidTokenException("Invalid token")
    
    return user
```

### Route Organization
✅ DO: Organize routes by feature with proper HTTP methods
```python
# src/app/api/v1/users.py (lines 1-50)
@router.get("/", response_model=List[UserRead])
async def get_users(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    page: int = 1,
    size: int = 10,
) -> dict[str, Any]:
    """Get paginated list of users."""
    return await user_service.get_users(db=db, page=page, size=size)

@router.post("/", response_model=UserRead)
async def create_user(
    user: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Create new user."""
    return await user_service.create_user(user=user, db=db)
```

### Response Models
✅ DO: Use Pydantic schemas for response validation
```python
# Always specify response_model
@router.get("/", response_model=List[UserRead])
@router.post("/", response_model=UserRead)
@router.put("/{user_id}", response_model=UserRead)
```

❌ DON'T: Return raw data without schema validation

### Error Handling
✅ DO: Use HTTP exception handlers for consistent error responses
```python
# src/app/main.py (lines 25-35)
@app.exception_handler(DuplicateValueException)
async def duplicate_value_exception_handler(request: Request, exc: DuplicateValueException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
```

## Touch Points / Key Files

### Main Application
- Main: `src/app/main.py` - FastAPI app setup, route registration, exception handlers

### API Dependencies
- Dependencies: `src/app/api/dependencies.py` - Authentication, user, admin dependencies

### API Versioning
- v1 Routes: `src/app/api/v1/` - Version 1 API endpoints
- Users: `src/app/api/v1/users.py` - User API endpoints (if separate from module router)

### Module Routers
- Auth Router: `src/app/modules/auth/router.py` - Authentication endpoints
- User Router: `src/app/modules/users/router.py` - User management endpoints
- Health Router: `src/app/modules/health/router.py` - Health check endpoints

## JIT Index Hints

### API Route Searches
```bash
# Find all API routes
rg -n "router\.(get|post|put|delete|patch)" src/app/api/ src/app/modules/

# Find route decorators
rg -n "@router\." src/app/api/ src/app/modules/

# Find API endpoints with response models
rg -n "response_model=" src/app/api/ src/app/modules/

# Find dependency injection
rg -n "Depends\(" src/app/api/ src/app/modules/
```

### API Version Searches
```bash
# Find API versioning
rg -n "prefix=\"/api/v" src/app/main.py

# Find versioned routes
rg -n "api/v1" src/app/
```

### Authentication Searches
```bash
# Find authentication dependencies
rg -n "get_current_user|get_current_admin" src/app/api/

# Find OAuth2 scheme usage
rg -n "oauth2_scheme" src/app/api/
```

## Common Gotchas

### Route Prefixes
✅ DO: Use consistent route prefixes
```python
# Main app prefix
api_router = APIRouter(prefix="/api/v1")

# Module router prefix
@router.get("/users")  # Becomes /api/v1/users
```

❌ DON'T: Double-prefix routes
```python
# Wrong - this becomes /api/v1/users/users
@router.get("/users/users")
```

### Dependency Chain
✅ DO: Dependencies must be properly ordered
```python
# Correct order
async def route_handler(
    user_data: UserCreate,  # Request body first
    db: AsyncSession,      # Database session
    current_user: dict,    # Authenticated user
):
```

### Response Models
✅ DO: Always specify response_model for validation
❌ DON'T: Return data without schema validation

### Error Handling
✅ DO: Use custom HTTP exceptions
❌ DON'T: Return generic error dictionaries

## Pre-PR Checks

### API Testing
```bash
# Test API endpoints
uv run pytest tests/test_api/ -v

# Test authentication
uv run pytest tests/test_auth.py -v

# Type check API layer
uv run mypy src/app/api/

# Lint API layer
uv run ruff check src/app/api/
```

### API Documentation
```bash
# Generate OpenAPI spec
uv run python -c "from src.app.main import app; import json; print(json.dumps(app.openapi(), indent=2))"

# Check API docs
# Visit http://localhost:8000/docs when running locally
```

### Route Validation
```bash
# List all routes
uv run python -c "from src.app.main import app; print([route.path for route in app.routes])"

# Test route registration
uv run python -c "from src.app.main import app; print(len(app.routes))"
```