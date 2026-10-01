# Posts Module AGENTS.md

## Package Identity
Blog post CRUD with ownership enforcement and multi-tenancy support. Follows BaseService pattern with ownership checking and tenant context injection.

## Setup & Run
```bash
# Run tests
uv run pytest tests/unit/modules/posts/test_service.py -v

# Type check
uv run mypy src/app/modules/posts/

# Lint
uv run ruff check src/app/modules/posts/
```

## Patterns & Conventions

### Code Organization
- ✅ DO: Inherit from `BaseService[T]` in `src/app/modules/_base/service.py`
- ✅ DO: Use `TenantContext` for tenant_id injection
- ✅ DO: Check ownership before update/delete operations
- ✅ DO: Use `TenantAwareCRUD` for database operations
- ❌ DON'T: Allow users to modify other users' posts

### Service Pattern
```python
# Example from src/app/modules/posts/service.py:

class PostService(BaseService[Post]):
    """Service for managing blog posts."""

    async def create(
        self,
        db: AsyncSession,
        object_in: PostCreate,
        tenant_id: str,
        user_id: str,
    ) -> Post:
        """Create a new post."""
        return await super().create(db, object_in, tenant_id=tenant_id)

    async def _check_ownership(
        self, post: Post, user_id: str
    ) -> None:
        """Check if user owns the post."""
        if post.user_id != user_id:
            raise ForbiddenException("You don't own this post")
```

### Ownership Checking
- All update/delete operations call `_check_ownership()`, which delegates to
  the core ownership seam (`core/security/ownership.py::require_resource_ownership`)
- Raises `ForbiddenError` if user is not owner; superusers bypass via the seam
- Use `TenantContext` dependency injection for tenant_id

### Database Operations
- Use `TenantAwareCRUD` for CRUD with tenant scoping
- Automatically filters by `tenant_id`
- Supports pagination via `page` and `page_size` parameters

## Touch Points / Key Files

- Service: `src/app/modules/posts/service.py` - Post operations, ownership checks
- CRUD: `src/app/modules/posts/crud.py` - Database operations
- Schemas: `src/app/modules/posts/schemas.py` - Request/response models
- Router: `src/app/modules/posts/router.py` - API endpoints
- Model: `src/app/modules/posts/models.py` - Post database model

## JIT Index Hints

- Find ownership checks: `rg -n "_check_ownership" src/app/modules/posts/`
- Find service methods: `rg -n "async def (create|get|update|delete)" src/app/modules/posts/service.py`
- Find router endpoints: `rg -n "@router\.(get|post|put|delete)" src/app/modules/posts/`
- Find tests: `find tests -name "*posts*"`

## Common Gotchas

- Always check ownership before update/delete
- Use `TenantContext` for tenant_id injection
- Posts are scoped to tenant AND user
- Pagination defaults to page=1, page_size=20
- Deleted posts are soft-deleted (check `is_deleted` flag if applicable)

## Pre-PR Checks

```bash
uv run mypy src/app/modules/posts/ && uv run ruff check src/app/modules/posts/ && uv run pytest tests/unit/modules/posts/test_service.py -v
```
