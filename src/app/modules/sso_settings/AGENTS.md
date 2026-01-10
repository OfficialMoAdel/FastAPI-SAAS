# SSO Settings Module AGENTS.md

## Package Identity
Manages OAuth/OIDC provider configuration with encryption at rest. Tenant-scoped settings for Google, GitHub, Microsoft, and Apple providers.

## Setup & Run
```bash
# Run tests
uv run pytest tests/unit/test_sso_settings/test_service.py -v

# Type check
uv run mypy src/app/modules/sso_settings/

# Lint
uv run ruff check src/app/modules/sso_settings/
```

## Patterns & Conventions

### Code Organization
- ✅ DO: Use `SSOSettingsService` class
- ✅ DO: Encrypt sensitive fields (client_id, client_secret) via encryption service
- ✅ DO: Use `audit_action` decorator for configuration changes
- ✅ DO: Use `TenantAwareCRUD` for database operations
- ❌ DON'T: Store client secrets in plaintext

### Encryption Pattern
```python
# Pattern from src/app/modules/sso_settings/service.py:

async def create(
    self,
    db: AsyncSession,
    object_in: SSOSettingsCreate,
    tenant_id: str,
    user_id: str,
) -> SSOSettings:
    """Create SSO settings with encryption."""
    # Encrypt sensitive fields
    encrypted_data = encryption_service.encrypt({
        "client_id": object_in.client_id,
        "client_secret": object_in.client_secret,
    })
    # Store encrypted fields
    return await super().create(
        db,
        object_in,
        tenant_id=tenant_id,
        encrypted_client_id=encrypted_data["client_id"],
        encrypted_client_secret=encrypted_data["client_secret"],
    )
```

### Audit Logging
- Use `audit_action` decorator for create/update/delete
- Logs: who changed what, when, tenant_id
- Stores in `audit_logs` module

### Tenant Scoping
- Settings are per-tenant (not per-user)
- Each tenant can configure multiple providers
- Provider name is unique within tenant

## Touch Points / Key Files

- Service: `src/app/modules/sso_settings/service.py` - Settings management, encryption
- CRUD: `src/app/modules/sso_settings/crud.py` - Database operations
- Schemas: `src/app/modules/sso_settings/schemas.py` - Request/response models
- Router: `src/app/modules/sso_settings/router.py` - API endpoints
- Model: `src/app/modules/sso_settings/models.py` - SSOSettings database model

## JIT Index Hints

- Find encryption calls: `rg -n "encrypt" src/app/modules/sso_settings/`
- Find audit decorators: `rg -n "@audit_action" src/app/modules/sso_settings/`
- Find service methods: `rg -n "async def (create|get|update|delete)" src/app/modules/sso_settings/service.py`
- Find tests: `find tests -name "*sso_settings*"`

## Common Gotchas

- SSOSettingsService does NOT inherit from BaseService (pending migration)
- Always encrypt client_id and client_secret
- Decrypt on read, encrypt on write
- Settings are tenant-scoped, not user-scoped
- Provider name must be unique per tenant
- Audit logs capture all configuration changes

## Pre-PR Checks

```bash
uv run mypy src/app/modules/sso_settings/ && uv run ruff check src/app/modules/sso_settings/ && uv run pytest tests/unit/test_sso_settings/test_service.py -v
```
