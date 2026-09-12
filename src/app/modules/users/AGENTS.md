# Users Module - Agent Guidance

## Package Identity

User management with tenant isolation, profile management, and authentication integration. Users belong to tenants with role-based access.

## Setup & Run

```bash
# Run user tests
uv run pytest tests/integration/test_user_api.py -v

# Create first superuser
uv run python scripts/create_first_superuser.py
```

## Patterns & Conventions

### DO: Use tenant-scoped queries

```python
from src.app.modules.users.crud import crud_users

users = await crud_users.get_multi(
    db=db,
    tenant_id=current_tenant.id,
    is_deleted=False,
)
```

### DON'T: Query users across tenants

```python
# BAD - No tenant filter
all_users = await crud_users.get_multi(db=db)  # Returns all tenants' users!
```

### DO: Use user_service for business logic

```python
from src.app.modules.users import UserService

user_service = UserService(db)
user = await user_service.get_user(username=username, db=db)
```

### DO: Soft delete users

```python
# Users are soft-deleted (is_deleted=True)
# Password reset on reactivation
await crud_users.update(db=db, id=user_id, object={"is_deleted": True})
```

## Module Structure

```
src/app/modules/users/
├── __init__.py           # Exports user_service
├── router.py             # /users, /user endpoints
├── service.py            # UserService class
├── models.py             # User model, user_roles association
├── schemas.py            # Pydantic schemas
└── crud.py               # FastCRUD operations
```

## Touch Points

- **User service**: `modules/users/service.py::UserService`
- **Models**: `modules/users/models.py::User`
- **Auth integration**: `modules/auth/` for authentication
- **RBAC**: `modules/rbac/` for user roles

## JIT Index Hints

```bash
# Find user endpoints
rg -n "router\.(get|post|patch|delete)" src/app/modules/users/

# Find user models
rg -n "class User" src/app/modules/users/

# Find user queries
rg -n "crud_users\.(get|get_multi|create)" src/app/

# Find user roles
rg -n "user_roles" src/app/modules/users/
```

## Common Gotchas

- **Tenant isolation**: Always filter by tenant_id
- **Soft deletes**: Use is_deleted flag, not hard delete
- **Username uniqueness**: Per-tenant, not global
- **Superuser access**: Superusers see all users
- **Profile updates**: Some fields restricted (email, tenant_id)

## Pre-PR Checks

```bash
uv run pytest tests/integration/test_user_api.py -v && \
uv run ruff check src/app/modules/users/ && \
uv run mypy src/app/modules/users/
```
