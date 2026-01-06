# Base Pattern Migration Plan (Validated Approach)

**Status:** VALIDATED | **Based On:** Spec 006 Implementation
**Created:** 2026-01-06 | **Supersedes:** `REFACTOR_PLAN_BASE_PATTERN_MIGRATION.md`

---

## Executive Summary

This plan reflects the **validated migration approach** from spec `006-migrate-base-pattern`. We successfully migrated `tiers` and `tenants` modules and learned what works vs. what requires more invasive changes.

### Key Insight

**Phase 1 (Safe):** Schema inheritance + exception standardization = immediate value, zero breaking changes
**Phase 2 (Future):** BaseService inheritance = requires router refactoring, deferred

### What We Validated

| Component | Status | Benefit | Breaking Change? |
|-----------|--------|---------|------------------|
| `BaseCreate` inheritance | ✅ Works | -3 lines/schema | No |
| `BaseRead` inheritance | ✅ Works | -6 lines/schema | No |
| `BaseUpdate` inheritance | ✅ Works | -3 lines/schema | No |
| `DuplicateError` | ✅ Works | Structured errors | No |
| `ModelNotFoundError` | ✅ Works | Structured errors | No |
| `BaseService` inheritance | ⏸️ Deferred | -40 lines/service | **Yes** |
| Delete `crud.py` | ⏸️ Deferred | File cleanup | **Yes** |

---

## Phase 1: Schema & Exception Migration (READY NOW)

### Scope

Migrate all modules to use:
- `BaseCreate`, `BaseRead`, `BaseUpdate` for schemas
- `DuplicateError`, `ModelNotFoundError` for exceptions

**No changes to:**
- Service class structure
- Router patterns
- `crud.py` files
- Dependency injection

### Time Estimate

| Module | Complexity | Time |
|--------|------------|------|
| tiers | LOW | 30 min | ✅ Done |
| tenants | MEDIUM | 45 min | ✅ Done |
| rate_limits | LOW | 30 min |
| posts | MEDIUM | 45 min |
| api_keys | MEDIUM | 45 min |
| audit_logs | MEDIUM | 45 min |
| feature_flags | MEDIUM | 45 min |
| notifications | MEDIUM | 45 min |
| webhooks | MEDIUM | 45 min |
| users | HIGH | 60 min |
| auth | HIGH | 60 min |
| rbac | HIGH | 60 min |
| **Total** | | **~9 hours** |

---

## Phase 1 Migration Pattern

### Step 1: Update Schemas (15-20 min per module)

**Before:**
```python
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class TenantCreate(BaseModel):
    name: str
    slug: str
    model_config = ConfigDict(extra="forbid")

class TenantResponse(BaseModel):
    id: int
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None
    model_config = ConfigDict(from_attributes=True)

class TenantUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    model_config = ConfigDict(extra="forbid")
```

**After:**
```python
from datetime import datetime
from pydantic import ConfigDict

from .._base.schemas import BaseCreate, BaseRead, BaseUpdate

class TenantCreate(BaseCreate):
    name: str
    slug: str
    model_config = ConfigDict(extra="forbid")  # Override if needed

class TenantResponse(BaseRead):
    # id, created_at, updated_at inherited
    name: str
    slug: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class TenantUpdate(BaseUpdate):
    name: str | None = None
    is_active: bool | None = None
    model_config = ConfigDict(extra="forbid")
```

**Lines saved:** ~6 per module (id, created_at, updated_at, config duplication)

---

### Step 2: Update Service Exceptions (10-15 min per module)

**Before:**
```python
from ...core.exceptions.http_exceptions import DuplicateValueException, NotFoundException

class TenantService:
    async def create_tenant(self, tenant: TenantCreate, db: AsyncSession):
        if await self.crud.exists(db=db, slug=tenant.slug):
            raise DuplicateValueException(f"Tenant with slug '{tenant.slug}' already exists")
        # ...

    async def get_tenant(self, tenant_id: int, db: AsyncSession):
        tenant = await self.crud.get(db=db, id=tenant_id)
        if tenant is None:
            raise NotFoundException("Tenant not found")
        return tenant
```

**After:**
```python
from .._base.exceptions import DuplicateError, ModelNotFoundError

class TenantService:
    async def create_tenant(self, tenant: TenantCreate, db: AsyncSession):
        if await self.crud.exists(db=db, slug=tenant.slug):
            raise DuplicateError("slug", tenant.slug)
        # ...

    async def get_tenant(self, tenant_id: int, db: AsyncSession):
        tenant = await self.crud.get(db=db, id=tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)
        return tenant
```

**Benefits:**
- Structured error attributes (`field`, `value`, `model_name`, `identifier`)
- Consistent error format across all modules
- Easier to handle in exception handlers

---

### Step 3: Update Tests (10-15 min per module)

**Before:**
```python
from src.app.core.exceptions.http_exceptions import NotFoundException

async def test_get_tenant_not_found(mock_db):
    with pytest.raises(NotFoundException):
        await tenant_service.get_tenant(999, mock_db)
```

**After:**
```python
from src.app.modules._base.exceptions import ModelNotFoundError

async def test_get_tenant_not_found(mock_db):
    with pytest.raises(ModelNotFoundError) as exc_info:
        await tenant_service.get_tenant(999, mock_db)
    
    assert exc_info.value.model_name == "Tenant"
    assert exc_info.value.identifier == 999
```

---

### Step 4: Quality Gates (5 min per module)

```bash
# Type check
uv run mypy src/app/modules/{module}/

# Lint
uv run ruff check src/app/modules/{module}/

# Tests
uv run pytest tests/unit/test_{module}_service.py -v
```

---

## Phase 1 Module Checklist

### Completed

- [x] `tiers` - Schemas + exceptions migrated (commit 2ad1879)
- [x] `tenants` - Schemas + exceptions migrated (commit 2ad1879)

### Remaining

| Module | Schema Migration | Exception Migration | Test Updates | Quality Gates |
|--------|------------------|---------------------|--------------|---------------|
| `rate_limits` | [ ] | [ ] | [ ] | [ ] |
| `posts` | [ ] | [ ] | [ ] | [ ] |
| `api_keys` | [ ] | [ ] | [ ] | [ ] |
| `audit_logs` | [ ] | [ ] | [ ] | [ ] |
| `feature_flags` | [ ] | [ ] | [ ] | [ ] |
| `notifications` | [ ] | [ ] | [ ] | [ ] |
| `webhooks` | [ ] | [ ] | [ ] | [ ] |
| `users` | [ ] | [ ] | [ ] | [ ] |
| `auth` | [ ] | [ ] | [ ] | [ ] |
| `rbac` | [ ] | [ ] | [ ] | [ ] |

---

## Phase 2: BaseService Migration (FUTURE)

### Why Deferred

BaseService requires `db` in `__init__`:

```python
class TierService(BaseService[Tier]):
    def __init__(self, db: AsyncSession):
        super().__init__(Tier, db)
```

**Current pattern (method-level db):**
```python
class TierService:
    async def create_tier(self, tier: TierCreate, db: AsyncSession):
        # db injected per-call by router
```

**Problem:** Every router currently does:
```python
@router.post("/tiers")
async def create_tier(
    tier: TierCreate,
    db: AsyncSession = Depends(async_get_db)
):
    return await tier_service.create_tier(tier, db)
```

**Would need to become:**
```python
def get_tier_service(db: AsyncSession = Depends(async_get_db)) -> TierService:
    return TierService(db)

@router.post("/tiers")
async def create_tier(
    tier: TierCreate,
    service: TierService = Depends(get_tier_service)
):
    return await service.create_tier(tier)
```

This requires updating **every endpoint in every router** - significant refactoring.

### Phase 2 Prerequisites

1. All modules complete Phase 1
2. Create router refactoring plan
3. Establish DI factory pattern standard
4. Update all routers systematically

### Phase 2 Scope

| Task | Files Affected | Estimated Time |
|------|----------------|----------------|
| Create DI factory functions | 13 modules | 2 hours |
| Update all routers | ~50 endpoints | 8 hours |
| Remove duplicate CRUD methods | 13 services | 4 hours |
| Update tests | ~200 tests | 6 hours |
| **Total** | | **~20 hours** |

---

## What NOT to Do

### Don't Change Model Base Import

**The plan said:**
```python
# Change from
from src.app.core.db.database import Base

# To
from ..._base.models import Base
```

**Reality:** `_base.models` just re-exports the same `Base`. This adds no value and creates confusion.

### Don't Delete crud.py Files

**The plan said:** Delete all `crud.py` files after migration.

**Reality:** We keep `crud.py` files. FastCRUD instances are still used directly by services:
```python
from .crud import crud_tiers

class TierService:
    async def create_tier(self, tier: TierCreate, db: AsyncSession):
        return await crud_tiers.create(db=db, object=tier)
```

### Don't Remove Tests for "Inherited" CRUD

**The plan said:** Remove CRUD tests because they're now inherited.

**Reality:** We're not inheriting from BaseService in Phase 1, so all existing tests remain valid.

---

## Available _base Components

### Currently Used (Phase 1)

| Component | Import | Purpose |
|-----------|--------|---------|
| `BaseCreate` | `from .._base.schemas import BaseCreate` | Create schema base |
| `BaseRead` | `from .._base.schemas import BaseRead` | Response schema with id, timestamps |
| `BaseUpdate` | `from .._base.schemas import BaseUpdate` | Partial update schema base |
| `DuplicateError` | `from .._base.exceptions import DuplicateError` | Unique constraint violation |
| `ModelNotFoundError` | `from .._base.exceptions import ModelNotFoundError` | 404 errors |
| `ValidationError` | `from .._base.exceptions import ValidationError` | Validation failures |

### Available for Future Use

| Component | Import | Purpose | When to Use |
|-----------|--------|---------|-------------|
| `BaseService[T]` | `from .._base.service import BaseService` | Generic CRUD service | Phase 2 |
| `get_base_service()` | `from .._base.service import get_base_service` | DI factory | Phase 2 |
| `BaseList[T]` | `from .._base.schemas import BaseList` | Paginated response | When standardizing list endpoints |
| `BasePagination` | `from .._base.schemas import BasePagination` | Page/per_page params | When standardizing pagination |
| `Base` | `from .._base.models import Base` | SQLAlchemy base | Not needed (same as core) |

---

## Sprint Plan

### Sprint 1: Low-Risk Modules (2-3 days)

| Day | Modules | Tasks |
|-----|---------|-------|
| 1 | `rate_limits`, `posts` | Schema + exception migration |
| 2 | `api_keys`, `audit_logs` | Schema + exception migration |
| 3 | Quality gates, commit | Full test suite, fix issues |

### Sprint 2: Feature Modules (2-3 days)

| Day | Modules | Tasks |
|-----|---------|-------|
| 1 | `feature_flags`, `notifications` | Schema + exception migration |
| 2 | `webhooks` | Schema + exception migration |
| 3 | Quality gates, commit | Full test suite, fix issues |

### Sprint 3: Core Modules (3-4 days)

| Day | Modules | Tasks |
|-----|---------|-------|
| 1 | `users` | Schema + exception migration (complex) |
| 2 | `auth` | Schema + exception migration (complex) |
| 3 | `rbac` | Schema + exception migration (complex) |
| 4 | Quality gates, integration tests | Full verification |

---

## Quality Gates

### Per-Module Gates

```bash
# Must pass before marking module complete
uv run mypy src/app/modules/{module}/ --no-error-summary
uv run ruff check src/app/modules/{module}/
uv run pytest tests/unit/test_{module}*.py -v --tb=short
```

### Sprint Gates

```bash
# Must pass before merging sprint
uv run mypy src/app/modules/ --no-error-summary
uv run ruff check src/app/modules/
uv run pytest tests/ -v --tb=short
```

---

## Success Metrics

### Phase 1 Completion Criteria

- [ ] All 13 modules use `BaseCreate`, `BaseRead`, `BaseUpdate`
- [ ] All services use `DuplicateError`, `ModelNotFoundError`
- [ ] All tests updated with new exception types
- [ ] Zero mypy errors
- [ ] Zero ruff errors
- [ ] 95%+ test pass rate
- [ ] No breaking API changes

### Quantified Benefits

| Metric | Before | After Phase 1 | Improvement |
|--------|--------|---------------|-------------|
| Schema boilerplate | ~200 lines | ~120 lines | -40% |
| Exception consistency | Mixed strings | Structured objects | 100% |
| Test assertions | String matching | Attribute checking | Better |

---

## Rollback Procedure

### Per-Module Rollback

```bash
git checkout HEAD~1 -- src/app/modules/{module}/
git checkout HEAD~1 -- tests/unit/test_{module}*.py
```

### Sprint Rollback

```bash
git revert HEAD  # Revert last commit
# Or
git reset --hard origin/main  # Full reset to main
```

---

## References

- **Validated Implementation:** `specs/006-migrate-base-pattern/`
- **Working Examples:** `src/app/modules/tiers/`, `src/app/modules/tenants/`
- **Base Module:** `src/app/modules/_base/`
- **Commit Reference:** `2ad1879` (tiers + tenants migration)

---

## Next Action

```bash
# Continue Phase 1 with next module
git checkout 006-migrate-base-pattern

# Pick next module (recommend: rate_limits - simple)
# Follow Phase 1 Migration Pattern above
```

---

**Document Version:** 2.0 (Validated)
**Status:** READY FOR IMPLEMENTATION
