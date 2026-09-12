# FastAPI SaaS Modules - Agent Guidance

## Package Identity
Contains the domain-specific business logic of the SaaS platform. Each module is a self-contained unit with standardized patterns.

## Patterns & Conventions

### The Module Structure (CRITICAL)
Modules follow this standardized structure. `models.py` is required **only
when the module owns database tables**; facade/worker/read-only modules omit
it deliberately (today: `app_configuration`, `dashboard`, `health`, `security`
— a thin passkey facade over `auth` — and `tasks`, which fronts the ARQ
worker). The module generator (`src/scripts/create_module.py`) scaffolds all
six files; delete `models.py` if the module owns no tables.
```
src/app/modules/{module_name}/
├── __init__.py          # Empty file
├── models.py            # SQLAlchemy database models (only if the module owns tables)
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

✅ DO: Import cross-module services via the module package root (P1-20)
Ruff `TID` (flake8-tidy-imports `banned-api`) forbids deep `*.service` /
`*.router` imports outside their own module (auth, users, tenants, posts).
Every module facade (`modules/{module}/__init__.py`) lazy-exports its
services, routers, models and crud via `__getattr__`, with an
`if TYPE_CHECKING:` block so mypy still sees the real classes:
```python
from src.app.modules.users import UserService
from src.app.modules.tenants import TenantService, get_tenant_service
```
Sanctioned seams (each documented where it lives): auth → `users.UserService`
(identity lookup); rbac + billing webhooks → `users.UserService`
(membership seam, tier downgrade); `api/deps` → `users.UserService` (request
auth); user_preferences → storage service + users crud/cache (profile image,
session invalidation); dashboard → audit/billing/users models (read-only
aggregation); security → `auth.services.passkey` (thin facade); tenants ↔
rate_limits models (TYPE_CHECKING relationship backrefs only).

❌ DON'T: Directly import another module's `service.py`/`router.py` internals
```python
# Wrong - bypasses the facade; ruff TID251 flags this
from src.app.modules.users.service import UserService

# Right - package root (lazy, cycle-safe, type-checked)
from src.app.modules.users import UserService
```

### Redis Acquisition (the ONE seam)
✅ DO: Acquire the Redis client through the provider or DI — never globals or direct construction
```python
# In a service (call-time; observes test patches of utils.cache.client):
from src.app.core.redis_provider import get_redis
redis_client = get_redis()

# In a router (FastAPI dependency):
from src.app.core.utils.cache import async_get_redis
redis: Annotated[Redis, Depends(async_get_redis)]

# Cache-key semantics only (no client needed):
from src.app.core.cache.client import cache_client
await cache_client.set(cache_client.tenant_key("feature:flag", tenant_id), "on", ttl=30)
```

❌ DON'T: `from redis.asyncio import Redis` to *construct* a client, or read
`utils.cache.client` directly (two modules were migrated off this; the
provider is the single owner of the Redis lifecycle, incl. the pool).
Importing the `Redis` *type* for annotations is fine.

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
✅ DO: Raise plain domain exceptions — HTTP mapping is central
```python
# src/app/shared/exceptions.py — AppException subclasses (NOT HTTPException)
class DuplicateError(AppException): ...
class ModelNotFoundError(AppException): ...
class ForbiddenError(AppException): ...
class ServiceUnavailableError(AppException): ...

# In service — raise the domain error, never an HTTP status:
from src.app.shared.exceptions import DuplicateError
if email_exists:
    raise DuplicateError("email", values.email)
```

Every domain base derives from `AppException` (`src/app/shared/exceptions.py`):
`AuthError` (auth), the billing errors, `StorageError` (storage),
`AIProviderError` + `SessionLimitExceededError` (ai), `CacheException` (core).
The old `modules/_base/exceptions.py` shim is deleted — never import it.

The HTTP translation lives in ONE place: `src/app/core/errors.py` builds the
`EXCEPTION_HANDLERS` table (`build_exception_table`, deferred H7 imports) and
registers each row via the `_make_table_handler` factory, mapping every domain
exception to a JSONResponse with its status code. Services stay HTTP-free;
routers don't catch anything.

`core/exceptions/http_exceptions.py` is a SEPARATE tree (FastCRUD-derived
HTTPException subclasses like `DuplicateValueException`, plus the HTTP-shaped
`TenantContextError`) used by modules that raise HTTP-shaped errors directly
(e.g. sso, billing routers). When adding a new module: prefer the shared
`AppException` errors + let the table translate.

❌ DON'T: re-create a `_base/exceptions` module, claim shared errors are
HTTPException-based (they are not), or hand-roll `HTTPException(409, ...)`
in a service — that bypasses the central mapping and the error-code contract.

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
