# RBAC Module - Agent Guidance

## Package Identity

Role-Based Access Control (RBAC) system with roles, permissions, and user-role assignments. Supports tenant-scoped roles with system and custom role types.

## Setup & Run

```bash
# Run RBAC tests
uv run pytest tests/integration/test_rbac*.py -v

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
Permission.USERS_WRITE = "users:write"
Permission.ROLES_READ = "roles:read"
```

## Module Structure

```
src/app/modules/rbac/
├── __init__.py           # Exports RoleService, Permission
├── router.py             # /roles endpoints
├── service.py            # RoleService class
├── models.py             # Role, Permission, UserRole, RolePermission
├── schemas.py            # Pydantic schemas
└── permissions.py        # Permission enum
```

## Touch Points

- **Permission dependency**: `src.app.core.rbac.dependencies::requires_permission`
- **Role service**: `modules/rbac/service.py::RoleService`
- **Permission enum**: `modules/rbac/permissions.py::Permission`
- **Models**: `modules/rbac/models.py` (Role, Permission, associations)

## JIT Index Hints

```bash
# Find permission checks
rg -n "requires_permission" src/app/

# Find role endpoints
rg -n "router\.(get|post|patch|delete)" src/app/modules/rbac/

# Find permission definitions
rg -n "class Permission" src/app/modules/rbac/

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
uv run pytest tests/integration/test_rbac*.py -v && \
uv run mypy src/app/modules/rbac/ && \
uv run ruff check src/app/modules/rbac/
```
