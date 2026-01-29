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
├── models.py             # UUIDMixin, TimestampMixin
└── rls.py                # Row Level Security helpers
```

## Row Level Security (RLS) Module

### Purpose
Database-level tenant isolation using PostgreSQL RLS policies. Provides fail-closed security that prevents cross-tenant data leaks even if application-level checks are bypassed.

### Key Functions

```python
from src.app.core.db.rls import set_rls_context, clear_rls_context, rls_context, bypass_rls

# Set tenant context (used in middleware)
await set_rls_context(conn, tenant_id=123, user_id=456)

# Clear context (used in finally blocks)
await clear_rls_context(conn)

# Context manager for request lifecycle
async with rls_context(session, tenant_id, user_id):
    # All queries are automatically filtered
    result = await session.execute(select(User))

# Bypass RLS for admin operations
async with bypass_rls(session, reason="Admin data export"):
    # Can query all tenants' data
    result = await session.execute(select(Tenant))
```

### Usage Patterns

**In middleware (automatic):**
```python
# src/app/middleware/tenant.py
from src.app.core.db.rls import rls_context

async with rls_context(db_session, tenant.id, user.id):
    response = await call_next(request)
```

**In background jobs:**
```python
# src/app/core/worker/rls_context.py
from src.app.core.db.rls import rls_context, clear_rls_context

# Set context before job runs
await set_rls_context(session, tenant_id, user_id)
# ... job executes ...
await clear_rls_context(session)
```

**For super-admin operations:**
```python
# Create audit log entry and bypass RLS
async with bypass_rls(session, reason="System maintenance"):
    # All data visible, bypass logged to audit table
    tenants = await session.execute(select(Tenant))
```

### Tables Protected
See migration `20250128_000011_enable_rls_policies.py` for full list. All tables with `tenant_id` foreign key are protected:
- users
- tenants
- posts
- audit_logs
- api_keys
- webhooks
- notifications
- files
- storage_settings
- feature_flags
- billing_customers
- billing_subscriptions
- billing_invoices
- billing_payment_methods
- user_preferences
- sso_providers
- magic_links
- passkey_credentials

### Performance Impact
Measured overhead: < 10% for typical queries (baseline vs RLS-enabled). See `tests/performance/test_rls_performance.py` for benchmarks.

## Database Circuit Breaker

### Purpose
Protects the database from cascading failures during outages or high load. Automatically stops sending queries when failure threshold is reached and fails fast or serves cached/fallback data.

### Key Features
- **Fail Fast**: Stops waiting for timeouts when DB is known to be down.
- **Cache Fallback**: Serves stale data from Redis if DB is unavailable.
- **Static Fallback**: Returns default values (empty lists, safe defaults) if configured.
- **Transaction Retries**: Automatically retries transient transaction failures with exponential backoff.
- **Read-Only Mode**: Helpers to detect degraded state and prevent write operations.

### Usage Patterns

**1. Basic Query with Cache Fallback**
Use for read-heavy data where eventual consistency is acceptable (dashboards, lists).
```python
from src.app.core.resilience import execute_query_with_circuit_breaker

results = await execute_query_with_circuit_breaker(
    db=db,
    query="SELECT * FROM users WHERE tenant_id = :tid",
    params={"tid": tenant_id},
    cache_key=f"users:list:{tenant_id}",
    cache_ttl=300  # 5 minutes
)
```

**2. Critical Transaction with Retry**
Use for writes that must succeed but might fail transiently (deadlocks, connection blips).
```python
from src.app.core.resilience import execute_transaction_with_circuit_breaker

async def update_balance(session):
    await session.execute(...)

await execute_transaction_with_circuit_breaker(
    db=db,
    operations=[update_balance],
    max_retries=3
)
```

**3. Graceful Degradation (Read-Only)**
Check circuit state before attempting complex writes.
```python
from src.app.core.resilience import db_circuit, get_read_only_fallback, CircuitState

if db_circuit.state == CircuitState.OPEN:
    return get_read_only_fallback()
```

### Testing
```bash
# Run circuit breaker unit tests
uv run pytest tests/unit/core/resilience/test_db_circuit_breaker.py

# Run integration tests (requires DB/Redis)
uv run pytest tests/integration/core/resilience/test_db_circuit_breaker_integration.py
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
