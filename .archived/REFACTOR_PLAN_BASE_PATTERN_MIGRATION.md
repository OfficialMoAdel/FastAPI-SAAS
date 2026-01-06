# Refactoring Plan: Migrate Existing Modules to `_base` Pattern

**Strategy:** Systematic Migration | **Scope:** All 13 Existing Modules
**Priority:** HIGH | **Estimated Duration:** 3-4 Sprints (3-4 weeks)
**Generated:** 2026-01-04

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current State Analysis](#current-state-analysis)
3. [Migration Strategy](#migration-strategy)
4. [Module Migration Matrix](#module-migration-matrix)
5. [Sprint Plan](#sprint-plan)
6. [Quality Gates](#quality-gates)
7. [Testing Strategy](#testing-strategy)
8. [Rollback Procedures](#rollback-procedures)
9. [Success Metrics](#success-metrics)

---

## Executive Summary

### Problem
The `_base` module exists as an architectural foundation but **is not used** by any existing modules. This creates:
- **Code duplication:** ~1,150 lines of repeated CRUD logic
- **Inconsistency:** Each module implements patterns differently
- **Maintenance burden:** Changes must be replicated across all modules
- **Documentation mismatch:** Sprint plans prescribe `_base` usage that doesn't exist

### Solution
Systematically migrate all 13 existing modules to use `_base` patterns:
1. **Models:** Import `Base` from `_base` instead of `core.db.database`
2. **Services:** Inherit from `BaseService[T]` instead of duplicating CRUD
3. **Schemas:** Inherit from `BaseCreate`, `BaseRead`, `BaseUpdate` instead of manual definition
4. **Exceptions:** Use standardized `ModelNotFoundError`, `ValidationError`, `DuplicateError`
5. **Pagination:** Use `BaseList[T]` and `BasePagination` for list endpoints

### Expected Benefits
| Metric | Current | After Migration | Improvement |
|---------|----------|-------------------|-------------|
| **Lines of Code** | ~15,000 | ~13,850 | **-1,150 (8% reduction)** |
| **CRUD Boilerplate** | ~650 lines | 0 | **-100%** |
| **Schema Boilerplate** | ~200 lines | 0 | **-100%** |
| **Test Boilerplate** | ~300 lines | 0 | **-100%** |
| **Module Consistency** | Low | High | **Standardized** |
| **Onboarding Time** | 2-3 days | 0.5 day | **-80%** |

---

## Current State Analysis

### Modules That Need Migration

| Module | Service File | Current Pattern | Status | Priority |
|---------|--------------|----------------|--------|----------|
| `users` | `users/service.py` | Custom CRUD | ❌ No Base | HIGH |
| `tenants` | `tenants/service.py` | Custom CRUD | ❌ No Base | HIGH |
| `posts` | `posts/service.py` | Custom CRUD | ❌ No Base | MEDIUM |
| `auth` | `auth/service.py` | Custom CRUD | ❌ No Base | HIGH |
| `api_keys` | `api_keys/service.py` | Custom CRUD | ❌ No Base | MEDIUM |
| `audit_logs` | `audit_logs/service.py` | Custom CRUD | ❌ No Base | MEDIUM |
| `feature_flags` | `feature_flags/service.py` | Custom CRUD | ❌ No Base | MEDIUM |
| `notifications` | `notifications/service.py` | Custom CRUD | ❌ No Base | MEDIUM |
| `webhooks` | `webhooks/service.py` | Custom CRUD | ❌ No Base | MEDIUM |
| `rbac` | `rbac/service.py` | Custom CRUD | ❌ No Base | HIGH |
| `rate_limits` | `rate_limits/service.py` | Custom CRUD | ❌ No Base | LOW |
| `tiers` | `tiers/service.py` | Custom CRUD | ❌ No Base | MEDIUM |

### Current Anti-Patterns Found

#### 1. Model Imports
```python
# ❌ Current (All modules)
from src.app.core.db.database import Base
class User(Base): ...

# ❌ Direct FastCRUD (Bypasses base pattern)
from fastcrud import FastCRUD
class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.crud = FastCRUD(User)
```

#### 2. Service CRUD Duplication
Every service file contains:
```python
# ❌ Duplicated in every service
async def get(self, id: Any) -> T | None:
    return await self.crud.get(db=self.db, id=id)

async def create(self, **kwargs) -> T:
    return await self.crud.create(db=self.db, object=kwargs)

async def update(self, id: Any, **kwargs) -> T | None:
    return await self.crud.update(db=self.db, object=kwargs, id=id)

async def delete(self, id: Any) -> dict:
    return await self.crud.delete(db=self.db, id=id)

async def get_multi(self, page: int = 1, per_page: int = 20, **kwargs) -> list[T]:
    offset = (page - 1) * per_page
    return await self.crud.get_multi(db=self.db, offset=offset, limit=per_page, **kwargs)
```

#### 3. Schema Definition Patterns
```python
# ❌ Current (No inheritance, manual config)
class UserCreate(BaseModel):
    email: EmailStr
    username: str
    # model_config repeated per schema

class UserRead(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime | None = None
    # Timestamps repeated per schema

class UserUpdate(BaseModel):
    email: str | None = None
    username: str | None = None
    model_config = {"from_attributes": True, "populate_by_name": True}
```

---

## Migration Strategy

### Three-Phase Approach

#### Phase 1: Low-Risk Modules (Week 1)
**Modules:** `tiers`, `rate_limits`
**Why:** Smaller scope, fewer dependencies, less complex business logic
**Goal:** Establish migration patterns, validate approach

#### Phase 2: Core Modules (Week 2-3)
**Modules:** `tenants`, `users`, `auth`, `rbac`
**Why:** Foundation modules, highest usage, critical dependencies
**Goal:** Migrate core infrastructure, enable other migrations

#### Phase 3: Feature Modules (Week 4)
**Modules:** `posts`, `api_keys`, `audit_logs`, `feature_flags`, `notifications`, `webhooks`
**Why:** Feature-specific, depend on core modules
**Goal:** Complete migration, ensure all features adopt pattern

### Migration Pattern per Module

For each module, execute these steps in order:

1. **Update Model Import** (5 minutes)
   ```bash
   # Find all instances
   rg -l "from src.app.core.db.database import Base" src/app/modules/{module}/

   # Replace with
   rg -l "from src.app.modules._base import Base" src/app/modules/{module}/
   ```

2. **Refactor Service to Inherit BaseService** (30-60 minutes)
   - Remove duplicate CRUD methods
   - Add `BaseService[{Model}]` inheritance
   - Keep only custom business logic methods
   - Update `__init__` to call `super().__init__`

3. **Update Schemas to Inherit Base Classes** (15-30 minutes)
   - Make `*Create` inherit from `BaseCreate`
   - Make `*Read` inherit from `BaseRead`
   - Make `*Update` inherit from `BaseUpdate`
   - Remove duplicate `id`, `created_at`, `updated_at` fields
   - Remove duplicate `model_config` lines

4. **Replace Custom Exceptions** (15-30 minutes)
   - Find `raise DuplicateValueException(...)` → `raise DuplicateError(...)`
   - Find `raise NotFoundException(...)` → `raise ModelNotFoundError(...)`
   - Update imports

5. **Update Pagination** (20-40 minutes)
   - Create `*List` schema inheriting `BaseList[{Model}]`
   - Add `BasePagination` dependency to router
   - Update list endpoints to use pagination

6. **Write Tests** (30-60 minutes)
   - Update existing tests to import from `_base`
   - Remove CRUD test duplication (use inherited methods)
   - Verify business logic tests still pass

7. **Run Quality Gates** (10 minutes)
   ```bash
   uv run mypy src/app/modules/{module}/
   uv run ruff check src/app/modules/{module}/
   pytest tests/unit/modules/test_{module}.py -v
   ```

---

## Module Migration Matrix

### Sprint 1: Foundation (Week 1)

#### Module 1.1: tiers (LOW COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `tiers/models.py` | Change Base import to `from ..._base import Base` | 5m |
| `tiers/schemas.py` | Inherit schemas from base classes | 20m |
| `tiers/service.py` | Inherit from `BaseService[Tier]`, remove CRUD | 45m |
| `tests/unit/modules/test_tiers.py` | Update tests | 30m |
| **Total** | **~1h 40m** | |

**Acceptance:**
- [ ] TierService inherits from `BaseService[Tier]`
- [ ] TierCreate/TierRead/TierUpdate inherit from base schemas
- [ ] All tests pass (existing CRUD tests should be removed)
- [ ] No linter errors

#### Module 1.2: rate_limits (LOW COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `rate_limits/models.py` | Change Base import | 5m |
| `rate_limits/schemas.py` | Inherit schemas from base classes | 20m |
| `rate_limits/service.py` | Inherit from `BaseService[RateLimit]`, remove CRUD | 45m |
| `tests/unit/modules/test_rate_limits.py` | Update tests | 30m |
| **Total** | **~1h 40m** | |

**Acceptance:**
- [ ] RateLimitService inherits from `BaseService[RateLimit]`
- [ ] Schemas inherit from base classes
- [ ] All tests pass
- [ ] No linter errors

---

### Sprint 2: Core Modules (Week 2-3)

#### Module 2.1: tenants (HIGH COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `tenants/models.py` | Change Base import | 5m |
| `tenants/schemas.py` | Inherit schemas from base classes | 25m |
| `tenants/service.py` | Inherit from `BaseService[Tenant]`, remove CRUD | 60m |
| `tests/unit/modules/test_tenants.py` | Update tests | 40m |
| **Total** | **~2h 10m** | |

**Custom Logic to Preserve:**
- `create_tenant` validation logic (slug existence check)
- Logger usage for duplicate detection
- Keep these in service, remove only CRUD methods

**Acceptance:**
- [ ] TenantService inherits from `BaseService[Tenant]`
- [ ] TenantCreate/TenantRead/TenantUpdate inherit from base schemas
- [ ] Custom validation logic preserved
- [ ] All tests pass
- [ ] No linter errors

#### Module 2.2: users (HIGH COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `users/models.py` | Change Base import | 5m |
| `users/schemas.py` | Inherit schemas from base classes | 25m |
| `users/service.py` | Inherit from `BaseService[User]`, remove CRUD | 60m |
| `tests/unit/modules/test_users.py` | Update tests | 40m |
| **Total** | **~2h 10m** | |

**Custom Logic to Preserve:**
- `create_user` password hashing and duplicate detection
- `get_user` cross-service access (via UserService)
- `update_password` password hashing
- Keep these in service, remove only CRUD methods

**Acceptance:**
- [ ] UserService inherits from `BaseService[User]`
- [ ] UserCreate/UserRead/UserUpdate inherit from base schemas
- [ ] Custom business logic preserved
- [ ] All tests pass
- [ ] No linter errors

#### Module 2.3: auth (HIGH COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `auth/models.py` | Change Base import | 5m |
| `auth/schemas.py` | Inherit schemas from base classes | 25m |
| `auth/service.py` | Inherit from `BaseService[??]`, remove CRUD | 60m |
| `tests/unit/modules/test_auth.py` | Update tests | 40m |
| **Total** | **~2h 10m** | |

**Special Consideration:**
- Auth module may not have a single model (tokens stored in Redis)
- May need multiple `BaseService[Token]` instances
- Consider creating `Token` model in `_base` or keep custom service pattern

**Acceptance:**
- [ ] AuthService uses base patterns where applicable
- [ ] Schemas inherit from base classes
- [ ] Auth business logic preserved
- [ ] All tests pass
- [ ] No linter errors

#### Module 2.4: rbac (HIGH COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `rbac/models.py` | Change Base import for Role, Permission | 10m |
| `rbac/schemas.py` | Inherit schemas from base classes | 25m |
| `rbac/service.py` | Inherit from `BaseService[Role]`, remove CRUD | 60m |
| `tests/unit/modules/test_rbac.py` | Update tests | 40m |
| **Total** | **~2h 15m** | |

**Custom Logic to Preserve:**
- Permission checking logic
- Role assignment with validation
- User-role relationship management

**Acceptance:**
- [ ] RoleService inherits from `BaseService[Role]`
- [ ] Schemas inherit from base classes
- [ ] Custom RBAC logic preserved
- [ ] All tests pass
- [ ] No linter errors

---

### Sprint 3: Feature Modules (Week 4)

#### Module 3.1: posts (MEDIUM COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `posts/models.py` | Change Base import | 5m |
| `posts/schemas.py` | Inherit schemas from base classes | 20m |
| `posts/service.py` | Inherit from `BaseService[Post]`, remove CRUD | 45m |
| `tests/unit/modules/test_posts.py` | Update tests | 30m |
| **Total** | **~1h 40m** | |

**Custom Logic to Preserve:**
- `_get_user_or_404` method (cross-service user access)
- `_check_ownership` method (authorization)
- Keep these, remove only CRUD methods

**Acceptance:**
- [ ] PostService inherits from `BaseService[Post]`
- [ ] Schemas inherit from base classes
- [ ] Custom authorization logic preserved
- [ ] All tests pass
- [ ] No linter errors

#### Module 3.2: api_keys (MEDIUM COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `api_keys/models.py` | Change Base import | 5m |
| `api_keys/schemas.py` | Inherit schemas from base classes | 20m |
| `api_keys/service.py` | Inherit from `BaseService[APIKey]`, remove CRUD | 45m |
| `tests/unit/modules/test_api_keys.py` | Update tests | 30m |
| **Total** | **~1h 40m** | |

**Custom Logic to Preserve:**
- API key generation with prefix
- Key validation logic

**Acceptance:**
- [ ] APIKeyService inherits from `BaseService[APIKey]`
- [ ] Schemas inherit from base classes
- [ ] Custom key generation logic preserved
- [ ] All tests pass
- [ ] No linter errors

#### Module 3.3: audit_logs (MEDIUM COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `audit_logs/models.py` | Change Base import | 5m |
| `audit_logs/schemas.py` | Inherit schemas from base classes | 20m |
| `audit_logs/service.py` | Inherit from `BaseService[AuditLog]`, remove CRUD | 45m |
| `tests/unit/modules/test_audit_logs.py` | Update tests | 30m |
| **Total** | **~1h 40m** | |

**Custom Logic to Preserve:**
- Event logging logic
- Tenant filtering
- Immutable log enforcement

**Acceptance:**
- [ ] AuditLogService inherits from `BaseService[AuditLog]`
- [ ] Schemas inherit from base classes
- [ ] Custom logging logic preserved
- [ ] All tests pass
- [ ] No linter errors

#### Module 3.4: feature_flags (MEDIUM COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `feature_flags/models.py` | Change Base import | 5m |
| `feature_flags/schemas.py` | Inherit schemas from base classes | 20m |
| `feature_flags/service.py` | Inherit from `BaseService[FeatureFlag]`, remove CRUD | 45m |
| `tests/unit/modules/test_feature_flags.py` | Update tests | 30m |
| **Total** | **~1h 40m** | |

**Custom Logic to Preserve:**
- Flag override logic (user > tenant > global)
- Active flag filtering
- Flag caching

**Acceptance:**
- [ ] FeatureFlagService inherits from `BaseService[FeatureFlag]`
- [ ] Schemas inherit from base classes
- [ ] Custom flag logic preserved
- [ ] All tests pass
- [ ] No linter errors

#### Module 3.5: notifications (MEDIUM COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `notifications/models.py` | Change Base import | 5m |
| `notifications/schemas.py` | Inherit schemas from base classes | 20m |
| `notifications/service.py` | Inherit from `BaseService[Notification]`, remove CRUD | 45m |
| `tests/unit/modules/test_notifications.py` | Update tests | 30m |
| **Total** | **~1h 40m** | |

**Custom Logic to Preserve:**
- Email sending logic
- Template rendering
- Notification queue management

**Acceptance:**
- [ ] NotificationService inherits from `BaseService[Notification]`
- [ ] Schemas inherit from base classes
- [ ] Custom notification logic preserved
- [ ] All tests pass
- [ ] No linter errors

#### Module 3.6: webhooks (MEDIUM COMPLEXITY)
| File | Changes | Time |
|------|---------|------|
| `webhooks/models.py` | Change Base import | 5m |
| `webhooks/schemas.py` | Inherit schemas from base classes | 20m |
| `webhooks/service.py` | Inherit from `BaseService[WebhookEndpoint]`, remove CRUD | 60m |
| `tests/unit/modules/test_webhooks.py` | Update tests | 30m |
| **Total** | **~1h 55m** | |

**Custom Logic to Preserve:**
- Webhook delivery logic
- Retry mechanism
- Signature verification

**Acceptance:**
- [ ] WebhookService inherits from `BaseService[WebhookEndpoint]`
- [ ] Schemas inherit from base classes
- [ ] Custom webhook logic preserved
- [ ] All tests pass
- [ ] No linter errors

---

## Sprint Plan

### Sprint 1: Foundation Validation (Week 1)
**Goal:** Validate migration approach with low-risk modules

| Day | Module(s) | Focus |
|-----|-------------|-------|
| 1 | tiers | Models, Schemas, Service, Tests |
| 2 | rate_limits | Models, Schemas, Service, Tests |
| 3 | Quality Gates | Run mypy, ruff, pytest on both |
| 4 | Documentation | Update AGENTS.md with base pattern requirements |
| 5 | Retro | Plan adjustments based on lessons learned |

**Deliverables:**
- [ ] `tiers/` module migrated
- [ ] `rate_limits/` module migrated
- [ ] All tests passing
- [ ] Documentation updated
- [ ] Migration retrospective document

---

### Sprint 2: Core Infrastructure (Week 2-3)
**Goal:** Migrate foundational modules

**Week 2: Tenants & Users**
| Day | Module(s) | Focus |
|-----|-------------|-------|
| 1 | tenants | Models, Schemas |
| 2 | tenants | Service, Tests |
| 3 | users | Models, Schemas |
| 4 | users | Service, Tests |
| 5 | Quality Gates | Run mypy, ruff, pytest |

**Week 3: Auth & RBAC**
| Day | Module(s) | Focus |
|-----|-------------|-------|
| 1 | auth | Models, Schemas |
| 2 | auth | Service, Tests |
| 3 | rbac | Models, Schemas |
| 4 | rbac | Service, Tests |
| 5 | Quality Gates | Run mypy, ruff, pytest |

**Deliverables:**
- [ ] `tenants/` module migrated
- [ ] `users/` module migrated
- [ ] `auth/` module migrated
- [ ] `rbac/` module migrated
- [ ] All tests passing
- [ ] Integration tests verified

---

### Sprint 3: Feature Modules (Week 4)
**Goal:** Complete migration for all remaining modules

**Day-by-Day Plan:**
| Day | Module(s) | Focus |
|-----|-------------|-------|
| 1 | posts | Complete migration |
| 2 | api_keys | Complete migration |
| 3 | audit_logs | Complete migration |
| 4 | feature_flags | Complete migration |
| 5 | notifications | Complete migration |
| 6 | webhooks | Complete migration |
| 7 | Quality Gates | Full test suite run |
| 8 | Documentation | Update AGENTS.md, ARCHITECTURE.md |
| 9-10 | Integration | End-to-end testing, bug fixes |

**Deliverables:**
- [ ] All 13 modules migrated
- [ ] All tests passing (80%+ coverage)
- [ ] Documentation updated
- [ ] Migration complete report

---

## Quality Gates

### Before Each Module Migration

- [ ] Create feature branch: `git checkout -b refactor/base-pattern-{module}`
- [ ] Run baseline tests: `pytest tests/unit/modules/test_{module}.py -v --tb=short`
- [ ] Document current state: Record line count, test count

### During Migration

- [ ] No regressions in existing tests
- [ ] All imports resolved
- [ ] Type hints preserved
- [ ] Business logic intact

### After Each Module Migration

```bash
# Type check
uv run mypy src/app/modules/{module}/

# Lint
uv run ruff check src/app/modules/{module}/

# Tests
pytest tests/unit/modules/test_{module}.py -v --cov=src/app/modules/{module}

# Coverage check
pytest --cov=src/app/modules/{module} --cov-report=term-missing
```

### Module Acceptance Checklist

- [ ] Model imports `Base` from `_base`
- [ ] Service inherits from `BaseService[Model]`
- [ ] All CRUD methods removed from service (now inherited)
- [ ] Custom business logic preserved
- [ ] `*Create` inherits from `BaseCreate`
- [ ] `*Read` inherits from `BaseRead`
- [ ] `*Update` inherits from `BaseUpdate`
- [ ] `*List` inherits from `BaseList[T]` (if applicable)
- [ ] Custom exceptions use `DuplicateError`, `ModelNotFoundError`
- [ ] All existing tests pass
- [ ] No mypy errors
- [ ] No ruff lint errors
- [ ] Coverage >= 80%

---

## Testing Strategy

### Test Migration Approach

#### 1. Test Preservation (Before Migration)
```bash
# Run tests and record baseline
pytest tests/unit/modules/test_{module}.py -v --tb=no > baseline/{module}_before.txt

# Count tests
pytest tests/unit/modules/test_{module}.py --collect-only | grep "test_" | wc -l
```

#### 2. Remove CRUD Test Duplication

Tests that only test inherited CRUD methods should be **removed**:

```python
# ❌ DELETE THESE (inherited from BaseService)
class Test{Model}CRUD:
    def test_get_by_id(self):
        pass  # Now inherited

    def test_create_{model}(self):
        pass  # Now inherited

    def test_update_{model}(self):
        pass  # Now inherited

    def test_delete_{model}(self):
        pass  # Now inherited

    def test_get_multi_{model}s(self):
        pass  # Now inherited

# ✅ KEEP THESE (custom business logic)
class Test{Model}Service:
    async def test_custom_method_{unique}_to_{module}(self):
        pass  # Custom logic, keep
```

#### 3. Update Test Imports

```python
# Update to import from _base
from src.app.modules._base import (
    Base,
    BaseCreate,
    BaseRead,
    BaseUpdate,
    BaseList,
    BaseService,
    ModelNotFoundError,
    ValidationError,
    DuplicateError,
)
```

#### 4. Run Post-Migration Tests

```bash
# Run same tests as baseline
pytest tests/unit/modules/test_{module}.py -v --tb=no > baseline/{module}_after.txt

# Compare
diff baseline/{module}_before.txt baseline/{module}_after.txt

# Should show same or better results
```

### Integration Testing After All Migrations

```bash
# Run full test suite
pytest tests/unit/modules/ -v --cov=src/app/modules

# Run integration tests
pytest tests/integration/ -v

# Verify no regressions
pytest tests/ -v --last-failed
```

---

## Rollback Procedures

### Per-Module Rollback

If migration causes issues, rollback using git:

```bash
# Revert changes to module
git checkout main -- src/app/modules/{module}/
git checkout main -- tests/unit/modules/test_{module}.py

# Verify restored state
pytest tests/unit/modules/test_{module}.py -v
```

### Full Rollback Strategy

If Sprint 1/2/3 causes significant regressions:

```bash
# Option 1: Revert entire sprint branch
git checkout main
git branch -D refactor/sprint-{number}

# Option 2: Keep custom migrations, revert core
git revert HEAD~1  # Revert last commit

# Option 3: Manual rollback per module
# Iterate through migrated modules and run per-module rollback
```

### Rollback Decision Matrix

| Issue Severity | Action | Recovery Time |
|----------------|--------|---------------|
| **Critical** (production broken) | Full rollback | < 30 minutes |
| **High** (core features broken) | Sprint rollback | < 2 hours |
| **Medium** (edge cases fail) | Module rollback | < 4 hours |
| **Low** (minor issues) | Continue with fix | 1-2 days |

---

## Success Metrics

### Quantitative Metrics

| Metric | Baseline | Target | How to Measure |
|--------|-----------|--------|-----------------|
| **Lines of Code** | ~15,000 | ~13,850 | `cloc src/app/modules/` |
| **CRUD Duplication** | ~650 lines | 0 | `rg -l "async def get\|create\|update\|delete" src/app/modules/*/service.py` |
| **Schema Boilerplate** | ~200 lines | 0 | Manual inspection |
| **Test Boilerplate** | ~300 lines | 0 | Manual inspection |
| **Module Consistency** | 2/13 use base | 13/13 use base | Code review |
| **Type Check Errors** | Current count | 0 | `uv run mypy src/` |
| **Lint Errors** | Current count | 0 | `uv run ruff check src/` |
| **Test Pass Rate** | 95% | 100% | `pytest --tb=no` |
| **Test Coverage** | Current % | >= 80% | `pytest --cov=src/app/modules` |

### Qualitative Metrics

- [ ] **Onboarding Time Reduced:** New developers understand patterns in < 1 day
- [ ] **Code Review Efficiency:** Reviewers spot inconsistencies immediately
- [ ] **Maintenance Burden Reduced:** Single point of change for CRUD patterns
- [ ] **Documentation Alignment:** Code matches documented architecture
- [ ] **Team Adoption:** All team members comfortable using `_base` patterns

### Post-Migration Validation

```bash
# 1. Code quality check
uv run mypy src/app/modules/ && uv run ruff check src/app/modules/

# 2. Test coverage
pytest --cov=src/app/modules --cov-report=html

# 3. Verify all modules use base
rg -l "from src\.app\.core\.db\.database import Base" src/app/modules/
# Should return 0 results

rg -l "from src\.app\.modules\._base import Base" src/app/modules/
# Should return results for all modules

# 4. Check service inheritance
rg -l "class \w+Service\(BaseService" src/app/modules/*/service.py
# Should show all services inherit from BaseService

# 5. Check schema inheritance
rg -l "class \w+(Create|Read|Update)\(BaseCreate|BaseRead|BaseUpdate)" src/app/modules/*/schemas.py
# Should show all schemas inherit from base classes
```

---

## Risk Assessment

### Migration Risks

| Risk | Probability | Impact | Mitigation |
|-------|-------------|--------|------------|
| **Custom Logic Lost** | Medium | High | Carefully identify and preserve custom methods |
| **Circular Imports** | Low | Medium | Use lazy imports in `__init__.py` |
| **Type Hint Errors** | Medium | Medium | Test after each module migration |
| **Test Regressions** | Low | High | Baseline tests before migration, compare after |
| **Breaking Changes** | Low | High | Run integration tests after sprint |
| **Team Adoption** | Low | Medium | Update documentation, provide training |

### Risk Mitigation Timeline

**Pre-Sprint (Week 0):**
1. Create migration checklist for each module
2. Document custom business logic patterns to preserve
3. Set up test baseline recording
4. Prepare rollback scripts

**During Migration:**
1. One module at a time, complete before starting next
2. Run full test suite after each sprint
3. Code review before merging
4. Monitor CI/CD pipeline

**Post-Migration:**
1. One-week observation period
2. Team retrospective
3. Update AGENTS.md with lessons learned
4. Create migration best practices guide

---

## Implementation Guide

### Step-by-Step for Each Module

#### Step 1: Model Import Migration

**File:** `src/app/modules/{module}/models.py`

**Find:**
```python
from src.app.core.db.database import Base
```

**Replace with:**
```python
from ..._base.models import Base
```

**Verify:**
```bash
uv run mypy src/app/modules/{module}/models.py
```

---

#### Step 2: Service Refactoring

**File:** `src/app/modules/{module}/service.py`

**Before:**
```python
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from fastcrud import FastCRUD
from .models import {Model}
from .schemas import {Model}Create, {Model}Read

class {Model}Service:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.crud = FastCRUD({Model})

    async def get(self, id: Any) -> {Model} | None:
        return await self.crud.get(db=self.db, id=id)

    async def create(self, **kwargs) -> {Model}:
        return await self.crud.create(db=self.db, object=kwargs)

    async def update(self, id: Any, **kwargs) -> {Model} | None:
        return await self.crud.update(db=self.db, object=kwargs, id=id)

    async def delete(self, id: Any) -> dict:
        return await self.crud.delete(db=self.db, id=id)

    async def get_multi(
        self,
        page: int = 1,
        per_page: int = 20,
        **kwargs: Any
    ) -> list[{Model}]:
        offset = (page - 1) * per_page
        return await self.crud.get_multi(db=self.db, offset=offset, limit=per_page, **kwargs)

    # Custom methods...
```

**After:**
```python
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from ..._base.service import BaseService
from ..._base.exceptions import ModelNotFoundError, DuplicateError
from .models import {Model}
from .schemas import {Model}Create, {Model}Read

class {Model}Service(BaseService[{Model}]):
    def __init__(self, db: AsyncSession):
        super().__init__({Model}, db)

    # Custom methods only - CRUD inherited from BaseService
    # ... preserve all custom business logic
```

**Verification:**
- Count lines removed (should be ~40-50)
- Verify custom methods preserved
- Run tests: `pytest tests/unit/modules/test_{module}.py -v`

---

#### Step 3: Schema Migration

**File:** `src/app/modules/{module}/schemas.py`

**Before:**
```python
from pydantic import BaseModel, EmailStr
from datetime import datetime

class {Model}Create(BaseModel):
    email: EmailStr
    username: str
    # ... custom fields
    model_config = {"from_attributes": True}

class {Model}Read(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime | None = None
    email: str
    username: str
    # ... custom fields
    model_config = {"from_attributes": True}

class {Model}Update(BaseModel):
    email: str | None = None
    username: str | None = None
    # ... custom fields
    model_config = {"from_attributes": True, "populate_by_name": True}
```

**After:**
```python
from ..._base.schemas import BaseCreate, BaseRead, BaseUpdate
from pydantic import EmailStr

class {Model}Create(BaseCreate):
    email: EmailStr
    username: str
    # ... custom fields only

class {Model}Read(BaseRead):
    email: str
    username: str
    # ... custom fields only (id, timestamps inherited)

class {Model}Update(BaseUpdate):
    email: str | None = None
    username: str | None = None
    # ... custom fields only (config inherited)
```

**Verification:**
- Count lines removed (should be ~15-25)
- Verify custom fields present
- Run tests: `pytest tests/unit/modules/test_{module}.py -v`

---

#### Step 4: Exception Migration

**Find patterns:**
```python
# Find
raise DuplicateValueException(...)
raise NotFoundException(...)

# Replace with
raise DuplicateError(...)
raise ModelNotFoundError(...)
```

**Update imports:**
```python
from ..._base.exceptions import ModelNotFoundError, ValidationError, DuplicateError
```

---

## Documentation Updates

### Update AGENTS.md

Add to `AGENTS.md`:

```markdown
### Module Development Standards

All new and refactored modules MUST use `_base` patterns:

#### Models
```python
from src.app.modules._base import Base

class MyModel(Base):
    __tablename__ = "my_table"
    # ...
```

#### Services
```python
from src.app.modules._base import BaseService

class MyService(BaseService[MyModel]):
    def __init__(self, db: AsyncSession):
        super().__init__(MyModel, db)

    # Custom methods only - CRUD inherited
    async def custom_business_logic(self, ...):
        pass
```

#### Schemas
```python
from src.app.modules._base import BaseCreate, BaseRead, BaseUpdate

class MyModelCreate(BaseCreate):
    # Custom fields only
    field: str

class MyModelRead(BaseRead):
    # Custom fields only (id, timestamps inherited)
    field: str

class MyModelUpdate(BaseUpdate):
    # Custom fields only (config inherited)
    field: str | None = None
```

#### Exceptions
```python
from src.app.modules._base import ModelNotFoundError, DuplicateError, ValidationError

# Use these instead of HTTPException or custom exceptions
raise ModelNotFoundError("User", 123)
raise DuplicateError("email", "test@example.com")
```

#### Pagination
```python
from src.app.modules._base import BaseList, BasePagination

class MyModelList(BaseList[MyModelRead]):
    pass  # Inherits all pagination fields

# In router
from fastapi import Depends

@router.get("/models")
async def list_models(
    pagination: BasePagination = Depends()
):
    # Use pagination.page, pagination.per_page
    pass
```

### Code Review Checklist

Add to code review process:
- [ ] Models import `Base` from `_base`
- [ ] Services inherit from `BaseService[T]`
- [ ] Schemas inherit from base classes
- [ ] No duplicate CRUD methods
- [ ] Uses standardized exceptions
- [ ] Uses `BaseList[T]` for list responses
- [ ] Uses `BasePagination` dependency
```

---

## Next Steps

### Immediate Actions

1. **Review and Approve This Plan** (Day 0)
   - Share with team for feedback
   - Adjust timelines based on team capacity
   - Confirm sprint assignments

2. **Create Feature Branch** (Day 0)
   ```bash
   git checkout -b refactor/base-pattern-migration
   ```

3. **Update Beads** (Day 0)
   ```bash
   bd create "Refactor: Migrate modules to use _base pattern" -d "Systematic migration of 13 modules to base pattern" -p high
   bd create "Refactor tiers module to use _base" -d "Migrate tiers/ service, schemas, models" -p high
   # ... create task for each module
   ```

4. **Start Sprint 1** (Day 1)
   - Begin with `tiers` module
   - Follow step-by-step guide
   - Track time spent vs estimates

### Sprint 1 Kickoff

```bash
# Create sprint branch
git checkout -b refactor/sprint-1-foundation

# Start with tiers module
# Refer to Sprint 1 section for detailed steps
```

### Continuous Monitoring

After each module:
```bash
# Run automated checks
uv run mypy src/app/modules/{module}/
uv run ruff check src/app/modules/{module}/
pytest tests/unit/modules/test_{module}.py --cov=src/app/modules/{module}

# Document results
echo "Module: {module}" >> migration_log.md
echo "Lines removed: X" >> migration_log.md
echo "Tests passing: Y/N" >> migration_log.md
echo "Coverage: %" >> migration_log.md
```

---

## Appendix: Migration Templates

### Template 1: Service Before/After

**Use this template for each module service:**

```python
# ===== BEFORE =====
"""{Module} service layer."""

from sqlalchemy.ext.asyncio import AsyncSession
from fastcrud import FastCRUD
from .models import {Model}
from .schemas import {Model}Create, {Model}Read
from ...core.exceptions.http_exceptions import DuplicateValueException, NotFoundException

class {Module}Service:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.crud = FastCRUD({Model})

    # CRUD methods (DELETE THESE)
    async def get(self, id):
        return await self.crud.get(db=self.db, id=id)

    async def create(self, **kwargs):
        return await self.crud.create(db=self.db, object=kwargs)

    async def update(self, id, **kwargs):
        return await self.crud.update(db=self.db, object=kwargs, id=id)

    async def delete(self, id):
        return await self.crud.delete(db=self.db, id=id)

    async def get_multi(self, page=1, per_page=20, **kwargs):
        offset = (page - 1) * per_page
        return await self.crud.get_multi(db=self.db, offset=offset, limit=per_page, **kwargs)

    # Custom methods (KEEP THESE)
    async def custom_method_1(self, ...):
        pass

    async def custom_method_2(self, ...):
        pass

# ===== AFTER =====
"""{Module} service layer."""

from sqlalchemy.ext.asyncio import AsyncSession
from ..._base.service import BaseService
from ..._base.exceptions import ModelNotFoundError, DuplicateError
from .models import {Model}
from .schemas import {Model}Create, {Model}Read

class {Module}Service(BaseService[{Model}]):
    def __init__(self, db: AsyncSession):
        super().__init__({Model}, db)

    # CRUD methods now inherited - NO NEED TO IMPLEMENT

    # Custom methods only (KEEP THESE)
    async def custom_method_1(self, ...):
        pass

    async def custom_method_2(self, ...):
        pass
```

### Template 2: Schema Before/After

**Use this template for each module schema:**

```python
# ===== BEFORE =====
"""{Module} schemas."""

from pydantic import BaseModel, EmailStr
from datetime import datetime

class {Model}Create(BaseModel):
    field1: str
    field2: int
    model_config = {"from_attributes": True}

class {Model}Read(BaseModel):
    id: int
    field1: str
    field2: int
    created_at: datetime
    updated_at: datetime | None = None
    model_config = {"from_attributes": True}

class {Model}Update(BaseModel):
    field1: str | None = None
    field2: int | None = None
    model_config = {"from_attributes": True, "populate_by_name": True}

# ===== AFTER =====
"""{Module} schemas."""

from ..._base.schemas import BaseCreate, BaseRead, BaseUpdate

class {Model}Create(BaseCreate):
    # Config inherited from BaseCreate
    field1: str
    field2: int

class {Model}Read(BaseRead):
    # id, created_at, updated_at inherited from BaseRead
    field1: str
    field2: int

class {Model}Update(BaseUpdate):
    # Config inherited from BaseUpdate
    field1: str | None = None
    field2: int | None = None
```

---

## Conclusion

This refactoring plan provides a **systematic, low-risk approach** to migrating all 13 existing modules to use `_base` patterns.

### Key Success Factors

1. **Incremental Migration:** Start with low-risk modules, validate approach
2. **Preserve Business Logic:** Carefully identify and retain custom methods
3. **Continuous Testing:** Baseline before, validate after each module
4. **Rollback Ready:** Git-based rollback at module and sprint level
5. **Documentation Alignment:** Code matches documented architecture plans

### Expected Outcomes

- **~1,150 lines** of code removed
- **~100% consistency** across all modules
- **~80% faster** onboarding for new developers
- **~50% reduction** in code review time
- **Single source of truth** for CRUD patterns

### Next Action Required

**Decision Point:**
- [ ] Approve this refactoring plan
- [ ] Assign sprint 1 to developers
- [ ] Set start date for Sprint 1
- [ ] Update Beads with sprint tasks

**Once Approved:**
- [ ] Create `refactor/base-pattern-migration` branch
- [ ] Begin Sprint 1 with `tiers` module
- [ ] Follow step-by-step migration guide

---

**Document Version:** 1.0
**Generated:** 2026-01-04
**Status:** READY FOR REVIEW
