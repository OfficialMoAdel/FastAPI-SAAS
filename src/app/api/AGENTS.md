# FastAPI SaaS API Layer - Agent Guidance

## Package Identity

API versioning, dependency injection seams, OpenAPI customization, and route aggregation. Routers live in feature modules; this package aggregates them and provides shared FastAPI dependencies.

## Structure

```
src/app/api/
├── __init__.py        # get_router() — wires v1 router into the app
├── deps/              # Shared dependency injection seams
│   ├── auth.py            # get_current_user, get_current_superuser, optional auth
│   ├── core.py            # DB session, pagination, common deps
│   ├── request_context.py # RequestContext resolution (principal/tenant seams)
│   └── tenant.py          # Tenant-scoping dependencies
├── monitoring.py      # API monitoring endpoints
├── openapi.py         # setup_custom_openapi(app) — single OpenAPI wiring point
└── v1/
    ├── __init__.py    # v1 router: aggregates ALL module routers under /v1
    └── admin/         # Superuser-protected admin endpoints
        ├── circuit_breakers.py
        └── jobs.py
```

## Patterns & Conventions

### Route Registration
✅ DO: Register new module routers in `src/app/api/v1/__init__.py` (one import + one `include_router`)
```python
# src/app/api/v1/__init__.py
from src.app.modules.posts.router import router as posts_router
router.include_router(posts_router)
```
❌ DON'T: Create endpoint files under `v1/` — there are none today (only `admin/`); endpoints belong in `src/app/modules/{module}/router.py`.

### Dependencies
✅ DO: Import deps from the package facade, not internals
```python
from src.app.api.deps import get_current_superuser, get_current_user
```
✅ DO: Use `Annotated` for clean dependency injection
```python
@router.get("/me")
async def get_me(current_user: Annotated[UserRead, Depends(get_current_user)]) -> UserRead:
    return current_user
```
❌ DON'T: Re-implement auth/tenant resolution inside a module router — the seams live in `deps/`.

### Thin Router Pattern
✅ DO: Keep routers thin — delegate all logic to the module service
```python
@router.post("/", response_model=PostRead)
async def create_post(
    post: PostCreate,
    request_context: Annotated[RequestContext, Depends(get_request_context)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> PostRead:
    return await PostService(db).create(post=post, ctx=request_context)
```

### Response Models
✅ DO: Always specify `response_model` for automatic serialization.
❌ DON'T: Return raw dicts from endpoints.

## Touch Points / Key Files

- Router aggregation: `src/app/api/v1/__init__.py` (prefix `/v1`; admin routers protected with `Depends(get_current_superuser)`)
- App-level wiring: `src/app/api/__init__.py::get_router`
- OpenAPI schema: `src/app/api/openapi.py::setup_custom_openapi` (wired once in `main.py`)
- Deps: `src/app/api/deps/auth.py`, `core.py`, `request_context.py`, `tenant.py`
- Admin endpoints: `src/app/api/v1/admin/circuit_breakers.py`, `jobs.py`

## JIT Index Hints

```bash
# Find all router definitions
rg -n "router = APIRouter" src/app/modules/

# Find endpoint decorators
rg -n "@router\.(get|post|put|delete)" src/app/modules/

# Find dependency functions
rg -n "^async def get_" src/app/api/deps/

# Verify route registration
uv run python -c "from src.app.main import app; print([r.path for r in app.routes])"
```

## Common Gotchas

- **Prefixes:** The `/v1` prefix is applied once in `v1/__init__.py`. Module routers define routes relative to their own prefix — never double-prefix.
- **Admin protection:** Everything under `v1/admin/` requires superuser via `dependencies=[Depends(get_current_superuser)]` — keep it that way.
- **Single OpenAPI hook:** Customize schemas in `api/openapi.py`, not by patching `app.openapi` elsewhere.
- **Optional auth:** Some endpoints accept anonymous requests — use the optional-auth dep from `deps/auth.py` rather than hand-rolling token parsing.

## Pre-PR Checks

```bash
uv run mypy src/app/api/ && uv run ruff check src/app/api/ && uv run pytest tests/contract/ -v
```
