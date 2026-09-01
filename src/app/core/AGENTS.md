# FastAPI SaaS Core Infrastructure - Agent Guidance

## Package Identity
Core infrastructure layer providing configuration, security, database, utilities, and background job processing for the FastAPI SaaS application.

## Setup & Run
```bash
# No core-specific setup - integrated with main application
# Core components are auto-imported and configured

# Test core components
uv run pytest tests/test_core.py -v

# Type check core
uv run mypy src/app/core/
```

## Patterns & Conventions

### Configuration Management
✅ DO: Use Pydantic Settings with environment validation
```python
# src/app/core/config.py (lines 1-50)
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_ignore_empty=True,
    )
    
    SECRET_KEY: str = Field(..., min_length=32)
    POSTGRES_SERVER: str = Field(..., description="Database server address")
```

❌ DON'T: Hardcode configuration values
```python
# Never do this
SECRET_KEY = "hardcoded-secret"
```

### Security Patterns
✅ DO: Use bcrypt for password hashing
```python
# src/app/core/security.py (lines 1-15)
def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
```

✅ DO: JWT with access/refresh token pattern
```python
# src/app/core/security.py (lines 20-50)
def create_access_token(
    subject: Union[str, Any], expires_delta: Union[timedelta, None] = None
) -> str:
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {"exp": expire, "sub": str(subject), "type": "access"}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt
```

### Database Patterns
✅ DO: Use async SQLAlchemy with proper session management
```python
# src/app/core/db/database.py (lines 1-30)
async_engine = create_async_engine(
    str(settings.POSTGRES_URI),
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
)

async_session_maker = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
```

✅ DO: Use dependency injection for database sessions
```python
# src/app/core/db/database.py (lines 40-50)
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

### Exception Handling
✅ DO: Use custom HTTP exceptions
```python
# src/app/core/exceptions/http_exceptions.py (lines 1-20)
class DuplicateValueException(HTTPException):
    def __init__(self, detail: str = "Duplicate value found", status_code: int = 409):
        super().__init__(status_code=status_code, detail=detail)

class NotFoundException(HTTPException):
    def __init__(self, detail: str = "Resource not found", status_code: int = 404):
        super().__init__(status_code=status_code, detail=detail)
```

### Background Job Patterns
✅ DO: Use ARQ for async background processing
```python
# src/app/core/worker/functions.py (lines 1-30)
async def send_email(ctx: dict, recipient: str, subject: str, body: str) -> None:
    """Send email via background job."""
    # Email sending logic here
    logger.info(f"Email sent to {recipient}: {subject}")
```

### Job Monitoring (Phase 2)
✅ DO: Use JobMonitor for background job observability
```python
# src/app/core/jobs/monitor.py (lines 10-30)
from src.app.core.job_monitoring import get_job_monitor

monitor = get_job_monitor()
await monitor.track_job_start(
    job_id=ctx["job_id"],
    function_name="process_data",
    tenant_id=tenant_id
)
try:
    # ... job logic ...
    await monitor.track_job_complete(job_id=ctx["job_id"], status="success")
except Exception as e:
    await monitor.track_job_complete(
        job_id=ctx["job_id"], 
        status="failed", 
        error=str(e)
    )
```

### Caching Patterns
✅ DO: Use Redis for caching with proper serialization
```python
# src/app/core/utils/cache.py (lines 1-20)
async def cache_get(key: str) -> Optional[Any]:
    """Get value from Redis cache."""
    try:
        cached = await redis_client.get(key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.error(f"Cache get error: {e}")
    return None
```

### Multi-Tenancy Patterns
✅ DO: Use ContextVar for async-safe tenant context
```python
# src/app/core/tenancy/context.py
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant

_current_tenant: ContextVar["Tenant | None"] = ContextVar("current_tenant", default=None)

def get_tenant_context() -> "Tenant | None":
    return _current_tenant.get()

def set_tenant_context(tenant: "Tenant | None") -> None:
    _current_tenant.set(tenant)

def clear_tenant_context() -> None:
    _current_tenant.set(None)  # Call in finally block
```

✅ DO: Clear tenant context in middleware finally block
```python
# src/app/middleware/tenant.py
async def dispatch(self, request, call_next):
    try:
        # ... validate and set tenant context
        set_tenant_context(tenant)
        response = await call_next(request)
        return response
    finally:
        clear_tenant_context()  # Prevents context leakage
```

✅ DO: Use dependency for tenant access in routes
```python
# src/app/api/dependencies.py
def get_current_tenant() -> Tenant:
    tenant = get_tenant_context()
    if tenant is None:
        raise HTTPException(status_code=500, detail="Tenant context not available")
    return tenant
```

❌ DON'T: Access tenant context without middleware setup
❌ DON'T: Forget to clear context in finally block

### Tenant-Scoped Queries (the ONE seam)
✅ DO: Build every tenant-scoped query through `tenant_scope()` / `scoped_query()`
```python
from src.app.core.db.tenant_crud import scoped_query, tenant_scope

# Plain tenant-scoped select (request path — context wins, fail-closed without one):
stmt = scoped_query(User).where(User.is_active == True)

# Criterion form — composes onto your own select / update / count:
stmt = select(User).options(joinedload(User.tier)).where(User.id == uid)
stmt = stmt.where(tenant_scope(User))

# Explicit tenant (worker path — context var unset, service holds its own tenant_id):
stmt = scoped_query(Role, self.tenant_id).where(Role.id == role_id)
```

❌ DON'T: Hand-roll `select(M).where(M.tenant_id == tid)` in services —
that re-implements the invariant per call site and drifts (see
`inject_tenant_filter` history: M-4 override, B1 sentinel, B6 flag-leak).
The context-vs-argument precedence, NO_TENANT rejection, and the explicit
`allow_unscoped` escape hatch live in `tenant_scope()` — one place.

## Touch Points / Key Files

### Configuration
- Settings: `src/app/core/config.py` - Pydantic settings with validation
- Environment: `.env.local` - Local development secrets

### Security
- Security: `src/app/core/security.py` - JWT, password hashing, token validation
- Token Blacklist: `src/app/core/db/token_blacklist.py` - Redis-based token blacklisting

### Database
- Database: `src/app/core/db/database.py` - Async SQLAlchemy setup
- Models: `src/app/core/db/models.py` - Base model classes
- CRUD Token: `src/app/core/db/crud_token_blacklist.py` - Token blacklist CRUD

### Utilities
- Cache: `src/app/core/utils/cache.py` - Redis caching utilities
- Queue: `src/app/core/utils/queue.py` - Background job queue utilities
- Rate Limit: `src/app/core/utils/rate_limit.py` - Rate limiting logic

### Background Jobs
- Worker Settings: `src/app/core/worker/settings.py` - ARQ worker configuration
- Worker Functions: `src/app/core/worker/functions.py` - Background job definitions

### Exceptions
- HTTP Exceptions: `src/app/core/exceptions/http_exceptions.py` - Custom HTTP exceptions
- Cache Exceptions: `src/app/core/exceptions/cache_exceptions.py` - Cache-related exceptions

### Resilience Patterns
✅ DO: Use database circuit breaker for fault tolerance
```python
# src/app/core/resilience/db_circuit_breaker.py
from src.app.core.resilience import execute_query_with_circuit_breaker

results = await execute_query_with_circuit_breaker(
    db=db,
    query="SELECT * FROM users WHERE tenant_id = :tid",
    params={"tid": tenant_id},
    cache_key=f"users:list:{tenant_id}",
    cache_ttl=300
)
```

✅ DO: Use centralized registry for circuit breaker management
```python
# src/app/core/resilience/registry.py
from src.app.core.resilience import get_registry

registry = get_registry()
breaker = registry.get("database")
if breaker.is_open:
    return get_fallback_response()
```

### Multi-Tenancy
- Tenant Context: `src/app/core/tenancy/context.py` - ContextVar for async-safe tenant isolation
- Tenant Middleware: `src/app/middleware/tenant.py` - X-Tenant-ID header validation
- Tenant Dependency: `src/app/api/dependencies.py` - `get_current_tenant()` dependency

### Observability
- Log Sanitizer: `src/app/core/observability/log_sanitizer.py` - Strips sensitive data from logs
- Structlog Config: `src/app/core/observability/logger.py` - Logging configuration with sanitizer integration
- Job Monitoring: `src/app/core/job_monitoring.py` - Background job status and health tracking

## Observability Module
Location: src/app/core/observability/

### Log Sanitizer
Purpose: Strips sensitive data from logs before output to prevent credential leakage.

Key files:
- `log_sanitizer.py`: LogSanitizer class, SanitizingProcessor

Patterns detected and redacted:
- JWTs (eyJ pattern)
- API keys (api_key, Api-Key, X-API-Key)
- Database URLs (postgresql://, mysql://, mongodb://)
- Email addresses (user@domain.com → u***@domain.com)
- Credit card numbers (Luhn algorithm validation)
- Cloud provider keys (AWS, GCP, Azure patterns)
- Bearer tokens
- Session tokens

Usage patterns:
```python
# Add to structlog processor chain
from src.app.core.observability.log_sanitizer import SanitizingProcessor

shared_processors = [
    SanitizingProcessor(),
    # ... other processors
]

structlog.configure(
    processors=shared_processors + [
        structlog.processors.JSONRenderer()
    ]
)
```

Integration with logger.py:
- SanitizingProcessor added to shared_processors
- Runs before JSON/Console renderer
- Sanitizes all log event dict values recursively
- Preserves log structure while redacting sensitive content

## JIT Index Hints

### Core Component Searches
```bash
# Find configuration settings
rg -n "class.*Settings" src/app/core/

# Find security functions
rg -n "def (create_|verify_|get_)" src/app/core/security.py

# Find database setup
rg -n "async_engine|async_session_maker" src/app/core/db/

# Find exception classes
rg -n "class.*Exception" src/app/core/exceptions/

# Find background jobs
rg -n "async def.*ctx:" src/app/core/worker/
```

### Configuration Searches
```bash
# Find all environment variables
rg -n "^[A-Z_]+:" src/app/core/config.py

# Find secret configuration
rg -n "SECRET|PASSWORD|TOKEN" src/app/core/config.py
```

## Common Gotchas

### Environment Variables
✅ DO: Use `.env.local` for local development secrets
❌ DON'T: Commit secrets to version control

### Database Sessions
✅ DO: Use dependency injection for sessions
❌ DON'T: Create sessions manually in routes

### Async/Await
✅ DO: All database operations must be async
❌ DON'T: Use sync database operations

### JWT Tokens
✅ DO: Include token type in JWT payload
```python
to_encode = {"exp": expire, "sub": str(subject), "type": "access"}
```

❌ DON'T: Create tokens without type verification

### Error Handling
✅ DO: Use custom HTTP exceptions with proper status codes
❌ DON'T: Return generic error responses

## Pre-PR Checks

### Core Testing
```bash
# Test core components
uv run pytest tests/ -k "test_core" -v

# Type check core
uv run mypy src/app/core/

# Lint core
uv run ruff check src/app/core/

# Test database connection
uv run pytest tests/test_database.py -v
```

### Configuration Validation
```bash
# Test configuration loading
uv run python -c "from src.app.core.config import settings; print(settings.model_dump())"

# Test database connection
uv run python -c "from src.app.core.db.database import async_engine; import asyncio; asyncio.run(async_engine.dispose())"
```