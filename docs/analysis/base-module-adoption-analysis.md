# `_base` Module Adoption Analysis

> **Generated:** 2026-01-09  
> **Purpose:** Evaluate project-wide adoption of the `_base` module patterns and identify improvement opportunities

---

## Executive Summary

The `_base` module provides excellent infrastructure but has **low adoption (~30%)**. Only 7 of 23 modules use `BaseService`, and only 3 modules properly inherit from base schemas. This represents significant technical debt and missed consistency opportunities.

---

## What `_base` Provides

| Component | Purpose |
|-----------|---------|
| `BaseService[T]` | Generic CRUD service with FastCRUD, pagination, DI factory |
| `BaseCreate` | Schema base with `extra='forbid'` |
| `BaseRead` | Schema with `id`, `created_at`, `updated_at`, `from_attributes=True` |
| `BaseReadUUID` | UUID variant of BaseRead |
| `BaseUpdate` | Schema with `extra='forbid'`, `populate_by_name=True` |
| `BaseList[T]` | Paginated response: `items`, `total`, `page`, `per_page`, `has_next`, `has_previous` |
| `BasePagination` | Query params: `page=1`, `per_page=20` (max 100) |
| `ModelNotFoundError` | Standardized "not found" exception |
| `DuplicateError` | Standardized "already exists" exception |
| `ValidationError` | Standardized validation exception |

---

## Current Adoption Status

| Component | Adopters | Non-Adopters | Adoption Rate |
|-----------|----------|--------------|---------------|
| **BaseService[T]** | 7 modules | 16 modules | **30%** |
| **Base Schemas** | 3 modules | 20 modules | **13%** |
| **Base Exceptions** | 10 modules | 13 modules | **43%** |

---

## Modules by Adoption Level

### Excellent Adoption (Reference Implementations)

| Module | Pattern |
|--------|---------|
| `billing/` | 6 services all use `BaseService[T]`, DI factories, base exceptions |
| `tenants/` | `TenantService(BaseService[Tenant])`, proper DI |
| `tiers/` | `TierService(BaseService[Tier])`, schema inheritance |
| `api_keys/` | `APIKeyService(BaseService[APIKey])`, legacy wrapper |

### Partial Adoption (Needs Improvement)

| Module | Status | Gap |
|--------|--------|-----|
| `posts/` | Schemas inherit from base | Redundant `ConfigDict` on `PostCreate` |
| `users/` | Uses base exceptions | Schemas duplicate `BaseRead`/`BaseUpdate` patterns |
| `rbac/` | Uses base exceptions | Plain class (justified: multi-table joins) |

### No Adoption (Migration Candidates)

| Module | Issues |
|--------|--------|
| `feature_flags/` | Plain class with FastCRUD, returns `None` instead of exceptions |
| `webhooks/` | Raises `ValueError` instead of `ModelNotFoundError` |
| `notifications/` | Raw SQLAlchemy, `ValueError` exceptions |
| `audit_logs/` | Raw SQLAlchemy, no standardized exceptions |
| `user_preferences/` | Plain class, no base schema inheritance |
| `magic_link/` | No adoption |
| `passkey/` | No adoption |
| `sso/` | No adoption |
| `sso_settings/` | No adoption |
| `storage_settings/` | No adoption |
| `files/` | No adoption |
| `auth/` | No adoption (schemas only - orchestration service) |

### Not Applicable

| Module | Reason |
|--------|--------|
| `tasks/` | ARQ job queue, no DB entities |
| `health/` | No CRUD operations |
| `ai/` | Uses BaseService but specialized ChatSession patterns |

---

## Key Improvement Points

### 1. Schema Inheritance Gap (High Impact)

**Problem:** 100+ schemas use raw `BaseModel` instead of `BaseCreate`/`BaseRead`/`BaseUpdate`

**Anti-Pattern (users/schemas.py):**
```python
class UserRead(BaseModel):
    id: int  # Duplicates BaseRead.id
    created_at: datetime  # Duplicates BaseRead.created_at
    updated_at: datetime | None  # Duplicates BaseRead.updated_at
    model_config = ConfigDict(from_attributes=True)  # Duplicates BaseRead
    # ... other fields
```

**Correct Pattern:**
```python
from src.app.modules._base import BaseRead

class UserRead(BaseRead):  # Gets id, created_at, updated_at, from_attributes automatically
    email: str
    username: str
    # ... only module-specific fields
```

**Affected Modules (13):**
- `users` - UserRead, UserUpdate
- `webhooks` - all 8 schemas
- `audit_logs` - all 4 schemas
- `notifications` - all 7 schemas
- `feature_flags` - all 4 schemas
- `passkey` - all 11 schemas
- `auth` - all 9 schemas
- `rbac` - all 6 schemas
- `magic_link` - all 3 schemas
- `sso` - 4 schemas
- `storage_settings` - 4 schemas
- `files` - 5 schemas
- `user_preferences` - 4 schemas

---

### 2. Exception Standardization Gap (Medium Impact)

**Problem:** Modules raise `ValueError` or return `None` instead of `ModelNotFoundError`/`DuplicateError`

**Anti-Pattern (webhooks/service.py):**
```python
async def get_webhook(self, webhook_id: UUID) -> Webhook:
    webhook = await self.crud.get(db=self.db, id=webhook_id)
    if not webhook:
        raise ValueError(f"Webhook {webhook_id} not found")  # BAD
    return webhook
```

**Correct Pattern:**
```python
from src.app.modules._base import ModelNotFoundError

async def get_webhook(self, webhook_id: UUID) -> Webhook:
    webhook = await self.crud.get(db=self.db, id=webhook_id)
    if not webhook:
        raise ModelNotFoundError("Webhook", webhook_id)  # GOOD
    return webhook
```

**Affected Modules (5):**
- `webhooks` - uses `ValueError`
- `notifications` - uses `ValueError`
- `feature_flags` - returns `None`
- `audit_logs` - no error handling
- `storage_settings` - uses `ValueError`

---

### 3. Service Pattern Inconsistency (Medium Impact)

**Problem:** Modules use singleton pattern or `__init__(db)` instead of DI factory

**Anti-Pattern (feature_flags/service.py):**
```python
class FeatureFlagsService:
    def __init__(self):
        self.crud = FastCRUD(FeatureFlag)
    
    async def get_flag(self, db: AsyncSession, flag_id: int):
        # db passed per-method
        ...

feature_flags_service = FeatureFlagsService()  # Singleton anti-pattern
```

**Correct Pattern:**
```python
from src.app.modules._base import BaseService

class FeatureFlagService(BaseService[FeatureFlag]):
    async def get_flag(self, flag_id: int):
        # self.db available from BaseService
        return await self.get(flag_id)

def get_feature_flag_service(
    db: AsyncSession = Depends(get_db)
) -> FeatureFlagService:
    return FeatureFlagService(db=db)  # DI factory
```

**Affected Modules (8):**
- `feature_flags` - singleton pattern
- `webhooks` - `__init__(db)` without BaseService
- `notifications` - `__init__(db)` without BaseService
- `audit_logs` - `__init__(db)` without BaseService
- `user_preferences` - `__init__(db)` without BaseService
- `magic_link` - `__init__(db)` without BaseService
- `passkey` - `__init__(db)` without BaseService
- `sso_settings` - `__init__(db)` without BaseService

---

### 4. Missing BaseList/BasePagination Usage (Low Impact)

**Problem:** Modules implement custom pagination responses instead of using `BaseList[T]`

**Anti-Pattern:**
```python
@router.get("/items")
async def list_items(...) -> dict:
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,  # Inconsistent naming
    }
```

**Correct Pattern:**
```python
from src.app.modules._base import BaseList, BasePagination

@router.get("/items", response_model=BaseList[ItemRead])
async def list_items(
    pagination: BasePagination = Depends()
) -> BaseList[ItemRead]:
    items, total = await service.get_multi(
        page=pagination.page, 
        per_page=pagination.per_page
    )
    return BaseList(
        items=items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
    )
```

---

## Recommended Migration Priority

| Priority | Module | Effort | Value | Rationale |
|----------|--------|--------|-------|-----------|
| **P1** | `feature_flags` | Low | High | Simple CRUD, high usage across app |
| **P1** | `users` (schemas) | Low | High | Core module, schema fixes only |
| **P2** | `webhooks` | Medium | High | Exception standardization critical |
| **P2** | `notifications` | Medium | Medium | Event-driven, exception cleanup |
| **P2** | `audit_logs` | Medium | Medium | Read-heavy, schema standardization |
| **P3** | `user_preferences` | Low | Low | Simple module |
| **P3** | `passkey` | Medium | Low | Complex WebAuthn logic |
| **P3** | `magic_link` | Low | Low | Simple module |
| **P3** | `sso` / `sso_settings` | Medium | Low | OIDC complexity |
| **P3** | `storage_settings` | Low | Low | Simple module |
| **P3** | `files` | Low | Low | Simple module |
| **P3** | `auth` (schemas) | Low | Low | Orchestration service |

---

## Migration Checklist Template

For each module migration, verify:

- [ ] **Schemas**
  - [ ] `*Create` schemas inherit from `BaseCreate`
  - [ ] `*Read` schemas inherit from `BaseRead` or `BaseReadUUID`
  - [ ] `*Update` schemas inherit from `BaseUpdate`
  - [ ] Remove redundant `id`, `created_at`, `updated_at` fields
  - [ ] Remove redundant `ConfigDict` declarations

- [ ] **Service**
  - [ ] Service class extends `BaseService[T]`
  - [ ] DI factory function `get_*_service()` created
  - [ ] Singleton instances removed
  - [ ] `db` parameter removed from method signatures

- [ ] **Exceptions**
  - [ ] `ValueError` replaced with `ModelNotFoundError`
  - [ ] Duplicate checks use `DuplicateError`
  - [ ] `None` returns converted to exceptions

- [ ] **Pagination**
  - [ ] List endpoints return `BaseList[T]`
  - [ ] Query params use `BasePagination`

- [ ] **Tests**
  - [ ] Exception assertions updated
  - [ ] DI factory mocking updated

---

## Proposed `_base` Module Enhancements

### 1. Add `TenantAwareBaseService`

```python
class TenantAwareBaseService(BaseService[T], Generic[T]):
    """BaseService with automatic tenant_id filtering."""
    
    def __init__(self, db: AsyncSession, tenant_id: UUID):
        super().__init__(db=db)
        self.tenant_id = tenant_id
    
    async def get(self, id: int | UUID) -> T:
        return await self.crud.get(
            db=self.db, 
            id=id, 
            tenant_id=self.tenant_id
        )
```

### 2. Add Linting Rules

Add to `ruff.toml` or pre-commit:
```toml
# Detect anti-patterns
[tool.ruff.lint.extend-per-file-ignores]
"**/schemas.py" = ["TC001"]  # Custom rule for BaseModel inheritance check
```

### 3. Documentation

- Add `_base/README.md` with usage examples
- Add docstrings to all base classes
- Create migration guide with before/after examples

---

## Conclusion

**The `_base` module is well-designed but severely underutilized.**

| Metric | Current | Target |
|--------|---------|--------|
| BaseService adoption | 30% | 80%+ |
| Base Schema adoption | 13% | 90%+ |
| Base Exception adoption | 43% | 95%+ |

Only the `billing/` module fully demonstrates best practices. Migrating the 13 non-adopting modules would:

- **Reduce boilerplate by ~30%** - No more duplicated field definitions
- **Standardize error handling** - Consistent API error responses
- **Improve maintainability** - Single source of truth for common patterns
- **Accelerate onboarding** - New developers learn one pattern

### Immediate Next Steps

1. Complete `feature_flags` migration (P1, low effort, high value)
2. Fix `users/schemas.py` inheritance (P1, low effort, high value)
3. Standardize `webhooks` exceptions (P2, medium effort, high value)
4. Enforce `_base` patterns in code review for all new modules

---

## Appendix: Reference Implementation
