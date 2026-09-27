"""Storage provider registry (Open/Closed Principle).

Decouples provider instantiation from hardcoded conditional branches.
New providers can register their factory callable dynamically without
modifying consumer code.

This module is the whole point of the storage seam's outbound half: it builds
the three real vendor adapters, so it imports the outbound package and
**nothing from ``src.app.modules``**. Before card 16 it imported four feature
modules here, which inverted the dependency direction (feature -> client for
the interface, client -> feature for the implementations). Callers that only
need a provider name should import ``StorageProviderType`` from
:mod:`src.app.clients.storage.provider_type` (a leaf) rather than from a
feature model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.app.clients.storage.azure_storage import AzureBlobStorage
from src.app.clients.storage.interfaces import StorageProvider
from src.app.clients.storage.local_storage import LocalStorage
from src.app.clients.storage.provider_type import StorageProviderType
from src.app.clients.storage.s3_storage import S3Storage

StorageFactory = Callable[[dict[str, Any]], StorageProvider]


class StorageProviderRegistry:
    """Registry mapping StorageProviderType to factory callables.

    The factory map is intentionally process-global (provider wiring is
    application configuration, not request state). ``unregister``/``reset``
    exist so tests can isolate factory overrides without leaking between cases.
    """

    _factories: dict[StorageProviderType, StorageFactory] = {}

    @classmethod
    def register(cls, provider_type: StorageProviderType, factory: StorageFactory) -> None:
        """Register a provider factory strategy."""
        cls._factories[provider_type] = factory

    @classmethod
    def unregister(cls, provider_type: StorageProviderType) -> None:
        """Remove a provider factory (primarily for test isolation)."""
        cls._factories.pop(provider_type, None)

    @classmethod
    def reset(cls) -> None:
        """Restore the built-in provider factories (primarily for test isolation)."""
        cls._factories.clear()
        cls.register(StorageProviderType.S3, _s3_factory)
        cls.register(StorageProviderType.AZURE, _azure_factory)
        cls.register(StorageProviderType.LOCAL, _local_factory)

    @classmethod
    def create(cls, provider_type: StorageProviderType, credentials: dict[str, Any]) -> StorageProvider:
        """Create a configured StorageProvider using registered factory.

        The factory is ``Callable[..., StorageProvider]`` — an unchecked
        annotation, so nothing else would stop a misbehaving factory from
        returning something the file layer cannot use. The failure would
        otherwise surface on first upload as a 500. Checking here costs one
        isinstance per cache-miss and keeps the error at the seam.
        """
        factory = cls._factories.get(provider_type)
        if factory is None:
            raise ValueError(f"Unknown provider: {provider_type}")
        provider = factory(credentials)
        if not isinstance(provider, StorageProvider):
            raise TypeError(
                f"factory for {provider_type} returned {type(provider).__name__}, "
                f"which is not a StorageProvider"
            )
        return provider


def _s3_factory(credentials: dict[str, Any]) -> StorageProvider:
    # Required config, like _azure_factory / _local_factory below. A NULL
    # credentials row decodes to {} (modules/storage/admin.py), and silently
    # defaulting would point a live provider at a bucket nobody configured.
    # Checked BEFORE the dependency guard so a misconfigured tenant sees the
    # config error, not the environment one.
    try:
        bucket = credentials["bucket_name"]
        region = credentials["region"]
    except KeyError as e:
        raise ValueError(f"S3 storage credentials missing required key: {e}") from e

    # Fail fast on the optional dependency. boto3 is imported function-locally
    # inside S3Storage (so this module imports without it) and is deliberately
    # not a declared dependency; without this guard a tenant that selects S3
    # discovers the gap on first upload as a bare ModuleNotFoundError 500.
    try:
        import boto3  # noqa: F401
    except ModuleNotFoundError as e:
        raise ValueError(
            "S3 storage requires the optional 'boto3' dependency (install with: uv pip install boto3)"
        ) from e

    return S3Storage(
        bucket=bucket,
        region=region,
        aws_access_key_id=credentials.get("access_key_id"),
        aws_secret_access_key=credentials.get("secret_access_key"),
    )


def _azure_factory(credentials: dict[str, Any]) -> StorageProvider:
    try:
        connection_string = credentials["connection_string"]
        container_name = credentials["container_name"]
    except KeyError as e:
        raise ValueError(f"Azure storage credentials missing required key: {e}") from e
    return AzureBlobStorage(
        connection_string=connection_string,
        container_name=container_name,
    )


def _local_factory(credentials: dict[str, Any]) -> StorageProvider:
    try:
        base_path = credentials["base_path"]
    except KeyError as e:
        raise ValueError(f"Local storage credentials missing required key: {e}") from e
    return LocalStorage(
        base_path=base_path,
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
