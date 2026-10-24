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

### DO: Address user writes by `id`, never by `username`

`uq_user_username_tenant` is a PARTIAL unique index —
`(username, tenant_id) WHERE is_deleted = false` — so a soft-deleted user's
username can be recycled (see `src/migrations/versions/01_foundation.py`).
FastCRUD's pre-flight `count()` does NOT filter `is_deleted`, so a write
filtered on `username=` can match two rows (live + recycled) and raise
`MultipleResultsFound` → HTTP 500.

```python
# GOOD - the row is already resolved; its PK identifies exactly one row
db_user = await user_service.get_user_for_management(username)
user_id = get_user_attr(db_user, "id")
await crud_users.update(db=db, object=values, id=user_id, commit=False)

# BAD - a recycled username makes the pre-flight COUNT return 2
await crud_users.update(db=db, object=values, username=username, commit=False)
```

This is why `UserService.update`, `update_tier` and `hard_delete` all resolve
the row first (which also performs the tenant match and the ownership check)
and then write by `id`. Pinned by
`tests/unit/modules/users/test_user_service_write_target.py`.

### DO: Invalidate cache — delete in Redis FIRST, then publish the L1 drop

A cache invalidation is two legs, and their ORDER is the correctness
contract (GATE 93vx):

1. **L2 delete** in Redis (`delete`, usually pipelined);
2. **L1 publish** telling the other workers to drop their in-process copy.

If the publish goes first, a peer that re-reads in the window between the
publish and the delete re-caches the value being removed — and that
resurrected value then lives until the L1 TTL. So the delete lands first,
and the publish only happens when the delete actually landed.

```python
# GOOD - drop locally, delete, then publish (batch API, one round trip)
pending = _auth_info_l1_seam.drop_locally(auth_info_keys)
await _batch_invalidate_redis(keys)          # the L2 delete
if pending:
    await _auth_info_l1_seam.publish_many(pending)

# BAD - publishing before the delete lets a peer resurrect the value
await _auth_info_l1_seam.publish_many(pending)
await _batch_invalidate_redis(keys)
```

Both the batch path (`invalidate_user_cache_batch`) and the single-user path
(`invalidate_user_cache`) follow this order. Pinned at the wire by
`tests/unit/modules/_base/test_p3_write_path_rtt.py`, which also asserts that
a FAILED delete publishes nothing at all.

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
