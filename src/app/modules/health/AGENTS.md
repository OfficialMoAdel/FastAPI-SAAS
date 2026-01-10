# Health Module AGENTS.md

## Package Identity
Simple health check endpoints for monitoring service status. No database models or service layer - uses core infrastructure checks.

## Setup & Run
```bash
# Run tests
uv run pytest tests/contract/test_tenant_middleware.py -v

# Type check
uv run mypy src/app/modules/health/

# Lint
uv run ruff check src/app/modules/health/
```

## Patterns & Conventions

### Code Organization
- Router only (no service.py, no crud.py, no models)
- Use `src/app/core/health.py` for actual health checks
- Return JSON responses with status indicators
- Use FastAPI dependency injection for database/cache checks

### Endpoints
```python
# Pattern from src/app/modules/health/router.py:

@router.get("/health", tags=["health"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """Check overall service health."""
    # Checks: DB, Redis, Queue
    return {"status": "healthy", "checks": {...}}

@router.get("/ready", tags=["health"])
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Check if service is ready to accept traffic."""
    # More strict: All dependencies must be healthy
    return {"status": "ready", "checks": {...}}
```

### Health Check Logic
- Database: Check connection via `db.execute(text("SELECT 1"))`
- Redis: Check via `redis.ping()`
- Queue: Check ARQ worker status

## Touch Points / Key Files

- Router: `src/app/modules/health/router.py` - Health and readiness endpoints
- Core health: `src/app/core/health.py` - Health check utilities

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

## Pre-PR Checks

```bash
uv run mypy src/app/modules/health/ && uv run ruff check src/app/modules/health/ && uv run pytest tests/contract/test_tenant_middleware.py -v
```
