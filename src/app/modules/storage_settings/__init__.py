"""Storage Settings module.

This module provides tenant-specific storage configuration management,
including support for S3, Azure Blob, and local filesystem providers.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.storage_settings.models import StorageSettings
    from src.app.modules.storage_settings.service import StorageSettingsService
    from src.app.modules.storage_settings.service import StorageNotConfiguredError
"""

__all__ = [
    "StorageSettings",
    "StorageSettingsCreate",
    "StorageSettingsRead",
    "StorageSettingsUpdate",
    "StorageSettingsService",
    "StorageNotConfiguredError",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "StorageSettings":
        from .models import StorageSettings

        return StorageSettings
    if name == "StorageSettingsService":
        from .service import StorageSettingsService

        return StorageSettingsService
    if name == "StorageNotConfiguredError":
        from .service import StorageNotConfiguredError

        return StorageNotConfiguredError
    if name in ("StorageSettingsCreate", "StorageSettingsRead", "StorageSettingsUpdate"):
        from . import schemas

        return getattr(schemas, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
