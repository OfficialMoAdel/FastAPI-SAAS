# RequestContext — Single Injectable Request Principal

> Architecture deepening #1: replaces the repeated `Depends(get_current_user)` +
> `Depends(get_current_tenant)` (+ `db`) ceremony that was previously duplicated
> at ~150 endpoint parameter positions.

## What it is

`RequestContext` (in `src/app/api/deps/request_context.py`) is a frozen,
slotted dataclass bundling the request's authenticated principal:

| Field    | Type               | Provided by (cached per request) |
|----------|--------------------|----------------------------------|
| `user`   | `AuthenticatedUser`| `get_current_user` / `get_auth_context` |
| `tenant` | `TenantContext`    | `get_current_tenant` (TenantMiddleware) |
| `db`     | `AsyncSession`     | `async_get_db` |

**Zero extra cost:** every underlying dependency is resolved once per request by
FastAPI's dependency cache; `get_request_context` only composes cached results.
Authentication, tenant-membership validation, and session-revocation checks run
exactly where they ran before.

## Usage

```python
from src.app.api.deps import RequestContextDep

@router.post("/upload", dependencies=[Depends(requires_permission(Permission.FILES_WRITE))])
async def upload_file(
    ctx: RequestContextDep,
    service: FileService = Depends(get_file_service),
) -> Any:
    result = await service.upload_file(
        tenant_id=ctx.tenant_id,   # UUID of current tenant
        user_id=ctx.user_id,       # int id of authenticated user
        ...
    )
```

### Helpers on the context

- `ctx.user_id` — authenticated user's integer id.
- `ctx.tenant_id` — current tenant id (alias-safe across context/model types).
- `ctx.is_superuser` — principal bypasses normal scoping.
- `ctx.has_tenant` — `False` for tenant-less/sentinel paths (`NO_TENANT`).
- `ctx.require_tenant()` — tenant or `ForbiddenException`.
- `ctx.owns(owner_id)` / `ctx.require_ownership(owner_id)` — centralizes the
  ownership-check bug class (superusers exempt).

## Migration guide

Before → after:

```python
# Before
tenant: Tenant = Depends(get_current_tenant),
_user: Any = Depends(get_current_user),

# After
ctx: RequestContextDep,
```

Rules:

1. Replace the pair with one `ctx: RequestContextDep`; place it **before any
   defaulted parameters** in the signature (Python syntax requirement).
2. `tenant.id` → `ctx.tenant_id`; `get_user_attr(_user, "id")` → `ctx.user_id`.
3. Keep RBAC permission guards (`requires_permission(...)`) as route-level
   `dependencies=[...]` — they are orthogonal to the context.
4. Unit tests calling endpoints directly should construct
   `RequestContext(user=..., tenant=..., db=...)` instead of passing separate
   mocks. HTTP-level tests override `get_request_context`, `get_current_user`,
   or `get_current_tenant` as before — all still work.

## Status

- ✅ Migrated (2025-09-01): all 19 module routers now use `RequestContextDep`:
  `files` (reference), `users` (7 endpoints), `security` (2 passkey endpoints;
  14 admin `get_current_superuser` guards preserved per “keep both” decision),
  `ai` (9/11 — 2 `RateLimitedUser` streaming endpoints kept with dedicated
  limiter), `posts` (5/6 — admin `erase_db_post` kept superuser guard),
  `webhooks` (11/11), `audit_logs` (5), `api_keys` (4), `auth` (3),
  `billing` (6), `dashboard` (1), `feature_flags` (5), `mfa` (6),
  `notifications` (6), `passkey` (4), `sso` (2), `sso_settings` (5),
  `storage_settings` (3), `tasks` (2), `tenants` (6), `user_preferences` (4).
  Zero direct `Depends(get_current_user)` / `Depends(tenant_dependency)` /
  `Depends(async_get_db)` remain in any module router (verified via
  `grep -rn`); remaining `async_get_db` uses are only inside `_get_*_service`
  factories where allowed. Unauthenticated / public routes (login, register,
  SSO callbacks, health) intentionally keep `async_get_db` and do not take
  `RequestContextDep`.

## Tests

- `tests/unit/api/deps/test_request_context.py` — context semantics + FastAPI
  composition/override behavior.
