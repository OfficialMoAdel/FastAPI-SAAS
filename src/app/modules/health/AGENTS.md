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

## Touch Points / Key Files

- Router: `src/app/modules/health/router.py` - Health and readiness endpoints
- Core health: `src/app/core/health.py` - Health check utilities and
  `collect_readiness_results` (the single collector both readiness
  surfaces use)
- Compatibility alias: `src/app/api/monitoring.py` - `/api/health`,
  `/api/health/live`, `/api/health/ready`

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
