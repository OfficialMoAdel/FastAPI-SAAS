# Health Module AGENTS.md

## Package Identity
The canonical HTTP health surface: `/health` and `/ready`. The check
implementations live in `src/app/core/health.py` (the canonical check
library, shared with `api/monitoring.py`); this module owns only the HTTP
surface. No database models and no service layer.

## Setup & Run
```bash
# Run tests
uv run pytest tests/unit/modules/health/ -v

# Type check
uv run mypy src/app/modules/health/

# Lint
uv run ruff check src/app/modules/health/
```

## Patterns & Conventions

### Code Organization
- Router only (no service.py, no crud.py, no models)
- Use `src/app/core/health.py` for actual health checks
- Return the declared Pydantic models, not hand-built dicts
- Use FastAPI dependency injection for database/cache checks

### Endpoints
```python
# Pattern from src/app/modules/health/router.py:

@router.get("/health", response_model=HealthCheck)
async def health() -> HealthCheck:
    """Liveness-style check: the process is up and serving."""
    return HealthCheck(
        status="healthy",
        environment=settings.env.ENVIRONMENT.value,
        version=settings.app.APP_VERSION,
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
    )


@router.get("/ready", response_model=ReadyCheck)
async def ready(
    response: Response,
    redis: Annotated[Redis, Depends(async_get_redis)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> ReadyCheck:
    """Readiness: every required dependency must be healthy.

    The 503 is carried on `response.status_code` — returning a
    `JSONResponse` would bypass FastAPI's response-model serialization
    and leave `response_model=ReadyCheck` inert.
    """
    ...
```

### Health Check Logic
- Database: Check connection via `db.execute(text("SELECT 1"))`
- Redis: Check via `redis.ping()`
- Queue: Check ARQ worker status
- The three probes run CONCURRENTLY (`asyncio.gather`), so a `/ready` costs the
  SLOWEST probe rather than the sum of three five-second timeouts. Return order
  stays `(database, redis, queue, events_ready)` because both route consumers
  unpack positionally, and each probe keeps its own timeout and failure result.
  `gather(return_exceptions=True)` is deliberate: an exception that escapes a
  probe's own handler becomes that dependency's `unhealthy` result instead of
  blanking the other two answers (and the page) — a lock failure here must not
  look like a total outage.

## Touch Points / Key Files

- Router: `src/app/modules/health/router.py` - Health and readiness endpoints
- Core health: `src/app/core/health.py` - Health check utilities and
  `collect_readiness_results` (the single collector both readiness
  surfaces use)
- Compatibility alias: `src/app/api/monitoring.py` - `/api/health`,
  `/api/health/live`, `/api/health/ready`

## Two surfaces, two jobs — do not conflate them

| route | job | status code |
|---|---|---|
| `GET /api/v1/ready` | **traffic gate** | 200 only when every dependency is healthy, else 503 |
| `GET /api/health/ready` | gate alias, same policy | same as above |
| `GET /api/health` | **diagnostic** | always 200; verdict is in the body (`ok`/`degraded`/`error`) |

A Kubernetes `readinessProbe` must target `/api/v1/ready`. Pointing it at
`/api/health` keeps a pod with a dead database in rotation, because the 200
there means "I answered", not "I can serve". The prose in
`api/monitoring.py::health_check` says the same thing next to the code.
`tests/unit/api/test_monitoring.py::TestDiagnosticVersusGatingSurface` pins the
asymmetry, so a consistency refactor cannot flatten it.

### Tenant exemption is per-middleware, deliberately

`/api/health*` is exempt from **tenant resolution only** (see
`_TENANT_HEALTH_ALIAS_EXEMPT` in `src/app/middleware/tenant.py`), so an
orchestrator does not need a tenant slug. It is **not** in
`COMMON_EXEMPT_PATHS`, so `ip_filter` and `attack_detection` still apply: these
routes run five live probes and are the most expensive path to serve per byte,
and exempting them everywhere would make the cheapest request the costliest
work. Before the tenant exemption existed the alias answered 400 to any
caller with no `X-Tenant-ID` — i.e. to every orchestrator — while the docs
above called it an orchestrator surface.

## JIT Index Hints

- Find health endpoints: `rg -n "@router\.(get|post)" src/app/modules/health/`
- Find DB checks: `rg -n "db\.execute" src/app/modules/health/`
- Find Redis checks: `rg -n "redis\.ping" src/app/modules/health/`
- Find tests: `find tests -name "*health*"`

## Common Gotchas

- No service layer - keep business logic minimal
- No database models - all checks use dependency injection
- Use `GET` requests only (no side effects)
- Ready endpoint should fail faster than health endpoint (stricter)
- Don't expose sensitive configuration in responses
- Return the pydantic model, not `JSONResponse`: FastAPI skips
  response-model serialization when the handler returns a `Response`
- The probes are patched at `src.app.core.health.*` in tests, because
  `collect_readiness_results` resolves them there

## Pre-PR Checks

```bash
uv run mypy src/app/modules/health/ && uv run ruff check src/app/modules/health/ && uv run pytest tests/unit/modules/health/ -v
```
