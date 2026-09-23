"""Storage exceptions.

Typed domain errors for file operations and storage configuration.

Each class declares its own HTTP mapping (``http_status`` / ``response_code``
/ ``public_message``) so ``core.errors`` needs no row for this family and
therefore no import of this module. The public messages are deliberately
fixed rather than taken from the instance: storage failures must not leak
provider internals (bucket names, keys, S3 error bodies) to the client.
"""

from __future__ import annotations

from src.app.shared.error_codes import ErrorCode
from src.app.shared.exceptions import AppException


class StorageError(AppException):
    """Base exception for all storage-related errors."""

    http_status = 500
    response_code = ErrorCode.INTERNAL_ERROR
    log_message = "Storage operation failed"
    log_error = True


class StorageNotConfiguredError(StorageError):
    """Raised when storage operations are attempted without configured storage."""

    http_status = 404
    response_code = ErrorCode.NOT_FOUND
    public_message = "Storage not configured for this tenant."
    log_message = "Storage not configured"
    log_error = False


class InvalidCredentialsError(StorageError):
    """Raised when storage provider credentials fail validation."""

    http_status = 400
    response_code = ErrorCode.INVALID_INPUT
    public_message = "Invalid storage provider credentials."
    log_message = "Invalid storage credentials"
    log_error = False


class FileEntityNotFoundError(StorageError):
    """Raised when a file record is not found."""

    http_status = 404
    response_code = ErrorCode.NOT_FOUND
    public_message = "File not found"
    log_message = "File entity not found"
    log_error = False


class FileTypeNotAllowedError(StorageError):
    """Raised when uploaded file type is not allowed or spoofed."""

    http_status = 400
    response_code = ErrorCode.INVALID_INPUT
    public_message = "File type not allowed."
    log_message = "File type not allowed"
    log_error = False


class QuotaExceededError(StorageError):
    """Raised when tenant file count or storage size quota is exceeded."""

    http_status = 507
    response_code = ErrorCode.STORAGE_QUOTA_EXCEEDED
    public_message = "Storage quota exceeded."
    log_message = "Storage quota exceeded"
    log_error = False


class StorageBackendError(StorageError):
    """Raised when underlying storage provider I/O fails."""

    http_status = 502
    response_code = ErrorCode.STORAGE_BACKEND_ERROR
    public_message = "Storage provider error during file operation."
    log_message = "Storage backend error"
    log_error = True


__all__ = [
    "StorageError",
    "StorageNotConfiguredError",
    "InvalidCredentialsError",
    "FileEntityNotFoundError",
    "FileTypeNotAllowedError",
    "QuotaExceededError",
    "StorageBackendError",
]
