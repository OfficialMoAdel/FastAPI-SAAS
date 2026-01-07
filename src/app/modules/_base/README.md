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
from ..._base.exceptions import ModelNotFoundError, DuplicateError
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

### Exceptions (`exceptions.py`)

```python
from ..._base.exceptions import ModelNotFoundError, DuplicateError, ValidationError

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

## When NOT to Use BaseService

Some modules should not inherit `BaseService`:

| Module Type | Reason |
|-------------|--------|
| Auth modules | Specialized authentication flows |
| Queue-based | No database (e.g., `tasks` with ARQ) |
| Append-only | No update/delete (e.g., `audit_logs`) |
| Complex orchestration | Multi-service logic (e.g., `sso`) |

For these, still use `_base` exceptions for consistency.
