"""Storage module.

Unified deep module providing file lifecycle management and tenant storage configuration.
"""

from __future__ import annotations

from typing import Any

from src.app.modules.storage.errors import (
    FileEntityNotFoundError,
    FileTypeNotAllowedError,
    InvalidCredentialsError,
    QuotaExceededError,
    StorageBackendError,
    StorageError,
    StorageNotConfiguredError,
)
from src.app.modules.storage.interfaces import StorageProvider, StorageResult
from src.app.modules.storage.models import File, StorageProviderType, StorageSettings

__all__ = [
    # Models
    "File",
    "StorageSettings",
    "StorageProviderType",
    # Providers
    "StorageProvider",
    "StorageResult",
    "LocalStorage",
    "S3Storage",
    "AzureBlobStorage",
    # Errors
    "StorageError",
    "StorageNotConfiguredError",
    "InvalidCredentialsError",
    "FileEntityNotFoundError",
    "FileTypeNotAllowedError",
    "QuotaExceededError",
    "StorageBackendError",
    # Core Facades (lazy)
    "Storage",
    "StorageService",
    "StorageAdmin",
    "StorageAdminService",
    "StoredObject",
    "Download",
    "FileService",
    "StorageSettingsService",
    "get_storage_admin",
    "get_file_service",
    "get_storage_settings_service",
    # Routers (lazy)
    "router",
    "files_router",
    "settings_router",
    "storage_settings_router",
    # Schemas (lazy)
    "S3Credentials",
    "AzureCredentials",
    "LocalCredentials",
    "StorageSettingsBase",
    "StorageSettingsCreate",
    "StorageSettingsUpdate",
    "StorageSettingsRead",
    "FileUploadResponse",
    "FileRead",
    "FileUpdate",
    "FileList",
    "FileListResponse",
    "SignedUrlResponse",
    "QuotaResponse",
]


def __getattr__(name: str) -> Any:
    """Lazy import routers, services, and schemas to keep __init__ lightweight."""
    if name == "LocalStorage":
        from src.app.modules.storage.local_storage import LocalStorage

        return LocalStorage
    if name == "S3Storage":
        from src.app.modules.storage.s3_storage import S3Storage

        return S3Storage
    if name == "AzureBlobStorage":
        from src.app.modules.storage.azure_storage import AzureBlobStorage

        return AzureBlobStorage

    if name in ("router", "files_router", "settings_router", "storage_settings_router"):
        from src.app.modules.storage import router as r

        return getattr(r, name)

    if name in (
        "Storage",
        "StorageService",
        "StorageAdmin",
        "StorageAdminService",
        "StoredObject",
        "Download",
        "FileService",
        "StorageSettingsService",
        "get_storage_admin",
        "get_file_service",
        "get_storage_settings_service",
    ):
        from src.app.modules.storage import service as s

        return getattr(s, name)

    if name in (
        "S3Credentials",
        "AzureCredentials",
        "LocalCredentials",
        "StorageSettingsBase",
        "StorageSettingsCreate",
        "StorageSettingsUpdate",
        "StorageSettingsRead",
        "FileUploadResponse",
        "FileRead",
        "FileUpdate",
        "FileList",
        "FileListResponse",
        "SignedUrlResponse",
        "QuotaResponse",
    ):
        from src.app.modules.storage import schemas as sc

        return getattr(sc, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
