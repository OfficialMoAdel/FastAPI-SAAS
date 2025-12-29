# Database Layer - Agent Guidance

## Package Identity

Async SQLAlchemy 2.0 ORM with PostgreSQL via asyncpg. Manages connections, sessions, and base model definitions.

## Setup & Run

```bash
# Check database connection
uv run python -c "from src.app.core.db.database import async_engine; print('Connected')"

# Run migrations
uv run alembic upgrade head

# Generate new migration
uv run alembic revision -m "description"
```

## Patterns & Conventions

### DO: Use async session properly

```python
from src.app.core.db.database import async_get_db
from sqlalchemy.ext.asyncio import AsyncSession

async def get_users(db: AsyncSession = Depends(async_get_db)):
    result = await db.execute(select(User))
    return result.scalars().all()
```

### DON'T: Use sync session in async context

```python
# BAD - Using sync session
from src.app.core.db.database import local_session

async def bad_example():
    with local_session() as session:  # Don't - this is sync
        session.execute(...)
```

### DO: Use declarative base pattern

```python
from src.app.core.db.database import Base
from sqlalchemy.orm import Mapped, mapped_column

class MyModel(Base):
    __tablename__ = "my_table"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
```

### DO: Add proper indexes

```python
# For foreign keys and frequently queried columns
tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
email: Mapped[str] = mapped_column(unique=True, index=True)
```

### DON'T: Forget unique constraints for tenant-scoped data

```python
# Use UniqueConstraint for tenant-specific uniqueness
__table_args__ = (
    UniqueConstraint("tenant_id", "slug", name="uq_tenant_slug"),
)
```

## Module Structure

```
src/app/core/db/
├── __init__.py           # Exports async_get_db, local_session
├── database.py           # Engine, sessionmaker, Base
└── models.py             # UUIDMixin, TimestampMixin
```

## Touch Points

- **Engine setup**: `database.py::async_engine`
- **Session factory**: `database.py::local_session`
- **Base class**: `database.py::Base`
- **Mixins**: `models.py::UUIDMixin`, `TimestampMixin`

## JIT Index Hints

```bash
# Find all models
rg -n "class.*Base" src/app/modules/

# Find foreign key relationships
rg -n "ForeignKey" src/app/modules/

# Find indexes
rg -n "index=True" src/app/modules/

# Find async sessions
rg -n "async_get_db" src/app/
```

## Common Gotchas

- **Session lifecycle**: Each request gets its own session via dependency injection
- **Connection pooling**: Default pool size is appropriate for dev; tune for prod
- **Transaction management**: Use `async with` for explicit transactions
- **Migrations**: Always review auto-generated migrations
- **UTC timestamps**: Use `datetime.now(UTC)` not `datetime.utcnow()`

## Pre-PR Checks

```bash
uv run mypy src/app/core/db/ && \
uv run alembic upgrade head && \
uv run ruff check src/app/core/db/
```
