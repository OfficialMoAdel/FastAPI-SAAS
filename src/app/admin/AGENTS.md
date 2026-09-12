# Admin Panel (CRUDAdmin) - Agent Guidance

## Package Identity

CRUDAdmin-powered operator surface for managing users, tiers, and posts
across ALL tenants. Mounted inside the app lifespan (never at import time);
returns `None` when `CRUD_ADMIN_ENABLED` is false.

## Module Structure

```
src/app/admin/
├── __init__.py      # Empty
├── initialize.py    # create_admin_interface() — CRUDAdmin factory + RLS-bypassing session
└── views.py         # register_admin_views() — model view registration (User, Tier, Post)
```

## Patterns & Conventions

### DO: Cross-tenant sessions via audited `bypass_rls` (C-2 contract)
The admin panel is an operator surface that manages ALL tenants, so its
sessions run inside an audited `bypass_rls` context — fail-closed: if the
bypass audit write fails, the session fails to yield. TenantMiddleware
exempts the admin mount path (`ADMIN_EXEMPT_PREFIX` in
`middleware/tenant.py`), so no `X-Tenant-ID` header is required or honored
here — every admin query intentionally sees all tenants.

```python
# src/app/admin/initialize.py
async with get_db_context() as session:
    async with bypass_rls(session, reason="CRUDAdmin panel data access (cross-tenant operator view)"):
        yield session
```

### DO: Wire all knobs through `settings.admin` / `settings.first_user`
Everything is configured via the `settings.admin` namespace
(`core/config/services.py`): enable flag, mount path, Redis session backend,
allowed IPs/networks, session limits, secure cookies, HTTPS enforcement
(auto-on in production), event/session tracking. Initial superuser creds come
from `settings.first_user` (empty → `initial_admin=None`, no bootstrap user).

### DO: Hash passwords via PasswordTransformer
User creation through the panel hashes passwords with a sync bcrypt wrapper
(`_sync_hash_password`, rounds from `settings.crypt.BCRYPT_ROUNDS`) via
`PasswordTransformer(password_field="password", hashed_field="hashed_password", ...)`.

### DON'T: Add a model view without an explicit `allowed_actions`
Each `admin.add_view(...)` declares the minimum action set (e.g. User has no
`"delete"` — hard deletes go through the API/worker paths, not the panel).

## Touch Points / Key Files

- Admin init: `src/app/admin/initialize.py` — `create_admin_interface()`
- Admin views: `src/app/admin/views.py` — User (view/create/update), Tier (all), Post (all; uses `PostCreateAdmin` schema)
- Mount: `src/app/main.py` `_init_admin()` — lifespan phase; `admin.initialize()` failure is logged and non-fatal (P1-17: API must boot without the panel)
- Mount-path exemption: `src/app/middleware/tenant.py` `_admin_mount_prefix()`
- Settings: `settings.admin.*`, `settings.first_user.*`
- Distinct from `src/app/api/v1/admin/` (circuit-breakers/jobs API endpoints, superuser-guarded routers — separate tree)

## JIT Index Hints

```bash
# Admin config surface
rg -n "CRUD_ADMIN" src/app/core/config/

# Admin panel wiring
rg -n "create_admin_interface|admin.initialize" src/app/

# What the panel can touch
rg -n "add_view" src/app/admin/views.py
```

## Common Gotchas

- **RLS bypass is the contract, not a bug**: never "fix" the bypass by adding tenant scoping to admin sessions — operators manage all tenants (see `core/db/rls.py` for the audit mechanics)
- **Import-time safety**: `create_admin_interface()` runs inside the lifespan; do not call it at module import (P1-17)
- **Empty first_user creds** → no bootstrap superuser is created; ensure one exists via `src/scripts/create_first_superuser.py`
- **`allowed_networks`** (CIDR) is a CRUDAdmin feature for panel access; the app's own `ip_filter.py` middleware does exact-key matching only — do not conflate them

## Pre-PR Checks

```bash
uv run pytest tests/unit/admin/ -v && uv run ruff check src/app/admin/ && uv run mypy src/app/admin/
```
