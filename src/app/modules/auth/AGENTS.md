# Auth Module - Agent Guidance

## Package Identity

Consolidated "deep" authentication & identity module: login/logout, token refresh and blacklisting, password lifecycle, email verification, OAuth/OIDC SSO (+ tenant SSO settings), WebAuthn passkeys, magic links, and MFA (TOTP + backup codes). Routers stay thin; all logic lives in services.

## Module Structure

```
src/app/modules/auth/
├── __init__.py       # Public facade — lazy exports (routers, services, models)
├── router.py         # Root router assembling auth, sso, sso_settings, passkey, magic_link, mfa
├── service.py        # AuthService (login/register/logout/refresh, constant-time auth)
├── crud.py           # DB CRUD for auth entities
├── models.py         # PasswordResetToken, EmailVerificationToken, SSOSettings,
│                     # UserSSOConnection, PasskeyCredential, MagicLink, MFAMethod
├── schemas.py        # Consolidated DTOs (LoginTokenResponse, LoginMFAResponse, ...)
├── errors.py         # Domain exceptions: AuthError(AppException) → MagicLinkError tree
├── rate_limit.py     # check_auth_rate_limit dependency
├── http_client.py    # Shared async HTTP client (SSO provider calls)
├── tasks.py          # ARQ background tasks (auth cleanup/emails)
├── routers/          # sso.py, sso_settings.py, passkey.py, magic_link.py, mfa.py
├── services/         # passwords.py, tokens.py (TokenIssuer), lockout.py, email_verification.py,
│                     # sso.py, sso_settings.py, passkey.py, magic_link.py, mfa.py
└── providers/        # OAuth adapters: base.py, google.py, github.py, microsoft.py, apple.py
```

## Setup & Run

```bash
# Auth tests (unit + integration)
uv run pytest tests/unit/modules/auth/ tests/integration/test_auth_api.py -v

# Type check
uv run mypy src/app/modules/auth/
```

## Patterns & Conventions

### DO: Use the public facade for cross-module access

```python
from src.app.modules.auth import AuthService, get_auth_service  # lazy import via __getattr__
```
❌ DON'T: Import `auth/service.py` or `auth/router.py` internals from other modules (ruff banned-api enforces this for `service`/`router`).

### DO: Keep auth logic in services — never in routers

Password (login), MFA, passkey, magic-link, and SSO doors verify credentials,
then hand off to `services/tokens.py::TokenIssuer.issue_session` — the one
choreography for claims → token pair → refresh cookie (when a response is
threaded through) → session registration → `auth.login_issued` audit event.
`IssuedSession.access_response()` is the only router-facing projection; it
contains access metadata and never a refresh-token field. Routers only wire
request/response; `service.py::AuthService` owns login, refresh, and logout
orchestration. SSO may transport `Set-Cookie` headers from the Issuance
response, but it must not recreate a refresh token from a response body. Password reset/change and email
verification belong to their subservices
(`services/passwords.py::PasswordLifecycleService`,
`services/email_verification.py::EmailVerificationService`) — call them
directly; `AuthService` carries no pass-throughs.

### DO: Route MFA challenges and completion through `MFAService`

- **Challenge** (`challenge_if_required`): all four login doors (password,
  magic link, passkey, SSO) call this single method instead of hand-rolling
  `is_mfa_enabled` + `TokenIssuer.issue_mfa_pending`. It returns a typed
  `LoginMFAResponse` when MFA is enabled, `None` otherwise.
- **Completion** (`complete_login`): the entire
  `POST /auth/mfa/complete-login` workflow — `mfa_verify` security budget →
  Redis availability → pending-token peek/scope → remaining TTL / IP grace →
  factor verify (`prevent_replay=True`) → consume → account lookup — lives in
  `services/mfa.py::MFAService.complete_login`, which returns `IssuedSession`
  from `TokenIssuer`. `routers/mfa.py::complete_mfa_login` is a thin mapping
  to `MFACompleteLoginResponse` (access token only; refresh stays in the
  cookie set by `TokenIssuer`).
- Per-endpoint rate-limit tests patch
  the security budget seam `core/security/budgets.py::check` (not the
  router's `check_mfa_mutate_rate_limit`, which guards the MFA
  state-mutation endpoints); the
  orchestration contract lives in
  `tests/unit/modules/auth/test_mfa_login_completion.py`.

### DO: Use the subservices for each identity mechanism

- `services/tokens.py::TokenIssuer` — token issuance/refresh
- `services/passwords.py::PasswordLifecycleService` — reset/change + `needs_rehash`
- `services/lockout.py::LoginLockoutPolicy` — brute-force lockout (injected into `AuthService.__init__`)
- `services/mfa.py::MFAService`, `services/passkey.py::PasskeyService`, `services/magic_link.py::MagicLinkService`, `services/sso.py::SSOService` (+ `sso_settings.py`)
- `services/email_verification.py::EmailVerificationService`

### DO: Raise domain errors, not HTTP exceptions

```python
# errors.py — AppException tree, translated centrally in core/errors.py
from src.app.modules.auth.errors import InvalidMagicLinkError
raise InvalidMagicLinkError()          # → 400 via the EXCEPTION_HANDLERS table
```
❌ DON'T: Raise raw `HTTPException` from auth services or add `@router` exception handlers.

### DO: Invalidate the Redis session on logout

Logout resolves the session id from the access token (`sid` claim → `jti` fallback) and calls `session_manager.invalidate_session`; revocation failures are logged, not masked. Tokens themselves are blacklisted via `core.security.blacklist_tokens` (both access + refresh).

### Security seams to respect

- Rate limiting: `rate_limit.py::check_auth_rate_limit` dependency on auth endpoints
- Lockout: `LoginLockoutPolicy` (ban-before-refresh semantics enforced)
- Client IP: always via `core.utils.ip_utils.get_client_ip` (proxy-aware)
- Request state: read guard-set values (`client_ip`, `ip_whitelisted`,
  `request_id`, `is_token_blacklisted`) via the `core.middleware_state` helpers
  (`get_client_ip_state`, `is_whitelisted`, `get_request_id`,
  `get_token_blacklisted`) — never direct `request.state.*` / `getattr` reads;
  wire names on `request.state` stay unchanged
- Events: emit via `core.events` (`EventPriority`) — e.g. security notifications — not direct cross-module service calls (only `users.service.UserService` is a sanctioned in-module dependency for auth-info lookup)

## Touch Points
- Core service: `service.py::AuthService` (login, refresh, logout; + `get_auth_service` factory)
- Session seam: `services/tokens.py::TokenIssuer.issue_session` (claims, token pair, refresh cookie, session registration, login audit); JWT machinery: `core/security/jwt_service.py`
- Errors: `errors.py` (`AuthError`, magic-link tree)
- OAuth providers: `providers/base.py` + google/github/microsoft/apple (use `http_client.py`)
- Auth deps (JWT verification): `src/app/api/deps/auth.py`
- Models registered for Alembic via `src/app/models/__init__.py`

## JIT Index Hints

```bash
# Auth endpoints
rg -n "router\.(post|get)" src/app/modules/auth/

# Find a mechanism's service
rg -n "class (SSO|Passkey|MagicLink|MFA|Password|Lockout|EmailVerification|Token)" src/app/modules/auth/services/

# Cookie handling
rg -n "refresh_token|set_cookie|clear_refresh" src/app/modules/auth/

# OAuth providers
rg -n "class .*Provider" src/app/modules/auth/providers/
```

## Common Gotchas

- **Cookie vs header**: access token in `Authorization: Bearer`, refresh token in an HTTP-only cookie (`login_helpers` in core/security names/sets it)
- **Issuance**: `TokenIssuer` is the sole refresh-cookie writer; auth routers consume only `IssuedSession.access_response()`
- **Logout**: must blacklist BOTH tokens and invalidate the Redis session — loud on failure (no silent swallow)
- **MFA**: login may return a challenge (`LoginMFAResponse`), not tokens — handle both response shapes
- **Secrets**: never log or return tokens; log sanitization strips JWTs automatically
- **SSO state**: pending OAuth/SSO state lives in `core/security/tokens.py::PendingTokenStore`, not in the session

## Pre-PR Checks

```bash
uv run pytest tests/unit/modules/auth/ -v && uv run ruff check src/app/modules/auth/ && uv run mypy src/app/modules/auth/
```

