# RBAC Module - Agent Guidance

## Package Identity

Role-Based Access Control (RBAC) system with roles, permissions, and user-role assignments. Supports tenant-scoped roles with system and custom role types.

## Setup & Run

```bash
# Run RBAC tests
uv run pytest tests/integration/modules/rbac/ tests/unit/modules/rbac/ tests/unit/core/rbac/ -v

# Seed permissions
uv run python scripts/seed_rbac.py
```

## Patterns & Conventions

### DO: Use requires_permission dependency

```python
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission

@router.get("/users", dependencies=[Depends(requires_permission(Permission.USERS_READ))])
async def list_users(...):
    ...
```

### DON'T: Check permissions manually in routes

```python
# BAD - Manual permission check
if not user_has_permission(current_user, "users_read"):
    raise ForbiddenException("No permission")
```

### DO: Create tenant-scoped roles

```python
from src.app.modules.rbac.service import RoleService

service = RoleService(db=db, tenant_id=tenant_id)
role = await service.create_role(RoleCreate(name="Editor", description="Can edit content"))
```

### DO: Use permission slugs consistently

```python
# Define permissions with descriptive slugs
Permission.USERS_READ = "users:read"
Permission.USERS_MANAGE = "users:manage"
Permission.ROLES_READ = "roles:read"
```

There is no `USERS_WRITE` — the member is `USERS_MANAGE` (`"users:manage"`).
Every enum member must also exist as a `permissions` table row before it can
be granted (see Module Structure above).

## Module Structure

```
src/app/modules/rbac/
├── __init__.py              # Exports RoleService, the Permission model, schemas, CRUD
├── router.py                # /roles endpoints (prefix /roles)
├── permissions_router.py    # /permissions endpoints (prefix /permissions)
├── service.py               # RoleService class
├── crud.py                  # Role/Permission/UserRole repositories (incl. delete_relations)
├── catalog.py               # Enum→table reconciliation (missing_permission_slugs, reconcile_permission_catalog)
├── models.py                # Permission (table), Role, RolePermission, UserRole
└── schemas.py               # Pydantic schemas
```

There is **no** `modules/rbac/permissions.py`. The `Permission` **enum** used
in route guards lives in `src/app/core/rbac/permissions.py`; the `Permission`
in `models.py` is the separate SQLAlchemy row that mirrors it (see
`catalog.py`).

### Two different `Permission` names

```python
from src.app.core.rbac.permissions import Permission        # str enum — route guards
from src.app.modules.rbac.models import Permission as PermissionRow  # SQLAlchemy model
```

Adding a member to the enum does NOT grant it: `add_permission_to_role`
resolves a slug against the `permissions` **table** and raises
`ModelNotFoundError` when absent. Run `reconcile_permission_catalog` (or
`uv run python scripts/seed_rbac.py`) after adding one.

## Touch Points

- **Permission dependency**: `src.app.core.rbac.dependencies::requires_permission`
- **Role service**: `modules/rbac/service.py::RoleService`
- **Permission enum**: `core/rbac/permissions.py::Permission` (NOT `modules/rbac/permissions.py` — no such file)
- **Models**: `modules/rbac/models.py` (Permission row, Role, associations)

## JIT Index Hints

```bash
# Find permission checks
rg -n "requires_permission" src/app/

# Find role endpoints
rg -n "router\.(get|post|patch|delete)" src/app/modules/rbac/

# Find permission definitions (enum + table mirror)
rg -n "class Permission" src/app/core/rbac/ src/app/modules/rbac/

# Find user role assignments
rg -n "UserRole" src/app/modules/rbac/
```

## Common Gotchas

- **Tenant isolation**: Roles are tenant-scoped - no cross-tenant access
- **System roles**: Cannot be modified or deleted (is_system=True)
- **Permission cache**: Permissions may be cached - allow for propagation delay
- **Superuser bypass**: Superusers bypass all permission checks
- **Cascade deletes**: Deleting role removes all user assignments

## Pre-PR Checks

```bash
uv run pytest tests/integration/modules/rbac/ tests/unit/modules/rbac/ tests/unit/core/rbac/ -v && \
uv run mypy src/app/modules/rbac/ && \
uv run ruff check src/app/modules/rbac/
```
