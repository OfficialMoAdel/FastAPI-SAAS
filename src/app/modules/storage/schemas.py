"""Storage Pydantic schemas.

This module unifies DTOs for file operations (upload, read, update, list, quotas)
and storage settings configuration (credentials, limits).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.app.modules._base import BaseList, BaseRead, BaseUpdate

# ============================================================================
# Storage Settings Schemas
# ============================================================================


class StorageProviderType(str, Enum):
    """Supported storage provider types."""

    S3 = "s3"
    AZURE = "azure"
    LOCAL = "local"


class S3Credentials(BaseModel):
    """S3 storage credentials."""

    access_key_id: Annotated[str, Field(min_length=1, max_length=128)]
    secret_access_key: Annotated[str, Field(min_length=1, max_length=256)]
    region: Annotated[str, Field(min_length=1, max_length=50)] = "us-east-1"
    bucket_name: Annotated[str, Field(min_length=3, max_length=63)]


class AzureCredentials(BaseModel):
    """Azure Blob storage credentials."""

    connection_string: Annotated[str, Field(min_length=1, max_length=1024)]
    container_name: Annotated[str, Field(min_length=3, max_length=63)]


class LocalCredentials(BaseModel):
    """Local storage configuration."""

    base_path: Annotated[str, Field(min_length=1, max_length=512)]
    base_url: Annotated[str, Field(max_length=256)] = "/files"

    @field_validator("base_path")
    @classmethod
    def _validate_base_path(cls, value: str) -> str:
        # P2-5: tenant admins must not escape the uploads root via relative
        # paths or parent traversal; the deployment default is absolute.
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("base_path must be an absolute path")
        if ".." in path.parts:
            raise ValueError("base_path must not contain parent '..' segments")
        return value


class StorageSettingsBase(BaseModel):
    """Base schema with common fields."""

    provider: StorageProviderType = StorageProviderType.LOCAL
    max_file_size_mb: Annotated[int, Field(ge=1, le=5000)] = 100
    allowed_file_types: list[str] | None = Field(
        default=["image/jpeg", "image/png", "image/gif", "application/pdf"],
        description="List of allowed MIME types",
    )
    max_file_count: Annotated[int, Field(ge=1, le=1_000_000)] = 10000
    storage_quota_bytes: Annotated[int, Field(ge=1024, le=10_000_000_000_000)] = (
        10 * 1024 * 1024 * 1024  # 10GB
    )


class StorageSettingsCreate(StorageSettingsBase):
    """Schema for creating storage settings."""

    s3_credentials: S3Credentials | None = None
    azure_credentials: AzureCredentials | None = None
    local_credentials: LocalCredentials | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider": "s3",
                "max_file_size_mb": 100,
                "allowed_file_types": ["image/jpeg", "image/png", "application/pdf"],
                "max_file_count": 10000,
                "storage_quota_bytes": 10737418240,
                "s3_credentials": {
                    "access_key_id": "AKIAIOSFODNN7EXAMPLE",
                    "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                    "region": "us-east-1",
                    "bucket_name": "my-app-files",
                },
            }
        }
    )


class StorageSettingsUpdate(BaseUpdate):
    """Schema for updating storage settings."""

    provider: StorageProviderType | None = None
    max_file_size_mb: Annotated[int, Field(ge=1, le=5000)] | None = None
    allowed_file_types: list[str] | None = None
    max_file_count: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    storage_quota_bytes: Annotated[int, Field(ge=1024, le=10_000_000_000_000)] | None = None

    s3_credentials: S3Credentials | None = None
    azure_credentials: AzureCredentials | None = None
    local_credentials: LocalCredentials | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "max_file_size_mb": 200,
                "max_file_count": 50000,
            }
        }
    )


class StorageSettingsRead(BaseRead, StorageSettingsBase):
    """Schema for reading storage settings."""

    tenant_id: UUID
    bucket_name: str | None = None
    region: str | None = None

    has_credentials: bool = False
    credentials_last_updated: datetime | None = None


# ============================================================================
# File Schemas
# ============================================================================


class FileUploadResponse(BaseModel):
    """Response after successful file upload."""

    id: int
    filename: str
    size_bytes: int
    mime_type: str
    storage_path: str
    uploaded_at: datetime
    is_public: bool = False
    description: str | None = None
    original_name: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 123,
                "filename": "document.pdf",
                "size_bytes": 1048576,
                "mime_type": "application/pdf",
                "storage_path": "tenants/550e8400-e29b-41d4-a716-446655440000/files/abc123.pdf",
                "uploaded_at": "2025-01-01T12:00:00Z",
                "is_public": False,
                "description": "Annual report",
            }
        },
    )


class FileRead(BaseRead):
    """File metadata read schema."""

    tenant_id: UUID
    user_id: int | None = None
    original_name: str
    storage_path: str
    mime_type: str
    size_bytes: int
    is_public: bool = False
    description: str | None = None


class FileUpdate(BaseUpdate):
    """Schema for updating file metadata."""

    original_name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    is_public: bool | None = None
    description: Annotated[str, Field(max_length=1000)] | None = None


class FileList(BaseList[FileRead]):
    """Standardized paginated file list response."""

    pass


class FileListResponse(BaseModel):
    """Legacy paginated file list response."""

    items: list[FileRead]
    total: int
    page: int
    per_page: int
    pages: int

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [],
                "total": 50,
                "page": 1,
                "per_page": 20,
                "pages": 3,
            }
        }
    )


class SignedUrlResponse(BaseModel):
    """Response with signed URL for file access."""

    url: str
    expires_in: int
    expires_at: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://bucket.s3.amazonaws.com/path?signature=xyz",
                "expires_in": 3600,
                "expires_at": "2025-01-01T13:00:00Z",
            }
        }
    )


class QuotaResponse(BaseModel):
    """Storage quota and usage statistics."""

    used: int = 0
    limit: int = 0
    remaining: int = 0
    file_count: int = 0
    max_file_count: int = 0
    storage_usage_bytes: int = 0
    storage_quota_bytes: int = 0
    usage_percentage: float = Field(default=0.0, ge=0.0)
    files_remaining: int = 0
    bytes_remaining: int = 0
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "file_count": 150,
                "max_file_count": 1000,
                "storage_usage_bytes": 1073741824,
                "storage_quota_bytes": 10737418240,
                "usage_percentage": 10.0,
                "files_remaining": 850,
                "bytes_remaining": 9663676416,
            }
        }
    )


__all__ = [
    # Storage Settings
    "StorageProviderType",
    "S3Credentials",
    "AzureCredentials",
    "LocalCredentials",
    "StorageSettingsBase",
    "StorageSettingsCreate",
    "StorageSettingsUpdate",
    "StorageSettingsRead",
    # Files
    "FileUploadResponse",
    "FileRead",
    "FileUpdate",
    "FileList",
    "FileListResponse",
    "SignedUrlResponse",
    "QuotaResponse",
]
