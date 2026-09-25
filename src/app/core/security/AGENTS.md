# Security Layer - Agent Guidance

## Package Identity

Core security services: JWT issuing/verification (PyJWT), async bcrypt password hashing, token blacklisting (Redis L1 + DB fallback), IP auto-ban, credential encryption, session management, SSRF protection, and rate-limit policy resolution.

> JWT library is **PyJWT** (`pyjwt>=2.8.0`) — not python-jose.

## Module Structure

```
src/app/core/security/
├── __init__.py                # Facade exports
├── password.py                # async get_password_hash / verify_password (bcrypt via executor)
├── jwt_service.py             # create_access_token, create_refresh_token, verify_token,
│                              # is_token_revoked, blacklist_tokens (authoritative)
├── jwt_utils.py               # decode helpers, cached JWT claims on request.state
├── tokens.py                  # TokenGenerator, hash_token, SSO/magic-link pending stores
├── token_blacklist_cache.py   # Redis L1 blacklist cache (+ Lua), warm_cache_from_db
├── auth_prefetch.py           # execute_composite_auth_prefetch (user+tenant in 1 RTT)
├── session_manager.py         # SessionManager (login session registry)
├── login_helpers.py           # refresh-cookie helpers (set/get/clear, cookie names)
├── pending_token_store.py     # PendingTokenStore (MFA/SSO/magic-link pending tokens)
├── auto_ban.py                # IP auto-ban: violations, ban/unban, L1 invalidation
├── rate_policy.py             # resolve_rate_limits, check_security_budget, rate keys, RateLimitVerdict
├── request_admission.py       # RequestAdmissionService: full admission decision (skips, identity, ban, limits)
├── rate_limit_cache.py        # tier rate-policy cache warm/invalidate
├── encryption.py              # CredentialEncryption (Fernet/MultiFernet key rotation)
├── url_validator.py           # SSRF protection for webhook URLs
├── logging.py                 # SecurityEvent types + log_security_event
└── rbac.py → moved            # RBAC checks live in src/app/core/rbac/
```

## Setup & Run

```bash
# Run auth/security tests
uv run pytest tests/unit/ -k "security or jwt or token" -v

# Type check security
uv run mypy src/app/core/security/
```

## Patterns & Conventions

### DO: Use async password operations

```python
from src.app.core.security import get_password_hash, verify_password

hashed = await get_password_hash(password)          # bcrypt runs in a thread executor
ok = await verify_password(plain, hashed)
```

### DON'T: Use synchronous bcrypt in async context

```python
# BAD - blocks the event loop
import bcrypt
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
```

### DO: Use the JWT service for token lifecycle

```python
from src.app.core.security.jwt_service import (
    create_access_token, create_refresh_token, verify_token, blacklist_tokens, TokenType,
)

token = await create_access_token({"sub": user.username, "type": TokenType.ACCESS})
await verify_token(token, expected_token_type=TokenType.ACCESS, db=db)
await blacklist_tokens(access_token, refresh_token, db)   # logout: blacklist BOTH
```

Token verification checks: signature → type → revocation (Redis L1 → DB fallback) → token-version bump.

### DO: Set proper cookie attributes (see `login_helpers.py`)

```python
response.set_cookie(key="refresh_token", value=..., httponly=True, secure=True, samesite="lax")
```

## Touch Points

- **Password hashing**: `password.py::get_password_hash` / `verify_password` (also `needs_rehash`)
- **Token issue/verify**: `jwt_service.py::create_access_token`, `verify_token`
- **Revocation**: `jwt_service.py::is_token_blacklisted` + `token_blacklist_cache.py`
- **Admission**: `request_admission.py::RequestAdmissionService` (skips → identity → ban → limits)
- **Request-state seam**: `src/app/core/middleware_state.py` (`get_client_ip_state`,
  `is_whitelisted`, `get_request_id`, `get_token_blacklisted`) — admission and
  rate-policy read guard-set state through it; never `getattr(request.state, ...)` directly
- **SSO/magic-link pending state**: `tokens.py::get_sso_state_manager`, `get_magic_link_manager`
- **FastAPI auth deps**: `src/app/api/deps/auth.py` (consumes this package)
- **Token blacklist DB side**: `src/app/core/db/crud_token_blacklist.py`

## JIT Index Hints

```bash
# Find JWT token creation
rg -n "create_access_token|create_refresh_token" src/app/

# Find password hashing
rg -n "get_password_hash|verify_password" src/app/

# Find token blacklisting
rg -n "blacklist" src/app/core/security/

# Find security events
rg -n "log_security_event|SecurityEventType" src/app/
```

## Common Gotchas

- **Password hashing**: always `await` the async wrappers — never call bcrypt inline in request paths
- **Token expiry**: short access tokens, longer refresh tokens; claims include token `type` and `jti`
- **Logout**: must blacklist both access and refresh tokens
- **Token-version bump** (password change / global logout) invalidates all issued tokens — don't bypass
- **Cache warmup**: blacklist cache is warmed at startup (`main.py::_warm_token_blacklist_cache`), capped by `settings.app.TOKEN_BLACKLIST_WARMUP_LIMIT` (0 disables); warmup failure is fail-closed in production
- **SSRF**: all outbound webhook URLs pass `url_validator.validate_webhook_url`
- **Whitelist ≠ trust**: a whitelisted IP skips the blacklist and (when
  `IP_WHITELIST_BYPASS_RATE_LIMIT`) rate limiting, but is still authenticated
  and authorized like any other caller. Whitelist membership is an admission
  signal, not an identity — never gate privileged actions on it. A bypass emits
  only `X-RateLimit-Bypassed: true`; it has no numeric quota headers because no
  counter ran.

## Pre-PR Checks

```bash
uv run pytest tests/unit/ -k "security or jwt or token" -v && uv run mypy src/app/core/security/ && uv run ruff check src/app/core/security/
```
