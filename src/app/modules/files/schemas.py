"""File Pydantic schemas.

This module defines request/response DTOs for file API operations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .._base import BaseList, BaseRead, BaseUpdate


class FileUploadResponse(BaseModel):
    """Response after successful file upload."""

    id: int
    original_name: str
    storage_path: str
    mime_type: str
    size_bytes: int
    is_public: bool = False
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "original_name": "document.pdf",
                "storage_path": "tenants/abc123/files/uuid-doc.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 1048576,
                "is_public": False,
                "created_at": "2026-01-07T12:00:00Z",
            }
        },
    )


class FileRead(BaseRead):
    """File metadata read schema.

    Inherits from BaseRead: id, created_at, updated_at
    """

    tenant_id: UUID
    user_id: UUID | None = None
    original_name: str
    storage_path: str
    mime_type: str
    size_bytes: int
    is_public: bool
    description: str | None = None


class FileUpdate(BaseUpdate):
    """Schema for updating file metadata."""

    original_name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    is_public: bool | None = None
    description: Annotated[str, Field(max_length=1000)] | None = None


class FileList(BaseList[FileRead]):
    """Standardized paginated file list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class FileListResponse(BaseModel):
    """Legacy paginated file list response.

    Deprecated: Use FileList for new endpoints.
    This schema is kept for backward compatibility with existing API contracts.
    """

    items: list[FileRead]
    total: int
    limit: int
    offset: int

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "id": 1,
                        "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
                        "user_id": "660e8400-e29b-41d4-a716-446655440001",
                        "original_name": "document.pdf",
                        "storage_path": "tenants/abc123/files/uuid-doc.pdf",
                        "mime_type": "application/pdf",
                        "size_bytes": 1048576,
                        "is_public": False,
                        "description": None,
                        "created_at": "2026-01-07T12:00:00Z",
                        "updated_at": None,
                    }
                ],
                "total": 1,
                "limit": 50,
                "offset": 0,
            }
        }
    )


class SignedUrlResponse(BaseModel):
    """Response with signed URL for file access."""

    url: str
    expires_in: int = 3600

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://storage.example.com/file.pdf?signature=abc123&expires=1234567890",
                "expires_in": 3600,
            }
        }
    )


__all__ = [
    "FileUploadResponse",
    "FileRead",
    "FileUpdate",
    "FileList",
    "FileListResponse",
    "SignedUrlResponse",
]
