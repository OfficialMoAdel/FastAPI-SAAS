# Magic Link Module AGENTS.md

## Package Identity
Passwordless authentication using magic links sent via email. Tokens stored in Redis with time-limited expiration, one-time use, and anti-enumeration.

## Setup & Run
```bash
# Run tests
uv run pytest tests/unit/test_magic_link/test_service.py -v

# Type check
uv run mypy src/app/modules/magic_link/

# Lint
uv run ruff check src/app/modules/magic_link/
```

## Patterns & Conventions

### Code Organization
- ⚠️ NOTE: MagicLinkService is NOT yet migrated to BaseService[T]
- Uses Redis for token storage (15 min TTL)
- Queues emails via ARQ background jobs
- Prevents username/email enumeration

### Magic Link Flow
```python
# Pattern from src/app/modules/magic_link/service.py:

1. User requests magic link via email or username
2. Service generates secure token (32 chars, URL-safe)
3. Token stored in Redis: key=magic_link:{token}, value=user_id, TTL=15min
4. Email queued via background job (no blocking)
5. User clicks link → token validated and exchanged for session
```

### Token Security
- Length: 32 characters URL-safe token
- Storage: Redis key `magic_link:{token}`, value = user_id
- TTL: 15 minutes (900 seconds)
- Usage: One-time use (deleted after redemption)
- Anti-enumeration: Always return success regardless of email validity

### Email Queueing
- Use `enqueue_email()` function from ARQ queue
- Don't block request waiting for email
- Email template: `templates/magic_link_email.html`
- Subject: "Your Magic Login Link"

## Touch Points / Key Files

- Service: `src/app/modules/magic_link/service.py` - Token generation, validation
- Router: `src/app/modules/magic_link/router.py` - Request/verify endpoints
- Schemas: `src/app/modules/magic_link/schemas.py` - Request/response models

## JIT Index Hints

- Find token generation: `rg -n "generate.*token" src/app/modules/magic_link/`
- Find Redis operations: `rg -n "redis\." src/app/modules/magic_link/`
- Find email queue: `rg -n "enqueue.*email" src/app/modules/magic_link/`
- Find tests: `find tests -name "*magic_link*"`

## Common Gotchas

- MagicLinkService does NOT inherit from BaseService (pending migration)
- Always use URL-safe tokens for email links
- Don't leak user existence via timing differences
- Delete token from Redis immediately after use (one-time)
- Email sending is async via ARQ - don't wait for it

## Pre-PR Checks

```bash
uv run mypy src/app/modules/magic_link/ && uv run ruff check src/app/modules/magic_link/ && uv run pytest tests/unit/test_magic_link/test_service.py -v
```
