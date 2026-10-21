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

### Two ways to obtain a session — both are pinned by a gate

There are TWO session constructors in this package, and they are the
same lifecycle in two shapes:

```python
# core/db/database.py
async_get_db()                     # a FastAPI dependency: `Depends(async_get_db)`
get_db_context()                   # an async context manager: `async with get_db_context() as db:`
```

`get_db_context()` exists because the auth path stopped taking a
session as a dependency parameter: `get_current_user` is now a pure
delegator and the shared resolver (`src/app/api/deps/auth.py::resolve_principal`)
opens the session itself. That refactor left the C-3 gate in
`scripts/perf-validation/isomorphism_validator.py` modelling only ONE of
the two shapes, so its pinned caller set drifted and the other shape was
never checked at all.

The gate now enumerates both — `C3_DB_CALLERS` (holders) and
`C3_SESSION_OPENERS` (openers) — and both are closed worlds: a new
member of either class fails a gate until it is classified.

❌ DON'T: add a session to an except handler. The 401 path must not
touch the database. A `rollback` is the one permitted use (a rollback on
a session that never connected performs zero pool checkouts, where a
query performs one) and it is permitted because that was measured, not
because it is convenient.

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
├── __init__.py              # Exports async_get_db, local_session, Base
├── database.py              # Engine, sessionmaker, Base, get_async_engine
├── models.py                # UUIDMixin, TimestampMixin
├── pk.py                    # Primary key type helpers
├── rls.py                   # Row Level Security helpers
├── tenant_crud.py           # Tenant-scoped CRUD helpers
├── crud_token_blacklist.py  # DB CRUD for blacklisted tokens
├── token_blacklist.py       # Token blacklist model
├── profiler.py              # Query profiling support
└── QUERY_OPTIMIZATION.md    # Query/index optimization guide
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
See migration `src/migrations/versions/08_enable_rls_policies.py` for full list. All tables with `tenant_id` foreign key are protected:
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
- mfa_methods

### Performance Impact
Measured overhead: < 10% for typical queries (baseline vs RLS-enabled). See `tests/performance/test_rls_performance.py` for benchmarks.

## Database Circuit Breaker

### Purpose
Protects the database from cascading failures during outages or high load. The
`db_circuit_guard()` context manager fails fast with HTTPException(503) when the
failure threshold is reached, instead of piling queries onto a struggling database.

### Key Features
- **Fail Fast**: Raises HTTPException(503) when the circuit is OPEN — no waiting on timeouts.
- **Infra-only tripping**: Only infrastructure errors (OperationalError, timeouts,
  disconnects) count toward the threshold. Business-logic errors (IntegrityError,
  404s, validation) never trip the circuit.
- **HALF_OPEN probing**: Allows one test request through per recovery window.
- **Admin Control**: Reset circuit breakers via API (`/admin/circuit-breakers`).

### Usage Patterns

**1. Guard a Query**
Wrap any database operation — the guard handles state checks and outcome recording.
```python
from src.app.core.resilience import db_circuit_guard

async with db_circuit_guard():
    result = await db.execute(stmt)
```

**2. Graceful Degradation (Read-Only)**
Check circuit state before attempting complex writes.
```python
from src.app.core.resilience import db_circuit
from src.app.core.utils.circuit_breaker import CircuitState

if db_circuit.state == CircuitState.OPEN:
    return {"status": "degraded", "mode": "read-only"}
```

### Testing
```bash
# Run circuit breaker unit tests
uv run pytest tests/unit/core/resilience/test_db_circuit_breaker.py

# Run integration tests (requires DB/Redis)
uv run pytest tests/integration/core/resilience/test_circuit_breaker.py
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
