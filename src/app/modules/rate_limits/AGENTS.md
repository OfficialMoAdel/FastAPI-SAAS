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
from src.app.modules.rate_limits.service import RateLimitService

# Rate limits are accessed through the service (BaseService flyweight CRUD),
# not a module-global crud instance (crud.py was removed in the C4 refactor).
svc = RateLimitService(db)
rate_limits = await svc.get_rate_limits_by_tier_id(tier_id)
```

### DON'T: Hardcode rate limits

```python
# BAD - Hardcoded limits
MAX_REQUESTS = 1000  # Don't do this
```

### DO: Apply rate limit middleware

```python
from src.app.shared.exceptions import RateLimitExceededError

# In middleware - rate limits are checked per request.
# Raise the domain error — the central table maps it to 429.
# (FastAPI Depends dependencies that must attach Retry-After headers
# are the sanctioned exception: see ai/middleware.py.)
if await rate_limiter.is_rate_limited(tenant_id, endpoint):
    raise RateLimitExceededError("Rate limit exceeded", retry_after=60)
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
├── __init__.py           # Exports RateLimit and Tier models
├── router.py             # /tiers/{tier_name}/rate-limits endpoints
├── models.py             # RateLimit and Tier models
├── schemas.py            # RateLimit and Tier schemas
└── service.py            # RateLimitService (composes TierService for tier ops)

## Touch Points

- **CRUD operations**: `modules/rate_limits/service.py::RateLimitService` (rate limits & tiers)
- **Models**: `modules/rate_limits/models.py::{RateLimit, Tier}`
- **Tier integration**: Tier model/schemas/service live in `modules/tiers/`; this module composes TierService for tier-shaped operations

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
