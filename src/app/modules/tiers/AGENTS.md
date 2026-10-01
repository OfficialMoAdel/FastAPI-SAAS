# Tiers Module - Agent Guidance

## Package Identity

Subscription tier management with rate limits, feature flags, and quota configurations. Tiers define tenant capabilities and resource limits.

## Setup & Run

```bash
# Run tier tests
uv run pytest tests/integration/modules/tiers/test_catalog_auth.py -v

# Create default tiers
uv run python src/scripts/create_first_tier.py

# List available tiers (crud.py was removed in the C4 refactor; use the service)
uv run python -c "from src.app.modules.tiers.service import TierService; print('Loaded')"
```

## Patterns & Conventions

### DO: Use tier for resource limits

```python
from src.app.modules.tiers import TierService, resolve_entitlement

# Tiers are accessed through the service (hand-rolled FastCRUD, not the
# BaseService flyweight — TierService composes its own crud handles),
# not a module-global crud instance (crud.py was removed in the C4 refactor).
tier_svc = TierService(db)
tier = await tier_svc.get_tier(tier_name)

# Non-HTTP limits (webhook test calls, email quotas, storage defaults)
# resolve through the entitlement seam — never hard-code them per caller.
entitlement = resolve_entitlement(tier.name)
max_files = entitlement.storage_file_count
```

### DON'T: hardcode tier-specific logic

```python
# BAD - Tier logic in multiple places
if tier_name == "enterprise":
    limit = 10000
elif tier_name == "pro":
    limit = 1000
```

### DO: Link rate limits to tiers

```python
# Rate limits are associated with tiers
# Get tier's rate limits from rate_limits module (via its service)
from src.app.modules.rate_limits.service import RateLimitService
rate_limits = await RateLimitService(db).get_rate_limits_by_tier_id(tier.id)
```

### DO: Use tier enum for comparisons

```python
from src.app.modules.tiers.models import TierName

TIER_HIERARCHY = {
    TierName.FREE: 0,
    TierName.PRO: 1,
    TierName.ENTERPRISE: 2,
}
```

## Module Structure
```
src/app/modules/tiers/
├── __init__.py           # Re-exports Tier, TierName, TierService, schemas, entitlement seam
├── router.py             # /tiers endpoints (served by TierService)
├── service.py            # TierService (canonical tier business logic)
├── entitlement.py        # Tier entitlement seam (TierName, Entitlement, resolve_* )
├── models.py             # Canonical Tier model (rate_limits re-exports)
└── schemas.py            # Canonical Tier schemas (rate_limits re-exports)
```
## Touch Points

- **Tier service**: `modules/tiers/service.py::TierService`
- **Models**: `modules/tiers/models.py::Tier`
- **Rate limits**: `modules/rate_limits/` for tier limits
- **Feature flags**: `modules/feature_flags/` for tier features

## JIT Index Hints

```bash
# Find tier endpoints
rg -n "router\.(get|post|patch|delete)" src/app/modules/tiers/

# Find tier models
rg -n "class Tier\|TierName" src/app/modules/tiers/

# Find tier comparisons
rg -n "tier_name\|tier\." src/app/modules/

# Find tier limits
rg -n "max_users\|rate_limit" src/app/modules/tiers/
```

## Common Gotchas

- **Tier names**: Use enum values for consistency (FREE, PRO, ENTERPRISE)
- **Default tier**: New tenants get default tier assignment
- **Tier changes**: Updating tier affects all associated tenants
- **Quota tracking**: Some limits may require additional tracking
- **Feature gates**: Tiers can gate access to features

## Pre-PR Checks

```bash
uv run pytest tests/integration/modules/tiers/test_catalog_auth.py -v && \
uv run ruff check src/app/modules/tiers/ && \
uv run mypy src/app/modules/tiers/
```
