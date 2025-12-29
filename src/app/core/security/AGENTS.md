# Security Layer - Agent Guidance

## Package Identity

JWT authentication, password hashing (bcrypt), token blacklisting, and OAuth2 utilities. Uses python-jose for JWT and bcrypt for password hashing.

## Setup & Run

```bash
# Verify security config
uv run python -c "from src.app.core.security import SECRET_KEY; print('Security module loaded')"

# Run auth tests
uv run pytest tests/integration/test_auth_api.py -v
```

## Patterns & Conventions

### DO: Use async password operations

```python
from src.app.core.security import get_password_hash, verify_password

async def create_user(password: str):
    hashed = await get_password_hash(password)
    return hashed

async def authenticate(password: str, hashed: str):
    return await verify_password(password, hashed)
```

### DON'T: Use synchronous bcrypt in async context

```python
# BAD - Blocking call
import bcrypt
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())  # Blocks event loop!
```

### DO: Use token blacklisting for logout

```python
from src.app.core.security import blacklist_tokens

async def logout(access_token: str, refresh_token: str, db: AsyncSession):
    await blacklist_tokens(access_token, refresh_token, db)
```

### DO: Set proper cookie attributes

```python
response.set_cookie(
    key="refresh_token",
    value=refresh_token,
    httponly=True,      # Prevents XSS
    secure=True,        # HTTPS only
    samesite="lax",
    max_age=max_age,
)
```

### DO: Use proper JWT claims

```python
await create_access_token(data={
    "sub": user["username"],
    "tenant_id": str(tenant_id),
    "role": "admin"
})
```

## Module Structure

```
src/app/core/security/
├── __init__.py           # Main exports: bcrypt, JWT, token functions
├── rbac.py               # RBAC permission checking
├── dependencies.py       # FastAPI dependencies for auth
└── oauth2.py             # OAuth2 scheme configurations
```

## Touch Points

- **Password hashing**: `core/security/__init__.py::get_password_hash`
- **Token creation**: `core/security/__init__.py::create_access_token`
- **Token verification**: `core/security/__init__.py::verify_token`
- **RBAC checks**: `core/security/rbac.py::requires_permission`

## JIT Index Hints

```bash
# Find JWT token creation
rg -n "create_access_token" src/app/

# Find password hashing
rg -n "get_password_hash\|verify_password" src/app/

# Find token blacklisting
rg -n "blacklist" src/app/core/security/

# Find permission checks
rg -n "requires_permission" src/app/
```

## Common Gotchas

- **Password hashing**: Always use bcrypt with proper salt rounds
- **Token expiry**: Access tokens short (15min), refresh tokens longer (7 days)
- **Cookie security**: Set `httponly=True`, `secure=True` in production
- **Token blacklisting**: Required for proper logout functionality
- **Superuser bypass**: Check `is_superuser` before permission checks

## Pre-PR Checks

```bash
uv run pytest tests/integration/test_auth_api.py -v && \
uv run mypy src/app/core/security/ && \
uv run ruff check src/app/core/security/
```
