# Passkey Module AGENTS.md

## Package Identity
WebAuthn (FIDO2) passwordless authentication. Uses webauthn library for challenge generation, registration, and authentication flows with Redis-backed challenge storage.

## Setup & Run
```bash
# Run tests
uv run pytest tests/unit/test_passkey/test_service.py -v

# Type check
uv run mypy src/app/modules/passkey/

# Lint
uv run ruff check src/app/modules/passkey/
```

## Patterns & Conventions

### Code Organization
- ⚠️ NOTE: PasskeyService is NOT yet migrated to BaseService[T]
- Uses `webauthn` library for WebAuthn operations
- Challenges stored in Redis (5 min TTL)
- Integrates with auth module for user sessions

### WebAuthn Flow
```python
# Pattern from src/app/modules/passkey/service.py:

Registration Flow:
1. User initiates registration
2. Generate registration challenge (credentials, user info)
3. Store challenge in Redis: passkey:challenge:{user_id}, TTL=5min
4. Client creates credential with private key
5. Verify credential and store public key in database
6. Return success

Authentication Flow:
1. User initiates authentication
2. Generate authentication challenge
3. Store challenge in Redis: passkey:auth_challenge:{user_id}
4. Client signs challenge with private key
5. Verify signature against stored public key
6. Create auth session and return
```

### Challenge Management
- Storage: Redis key `passkey:challenge:{user_id}`
- TTL: 5 minutes (300 seconds)
- One-time use: Delete challenge after verification
- Challenge format: JSON from webauthn library

### Credential Storage
- Public key stored as JSON in database
- Includes: credential_id, public_key, sign_count, transports
- Counter stored to prevent replay attacks
- User-scoped via user_id

## Touch Points / Key Files

- Service: `src/app/modules/passkey/service.py` - WebAuthn operations
- Router: `src/app/modules/passkey/router.py` - Registration/auth endpoints
- Schemas: `src/app/modules/passkey/schemas.py` - WebAuthn request/response models
- Model: `src/app/modules/passkey/models.py` - Passkey credential model

## JIT Index Hints

- Find challenge generation: `rg -n "generate.*challenge" src/app/modules/passkey/`
- Find credential registration: `rg -n "register" src/app/modules/passkey/`
- Find authentication: `rg -n "authenticate" src/app/modules/passkey/`
- Find Redis operations: `rg -n "redis\." src/app/modules/passkey/
- Find tests: `find tests -name "*passkey*"`

## Common Gotchas

- PasskeyService does NOT inherit from BaseService (pending migration)
- Challenges are one-time use - always delete after verification
- Always verify signature against stored public key
- Increment sign_count to prevent replay attacks
- Requires HTTPS in production (WebAuthn requirement)
- Challenges expire quickly (5 min) - handle expired gracefully

## Pre-PR Checks

```bash
uv run mypy src/app/modules/passkey/ && uv run ruff check src/app/modules/passkey/ && uv run pytest tests/unit/test_passkey/test_service.py -v
```
