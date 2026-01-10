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
- Uses `boto3` library
- Configuration from `storage_settings` module (AWS keys, bucket, region)
- Supports presigned URLs for temporary access
- Automatic retry on transient errors

### Path Structure
- Files stored with tenant/user scoping: `{tenant_id}/{user_id}/{filename}`
- Prevents path traversal via key validation
- Supports folder-like structure via `/` separator

## Touch Points / Key Files

- Interface: `src/app/modules/storage/__init__.py` - StorageProvider, factory
- S3 impl: `src/app/modules/storage/s3_storage.py` - S3Storage implementation
- Azure impl: `src/app/modules/storage/azure_storage.py` - AzureStorage implementation (if exists)
- Settings: `src/app/modules/storage_settings/` - Provider configuration

## JIT Index Hints

- Find provider implementations: `rg -n "class.*Storage" src/app/modules/storage/`
- Find factory method: `rg -n "def get_storage" src/app/modules/storage/`
- Find S3 operations: `rg -n "s3_client\." src/app/modules/storage/s3_storage.py`
- Find tests: `find tests -name "*storage*"`

## Common Gotchas

- No database models - pure interface layer
- Provider settings come from storage_settings module
- Always validate keys to prevent path traversal
- S3 credentials should use IAM roles when possible
- Upload failures should rollback database transaction
- Large files require multipart upload (not implemented)

## Pre-PR Checks

```bash
uv run mypy src/app/modules/storage/ && uv run ruff check src/app/modules/storage/ && uv run pytest tests/unit/modules/test_storage_interface.py -v
```
