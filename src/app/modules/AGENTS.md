# FastAPI SaaS Modules - Agent Guidance

## Package Identity
Core business logic modules for the FastAPI SaaS application. Each module follows the router → service → CRUD → models → schemas pattern with async/await throughout.

## Setup & Run
```bash
# No module-specific setup - uses root project configuration
# All modules are auto-imported via src.app.main

# Test specific module
uv run pytest tests/test_{module_name}.py

# Type check module
uv run mypy src/app/modules/{module_name}/
```

## Patterns & Conventions

### Module Structure (CRITICAL)
Each module MUST follow this exact file structure:
```
src/app/modules/{module_name}/
├── __init__.py          # Empty file
├── router.py            # FastAPI routes (delegates to service)
├── service.py           # Business logic layer
├── crud.py              # FastCRUD operations (8 lines max)
├── models.py            # SQLAlchemy models
└── schemas.py           # Pydantic schemas
```

### Router Pattern
✅ DO: Delegate all business logic to service layer
```python
# src/app/modules/users/router.py (lines 20-30)
@router.post("/", response_model=UserRead)
async def create_user(user: UserCreate, db: Annotated[AsyncSession, Depends(get_db_session)]) -> dict[str, Any]:
    return await user_service.create_user(user=user, db=db)
```

❌ DON'T: Put business logic in router
```python
# Never do database operations or validation in router
```

### Service Layer Pattern
✅ DO: All business logic, validation, and error handling
```python
# src/app/modules/users/service.py (lines 20-63)
async def create_user(self, user: UserCreate, db: AsyncSession) -> dict[str, Any]:
    # Validation
    email_exists = await crud_users.exists(db=db, email=user.email)
    if email_exists:
        raise DuplicateValueException("Email is already registered")
    
    # Business logic
    user_dict = user.model_dump()
    user_dict["hashed_password"] = get_password_hash(password=user_dict["password"])
    
    # Database operation via CRUD
    return await crud_users.create(db=db, object=user_internal, schema_to_select=UserRead)
```

### CRUD Pattern
✅ DO: Use FastCRUD with minimal wrapper
```python
# src/app/modules/users/crud.py (lines 1-8)
from fastcrud import FastCRUD
from .models import User
from .schemas import UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead

CRUDUser = FastCRUD[User, UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead]
crud_users = CRUDUser(User)
```

### Async/Await Pattern
✅ DO: All database operations must be async
```python
# Service methods always async
async def get_user(self, username: str, db: AsyncSession) -> dict[str, Any]:
    return await crud_users.get(db=db, username=username, is_deleted=False)
```

### Error Handling Pattern
✅ DO: Use custom HTTP exceptions from core.exceptions
```python
# src/app/modules/users/service.py (lines 44-45)
if email_exists:
    raise DuplicateValueException("Email is already registered")
```

## Touch Points / Key Files

### User Module (Reference Implementation)
- Router: `src/app/modules/users/router.py` - Route definitions
- Service: `src/app/modules/users/service.py` - Business logic patterns
- CRUD: `src/app/modules/users/crud.py` - FastCRUD setup
- Models: `src/app/modules/users/models.py` - SQLAlchemy models
- Schemas: `src/app/modules/users/schemas.py` - Pydantic schemas

### Auth Module
- Router: `src/app/modules/auth/router.py` - JWT token endpoints
- Service: `src/app/modules/auth/service.py` - Authentication logic
- Logout: `src/app/modules/auth/logout.py` - Token blacklisting

### Other Modules
- Posts: `src/app/modules/posts/` - Content management
- Tasks: `src/app/modules/tasks/` - Background job management
- Tiers: `src/app/modules/tiers/` - Subscription tier logic
- Rate Limits: `src/app/modules/rate_limits/` - Rate limiting rules

## JIT Index Hints

### Find Module Components
```bash
# List all modules
ls src/app/modules/

# Find service classes
rg -n "class.*Service" src/app/modules/

# Find CRUD operations
rg -n "FastCRUD" src/app/modules/

# Find router endpoints
rg -n "router\.(get|post|put|delete)" src/app/modules/

# Find database models
rg -n "class.*Base" src/app/modules/

# Find Pydantic schemas
rg -n "class.*Schema" src/app/modules/
```

### Module-Specific Searches
```bash
# User module patterns
rg -n "user_service" src/app/modules/
rg -n "crud_users" src/app/modules/

# Auth module patterns  
rg -n "authenticate_user" src/app/modules/
rg -n "create_access_token" src/app/modules/
```

## Common Gotchas

### Database Session Handling
✅ DO: Always accept `db: AsyncSession` as parameter
❌ DON'T: Create database sessions in service layer

### Import Patterns
✅ DO: Use absolute imports from service layer
```python
from ...core.exceptions.http_exceptions import DuplicateValueException
from ...core.security import get_password_hash
```

❌ DON'T: Use relative imports
```python
from ..core.exceptions import DuplicateValueException
```

### Schema Selection
✅ DO: Always specify `schema_to_select` in CRUD operations
```python
await crud_users.get(db=db, username=username, schema_to_select=UserRead)
```

❌ DON'T: Let CRUD return full model objects

### Soft Deletes
✅ DO: Use `is_deleted=False` for reads
```python
await crud_users.get(db=db, username=username, is_deleted=False)
```

❌ DON'T: Forget soft delete filtering

## Pre-PR Checks

### Module Testing
```bash
# Test specific module
uv run pytest tests/test_users.py -v

# Type check module
uv run mypy src/app/modules/users/

# Lint module
uv run ruff check src/app/modules/users/
```

### Database Migration
```bash
# Generate migration for new models
uv run alembic revision --autogenerate -m "Add {module_name} module"

# Apply migration
uv run alembic upgrade head
```