# Storage Module AGENTS.md

## Package Identity
Abstract cloud storage interface with S3 implementation. Factory pattern for provider selection (S3, Azure, future providers).

## Setup & Run
```bash
# Run tests (requires storage mock)
uv run pytest tests/unit/modules/test_storage_interface.py -v

# Type check
uv run mypy src/app/modules/storage/

# Lint
uv run ruff check src/app/modules/storage/
```

## Patterns & Conventions

### Code Organization
- Factory pattern via `get_storage()` function
- Abstract `StorageProvider` interface
- Concrete implementations (S3Storage, AzureStorage)
- Provider configuration from `storage_settings` module

### Provider Interface
```python
# Pattern from src/app/modules/storage/__init__.py:

class StorageProvider(ABC):
    """Abstract storage provider interface."""

    @abstractmethod
    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str,
    ) -> str:
        """Upload file and return public URL."""
        pass

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """Download file data."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete file."""
        pass
```

### Factory Pattern
```python
# Pattern from src/app/modules/storage/__init__.py:

def get_storage(provider: str, settings: StorageSettingsRead) -> StorageProvider:
    """Get configured storage provider instance."""
    providers = {
        "s3": S3Storage,
        "azure": AzureStorage,
    }
    provider_class = providers.get(provider)
    if not provider_class:
        raise ValueError(f"Unknown storage provider: {provider_name}")
    return provider_class(settings)
```

### S3 Implementation
- Uses the `boto3` library — a **declared runtime dependency** (dxub.9,
  DECIDED: DECLARE; `pyproject.toml` + `uv.lock`), imported lazily at client
  construction. If it is somehow missing, `_s3_factory` raises a
  `ValueError` naming the package; it is never a bare `ModuleNotFoundError`.
- Configuration from `storage_settings` module (AWS keys, bucket, region)
- Supports presigned URLs for temporary access
- Automatic retry on transient errors

### Path Structure
- Files stored with tenant scoping: `tenants/{tenant_id}/files/{uuid}.{ext}`
- Prevents path traversal via key validation
- Supports folder-like structure via `/` separator

## Touch Points / Key Files

- Interface: `src/app/clients/storage/interfaces.py` — `StorageProvider`, `StorageResult` (outbound seam)
- Service: `src/app/modules/storage/service.py` - StorageService, StorageAdminService, StoredObject, Download
- Models: `src/app/modules/storage/models.py` - File, StorageSettings, StorageProviderType (re-exported from the outbound seam)
- Schemas: `src/app/modules/storage/schemas.py` - FileRead, FileUploadResponse, StorageSettingsRead, etc.
- Routers: `src/app/modules/storage/routers/files.py` (/files), `settings_router.py` (/storage)
- Providers + registry (OUTBOUND, card 16): `src/app/clients/storage/s3_storage.py`,
  `azure_storage.py`, `local_storage.py`, `registry.py` (`StorageProviderRegistry`),
  `provider_type.py` (`StorageProviderType`)

## Outbound Seam

The provider interface, the three vendor adapters, and `StorageProviderRegistry`
live in `src/app/clients/storage/`, not here. `src/app/clients/storage/` imports
NO feature module; this package imports downward into it. `StorageAdminService`
asks the registry for a provider by `StorageProviderType` name and never
imports an adapter. The Transfer/Admin split is unchanged: transfer owns file
movement, admin owns settings/decrypt/provider cache (`ResolvedStorage` is the
only object crossing between them).
## JIT Index Hints

- Find provider implementations: `rg -n "class.*Storage" src/app/modules/storage/`
- Find factory method: `rg -n "def get_storage" src/app/modules/storage/`
- Find S3 operations: `rg -n "s3_client\." src/app/clients/storage/s3_storage.py`
- Find tests: `find tests -name "*storage*"`

## Common Gotchas

- File and StorageSettings models are owned here
- Multi-tenant: credentials encrypted per tenant, files isolated by tenant_id
- Always validate keys to prevent path traversal
- S3 credentials should use IAM roles when possible
- Upload failures should rollback database transaction
- Large files require multipart upload (not implemented)
- **`storage_path` is `String(1024)` in the ORM and NOW `String(1024)` in the migration too** (`migrations/versions/05_content_storage.py`), corrected in place by `FastAPI_saas-auz2`. It used to be `String(500)`, and MEASURED on a migrated database (`perf-results/RESULT-20261018-9cmn/`, bead `FastAPI_saas-9cmn`) the column accepted 500 characters, refused 501, and **refused 1024 — the width the ORM declares**. The refusal was an untranslated `asyncpg.StringDataRightTruncationError`, which reached a caller as a 500, not as a validation message. The column was widened, not the model narrowed: 1024 is Azure Blob's maximum blob name, and a key longer than that fails at the storage provider anyway. `mime_type` is the same shape (ORM `String(128)`, column 100) but stays benign and allowlisted, because no MIME type this application writes exceeds 100 characters. `tests/unit/migrations/test_width_parity_storage_and_audit.py` pins the width, so widening the model or re-narrowing the column without changing both fails the test.

## Pre-PR Checks

```bash
uv run mypy src/app/modules/storage/ && uv run ruff check src/app/modules/storage/ && uv run pytest tests/unit/modules/test_storage_interface.py -v
```
