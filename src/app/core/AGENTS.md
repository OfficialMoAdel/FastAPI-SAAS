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

### Event bus — priority, degradation and payload contracts
✅ DO: publish through the bus and treat the payload as read-only
```python
from src.app.core.events import EventPriority, event_bus

await event_bus.publish("user.created", {"user_id": str(user.id)}, priority=EventPriority.NORMAL)
```
- `CRITICAL` runs the handlers **inline** in the publishing task; `NORMAL`/`LOW` enqueue to ARQ. Nothing in the tree publishes `CRITICAL` today — the inline path is the ARQ-degradation path.
- **Degradation:** when ARQ is unavailable (no queue wired, circuit open, enqueue failed) the bus runs the handlers inline, bounded by the concurrency semaphore and `settings.events.EVENT_HANDLER_TIMEOUT_SECONDS`; a failing handler is DLQ-routed, never dropped. DLQ-and-return was rejected — the DLQ lives in Redis, so it would lose the event exactly when Redis is down.
- **Payload:** every handler of one publish receives the SAME dict and MUST NOT mutate it.
- **Queues:** arq polls one queue per worker. `NORMAL` → `arq:queue`; `LOW` → `arq:queue_low_priority`, consumed by the second worker (`src/app/core/worker/settings.py::LowPriorityWorkerSettings`).
❌ DON'T: read event-bus knobs through the flat `settings.EVENT_BUS_*` form — use `settings.events.*`. The flat form resolves only through the config layer's delegation `__getattr__`, which no static analyser (and no flat-access ratchet) can see.

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

**The HALF_OPEN probe slot is a lease, and it is never left claimed.** A
probe is claimed before a recovery attempt and released when it reports its
outcome. A CANCELLED probe (`asyncio.CancelledError` is a `BaseException`, so
an `except Exception` arm never sees it) used to leave the slot claimed for
the life of the process — every later HALF_OPEN caller was then rejected,
which for the DB breaker means 503 for ALL traffic. Three things close that:
the decorator and `db_circuit_guard` release the slot on a `BaseException`;
the claim is a lease (a claim older than the recovery window is stale and is
reclaimed); and `CircuitBreaker.probe_gate()` releases it on every exit path.

**Gate on the single-flight probe, not on `is_closed`.** `is_closed` is True
during the virtual HALF_OPEN, so every caller that gates on it passes at
once and floods the dependency that is recovering. `allow_single_flight()`
(or `probe_gate()`) admits every call while CLOSED, none while OPEN, and
exactly ONE while HALF_OPEN. Use it for the SIDE-EFFECTING callers — an
outbound send or a webhook POST. Do NOT use it for a read-only cache lookup
whose "unavailable" answer is "revoked" (the token-blacklist path): denying
a probe there would turn a recovery window into a mass-revocation window.

### Cache & distributed locks
The cache is a read-ahead optimisation and a best-effort write, so the seam
FAILS OPEN: `get` reads as a miss, `set`/`delete`/`exists` degrade quietly.
A Redis outage must never turn a succeeded business operation into a 500.

The ONE exception is the claim (`set_nx`): "the key was taken" and "the
claim could not be made" are different answers, and at least one production
caller (the billing webhook lock) falls back to the DB idempotency check
when the claim ERRORS. Collapsing an error into `False` would silently drop
that fallback — and a Stripe event could then be processed twice.

A distributed lock carries an OWNERSHIP TOKEN and is released with an
ownership check (`cache_client.release_lock_if_owned`, the
`lock_release_if_owned` Lua the repo already owns). A blind `delete` lets a
slow holder whose lock already expired delete the lock a DIFFERENT worker
acquired in the meantime. There is deliberately no blind-delete fallback: a
lock that cannot be released expires on its own TTL.

### Tenancy & RLS
Tenant context lives in contextvars (`core/tenancy/context.py` — bind with `bound_tenant`/`bind_tenant`, release with `unbind_tenant`); Postgres RLS scoping via `core/db/rls.py` (`set_rls_context`, `rls_context`, `bypass_rls`). Background jobs use `core/worker/rls_context.py`.

**Resolution** (`core/tenancy/resolver.py`): slug → L1 in-process cache → L2 Redis → L3 DB, under a per-slug stampede lock. A corrupt L1 entry (an id that is not a UUID) is **dropped at the point of detection** and the lookup falls through to L2/L3 — it never 500s a slug until the TTL ages the entry out. A negative L1 entry (the tenant is known to be missing) short-circuits: the database is not consulted for it.

**Fail-closed accessors:** `require_active_tenant()` and `RequestContext.require_tenant()` raise `TenantContextError` for a missing OR inactive tenant. Use them on tenant-scoped paths; they are not used on exempt paths — the exempt-path modules (tenants, app_configuration, health, the Stripe receivers, the admin mount) deliberately use unscoped seams, and the billing webhook handlers resolve the tenant from the payload rather than the context. The `NO_TENANT` sentinel (bound on exempt paths) is falsy and inactive: it makes `require_tenant_match`/`resolve_tenant_id` skip, and it makes the fail-closed accessors raise.

**RLS GUCs on a pooled connection:** every mutation inside `core/db/rls.py` marks the connection dirty *before* mutating, so the checkout listener re-asserts instead of skipping. Code outside that module that changes the GUCs with raw SQL must call `mark_rls_dirty(connection)` — otherwise the checkout skip can carry a stale scope into the next request.

**Model columns:** there is no tenant mixin or shared column factory — a declarative mixin cannot add a column to a `MappedAsDataclass` model. Tenant-scoped models declare their `tenant_id` column directly, with the `tenants.id` foreign key, the index and the nullability visible in the model that owns them.

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
