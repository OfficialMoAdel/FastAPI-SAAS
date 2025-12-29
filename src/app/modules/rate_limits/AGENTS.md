# Rate Limits Module - Agent Guidance

## Package Identity

Tier-based rate limiting for API requests. Integrates with the Tier module to enforce per-tenant rate limits based on subscription level.

## Setup & Run

```bash
# Run rate limit tests
uv run pytest tests/integration/test_rate_limit_api.py -v

# View rate limit configuration
rg -n "RateLimit" src/app/modules/rate_limits/
```

## Patterns & Conventions

### DO: Use tier-based rate limits

```python
from src.app.modules.rate_limits.crud import crud_rate_limits

# Get rate limits for a tier
rate_limits = await crud_rate_limits.get_multi(
    db=db,
    tenant_id=tenant_id,
    tier_name=tier_name,
)
```

### DON'T: Hardcode rate limits

```python
# BAD - Hardcoded limits
MAX_REQUESTS = 1000  # Don't do this
```

### DO: Apply rate limit middleware

```python
# In middleware - rate limits are checked per request
if await rate_limiter.is_rate_limited(tenant_id, endpoint):
    raise HTTPException(status_code=429, detail="Rate limit exceeded")
```

### DO: Track rate limit headers

```python
# Return rate limit info in headers
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 3600
```

## Module Structure

```
src/app/modules/rate_limits/
├── __init__.py           # Exports crud_rate_limits
├── router.py             # /rate_limits endpoints
├── crud.py               # FastCRUD operations
├── models.py             # RateLimit model
├── schemas.py            # Pydantic schemas
└── service.py            # RateLimitService
```

## Touch Points

- **CRUD operations**: `modules/rate_limits/crud.py`
- **Models**: `modules/rate_limits/models.py::RateLimit`
- **Tier integration**: `modules/tiers/` for limit lookup
- **Middleware**: `src/app/middleware/rate_limit.py`

## JIT Index Hints

```bash
# Find rate limit endpoints
rg -n "router\.(get|post|patch|delete)" src/app/modules/rate_limits/

# Find rate limit models
rg -n "class RateLimit" src/app/modules/rate_limits/

# Find rate limit checks
rg -n "rate_limit" src/app/middleware/

# Find tier rate limits
rg -n "tier.*rate_limit\|rate_limit.*tier" src/app/modules/
```

## Common Gotchas

- **Tier dependency**: Rate limits require associated Tier to exist
- **Granularity**: Limits can be per endpoint, per tenant, or global
- **Reset time**: Rate limits reset based on time window configuration
- **429 handling**: Return proper HTTP 429 with retry-after header

## Pre-PR Checks

```bash
uv run pytest tests/integration/test_rate_limit_api.py -v && \
uv run ruff check src/app/modules/rate_limits/
```
