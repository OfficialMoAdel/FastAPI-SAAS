# FastAPI SaaS Modules - Agent Guidance

## Package Identity
Contains the domain-specific business logic of the SaaS platform. Each module is a self-contained unit with standardized patterns.

## Patterns & Conventions

### The Module Structure (CRITICAL)
Every module MUST follow this standardized structure:
```
src/app/modules/{module_name}/
├── __init__.py          # Empty file
├── models.py            # SQLAlchemy database models
├── schemas.py           # Pydantic data models
├── service.py           # Business logic layer
├── router.py            # API endpoint definitions
└── crud.py              # (Optional) Custom CRUD operations if FastCRUD isn't enough
```

### The `_base` Pattern
Most modules should inherit from standardized base classes in `src/app/modules/_base/`:

- **`BaseService`**: Provides standard CRUD orchestration, logging, and exceptions.
- **`BaseCreate`**: For POST requests (creation).
- **`BaseRead`**: For GET responses (single/multi retrieval).
- **`BaseReadUUID`**: For response models using UUID primary keys.
- **`BaseUpdate`**: For PATCH requests (partial updates).

### Service Layer Pattern
✅ DO: Inherit from `BaseService` for standardized CRUD
```python
# src/app/modules/users/service.py (lines 1-20)
from src.app.modules._base.service import BaseService

class UserService(BaseService[User]):
    def __init__(self, db: AsyncSession):
        super().__init__(User, db)
```

✅ DO: Use service layer for business logic, validation, and error handling
```python
# src/app/modules/users/service.py (lines 20-40)
async def create_user(self, user: UserCreate) -> UserRead:
    # Validation
    email_exists = await self.crud.exists(email=user.email)
    if email_exists:
        raise DuplicateError("Email is already registered")

    # Business logic (hashing, etc.)
    user_dict = user.model_dump()
    user_dict["hashed_password"] = get_password_hash(password=user_dict["password"])

    # Create via CRUD
    return await self.crud.create(object=UserCreateInternal(**user_dict))
```

❌ DON'T: Put business logic in the router
```python
# Wrong - router should be thin
@router.post("/")
async def create_user(user: UserCreate, db: AsyncSession):
    # No validation or hashing here!
    return await crud.create(db=db, object=user)
```

### Cross-Module Communication
✅ DO: Use the Event Bus for cross-module actions
```python
# src/app/modules/users/service.py
from src.app.core.events import event_bus

async def create_user(self, user: UserCreate) -> UserRead:
    # ... create user ...

    # Emit event for other modules to listen for
    await event_bus.publish("user_registered", {"user_id": user.id})
```

❌ DON'T: Directly import other modules' services
```python
# Wrong - creates tight coupling
from src.app.modules.notifications.service import NotificationService
await NotificationService.send_welcome_email(user.email)
```

### CRUD Pattern
✅ DO: Use FastCRUD with minimal wrapper (handled by `BaseService`)
```python
# src/app/modules/users/crud.py (lines 1-8)
from fastcrud import FastCRUD
from src.app.modules.users.models import User
from src.app.modules.users.schemas import (
    UserCreateInternal, UserUpdate, UserUpdateInternal,
    UserDelete, UserRead
)

CRUDUser = FastCRUD[User, UserCreateInternal, UserUpdate, UserUpdateInternal, UserDelete, UserRead]
crud_users = CRUDUser(User)
```

### Async/Await Pattern
✅ DO: All database operations must be async
```python
# Service methods always async
async def get_user(self, id: int) -> UserRead:
    return await self.crud.get(id=id)
```

### Error Handling Pattern
✅ DO: Use standardized exceptions
```python
# src/app/core/exceptions/http_exceptions.py
from fastapi import HTTPException

class DuplicateError(HTTPException):
    def __init__(self, detail: str = "Duplicate value found"):
        super().__init__(status_code=409, detail=detail)

class ModelNotFoundError(HTTPException):
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status_code=404, detail=detail)

# In service
if email_exists:
    raise DuplicateError("Email is already registered")
```

## Touch Points / Key Files

### The Base Pattern
- Base Service: `src/app/modules/_base/service.py` - Template for all services
- Base Schemas: `src/app/modules/_base/schemas.py` - Template for all schemas
- Base Models: `src/app/modules/_base/models.py` - Common model mixins

### Module Examples
- Users Module: `src/app/modules/users/` - Authentication and profile logic
- Tenants Module: `src/app/modules/tenants/` - Multi-tenancy logic
- Tiers Module: `src/app/modules/tiers/` - Subscription tiers
- Feature Flags: `src/app/modules/feature_flags/` - System and tenant flags

## JIT Index Hints

### Find Module Components
```bash
# List all modules
ls src/app/modules/

# Find service classes
rg -n "class.*Service" src/app/modules/

# Find routers
rg -n "router = APIRouter" src/app/modules/

# Find database models
rg -n "__tablename__" src/app/modules/

# Find Pydantic schemas
rg -n "class.*Schema" src/app/modules/
```

### Find Base Patterns
```bash
# See base service implementation
rg -n "class BaseService" src/app/modules/_base/

# See base schemas
ls src/app/modules/_base/schemas.py
```

## Common Gotchas

### Module Imports
✅ DO: Use absolute imports starting with `src.app.`
```python
from src.app.modules.users.schemas import UserCreate
```

❌ DON'T: Use relative imports
```python
from ..schemas import UserCreate
```

### Service Initialization
✅ DO: Pass the model class to `BaseService`
```python
class UserService(BaseService[User]):
    def __init__(self, db: AsyncSession):
        super().__init__(User, db)
```

### Cross-Module Imports
❌ DON'T: Import services from other modules directly
```python
# Wrong - creates tight coupling
from src.app.modules.notifications.service import NotificationService

# Right - use event bus
await event_bus.publish("event_name", data)
```

## Pre-PR Checks

```bash
# Test specific module
uv run pytest tests/modules/test_users.py -v

# Type check module
uv run mypy src/app/modules/users/

# Lint module
uv run ruff check src/app/modules/users/

# Generate migration if model changed
uv run alembic revision --autogenerate -m "Update users module"
```
