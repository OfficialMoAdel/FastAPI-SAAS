"""Storage module for file storage operations.

This module defines the storage seam: an abstract interface with three
adapters (S3, Azure Blob, local filesystem).

Provider selection is tenant-scoped and lives in
:mod:`src.app.modules.storage_settings.service` — use
``StorageSettingsService.get_provider(tenant_id)`` to obtain a configured
provider instance. There is deliberately no global-settings factory here;
global configuration cannot serve per-tenant provider/credential choices.

Exports:
    - StorageProvider: Abstract base class for storage providers
    - StorageResult: Result dataclass for storage operations
"""

from src.app.modules.storage.interfaces import StorageProvider, StorageResult

__all__ = [
    "StorageProvider",
    "StorageResult",
]
