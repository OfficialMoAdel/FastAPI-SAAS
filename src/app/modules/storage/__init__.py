"""Storage module for file storage operations.

This module provides a unified interface for file storage across
different providers (S3, Azure Blob, GCS, local filesystem).

Exports:
    - StorageProvider: Abstract base class for storage providers
    - StorageResult: Result dataclass for storage operations
    - S3Storage: AWS S3 storage implementation
    - get_storage: Factory function for creating storage instances

Usage:
    from src.app.modules.storage import get_storage, StorageProvider

    # Create S3 storage instance
    storage = get_storage(
        provider="s3",
        bucket="my-bucket",
        region="us-east-1"
    )

    # Upload a file
    result = await storage.upload(file, "uploads/file.txt", "text/plain")
"""

from __future__ import annotations

from src.app.modules.storage.interfaces import StorageProvider, StorageResult
from src.app.modules.storage.s3_storage import S3Storage


def get_storage(
    provider: str = "s3",
    **kwargs,
) -> StorageProvider:
    """Factory function for creating storage provider instances.

    Args:
        provider: Storage provider type ("s3", "azure", "gcs", "local").
        **kwargs: Provider-specific configuration.

    Returns:
        StorageProvider instance.

    Raises:
        ValueError: If provider is not supported.

    Usage:
        storage = get_storage(
            provider="s3",
            bucket="my-bucket",
            region="us-west-2"
        )
    """
    providers = {
        "s3": S3Storage,
        # Future providers:
        # "azure": AzureBlobStorage,
        # "gcs": GoogleCloudStorage,
        # "local": LocalFileStorage,
    }

    if provider not in providers:
        supported = ", ".join(providers.keys())
        raise ValueError(f"Unsupported storage provider: {provider}. Supported providers: {supported}")

    storage_class = providers[provider]
    return storage_class(**kwargs)


__all__ = [
    # Interfaces
    "StorageProvider",
    "StorageResult",
    # Implementations
    "S3Storage",
    # Factory
    "get_storage",
]
