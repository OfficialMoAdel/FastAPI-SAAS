# FastAPI SaaS Core Infrastructure - Agent Guidance

## Package Identity

Shared infrastructure layer: grouped settings, async database, security services, cache, event bus, resilience, tenancy, RBAC, background worker, and observability. Everything here is framework-adjacent and reusable by all modules.

## Structure

```
src/app/core/
├── setup.py            # Reusable infrastructure startup/teardown hooks
├── runtime.py          # Generic StartupPhase/compose_phases lifecycle mechanism
├── config/             # Grouped Pydantic Settings namespaces
│   ├── __init__.py         # Exports `settings`, `Settings`, `EnvironmentOption`
│   ├── app.py, auth.py, cache.py, crypt.py, database.py, environment.py,
│   ├── integrations.py, performance.py, security.py, services.py
├── db/                 # Engine, sessions, RLS, token blacklist DB  → [db/AGENTS.md](db/AGENTS.md)
├── security/           # JWT, passwords, blacklist cache, ban, encryption → [security/AGENTS.md](security/AGENTS.md)
├── cache/              # Redis cache client, deps, invalidation channel, warmer
├── events/             # Internal event bus, public registration seam, + DLQ
├── exceptions/         # cache_exceptions.py (CacheException tree, still live)
├── monitoring/         # Metrics collection
├── observability/      # structlog config, log sanitizer
├── rbac/               # Permission checking (requires_permission)
├── resilience/         # Circuit breakers (db_circuit_guard), retry policies
├── tenancy/            # Tenant context (contextvars) helpers
├── utils/              # cache (client slots), queue, and other pure utilities
├── worker/             # ARQ worker: main.py, functions.py, rls_context.py, settings.py
├── env_validator.py    # EnvironmentValidator (refuses bad prod env)
├── errors.py           # EXCEPTION_HANDLERS table + setup_exception_handlers(app)
├── health.py           # Health check helpers
├── job_monitoring.py   # Background job status/health tracking
├── json_utils.py       # ORJSON helpers
├── logger.py           # get_logger / setup_logging (structlog + sanitizer)
├── middleware_exempt.py# is_path_exempt — single source of exempt paths
├── pagination.py       # Keyset/offset pagination primitives
├── redis_provider.py   # THE Redis seam: get_redis / get_redis_pool / close_redis_pool
└── schemas.py          # Shared core schemas
```

Note: the app-level event bus entry point is `src/app/events.py` (initializes `core/events`).

## Setup & Run

```bash
# Test core
uv run pytest tests/unit/core/ -v

# Type check core (strict — disallow_untyped_defs is ON for core.*)
uv run mypy src/app/core/
```

## Patterns & Conventions

### Configuration — grouped namespaces
✅ DO: Access settings through the namespace that owns it
```python
from src.app.core.config import settings

settings.app.APP_NAME
settings.env.ENVIRONMENT
settings.cors.CORS_ORIGINS
```
❌ DON'T: Hardcode configuration values or add new flat fields — add a field to the matching namespace file in `core/config/`.

### Redis — the ONE seam
✅ DO: Acquire the Redis client through the provider or DI
```python
from src.app.core.redis_provider import get_redis            # call-time
redis: Annotated[Redis, Depends(async_get_redis)]            # router (from core.utils.cache)
```
❌ DON'T: Construct `redis.Redis(...)` directly in services or read module-level globals.

### Error handling — central translation
Domain exceptions derive from `AppException` (`src/app/shared/exceptions.py`). `core/errors.py` builds the `EXCEPTION_HANDLERS` table (`build_exception_table` + `_make_table_handler`) and is registered by `src/app/composition.py::create_application`. Services stay HTTP-free; routers don't catch anything.
❌ DON'T: Raise raw `HTTPException(409, ...)` from a service, or re-create `_base/exceptions` (deleted). There is no HTTP-shaped exception tree to reach for: the old FastCRUD-derived shim under `core/exceptions/` was deleted once its last importer migrated, and every error — including router-level ones — is a `src/app/shared/exceptions` domain error translated by `core/errors.py`. A 409 is `DuplicateError`, a 404 is `ModelNotFoundError`. `core/exceptions/cache_exceptions.py` survives for the Redis `CacheException` tree, which is a separate concern.

### Resilience
Wrap DB operations with the circuit guard:
```python
from src.app.core.resilience import db_circuit_guard
async with db_circuit_guard():
    result = await db.execute(stmt)
```

### Tenancy & RLS
Tenant context lives in contextvars (`core/tenancy/context.py` — always `clear_tenant_context()` in `finally`); Postgres RLS scoping via `core/db/rls.py` (`set_rls_context`, `rls_context`, `bypass_rls`). Background jobs use `core/worker/rls_context.py`.

## Touch Points

- Composition roots: `main.py` + `src/app/composition.py` for web wiring;
  `core/setup.py` for reusable infrastructure hooks; `core/runtime.py` for the
  phase mechanism
- Error translation: `core/errors.py::setup_exception_handlers`
- Redis: `core/redis_provider.py`
- Logger: `core/logger.py::get_logger` + `core/observability/log_sanitizer.py` (redacts JWTs, API keys, DB URLs, emails, cards, bearer/session tokens)
- Exempt paths: `core/middleware_exempt.py::is_path_exempt`
- Pagination: `core/pagination.py` (keyset pagination is the default for list endpoints)
- Worker: `core/worker/main.py` + `functions.py` (ARQ tasks)

## JIT Index Hints

```bash
# Find settings fields
rg -n "^[A-Z_]+:" src/app/core/config/

# Find exception classes
rg -n "class .*Error|class .*Exception" src/app/core/ src/app/shared/

# Find Redis usage
rg -n "get_redis|async_get_redis" src/app/

# Find circuit guard usage
rg -n "db_circuit_guard" src/app/

# Find ARQ job functions
rg -n "async def .*ctx" src/app/core/worker/functions.py
```

## Common Gotchas

- **Strict typing**: `core.*` runs with `disallow_untyped_defs = true` (mypy override) — type every function.
- **Sessions**: always via DI (`async_get_db`); never construct sessions in routes.
- **Async only**: no blocking I/O; bcrypt/DB hashing already run in executors.
- **UTC**: use `datetime.now(UTC)`, never `datetime.utcnow()`.
- **RLS bypass** requires an explicit reason and is audited — reserve for admin ops.
- **create_all gate**: outside `local`, startup `create_all` requires explicit `settings.app.CREATE_TABLES_ON_STARTUP=true` (tables created this way lack RLS policies).
- **L1 invalidation fan-out**: `L1Cache.invalidate_many()` drops EVERY key locally BEFORE it publishes any of them, then fans the whole batch out through ONE Redis pipeline (`InvalidationChannel.publish_many`). Two invariants make it safe: pub/sub does not echo to the publisher, so publishing before dropping would leave this process serving the very entry it just invalidated; and the trailing `execute()` must stay INSIDE the `async with`, because redis-py 5.3.1's `Pipeline.__aexit__` awaits `reset()`, which clears the command stack — a dedented `execute()` sends an EMPTY batch and silently invalidates nothing. Both are pinned by `tests/benchmarks/bench_cache_invalidation.py`.
- **L1 registry is a closed world**: `register_l1_channel` populates a process-global registry that `tests/unit/core/cache/test_l1_registry_contract.py` holds to an exact expected set. A new OWNER channel must be added to that set deliberately; a throwaway seam built in a test must not register at all (stub `l1.register_l1_channel`, as `tests/unit/core/cache/test_l1.py` does) or it breaks the contract test for every later test in the session.

## Pre-PR Checks

```bash
uv run mypy src/app/core/ && uv run ruff check src/app/core/ && uv run pytest tests/unit/core/ -v
```
