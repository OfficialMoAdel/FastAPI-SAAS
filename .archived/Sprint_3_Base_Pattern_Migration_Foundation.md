# Sprint 3: Base Pattern Migration - Foundation

**Priority:** HIGH
**Sprint Goal:** Validate migration approach with low-risk modules (tiers, rate_limits)
**Depends On:** Sprint 2 Foundation
**Quality Gates:** tests-pass, coverage-80%, security-pass
**Duration:** 1 week (5 working days)
**Assigned Teams:** Architecture, Testing

---

## Overview

Sprint 3 establishes the **base pattern migration approach** by migrating low-risk modules first to validate the refactoring strategy before tackling complex core infrastructure modules.

### Strategy
- **Low-Risk First:** Migrate simpler modules (`tiers`, `rate_limits`) to validate approach
- **Validate Pattern:** Ensure `_base` inheritance works correctly
- **Document Lessons:** Capture learnings before Sprint 4 (core modules)
- **Minimal Impact:** These modules have fewer dependencies, reducing risk

### Sprint Tasks Summary

| Task | Module | Files | Tests | Est. Complexity | Duration |
|------|---------|-------|-------|-----------------|----------|
| 3.1 | tiers | 3 modified, 1 deleted | 25 | Low | 1h 40m |
| 3.2 | rate_limits | 3 modified, 1 deleted | 20 | Low | 1h 40m |
| 3.3 | Quality Gates Validation | N/A | All sprint tests | N/A | 0.5 days |

**Total Files:** 6 modified, 2 deleted
**Total Tests:** ~45 existing tests (update only)
**Depends On:** Sprint 0 Foundation complete

---

## Sprint 0 Dependencies (BLOCKING)

Before starting Sprint 3, ensure Sprint 0 Foundation components are complete:

| Component | File | Status | Required For |
|-----------|------|--------|--------------|
| Base Module | `src/app/modules/_base/` | ✅ | All migrations |
| Encryption | `src/app/core/security/encryption.py` | ✅ | Not used in these modules |
| Token Generator | `src/app/core/security/tokens.py` | ✅ | Not used in these modules |
| Test Fixtures | `tests/conftest.py` | ✅ | All tests |

**Verification Command:**
```bash
# Verify Sprint 0 files exist
python -c "from src.app.modules._base import Base; print('✅ Base OK')"
python -c "from src.app.modules._base import BaseService; print('✅ BaseService OK')"
python -c "from src.app.modules._base import BaseCreate, BaseRead, BaseUpdate; print('✅ Schemas OK')"
```

---

## Task Overview

---

## 3.1 Migrate tiers Module

**Priority:** HIGH
**Complexity:** LOW

### Files to Modify

| File | Action | Lines Removed |
|------|--------|---------------|
| `src/app/modules/tiers/models.py` | Update Base import | 0 |
| `src/app/modules/tiers/schemas.py` | Inherit from base schemas | ~20 |
| `src/app/modules/tiers/service.py` | Inherit from BaseService, remove CRUD | ~45 |
| `src/app/modules/tiers/crud.py` | DELETE (no longer needed) | ~50 |
| `tests/unit/modules/test_tiers.py` | Update tests, remove CRUD tests | ~15 |

**Total Lines Saved:** ~130 lines

### Implementation Steps

#### Step 3.1.1: Update Model Import (5 minutes)

**File:** `src/app/modules/tiers/models.py`

**Find:**
```python
from src.app.core.db.database import Base
```

**Replace with:**
```python
from ..._base.models import Base
```

**Verification:**
```bash
uv run mypy src/app/modules/tiers/models.py
```

#### Step 3.1.2: Refactor Schemas (20 minutes)

**File:** `src/app/modules/tiers/schemas.py`

**Before:**
```python
from pydantic import BaseModel
from datetime import datetime
from ...core.schemas import TimestampSchema

class TierBase(BaseModel):
    name: str
    slug: str
    description: str | None = None

class TierCreate(TierBase):
    pass

class TierRead(TierBase):
    id: int
    created_at: datetime
    updated_at: datetime | None = None

class TierUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    model_config = {"from_attributes": True, "populate_by_name": True}
```

**After:**
```python
from ..._base.schemas import BaseCreate, BaseRead, BaseUpdate
from pydantic import EmailStr

class TierCreate(BaseCreate):
    name: str
    slug: str
    description: str | None = None
    # model_config inherited from BaseCreate

class TierRead(BaseRead):
    name: str
    slug: str
    description: str | None = None
    # id, created_at, updated_at inherited from BaseRead

class TierUpdate(BaseUpdate):
    name: str | None = None
    description: str | None = None
    # model_config inherited from BaseUpdate
```

**Benefits:**
- -20 lines of duplicate config
- Consistent validation
- Automatic timestamps

#### Step 3.1.3: Refactor Service (45 minutes)

**File:** `src/app/modules/tiers/service.py`

**Before:**
```python
from sqlalchemy.ext.asyncio import AsyncSession
from fastcrud import FastCRUD, compute_offset, paginated_response
from ...core.exceptions.http_exceptions import DuplicateValueException, NotFoundException
from .crud import crud_tiers
from .schemas import TierCreateInternal, TierResponse

class TierService:
    def __init__(self) -> None:
        self.crud = crud_tiers

    async def create_tier(self, tier: TierCreateInternal, db: AsyncSession) -> dict[str, Any]:
        tier_dict = tier.model_dump()
        tier_exists = await self.crud.exists(db=db, slug=tier_dict["slug"])
        if tier_exists:
            logger.warning("Tier creation failed: duplicate_slug | slug=%s", tier_dict["slug"])
            raise DuplicateValueException("Tier Name not available")
        created_tier = await self.crud.create(db=db, object=tier_dict)
        return created_tier

    async def get_tiers(self, db: AsyncSession, page: int = 1, items_per_page: int = 10) -> dict[str, Any]:
        offset = (page - 1) * items_per_page
        tiers_data = await self.crud.get_multi(db=db, offset=offset, limit=items_per_page)
        return paginated_response(crud_data=tiers_data, page=page, items_per_page=items_per_page)

    async def get_tier_by_id(self, tier_id: int, db: AsyncSession) -> dict[str, Any]:
        tier = await self.crud.get(db=db, id=tier_id)
        if not tier:
            raise NotFoundException(f"Tier with id {tier_id} not found")
        return tier

    async def update_tier(self, tier_id: int, tier_update: dict, db: AsyncSession) -> dict[str, Any]:
        tier = await self.get_tier_by_id(tier_id, db)
        updated_tier = await self.crud.update(db=db, object=tier_update, id=tier_id)
        return updated_tier

    async def delete_tier(self, tier_id: int, db: AsyncSession) -> dict[str, Any]:
        await self.crud.delete(db=db, id=tier_id)
        return {"detail": "Tier deleted successfully"}
```

**After:**
```python
from sqlalchemy.ext.asyncio import AsyncSession
from ..._base.service import BaseService
from ..._base.exceptions import DuplicateError, ModelNotFoundError
from .models import Tier
from .schemas import TierCreate, TierRead, TierUpdate
from ...core.schemas import TimestampSchema

logger = logging.getLogger(__name__)

class TierService(BaseService[Tier]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(Tier, db)

    async def create_tier(self, tier: TierCreate, db: AsyncSession) -> Tier:
        # Custom validation only - CRUD inherited
        tier_dict = tier.model_dump()
        existing = await self.get(slug=tier_dict["slug"])
        if existing:
            logger.warning("Tier creation failed: duplicate_slug | slug=%s", tier_dict["slug"])
            raise DuplicateError("slug", tier.slug)
        # create() inherited from BaseService
        return await self.create(**tier_dict)

    async def get_tiers(
        self,
        page: int = 1,
        per_page: int = 10,
        **kwargs
    ) -> list[Tier]:
        # get_multi() inherited from BaseService
        return await self.get_multi(page=page, per_page=per_page, **kwargs)

    async def get_tier_by_id(self, tier_id: int) -> Tier:
        tier = await self.get(id=tier_id)
        if not tier:
            raise ModelNotFoundError("Tier", tier_id)
        return tier

    async def get_tier_by_slug(self, slug: str) -> Tier:
        tier = await self.get(slug=slug)
        if not tier:
            raise ModelNotFoundError("Tier", slug)
        return tier

    async def update_tier(self, tier_id: int, tier_update: TierUpdate, db: AsyncSession) -> Tier:
        tier = await self.get(id=tier_id)
        if not tier:
            raise ModelNotFoundError("Tier", tier_id)
        # update() inherited from BaseService
        return await self.update(id=tier_id, **tier_update.model_dump(exclude_unset=True))

    async def delete_tier(self, tier_id: int) -> dict:
        # delete() inherited from BaseService
        return await self.delete(id=tier_id)
```

**Benefits:**
- -45 lines of CRUD code removed
- Consistent error handling
- Type-safe generic inheritance

#### Step 3.1.4: Update Router (15 minutes)

**File:** `src/app/modules/tiers/router.py` (if pagination exists)

**Add BasePagination dependency:**
```python
from ..._base.schemas import BasePagination
from fastapi import Depends

@router.get("/tiers", response_model=TierList)
async def list_tiers(
    pagination: BasePagination = Depends(),
    db: AsyncSession = Depends(async_get_db)
):
    tiers = await tier_service.get_tiers(
        page=pagination.page,
        per_page=pagination.per_page,
        db=db
    )
    # ... existing pagination logic
```

#### Step 3.1.5: Delete CRUD File (5 minutes)

**File:** `src/app/modules/tiers/crud.py`

**Action:** Delete file (no longer needed)

```bash
git rm src/app/modules/tiers/crud.py
```

#### Step 3.1.6: Update Tests (30 minutes)

**File:** `tests/unit/modules/test_tiers.py`

**Tests to REMOVE (CRUD is now inherited):**
```python
# DELETE THESE - now inherited from BaseService
class TestTierCRUD:
    def test_get_by_id(self):
        pass  # Inherited

    def test_create_tier(self):
        pass  # Inherited

    def test_update_tier(self):
        pass  # Inherited

    def test_delete_tier(self):
        pass  # Inherited

    def test_get_multi_tiers(self):
        pass  # Inherited
```

**Tests to KEEP (custom business logic):**
```python
class TestTierService:
    async def test_create_tier_duplicate_slug(self):
        # Custom validation logic
        pass

    async def test_get_tier_by_slug(self):
        # Custom get method
        pass
```

#### Step 3.1.7: Run Quality Gates (10 minutes)

```bash
# Type check
uv run mypy src/app/modules/tiers/

# Lint
uv run ruff check src/app/modules/tiers/

# Tests
pytest tests/unit/modules/test_tiers.py -v --cov=src/app/modules/tiers

# Coverage check
pytest --cov=src/app/modules/tiers --cov-report=term-missing
```

### Acceptance Criteria

- [ ] Tier model imports `Base` from `_base`
- [ ] TierCreate/TierRead/TierUpdate inherit from base schemas
- [ ] TierService inherits from `BaseService[Tier]`
- [ ] CRUD methods removed from service (inherited)
- [ ] Custom validation logic (slug check) preserved
- [ ] `crud.py` file deleted
- [ ] All 25 tests pass
- [ ] No mypy errors
- [ ] No ruff lint errors
- [ ] Coverage >= 80%

---

## 3.2 Migrate rate_limits Module

**Priority:** MEDIUM
**Complexity:** LOW

### Files to Modify

| File | Action | Lines Removed |
|------|--------|---------------|
| `src/app/modules/rate_limits/models.py` | Update Base import | 0 |
| `src/app/modules/rate_limits/schemas.py` | Inherit from base schemas | ~18 |
| `src/app/modules/rate_limits/service.py` | Inherit from BaseService, remove CRUD | ~42 |
| `src/app/modules/rate_limits/crud.py` | DELETE (no longer needed) | ~48 |
| `tests/unit/modules/test_rate_limits.py` | Update tests, remove CRUD tests | ~12 |

**Total Lines Saved:** ~120 lines

### Implementation Steps

#### Step 3.2.1: Update Model Import (5 minutes)

**File:** `src/app/modules/rate_limits/models.py`

**Find and replace:**
```python
# BEFORE:
from src.app.core.db.database import Base

# AFTER:
from ..._base.models import Base
```

#### Step 3.2.2: Refactor Schemas (20 minutes)

**File:** `src/app/modules/rate_limits/schemas.py`

**Before:**
```python
from pydantic import BaseModel
from datetime import datetime

class RateLimitCreate(BaseModel):
    endpoint: str
    max_requests: int
    period_seconds: int
    model_config = {"from_attributes": True}

class RateLimitRead(BaseModel):
    id: int
    endpoint: str
    max_requests: int
    period_seconds: int
    created_at: datetime
    updated_at: datetime | None = None
    model_config = {"from_attributes": True}

class RateLimitUpdate(BaseModel):
    max_requests: int | None = None
    period_seconds: int | None = None
    model_config = {"from_attributes": True, "populate_by_name": True}
```

**After:**
```python
from ..._base.schemas import BaseCreate, BaseRead, BaseUpdate
from pydantic import Field

class RateLimitCreate(BaseCreate):
    endpoint: str = Field(..., max_length=255)
    max_requests: int = Field(..., gt=0)
    period_seconds: int = Field(..., gt=0)
    # Config inherited

class RateLimitRead(BaseRead):
    endpoint: str
    max_requests: int
    period_seconds: int
    # id, timestamps inherited

class RateLimitUpdate(BaseUpdate):
    max_requests: int | None = Field(None, gt=0)
    period_seconds: int | None = Field(None, gt=0)
    # Config inherited
```

#### Step 3.2.3: Refactor Service (45 minutes)

**File:** `src/app/modules/rate_limits/service.py`

**Before:**
```python
from sqlalchemy.ext.asyncio import AsyncSession
from fastcrud import FastCRUD
from .crud import crud_rate_limits
from .schemas import RateLimitCreateInternal

class RateLimitService:
    def __init__(self) -> None:
        self.crud = crud_rate_limits

    async def create_rate_limit(self, rate_limit: RateLimitCreateInternal, db: AsyncSession) -> dict:
        return await self.crud.create(db=db, object=rate_limit.model_dump())

    async def get_rate_limits(self, db: AsyncSession, page: int = 1, items_per_page: int = 10) -> dict:
        offset = (page - 1) * items_per_page
        return await self.crud.get_multi(db=db, offset=offset, limit=items_per_page)

    async def get_by_endpoint(self, endpoint: str, db: AsyncSession) -> dict:
        return await self.crud.get(db=db, endpoint=endpoint)

    async def update_rate_limit(self, rate_limit_id: int, rate_limit_update: dict, db: AsyncSession) -> dict:
        return await self.crud.update(db=db, object=rate_limit_update, id=rate_limit_id)

    async def delete_rate_limit(self, rate_limit_id: int, db: AsyncSession) -> dict:
        await self.crud.delete(db=db, id=rate_limit_id)
        return {"detail": "Rate limit deleted"}
```

**After:**
```python
from sqlalchemy.ext.asyncio import AsyncSession
from ..._base.service import BaseService
from .models import RateLimit
from .schemas import RateLimitCreate, RateLimitRead, RateLimitUpdate

class RateLimitService(BaseService[RateLimit]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(RateLimit, db)

    # All CRUD methods inherited - no need to implement
    # Custom methods only (if any)
    async def get_by_endpoint(self, endpoint: str) -> RateLimit:
        rate_limit = await self.get(endpoint=endpoint)
        if not rate_limit:
            raise ModelNotFoundError("RateLimit", endpoint)
        return rate_limit
```

#### Step 3.2.4: Delete CRUD File (5 minutes)

**File:** `src/app/modules/rate_limits/crud.py`

**Action:** Delete file

```bash
git rm src/app/modules/rate_limits/crud.py
```

#### Step 3.2.5: Update Tests (30 minutes)

**File:** `tests/unit/modules/test_rate_limits.py`

**Remove CRUD tests** (inherited):
```python
# DELETE THESE
class TestRateLimitCRUD:
    def test_get_by_id(self):
        pass  # Inherited

    def test_create(self):
        pass  # Inherited

    def test_update(self):
        pass  # Inherited

    def test_delete(self):
        pass  # Inherited
```

**Keep custom logic tests:**
```python
class TestRateLimitService:
    async def test_get_by_endpoint(self):
        # Custom get method
        pass
```

#### Step 3.2.6: Run Quality Gates (10 minutes)

```bash
# Type check
uv run mypy src/app/modules/rate_limits/

# Lint
uv run ruff check src/app/modules/rate_limits/

# Tests
pytest tests/unit/modules/test_rate_limits.py -v --cov=src/app/modules/rate_limits

# Coverage check
pytest --cov=src/app/modules/rate_limits --cov-report=term-missing
```

### Acceptance Criteria

- [ ] RateLimit model imports `Base` from `_base`
- [ ] RateLimitCreate/Read/Update inherit from base schemas
- [ ] RateLimitService inherits from `BaseService[RateLimit]`
- [ ] CRUD methods removed from service (inherited)
- [ ] Custom methods preserved
- [ ] `crud.py` file deleted
- [ ] All 20 tests pass
- [ ] No mypy errors
- [ ] No ruff lint errors
- [ ] Coverage >= 80%

---

## 3.3 Quality Gates Validation

**Duration:** 0.5 days

### Quality Gate Checklist

```bash
# Run all sprint tests
pytest tests/unit/modules/test_tiers.py tests/unit/modules/test_rate_limits.py -v

# Check coverage for both modules
pytest --cov=src/app/modules/tiers src/app/modules/rate_limits --cov-report=html

# Type check both modules
uv run mypy src/app/modules/tiers/ src/app/modules/rate_limits/

# Lint both modules
uv run ruff check src/app/modules/tiers/ src/app/modules/rate_limits/

# Verify no old imports remain
rg -l "from src\.app\.core\.db\.database import Base" src/app/modules/tiers/ src/app/modules/rate_limits/
# Should return 0 results

# Verify base imports are used
rg -l "from src\.app\.modules\._base import Base" src/app/modules/tiers/ src/app/modules/rate_limits/
# Should return results

# Check service inheritance
rg -l "class \w+Service\(BaseService" src/app/modules/tiers/service.py src/app/modules/rate_limits/service.py
# Should show both services inherit

# Check schema inheritance
rg -l "class \w+(Create|Read|Update)\(Base(Create|Read|Update))" src/app/modules/tiers/schemas.py src/app/modules/rate_limits/schemas.py
# Should show all schemas inherit
```

### Module Metrics

| Metric | tiers | rate_limits | Sprint Total |
|---------|-------|-------------|--------------|
| Lines Removed | ~130 | ~120 | **~250** |
| Files Modified | 3 | 3 | **6** |
| Files Deleted | 1 | 1 | **2** |
| Tests Updated | 25 | 20 | **45** |
| Custom Methods Preserved | 3 | 1 | **4** |

### Success Criteria

- [ ] All tier and rate_limit modules use `_base` patterns
- [ ] No direct imports from `core.db.database`
- [ ] All services inherit from `BaseService[T]`
- [ ] All schemas inherit from base classes
- [ ] All CRUD files deleted
- [ ] All tests pass (100%)
- [ ] Coverage >= 80% for both modules
- [ ] No mypy errors
- [ ] No ruff lint errors
- [ ] Document lessons learned

---

## Definition of Done

- [ ] Both modules fully migrated to `_base` pattern
- [ ] All quality gates passed
- [ ] Sprint retrospective document created
- [ ] Lessons learned documented for Sprint 4
- [ ] Code committed and pushed
- [ ] Beads tasks updated/closed

---

## Sprint Output Artifacts

After Sprint 3, following artifacts must be delivered:

| Artifact | Location | Purpose |
|----------|-----------|---------|
| Migrated tiers module | `src/app/modules/tiers/` | Validated base pattern |
| Migrated rate_limits module | `src/app/modules/rate_limits/` | Validated base pattern |
| Updated tests | `tests/unit/modules/test_tiers.py`, `test_rate_limits.py` | Tests updated |
| Sprint retrospective | `IMPLEMENTATION_TASKS/Sprint_3_Retrospective.md` | Lessons learned |
| Migration log | `migration_log.md` | Metrics recorded |

---

## Risk Mitigation

### Risks Identified

| Risk | Probability | Impact | Mitigation |
|-------|-------------|--------|------------|
| **Custom Logic Lost** | Low | Low | Carefully document custom methods before removal |
| **Circular Imports** | Low | Low | Use lazy imports in `__init__.py` |
| **Test Regressions** | Low | Medium | Run baseline tests before migration |
| **Schema Validation Issues** | Low | Low | Test schema validation thoroughly |

### Rollback Plan

If Sprint 3 has issues:

```bash
# Per-module rollback
git checkout HEAD~1 -- src/app/modules/tiers/
git checkout HEAD~1 -- tests/unit/modules/test_tiers.py

git checkout HEAD~1 -- src/app/modules/rate_limits/
git checkout HEAD~1 -- tests/unit/modules/test_rate_limits.py

# Full sprint rollback
git checkout main
git branch -D refactor/sprint-3-foundation
```

---

## Integration Points

1. **Tenants Module** - Depends on Sprint 4 migration
2. **Users Module** - Depends on Sprint 4 migration
3. **RBAC Module** - Depends on Sprint 4 migration
4. **Auth Module** - Depends on Sprint 4 migration

---

## Success Metrics

| Metric | Baseline | Target | How to Measure |
|--------|-----------|--------|-----------------|
| Lines of Code | ~15,000 | ~14,750 | `cloc src/app/modules/` |
| CRUD Duplication | ~650 | ~505 | `rg -l "async def (get|create|update|delete)"` |
| Test Pass Rate | 95% | 100% | `pytest --tb=no` |
| Coverage | TBD | >= 80% | `pytest --cov=src/app/modules/` |
| Module Consistency | 2/13 use base | 4/13 use base | Code review |

---

## References

- [REFACTOR_PLAN_BASE_PATTERN_MIGRATION.md](../REFACTOR_PLAN_BASE_PATTERN_MIGRATION.md)
- [IMPLEMENTATION_WORKFLOW.md Sprint 3](../IMPLEMENTATION_WORKFLOW.md#sprint-3-foundation-validation-week-1)
- [AGENTS.md](../AGENTS.md) - Module development standards
