"""Storage provider registry (Open/Closed Principle).

Decouples provider instantiation from hardcoded conditional branches.
New providers can register their factory callable dynamically without
modifying consumer code.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.app.modules.storage.azure_storage import AzureBlobStorage
from src.app.modules.storage.interfaces import StorageProvider
from src.app.modules.storage.local_storage import LocalStorage
from src.app.modules.storage.models import StorageProviderType
from src.app.modules.storage.s3_storage import S3Storage

StorageFactory = Callable[[dict[str, Any]], StorageProvider]


class StorageProviderRegistry:
    """Registry mapping StorageProviderType to factory callables."""

    _factories: dict[StorageProviderType, StorageFactory] = {}

    @classmethod
    def register(cls, provider_type: StorageProviderType, factory: StorageFactory) -> None:
        """Register a provider factory strategy."""
        cls._factories[provider_type] = factory

    @classmethod
    def create(cls, provider_type: StorageProviderType, credentials: dict[str, Any]) -> StorageProvider:
        """Create a configured StorageProvider using registered factory."""
        factory = cls._factories.get(provider_type)
        if factory is None:
            raise ValueError(f"Unknown provider: {provider_type}")
        return factory(credentials)


def _s3_factory(credentials: dict[str, Any]) -> StorageProvider:
    return S3Storage(
        bucket=credentials.get("bucket_name", credentials.get("bucket", "default-bucket")),
        region=credentials.get("region", "us-east-1"),
        aws_access_key_id=credentials.get("access_key_id"),
        aws_secret_access_key=credentials.get("secret_access_key"),
    )


def _azure_factory(credentials: dict[str, Any]) -> StorageProvider:
    return AzureBlobStorage(
        connection_string=credentials["connection_string"],
        container_name=credentials["container_name"],
    )


def _local_factory(credentials: dict[str, Any]) -> StorageProvider:
    return LocalStorage(
        base_path=credentials["base_path"],
        base_url=credentials.get("base_url", "/files"),
    )


# Pre-register built-in providers
StorageProviderRegistry.register(StorageProviderType.S3, _s3_factory)
StorageProviderRegistry.register(StorageProviderType.AZURE, _azure_factory)
StorageProviderRegistry.register(StorageProviderType.LOCAL, _local_factory)

__all__ = [
    "StorageFactory",
    "StorageProviderRegistry",
]
