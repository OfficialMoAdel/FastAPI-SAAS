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
├── rate_policy.py             # resolve_rate_limits, build_rate_limit_key, build_api_key_rate_limit_key, RateLimitVerdict
├── api_key_identity.py        # ADR-0023: the API-key cache-key format + "is this cached payload still a grant?"
├── budgets.py                 # the security budget seam: check/probe, BUDGETS, validate_budget_definitions
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

## Rate-limit engine — the degradation contract

`rate_policy.uses_gcra()` is the ONE place that answers "which engine". The
composite Lua scripts hard-code GCRA arithmetic (that is what buys their
single round-trip), so a caller must consult it before taking a composite
path.

**A degradation is never silent.** Two cases mean "the limit was NOT
enforced by the configured algorithm":

* GCRA is configured but the call failed — the fixed window takes over, and
  the 2x boundary burst GCRA exists to remove is back;
* GCRA is configured but the limiter was never initialised (Redis was absent
  at boot, so `init_gcra_rate_limiter` never ran) — the fixed window enforces
  the limit from the start.

Both are counted (`record_fail_open("rate_limit:gcra_degraded" /
"rate_limit:gcra_uninitialized")`) and the flag travels with the result:
`consume()` returns `(is_limited, remaining, retry_after, degraded)`, and the
composites return the same 4-tuple inside their result. The verdict turns the
flag into `X-RateLimit-Algorithm: fixed-window (degraded)`, so the swap is
visible in the response traffic on the authenticated hot path — the one the
merged Lua exists to optimize.

`RateLimiter.check_rate_limit_algorithm` keeps its 3-tuple contract (many
callers and every injected test engine depend on it);
`check_rate_limit_algorithm_with_status` is the variant that carries the
flag.

**Header contract:** `build_verdict` owns the canonical `X-RateLimit-*` set
(`Limit`, `Remaining`, `Period`, `Retry-After`). `X-RateLimit-Reset` is the
WINDOW reset (`rate_policy.window_reset_seconds`) — the remainder of the
window the counter lives in, never the retry-after (which is 0 on every
allowed request).

**Tier policy resolution** (`resolve_rate_limits`): tier-policy cache →
DB → `settings.rate_limit.TIER_RATE_LIMITS` → the generic default. A NAMED
tier that is not configured is a configuration mistake and is logged
(`rate_limit_tier_not_configured`); the anonymous fallback is not. The period
is floored at 1s, so a 429 always carries a `Retry-After`.

**Invalidation:** `rate_limit_cache.invalidate_tier_policy` goes through the
L1 seam, which owns BOTH the policy list and the compiled per-path map.
Popping only the list left the map serving the OLD limits for up to its own
300 s TTL, in the very process that performed the write.

## Touch Points

- **Password hashing**: `password.py::get_password_hash` / `verify_password` (also `needs_rehash`)
- **Token issue/verify**: `jwt_service.py::create_access_token`, `verify_token`
- **Revocation**: `jwt_service.py::is_token_blacklisted` + `token_blacklist_cache.py`
- **Admission**: `request_admission.py::RequestAdmissionService` (skips → identity → ban → limits)
- **API-key budget**: a request carrying `X-API-Key` and no bearer credential is
  metered on the owner's OWN tier budget on the owner's own bucket
  (`build_rate_limit_key`, the same bucket that owner's Bearer session spends)
  when admission could resolve the key's identity — see ADR-0023 below.
  Otherwise (cold key, or an owner with no tier) it is metered on
  `settings.rate_limit.API_KEY_LIMIT` via `build_api_key_rate_limit_key`, keyed by
  source IP because an UNRESOLVED key cannot be allowed to name a bucket. Either
  way it is exempt from the anonymous IP aggregate ceiling and its violations do
  not escalate to auto-ban. `api_key_metered` must mean "an API-key CREDENTIAL was
  presented" — never "no user was resolved", or a resolved key re-enables
  auto-ban for every caller behind a shared NAT. Oracles:
  `tests/unit/core/security/test_api_key_admission_budget.py`,
  `tests/unit/core/security/test_api_key_identity_admission.py`.
- **API-key identity is resolved at the EDGE, once** (ADR-0023,
  `FastAPI_saas-hfps`): `_get_rate_limit_config` calls
  `api_key_identity.resolve_api_key_identity` for a presented key and binds the
  result with `middleware_state.set_api_key_identity`; the API-key auth dep
  passes it to `validate_api_key(api_key, identity=...)`, which then skips the
  cache probe it would otherwise repeat. The request therefore spends the same ONE
  Redis GET it spent before. Do not add a second resolution path, and do not let
  `api_keys/service.py` grow its own copy of the cache-key format or the grant
  rules — `api_key_identity.py` is their one owner, re-exported by the module.
  `_get_cached_tenant_active`, the "not yet valid" refusal and the DB fallback
  still run when an identity is supplied: skipping the probe skips a GET, not a
  check.
- **Request-state seam**: `src/app/core/middleware_state.py` (`get_client_ip_state`,
  `is_whitelisted`, `get_request_id`, `get_token_blacklisted`, `get_api_key_identity`) —
  admission and rate-policy read guard-set state through it; never `getattr(request.state, ...)` directly
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
- **Cache warmup**: blacklist cache is warmed at startup (`composition.py::_warm_token_blacklist_cache`, run in the composition lifespan), capped by `settings.app.TOKEN_BLACKLIST_WARMUP_LIMIT` (0 disables); warmup failure is fail-closed in production
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
