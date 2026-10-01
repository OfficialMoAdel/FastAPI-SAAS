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
- ✅ DO: Hash keys with SHA256 before storing in database
- ❌ DON'T: Store raw API keys in database - always hash
- ❌ DON'T: Allow key prefix changes after creation

### Key Generation Pattern
```python
# Example from src/app/modules/api_keys/service.py:
def _generate_api_key(self, prefix: str = "ak") -> str:
    """Generate secure API key with prefix."""
    random_part = secrets.token_urlsafe(32)
    return f"{prefix}_{random_part}"

def _hash_api_key(self, api_key: str) -> str:
    """Hash API key for storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()
```

### API Key Structure
- Prefix: `ak_` for API keys
- Length: 35 characters (prefix + 32 random chars)
- Storage: SHA256 hash only
- Display: Show full key only on creation

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
- **Grace Period**: During rotation, both old and new keys work for the grace period (default 7 days)
- **valid_from column**: New keys have valid_from = now + grace_period, allowing overlap
- **Audit Logging**: All key operations (create, revoke, rotate) are logged to audit trail

## Pre-PR Checks

```bash
uv run mypy src/app/modules/api_keys/ && uv run ruff check src/app/modules/api_keys/ && uv run pytest tests/unit/modules/auth/test_auth_service.py -v
```