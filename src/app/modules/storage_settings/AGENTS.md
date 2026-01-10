# Storage Settings Module AGENTS.md

## Package Identity
Manages cloud storage provider configuration (S3, Azure). Quota management, provider settings, and tenant-scoped storage limits.

## Setup & Run
```bash
# Run tests
uv run pytest tests/integration/test_storage_settings_api.py -v

# Type check
uv run mypy src/app/modules/storage_settings/

# Lint
uv run ruff check src/app/modules/storage_settings/
```

## Patterns & Conventions

### Code Organization
- Service class for settings and quota management
- Integrates with `storage` module for provider abstraction
- Quota limits enforce on file uploads
- Per-tenant configuration

### Quota Management
```python
# Pattern from src/app/modules/storage_settings/service.py:

async def get_active_quota(
    self,
    db: AsyncSession,
    tenant_id: str,
    user_id: str | None = None,
) -> QuotaInfo:
    """Get active quota limits and usage."""
    settings = await self.get_by_tenant(db, tenant_id)
    usage = await self._calculate_usage(db, tenant_id, user_id)
    return QuotaInfo(
        max_size_gb=settings.max_size_gb,
        max_files=settings.max_files,
        current_size_gb=usage.size_gb,
        current_files=usage.file_count,
    )
```

### Provider Configuration
- Settings include: provider_type, credentials, bucket/container name, region
- Credentials encrypted at rest (for S3 access keys, Azure connection strings)
- Active/inactive status per provider
- Failover to backup provider if configured

### Usage Calculation
- Aggregates from `files` module (file sizes)
- Can filter per-user or per-tenant
- Updates on file upload/delete events
- Cached in Redis for performance (5 min TTL)

## Touch Points / Key Files

- Service: `src/app/modules/storage_settings/service.py` - Settings, quota management
- CRUD: `src/app/modules/storage_settings/crud.py` - Database operations
- Schemas: `src/app/modules/storage_settings/schemas.py` - Request/response models
- Router: `src/app/modules/storage_settings/router.py` - API endpoints
- Model: `src/app/modules/storage_settings/models.py` - StorageSettings database model

## JIT Index Hints

- Find quota checks: `rg -n "quota" src/app/modules/storage_settings/`
- Find provider config: `rg -n "provider" src/app/modules/storage_settings/`
- Find usage calculation: `rg -n "usage" src/app/modules/storage_settings/`
- Find tests: `find tests -name "*storage_settings*"`

## Common Gotchas

- StorageSettingsService does NOT inherit from BaseService (pending migration)
- Credentials encrypted at rest
- Quota checks happen BEFORE upload (not after)
- Usage is cached - may be slightly stale
- Provider switching clears existing cache
- Quota limits are enforced per-tenant AND optionally per-user

## Pre-PR Checks

```bash
uv run mypy src/app/modules/storage_settings/ && uv run ruff check src/app/modules/storage_settings/ && uv run pytest tests/integration/test_storage_settings_api.py -v
```
