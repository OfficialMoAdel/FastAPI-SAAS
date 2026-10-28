# Rate Limits Module - Agent Guidance

## Package Identity

Tier-based rate limiting for API requests. Integrates with the Tier module to enforce per-tenant rate limits based on subscription level.

## Setup & Run

```bash
# Run rate limit tests
uv run pytest tests/unit/modules/rate_limits/test_rate_limit_caching.py -v

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

### DO: Single-flight the tier-policy read through the cache seam

`get_rate_limits_by_tier_id` is the hot read behind every rate-limit decision,
so its cold miss is single-flighted:

```python
await cache_client.read_through(
    cache_key, _fetch, ttl=_TIER_POLICY_CACHE_TTL,
    policy=ReadThroughPolicy(
        give_up_seconds=_TIER_POLICY_GIVE_UP_SECONDS,
        lock_ttl_seconds=_TIER_POLICY_LOCK_TTL_SECONDS,
    ),
)
```

`src/app/core/cache/read_through.py` is the ONE read-through seam (also used by
the dashboard). Two budgets are passed separately on purpose: the claim TTL and
the waiter's patience. The seam rejects a policy where the waiter outlives the
claim, because that waiter then computes alongside the next claimer. The retired
`stampede.get_with_lock` used a single `lock_timeout` for both.

### DON'T: Hardcode rate limits

```python
# BAD - Hardcoded limits
MAX_REQUESTS = 1000  # Don't do this
```

### DO: Apply rate limit middleware

```python
from src.app.core.security.budgets import Identity, SecurityAction, check
from src.app.shared.exceptions import RateLimitExceededError

# General path limits are enforced by the ASGI RateLimitMiddleware — this
# module only manages tier path policies via the rate-limits API.
# Endpoint brute-force budgets cross the security budget seam, which owns
# the identity keying, the leg set, the fail-closed policy and the refusal
# (check() raises on the first limited leg; probe() returns a Verdict):
await check(SecurityAction.AUTH, Identity(ip=client_ip))
# check() raises RateLimitExceededError itself. For a transport that must
# attach X-RateLimit-* headers, use probe() and read the Verdict:
#   verdict = await probe(SecurityAction.AUTH, Identity(ip=client_ip))
# (FastAPI Depends adapters that must attach Retry-After headers are the
# sanctioned exception: see ai/middleware.py.)
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
uv run pytest tests/unit/modules/rate_limits/test_rate_limit_caching.py -v && \
uv run ruff check src/app/modules/rate_limits/
```
