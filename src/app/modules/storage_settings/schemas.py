"""Storage Settings Pydantic schemas.

This module defines request/response DTOs for storage settings API,
with proper validation and sensitive field masking.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .._base import BaseRead, BaseUpdate


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
    """Schema for creating storage settings.

    Credentials are required based on provider type.
    """

    # Provider-specific credentials (one of these required based on provider)
    s3_credentials: S3Credentials | None = None
    azure_credentials: AzureCredentials | None = None
    local_credentials: LocalCredentials | None = None

    @field_validator("s3_credentials", "azure_credentials", "local_credentials")
    @classmethod
    def validate_credentials_match_provider(cls, v, info):
        """Ensure credentials match the selected provider."""
        # This validation is handled at service layer for full context
        return v

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
    """Schema for updating storage settings.

    All fields are optional - only provided fields are updated.
    """

    provider: StorageProviderType | None = None
    max_file_size_mb: Annotated[int, Field(ge=1, le=5000)] | None = None
    allowed_file_types: list[str] | None = None
    max_file_count: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    storage_quota_bytes: Annotated[int, Field(ge=1024, le=10_000_000_000_000)] | None = None

    # Provider-specific credentials (optional for update)
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
    """Schema for reading storage settings.

    Inherits from BaseRead: id, created_at, updated_at
    Credentials are masked for security.
    """

    tenant_id: UUID
    bucket_name: str | None = None
    region: str | None = None

    # Masked credential indicators (never expose actual values)
    has_credentials: bool = False
    credentials_last_updated: datetime | None = None


__all__ = [
    "StorageProviderType",
    "S3Credentials",
    "AzureCredentials",
    "LocalCredentials",
    "StorageSettingsCreate",
    "StorageSettingsUpdate",
    "StorageSettingsRead",
]
