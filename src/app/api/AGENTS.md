# FastAPI SaaS API Layer - Agent Guidance

## Package Identity
Manages API versioning and routing aggregation for the FastAPI SaaS platform. Routers are defined within feature modules and aggregated here.

## Patterns & Conventions

### API Versioning
✅ DO: Use versioned API structure with aggregation
```python
# src/app/api/v1/api.py (lines 1-30)
from fastapi import APIRouter
from src.app.modules.users.router import router as user_router
from src.app.modules.tiers.router import router as tier_router

api_router = APIRouter()
api_router.include_router(user_router, prefix="/users", tags=["users"])
api_router.include_router(tier_router, prefix="/tiers", tags=["tiers"])
```

### Thin Router Pattern
✅ DO: Keep routers thin - delegate all logic to service layer
```python
# src/app/modules/users/router.py (lines 10-20)
@router.post("/", response_model=UserRead)
async def create_user(
    user: UserCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)]
) -> UserRead:
    """Create a new user."""
    user_service = UserService(db)
    return await user_service.create(user=user)
```

❌ DON'T: Put business logic or validation in the router
```python
# Wrong - no hashing or validation here
@router.post("/")
async def create_user(user: UserCreate, db: AsyncSession):
    user_dict = user.model_dump()
    user_dict["hashed_password"] = hash_password(...)  # Bad!
    return await crud.create(db=db, object=user_dict)
```

### Dependency Injection
✅ DO: Use `Annotated` for clean dependency injection
```python
# src/app/api/dependencies.py (lines 1-30)
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(async_get_db)]
) -> UserRead:
    """Get current authenticated user from JWT token."""
    # ... decode and validate token ...
    return user

# In router
@router.get("/me")
async def get_me(
    current_user: Annotated[UserRead, Depends(get_current_user)]
) -> UserRead:
    return current_user
```

### Response Models
✅ DO: Always specify `response_model` for automatic serialization
```python
# Good - automatic validation and serialization
@router.get("/{id}", response_model=UserRead)
async def get_user(id: int) -> UserRead:
    # ... fetch user ...
    return user

# Bad - returns raw data without schema validation
@router.get("/{id}")
async def get_user(id: int):
    return {"id": id, "name": "John"}  # Not validated!
```

## Touch Points / Key Files

### API Aggregation
- API Router: `src/app/api/v1/api.py` - Aggregates all module routers

### Dependencies
- Common Dependencies: `src/app/api/dependencies.py` - Auth, pagination, tenant context

### Module Routers
- Auth Router: `src/app/modules/auth/router.py` - Authentication endpoints
- Users Router: `src/app/modules/users/router.py` - User management
- Tiers Router: `src/app/modules/tiers/router.py` - Subscription tiers

## JIT Index Hints

### Find API Routes
```bash
# Find all router definitions
rg -n "router = APIRouter" src/app/modules/

# Find endpoint decorators
rg -n "@router\.(get|post|put|delete)" src/app/modules/

# Find routes with specific response models
rg -n "response_model=" src/app/modules/
```

### Find Dependencies
```bash
# Find all dependency injection functions
rg -n "async def get_" src/app/api/
```

## Common Gotchas

### Route Prefixes
✅ DO: Apply consistent prefixes when aggregating
```python
# In api.py - define prefix once
api_router.include_router(user_router, prefix="/users", tags=["users"])

# In module router - define routes relative to prefix
@router.get("/{id}")  # Becomes /api/v1/users/{id}
```

❌ DON'T: Double-prefix routes
```python
# Wrong - prefix defined in both places
@router.get("/users/{id}")  # Becomes /api/v1/users/users/{id}
```

### Response Validation
✅ DO: Use `response_model` for all endpoints
❌ DON'T: Return unvalidated data

### Service Usage
✅ DO: Inject service into router or create it per request
```python
# Option 1: Create per request
@router.get("/{id}")
async def get_user(id: int, db: AsyncSession) -> UserRead:
    user_service = UserService(db)
    return await user_service.get(id=id)

# Option 2: Dependency injection (for shared state)
def get_user_service(db: AsyncSession) -> UserService:
    return UserService(db)

@router.get("/{id}")
async def get_user(
    id: int,
    user_service: Annotated[UserService, Depends(get_user_service)]
) -> UserRead:
    return await user_service.get(id=id)
```

## Pre-PR Checks

```bash
# Type check API layer
uv run mypy src/app/api/

# Lint API layer
uv run ruff check src/app/api/

# Test API endpoints
uv run pytest tests/api/ -v

# Verify route registration
uv run python -c "from src.app.main import app; print([r.path for r in app.routes])"
```
