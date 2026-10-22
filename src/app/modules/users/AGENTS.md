# Users Module - Agent Guidance

## Package Identity

User management with tenant isolation, profile management, and authentication integration. Users belong to tenants with role-based access.

## Setup & Run

```bash
# Run user tests
uv run pytest tests/integration/modules/users/test_user_caching.py -v

# Create first superuser
uv run python src/scripts/create_first_superuser.py
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
- **Email is normalised on CREATE *and* UPDATE** — `UserBase.normalize_email` (create) and `UserUpdate.normalize_email` / `UserUpdateInternal.normalize_email` (update) both strip + lowercase. The unique index `uq_user_email_tenant` is case-SENSITIVE, so the update path's duplicate check (`UserService._email_taken`) is case-INSENSITIVE on purpose, matching the login lookup (`crud.py`: `func.lower(User.email) == email.lower()`). **Why this matters**: the check must be at least as strict as the lookup that reads it. If the check were exact-match, `Victim@X.com` could be written beside `victim@x.com`; the case-insensitive login lookup would then match BOTH rows and — ending in `scalar_one_or_none()` — raise `MultipleResultsFound`, 500-ing every login for that address. Do not "simplify" the duplicate check back to an exact comparison.
- **`profile_image_url` is nullable and has NO default.** It used to default to `https://www.profileimageurl.com`, a docs example that had become a runtime value: every user row was written with a third-party avatar URL nobody chose. The column was already `nullable=True` in the database (`01_foundation.py:77`) and `user_preferences` already writes `profile_image_url=None` to clear an avatar, so the ORM's NOT NULL was the outlier. A user with no avatar has `None`, not a placeholder.

## Pre-PR Checks

```bash
uv run pytest tests/integration/modules/users/test_user_caching.py -v && \
uv run ruff check src/app/modules/users/ && \
uv run mypy src/app/modules/users/
```
