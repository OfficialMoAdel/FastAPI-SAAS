# Base Module (`_base`)

Provides foundational classes and patterns for all modules in the application.

## Components

### Schemas (`schemas.py`)

```python
from ..._base.schemas import BaseCreate, BaseRead, BaseReadUUID, BaseUpdate

class MyModelCreate(BaseCreate):
    name: str
    # model_config inherited

class MyModelRead(BaseRead):       # For int PK
    name: str
    # id, created_at, updated_at inherited

class MyModelReadUUID(BaseReadUUID):  # For UUID PK
    name: str
    # id (UUID), created_at, updated_at inherited

class MyModelUpdate(BaseUpdate):
    name: str | None = None
    # model_config inherited
```

### Service (`service.py`)

```python
from sqlalchemy.ext.asyncio import AsyncSession
from ..._base.service import BaseService
from ...shared.exceptions import ModelNotFoundError, DuplicateError
from .models import MyModel

class MyModelService(BaseService[MyModel]):
    def __init__(self, db: AsyncSession):
        super().__init__(MyModel, db)

    async def create_item(self, data: MyModelCreate) -> dict:
        # Check for duplicates
        existing = await self.crud.exists(db=self.db, name=data.name)
        if existing:
            raise DuplicateError("name", data.name)
        return await self.crud.create(db=self.db, object=data)

    async def get_item(self, item_id: int) -> dict:
        item = await self.crud.get(db=self.db, id=item_id)
        if not item:
            raise ModelNotFoundError("MyModel", item_id)
        return item

# DI factory function
def get_my_model_service(db: AsyncSession) -> MyModelService:
    return MyModelService(db)
```

### Router (`router.py`)

```python
from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from ...core.db.database import async_get_db
from .service import MyModelService, get_my_model_service

router = APIRouter(tags=["my-models"])

# Type alias for DI
MyModelServiceDep = Annotated[
    MyModelService,
    Depends(lambda db=Depends(async_get_db): get_my_model_service(db))
]

@router.get("/{id}")
async def get_item(id: int, service: MyModelServiceDep):
    return await service.get_item(id)
```

### Exceptions (`src/app/shared/exceptions.py`)

```python
from ...shared.exceptions import ModelNotFoundError, DuplicateError, ValidationError

# Use directly - no need to subclass
raise ModelNotFoundError("User", user_id)
raise DuplicateError("email", email)
raise ValidationError("Invalid input")
```

## New Module Template

When creating a new module, use this structure:

```
src/app/modules/{module_name}/
├── __init__.py           # Export router
├── models.py             # SQLAlchemy model
├── schemas.py            # Pydantic schemas (inherit from _base)
├── crud.py               # FastCRUD instance
├── service.py            # Business logic (inherit BaseService)
└── router.py             # FastAPI router (use DI pattern)
```

### Checklist for New Modules

- [ ] Model inherits from `Base` (from `core.db.database`)
- [ ] Schemas inherit from `BaseCreate`, `BaseRead`/`BaseReadUUID`, `BaseUpdate`
- [ ] Service inherits from `BaseService[Model]`
- [ ] Service has `get_*_service(db)` factory function
- [ ] Router uses `Depends()` for service injection
- [ ] Exceptions use `ModelNotFoundError`, `DuplicateError` from `_base`
- [ ] Router registered in `api/v1/__init__.py`

## Service Pattern Decision Guide

The codebase uses two service patterns by design. Choose based on your service's characteristics.

### Pattern 1: BaseService (for standard entity CRUD)

Use `BaseService[Model]` when:
- Your service primarily manages a single entity type
- Standard CRUD operations (get/list/create/update/delete) apply
- Pagination is needed for listing
- You want inherited FastCRUD integration

**Current services using BaseService:**
| Service | Model | Notes |
|---------|-------|-------|
| `APIKeyService` | APIKey | Standard key management |
| `TenantService` | Tenant | Tenant CRUD with customization |
| `PostService` | Post | Content management |
| `TierService` | Tier | Subscription tier management |
| `RateLimitService` | RateLimit | Rate limit configuration |

### Pattern 2: Standalone Service (for specialized operations)

Use standalone class when:
- Operations don't fit standard CRUD patterns
- Multiple external services are orchestrated
- Complex business logic dominates
- Authentication/security flows are primary concern
- Write-only or append-only operations

**Current standalone services:**
| Service | Reason |
|---------|--------|
| `AuthService` | Token generation, password verification flows |
| `SSOService` | External OAuth provider orchestration |
| `PasskeyService` | WebAuthn registration/authentication flows |
| `MagicLinkService` | Token-based passwordless auth |
| `FileService` | External storage provider operations |
| `StorageSettingsService` | Encrypted config with validation |
| `SSOSettingsService` | Encrypted provider credentials |
| `WebhookService` | Event delivery with retry logic |
| `NotificationService` | Email sending, no DB entity |
| `AuditLogService` | Append-only event logging |
| `TaskService` | Background job management |
| `UserService` | Complex user operations (roles, SSO links) |
| `RoleService` | RBAC with permission queries |
| `PreferencesService` | JSON preference operations |
| `FeatureFlagsService` | Simple flag lookups |

### Guidelines

1. **Don't force BaseService** - If your service has mostly custom methods, standalone is cleaner
2. **Consistency within pattern** - Follow the pattern conventions (DI factories, exception handling)
3. **Use shared exceptions** - Both patterns should use `ModelNotFoundError`, `DuplicateError`, etc. from `src.app.shared.exceptions`.
4. **Document choice** - Add a docstring explaining why the pattern was chosen
