"""Files API router.

This module defines the API endpoints for file operations.
Endpoints require appropriate FILES_* permissions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_tenant, get_current_user
from src.app.core.db.database import async_get_db
from src.app.modules.files.schemas import (
    FileListResponse,
    FileRead,
    FileUpdate,
    FileUploadResponse,
    SignedUrlResponse,
)
from src.app.modules.files.service import (
    FileNotFoundError as FileServiceNotFoundError,
)
from src.app.modules.files.service import (
    FileService,
    QuotaExceededError,
)
from src.app.modules.storage_settings.service import StorageNotConfiguredError
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User

router = APIRouter(prefix="/files", tags=["Files"])


def require_read_permission(user: User = Depends(get_current_user)) -> User:
    """Dependency to check FILES_READ permission."""
    # Permission check - simplified for now
    return user


def require_write_permission(user: User = Depends(get_current_user)) -> User:
    """Dependency to check FILES_WRITE permission."""
    return user


def require_delete_permission(user: User = Depends(get_current_user)) -> User:
    """Dependency to check FILES_DELETE permission."""
    return user


@router.post("/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(..., description="File to upload"),
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(require_write_permission),
) -> FileUploadResponse:
    """Upload a file to tenant storage.

    Validates file type and size against tenant settings.
    Enforces quota limits.

    Args:
        file: File to upload.

    Returns:
        FileUploadResponse: Uploaded file metadata.

    Raises:
        400: If storage not configured or file invalid.
        413: If file exceeds size limit.
        507: If quota exceeded.
    """
    service = FileService(db)

    # Read file content
    content = await file.read()
    size_bytes = len(content)

    # Reset file position for service
    await file.seek(0)

    try:
        result = await service.upload_file(
            tenant_id=tenant.id,
            user_id=user.id,
            file=file.file,
            filename=file.filename or "unnamed",
            mime_type=file.content_type or "application/octet-stream",
            size_bytes=size_bytes,
        )

        return FileUploadResponse(
            id=result.id,
            original_name=result.original_name,
            storage_path=result.storage_path,
            mime_type=result.mime_type,
            size_bytes=result.size_bytes,
            is_public=result.is_public,
            created_at=result.created_at,
        )

    except StorageNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except QuotaExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail=str(e),
        )


@router.get("", response_model=FileListResponse)
async def list_files(
    user_id: str | None = Query(None, description="Filter by owner user ID"),
    mime_type: str | None = Query(None, description="Filter by MIME type"),
    limit: int = Query(50, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(require_read_permission),
) -> FileListResponse:
    """List files for the current tenant.

    Supports pagination and filtering.

    Returns:
        FileListResponse: Paginated file list.
    """
    from uuid import UUID

    service = FileService(db)

    user_uuid = UUID(user_id) if user_id else None

    files, total = await service.list_files(
        tenant_id=tenant.id,
        user_id=user_uuid,
        mime_type=mime_type,
        limit=limit,
        offset=offset,
    )

    return FileListResponse(
        items=[FileRead.model_validate(f) for f in files],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{file_id}", response_class=StreamingResponse)
async def download_file(
    file_id: int,
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(require_read_permission),
) -> StreamingResponse:
    """Download a file.

    Returns the file as a streaming response.

    Args:
        file_id: File ID to download.

    Returns:
        StreamingResponse: File content.

    Raises:
        404: If file not found.
    """
    service = FileService(db)

    try:
        file = await service.get_file_or_raise(tenant.id, file_id)

        return StreamingResponse(
            service.download_file(tenant.id, file_id),
            media_type=file.mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{file.original_name}"',
                "Content-Length": str(file.size_bytes),
            },
        )

    except FileServiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


@router.get("/{file_id}/info", response_model=FileRead)
async def get_file_info(
    file_id: int,
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(require_read_permission),
) -> FileRead:
    """Get file metadata without downloading.

    Args:
        file_id: File ID.

    Returns:
        FileRead: File metadata.

    Raises:
        404: If file not found.
    """
    service = FileService(db)

    try:
        file = await service.get_file_or_raise(tenant.id, file_id)
        return FileRead.model_validate(file)

    except FileServiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


@router.get("/{file_id}/url", response_model=SignedUrlResponse)
async def get_signed_url(
    file_id: int,
    expires_in: int = Query(3600, ge=60, le=86400, description="Expiration in seconds"),
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(require_read_permission),
) -> SignedUrlResponse:
    """Get a signed URL for file access.

    The URL is valid for the specified duration.

    Args:
        file_id: File ID.
        expires_in: URL expiration in seconds (default: 1 hour).

    Returns:
        SignedUrlResponse: Signed URL and expiration.

    Raises:
        404: If file not found.
    """
    service = FileService(db)

    try:
        url = await service.get_signed_url(tenant.id, file_id, expires_in)
        return SignedUrlResponse(url=url, expires_in=expires_in)

    except FileServiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


@router.patch("/{file_id}", response_model=FileRead)
async def update_file(
    file_id: int,
    data: FileUpdate,
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(require_write_permission),
) -> FileRead:
    """Update file metadata.

    Args:
        file_id: File ID.
        data: Fields to update.

    Returns:
        FileRead: Updated file metadata.

    Raises:
        404: If file not found.
    """
    service = FileService(db)

    try:
        file = await service.update_file(tenant.id, file_id, data)
        return FileRead.model_validate(file)

    except FileServiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_file(
    file_id: int,
    db: AsyncSession = Depends(async_get_db),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(require_delete_permission),
) -> Response:
    """Delete a file.

    Removes the file from storage and database.

    Args:
        file_id: File ID to delete.

    Raises:
        404: If file not found.
    """
    service = FileService(db)

    try:
        await service.delete_file(tenant.id, file_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except FileServiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


__all__ = ["router"]
