# Tiers Module - Agent Guidance

## Package Identity

Subscription tier management with rate limits, feature flags, and quota configurations. Tiers define tenant capabilities and resource limits.

## Setup & Run

```bash
# Run tier tests
uv run pytest tests/integration/test_tier_api.py -v

# Create default tiers
uv run python scripts/create_first_tier.py

# List available tiers
uv run python -c "from src.app.modules.tiers.crud import crud_tiers; print('Loaded')"
```

## Patterns & Conventions

### DO: Use tier for resource limits

```python
from src.app.modules.tiers.crud import crud_tiers

tier = await crud_tiers.get(db=db, name=tier_name)
max_users = tier.max_users
rate_limit = tier.rate_limit
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
# Get tier's rate limits from rate_limits module
rate_limits = await crud_rate_limits.get_multi(
    db=db,
    tier_name=tier.name,
)
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
├── __init__.py           # Exports tier_service, TierName
├── router.py             # /tiers endpoints
├── service.py            # TierService class
├── models.py             # Tier model, TierName enum
├── schemas.py            # Pydantic schemas
└── crud.py               # FastCRUD operations
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
uv run pytest tests/integration/test_tier_api.py -v && \
uv run ruff check src/app/modules/tiers/ && \
uv run mypy src/app/modules/tiers/
```
