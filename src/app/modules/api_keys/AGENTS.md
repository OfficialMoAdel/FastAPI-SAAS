# API Keys Module AGENTS.md

## Package Identity
Manages API key generation, validation, and revocation for programmatic access. Uses secure key generation with prefix-based structure and SHA256 hashing.

## Setup & Run
```bash
# Run tests
uv run pytest tests/unit/modules/auth/test_auth_service.py -v

# Type check
uv run mypy src/app/modules/api_keys/

# Lint
uv run ruff check src/app/modules/api_keys/
```

## Patterns & Conventions

### Code Organization
- ✅ DO: Inherit from `BaseService[T]` in `src/app/modules/_base/service.py`
- ✅ DO: Use `TenantAwareCRUD` for database operations
- ✅ DO: Generate secure keys using `secrets.token_urlsafe()`
- ✅ DO: Hash keys with `hash_token` from `src/app/core/security/tokens.py` before storing
- ❌ DON'T: Store raw API keys in database - always hash
- ❌ DON'T: Allow key prefix changes after creation

### Key Generation Pattern
```python
# src/app/modules/api_keys/service.py — the real implementation
def _generate_key(self) -> tuple[str, str, str]:
    """Return (full_key, prefix, hashed_key). KEY_LENGTH = 32 random chars,
    KEY_PREFIX_LENGTH = 8 prefix chars stored in the clear for display."""
    raw_key = secrets.token_urlsafe(self.KEY_LENGTH)
    prefix = raw_key[: self.KEY_PREFIX_LENGTH]
    return raw_key, prefix, self._hash_key(raw_key)

def _hash_key(self, key: str) -> str:
    """Peppered HMAC via the token seam (core/security/tokens.py), NOT SHA256."""
    from src.app.core.security.tokens import hash_token
    return hash_token(key)
```

### API Key Structure
- Material: `secrets.token_urlsafe(32)` — URL-safe, 32 bytes of entropy
- Stored prefix: the first 8 characters (`KEY_PREFIX_LENGTH`), for display
- Storage: peppered HMAC via `hash_token` only; the raw key is returned once
- Display: show the full key only on creation

## Issuing a key for somebody else (FastAPI_saas-aepv)

`APIKeyCreate.user_id` is an OPTIONAL target; it defaults to `None`, which mints
for the caller exactly as before. The gate is unchanged: the whole router already
requires `Permission.USERS_MANAGE`. `APIKeyService._resolve_key_owner` decides
what happens, and the four outcomes are the whole contract:

- **no target, or your own id** → the caller, with **no query at all**. A
  capability that costs a users read on every ordinary key is one people work
  around.
- **another user, active, same tenant** → the key and the audit row are issued
  for THAT user (`audit(..., user_id=owner, details={"issued_for_user_id": ...})`
  so the trail still says who asked).
- **absent, other tenant, or soft-deleted** → `ModelNotFoundError`. All three
  are one answer on purpose: anything else turns this endpoint into an oracle for
  which user ids exist.
- **present but inactive** → `ForbiddenError`. The row is already visible to
  this tenant, so naming it leaks nothing, and 403 says more than 404.

Never repurpose `create_api_key`'s `user_id` parameter to mean the target: the
router passes the CALLER there, and every other operation scopes to the caller.
Oracle: `tests/unit/modules/api_keys/test_issue_key_for_another_user.py`.

## Touch Points / Key Files

- Service: `src/app/modules/api_keys/service.py` - Key generation, validation, revocation
- CRUD: `src/app/modules/api_keys/crud.py` - Database operations
- Schemas: `src/app/modules/api_keys/schemas.py` - Request/response models
- Router: `src/app/modules/api_keys/router.py` - API endpoints
- Model: `src/app/modules/api_keys/models.py` - APIKey database model

## JIT Index Hints

- Find API key validation: `rg -n "validate_api_key" src/app/modules/api_keys/`
- Find key generation: `rg -n "_generate.*key" src/app/modules/api_keys/`
- Find prefix usage: `rg -n "['\"]ak_" src/app/modules/api_keys/`
- Find tests: `find tests -name "*api_keys*"`

## Common Gotchas

- API keys are hashed with SHA256 - never store raw keys
- Only return full key on creation, use `******` on subsequent reads
- Keys are tenant-scoped via `tenant_id`
- Revoked keys cannot be reactivated - create new instead
- Rate limiting applies to API key authentication
- **Scopes are fail-closed, and the default is `["read"]`**:
  `api_key_scope_allows` (`src/app/api/deps/auth.py:345`) denies a key with
  an empty scope set everywhere, so `APIKeyBase.scopes`
  (`schemas.py:17`) defaults to `["read"]` — an omitted field mints a
  READ-ONLY key, not a dead one. `create_api_key` only persists a TRUTHY
  `scopes` (`service.py:113`), so an explicit `scopes: []` or `scopes: null`
  in the request body is a STATEMENT, not an omission: it still yields a key
  that is denied everywhere (403 `SCOPE_MISSING`). Pass `["admin"]` for the
  wildcard, or `read:<resource>` / `write:<resource>` to qualify. Rotate
  copies the old key's scopes verbatim (`service.py:716`), so a scoped key
  stays scoped across rotation. Oracle:
  `tests/unit/modules/api_keys/test_default_scopes.py`.
- **`scopes` is a `Text` column and that is load-bearing**
  (FastAPI_saas-vthy): the model maps `Text` (`models.py:55`),
  `create_api_key` stores `json_dumps(...)` AS A STRING (`service.py:113`),
  `validate_api_key` reads the value back with `json_loads(key.scopes)`,
  and rotate re-binds whatever came back verbatim. Measure
  this: on a `jsonb` column SQLAlchemy's asyncpg dialect hands the service a
  Python **list**, `json_loads` raises `JSONDecodeError`, and every
  `X-API-Key` request 500s — while `POST /api/v1/api-keys` keeps working, so
  the fault reads like an auth problem rather than a schema one. Don't
  "improve" the column to `jsonb`/`JSON` without changing all three call sites
  at once. Oracle:
  `tests/unit/migrations/test_api_keys_column_type_parity.py`; measurement in
  `perf-results/RESULT-20261017-vthy/api_keys_column_type_parity.json`.
- **The cache-key format and the grant rules belong to core**
  (FastAPI_saas-hfps, [ADR-0023](../../../../docs/adr/0023-admission-owns-api-key-identity.md)):
  `API_KEY_CACHE_PREFIX`, `API_KEY_CACHE_TTL`, `api_key_cache_key`,
  `read_cached_grant` and `resolve_api_key_identity` live in
  `src/app/core/security/api_key_identity.py` and are RE-EXPORTED here as
  `X as X` (a bare import would be deleted as "unused" by ruff's F401 on the
  next run, silently breaking every importer of `service.API_KEY_CACHE_PREFIX`).
  Why: admission reads this cache BEFORE this module does, to choose the
  caller's rate-limit bucket, and two owners of "which entry belongs to this
  key" would eventually disagree. `validate_api_key(api_key, identity=...)`
  accepts the grant admission already resolved: it skips the cache probe
  (the same ONE Redis GET, not an extra one) and still runs
  `_get_cached_tenant_active`, the "not yet valid" refusal and the DB fallback.
  The cached payload now carries the owner's `tier_id` / `tier_name`, learned
  by `_validate_from_db`'s LEFT JOIN on user → tier — so a mocked key row in a
  test must model `.user` or `json_dumps` will refuse the MagicMock. Oracle:
  `tests/unit/core/security/test_api_key_identity_admission.py`.
- **Grace Period**: During rotation, both old and new keys work for the grace period (default 7 days)
- **valid_from column**: New keys have valid_from = now + grace_period, allowing overlap
- **Audit Logging**: All key operations (create, revoke, rotate) are logged to audit trail

## Pre-PR Checks

```bash
uv run mypy src/app/modules/api_keys/ && uv run ruff check src/app/modules/api_keys/ && uv run pytest tests/unit/modules/auth/test_auth_service.py -v
```