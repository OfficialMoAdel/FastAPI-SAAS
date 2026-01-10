# Files Module AGENTS.md

## Package Identity
Handles file upload, download, and management with quota enforcement. Integrates with cloud storage (S3, Azure) and storage_settings for provider configuration.

## Setup & Run
```bash
# Run tests
uv run pytest tests/integration/test_files_api.py -v

# Type check
uv run mypy src/app/modules/files/

# Lint
uv run ruff check src/app/modules/files/
```

## Patterns & Conventions

### Code Organization
- ⚠️ NOTE: FileService is NOT yet migrated to BaseService[T] pattern
- Use TenantContext for tenant_id injection
- Enforce quota limits from storage_settings module
- Cleanup uploaded files on processing failures

### File Upload Flow
```python
# Pattern from src/app/modules/files/service.py:
1. Validate file (type, size)
2. Check quota limits via storage_settings
3. Upload to cloud storage (S3/Azure)
4. Create File record in database
5. Return FileResponse
```

### Storage Abstraction
- Use `get_storage()` factory from `src/app/modules/storage/`
- Abstracted provider interface: upload, download, delete
- Files stored with tenant/user scoping in path: `{tenant_id}/{user_id}/{filename}`

### Quota Management
- Check quota before upload via `storage_settings.service.get_active_quota()`
- Track usage per tenant/user
- Block upload if quota exceeded (returns 400 error)

## Touch Points / Key Files

- Service: `src/app/modules/files/service.py` - File operations, quota checks
- Router: `src/app/modules/files/router.py` - Upload/download endpoints
- Schemas: `src/app/modules/files/schemas.py` - File request/response models
- Model: `src/app/modules/files/models.py` - File database model
- Storage abstraction: `src/app/modules/storage/__init__.py` - Cloud storage interface

## JIT Index Hints

- Find quota checks: `rg -n "check.*quota" src/app/modules/files/`
- Find upload flow: `rg -n "upload" src/app/modules/files/`
- Find storage calls: `rg -n "get_storage\(\)" src/app/modules/files/`
- Find tests: `find tests -name "*files*"`

## Common Gotchas

- FileService does NOT inherit from BaseService (pending migration)
- Must wrap file operations in try/except to cleanup on failure
- Quota limits come from storage_settings module
- Files are deleted from cloud AND database (atomic operation)
- Large files require streaming upload (not implemented)

## Pre-PR Checks

```bash
uv run mypy src/app/modules/files/ && uv run ruff check src/app/modules/files/ && uv run pytest tests/integration/test_files_api.py -v
```
