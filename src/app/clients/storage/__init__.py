"""Outbound storage seam: provider interface, vendor adapters, and factories.

This package is the storage client's whole dependency surface. It imports
**no feature module** — before card 16 it imported four of them
(``modules.storage.s3_storage`` / ``azure_storage`` / ``local_storage`` /
``interfaces`` plus the ``models`` enum), which made the outbound side depend
on the feature side while ``StorageAdminService`` reached back here to build a
provider. Construction and vendor behaviour now live on this side and the
feature package imports downward only.

``StorageProviderType`` and the provider interface are re-exported eagerly
because they are dependency-free. Everything that pulls a vendor SDK
(``StorageProviderRegistry``, ``S3Storage``, ``AzureBlobStorage``,
``LocalStorage``) is resolved through :func:`__getattr__` instead, because
``src.app.modules.storage.models`` imports :data:`StorageProviderType` for its
column default: an eager ``registry`` import here would re-create the cycle
``clients.storage -> registry -> s3_storage -> modules.storage -> models ->
clients.storage.provider_type``, and would put boto3/azure on the model-import
path. The lazy pattern is the one ``src/app/modules/storage/__init__.py`` and
``core/resilience/breakers.py`` already use.
"""

from __future__ import annotations

from typing import Any

from src.app.clients.storage.interfaces import StorageProvider, StorageResult
from src.app.clients.storage.provider_type import StorageProviderType

__all__ = [
    "StorageFactory",
    "StorageProvider",
    "StorageProviderRegistry",
    "StorageProviderType",
    "StorageResult",
]

_LAZY: dict[str, str] = {
    "StorageFactory": "src.app.clients.storage.registry",
    "StorageProviderRegistry": "src.app.clients.storage.registry",
    "S3Storage": "src.app.clients.storage.s3_storage",
    "AzureBlobStorage": "src.app.clients.storage.azure_storage",
    "LocalStorage": "src.app.clients.storage.local_storage",
}


def __getattr__(name: str) -> Any:
    """Resolve the vendor-SDK-bearing names on first access."""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)
