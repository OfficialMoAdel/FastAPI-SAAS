# Auth Module - Agent Guidance

## Package Identity

Authentication service handling login, logout, token refresh using JWT with HTTP-only cookies. Integrates with security layer for password verification and token management.

## Setup & Run

```bash
# Run auth integration tests
uv run pytest tests/integration/test_auth_api.py -v

# Test token refresh
curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -H "Cookie: refresh_token=<token>"
```

## Patterns & Conventions

### DO: Use AuthService for auth logic

```python
from src.app.modules.auth.service import AuthService

auth_service = AuthService()
tokens = await auth_service.login(
    username="user@example.com",
    password="password123",
    response=response,
    db=db,
)
```

### DON'T: Implement auth logic in routers

```python
# BAD - Auth logic in router
@router.post("/login")
async def login(...):
    # Hash checking, token creation here - don't do this
```

### DO: Handle token refresh properly

```python
@router.post("/refresh")
async def refresh_access_token(request: Request, db: AsyncSession = Depends(async_get_db)):
    refresh_token = request.cookies.get("refresh_token")
    return await auth_service.refresh_access_token(refresh_token, db)
```

### DO: Blacklist tokens on logout

```python
@router.post("/logout")
async def logout(response: Response, access_token: str = Depends(oauth2_scheme)):
    return await auth_service.logout(access_token, refresh_token, response, db)
```

### DO: Use proper HTTP status codes

```python
# 200 - Successful login
# 202 - Async operation accepted
# 401 - Unauthorized (invalid credentials)
# 403 - Forbidden (valid token, no permission)
```

## Module Structure

```
src/app/modules/auth/
├── __init__.py           # Unified lazy exports (AuthService, SSOService, PasskeyService, etc.)
├── router.py             # Root router assembling auth, sso, passkey, magic-link, mfa
├── service.py            # AuthService (core login/register/logout/refresh)
├── models.py             # Consolidated models (tokens, sso, passkeys, magic link, mfa)
├── schemas.py            # Consolidated DTOs across all auth mechanisms
├── crud.py               # Database CRUD for auth entities
├── errors.py             # Domain exceptions (AuthError, MagicLinkError, etc.)
├── rate_limit.py         # check_auth_rate_limit dependency
├── routers/              # Sub-routers (sso, sso_settings, passkey, magic_link, mfa)
├── services/             # Subservices (passwords, tokens, lockout, sso, passkey, magic_link, mfa)
└── providers/            # OAuth provider adapters (google, github, microsoft, apple, base)
```

## Touch Points

- **Auth service**: `modules/auth/service.py::AuthService`
- **Token issuance**: `modules/auth/services/tokens.py::TokenIssuer`
- **User auth**: `src.app.core.security::authenticate_user`
- **Token blacklist**: `src.app.core.db.crud_token_blacklist`

## JIT Index Hints

```bash
# Find auth endpoints
rg -n "router\.(post|get)" src/app/modules/auth/

# Find login/logout logic
rg -n "auth_service\.(login|logout|refresh)" src/app/

# Find token cookie handling
rg -n "refresh_token.*cookie" src/app/modules/auth/

# Find OAuth2 scheme usage
rg -n "oauth2_scheme" src/app/
```

## Common Gotchas

- **Cookie vs Header**: Access token in Authorization header, refresh in cookie
- **HTTP-only cookies**: Cannot be accessed via JavaScript (security feature)
- **Secure flag**: Cookies require HTTPS in production
- **Token expiry**: Access tokens expire; refresh for new access token
- **Logout**: Must blacklist both access and refresh tokens

## Pre-PR Checks

```bash
uv run pytest tests/integration/test_auth_api.py -v && \
uv run ruff check src/app/modules/auth/ && \
uv run mypy src/app/modules/auth/
```
