"""Storage exceptions.

Typed domain errors for file operations and storage configuration.
"""

from __future__ import annotations

from src.app.shared.exceptions import AppException


class StorageError(AppException):
    """Base exception for all storage-related errors."""

    pass


class StorageNotConfiguredError(StorageError):
    """Raised when storage operations are attempted without configured storage."""

    pass


class InvalidCredentialsError(StorageError):
    """Raised when storage provider credentials fail validation."""

    pass


class FileEntityNotFoundError(StorageError):
    """Raised when a file record is not found."""

    pass


class FileTypeNotAllowedError(StorageError):
    """Raised when uploaded file type is not allowed or spoofed."""

    pass


class QuotaExceededError(StorageError):
    """Raised when tenant file count or storage size quota is exceeded."""

    pass


class StorageBackendError(StorageError):
    """Raised when underlying storage provider I/O fails."""

    pass


__all__ = [
    "StorageError",
    "StorageNotConfiguredError",
    "InvalidCredentialsError",
    "FileEntityNotFoundError",
    "FileTypeNotAllowedError",
    "QuotaExceededError",
    "StorageBackendError",
]
