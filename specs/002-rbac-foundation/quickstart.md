# Quickstart Guide

## 1. Setup

Ensure the module directory exists:
```bash
mkdir -p src/app/modules/rbac
touch src/app/modules/rbac/__init__.py
```

## 2. Models
Implement SQLAlchemy models in `src/app/modules/rbac/models.py`. Ensure `Role` has `tenant_id` and `is_system`.

## 3. Core Logic
1. Define permissions enum in `src/app/core/rbac/permissions.py`.
2. Implement dependency `requires_permission` in `src/app/core/rbac/dependencies.py`.
   - Must check `is_superuser`.
   - Must validate `tenant_id`.

## 4. Migrations (Critical)
Run `alembic revision --autogenerate -m "rbac_foundation"`.
**EDIT THE MIGRATION FILE MANUALLY**:
- Import `op` from alembic.
- Use `op.bulk_insert` to seed:
  1. All Permissions.
  2. "Admin" Role (is_system=True) for System Tenant.
  3. "Member" Role (is_system=True).
  4. Link Permissions to "Admin" Role.

## 5. Caching
Implement invalidation hook:
- On `Role.save()` / `Role.delete()`: Clear cache keys.
- On `UserRole.save()`: Clear user permission cache.

## 6. Testing
- Run `pytest tests/unit/test_rbac_service.py`
- Verify 403 Forbidden when accessing Admin endpoints as Member.
