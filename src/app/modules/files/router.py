"""Files API router.

This module defines the API endpoints for file operations.
Endpoints require appropriate FILES_* permissions.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse

from src.app.api.dependencies import get_current_tenant
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.files.schemas import (
    FileList,
    FileRead,
    FileUpdate,
    FileUploadResponse,
    SignedUrlResponse,
)
from src.app.modules.files.service import (
    FileEntityNotFoundError as FileServiceNotFoundError,
)
from src.app.modules.files.service import (
    FileService,
    QuotaExceededError,
    get_file_service,
)
from src.app.modules.storage_settings import StorageNotConfiguredError
from src.app.modules.tenants import Tenant
from src.app.modules.users import User

router = APIRouter(prefix="/files", tags=["Files"])


@router.post("/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(..., description="File to upload"),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(requires_permission(Permission.FILES_WRITE)),
    service: FileService = Depends(get_file_service),
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
    # Get file size without reading entire content into memory
    # Use UploadFile.size if available (FastAPI provides this from Content-Length header)
    # Otherwise, use seek/tell to determine size (O(1) relative to content reading)
    if file.size is not None:
        size_bytes = file.size
    else:
        # Fallback: seek to end to get size, then reset position
        # Uses underlying file object directly because UploadFile.seek()
        # does not support the whence parameter
        file.file.seek(0, 2)
        size_bytes = file.file.tell()
        file.file.seek(0)

    try:
        result = await service.upload_file(
            tenant_id=tenant.id,
            user_id=_user.id,
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


@router.get("", response_model=FileList)
async def list_files(
    user_id: int | None = Query(None, description="Filter by owner user ID"),
    mime_type: str | None = Query(None, description="Filter by MIME type"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=100, description="Page size"),
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(requires_permission(Permission.FILES_READ)),
    service: FileService = Depends(get_file_service),
) -> dict:
    """List files for the current tenant.

    Supports pagination and filtering.

    Returns:
        FileList: Paginated file list.
    """
    offset = (page - 1) * per_page

    files_data = await service.list_files(
        tenant_id=tenant.id,
        user_id=user_id,
        mime_type=mime_type,
        limit=per_page,
        offset=offset,
    )

    data = files_data.get("data", [])
    total = files_data.get("total_count", 0)

    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/{file_id}", response_class=StreamingResponse)
async def download_file(
    file_id: int,
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(requires_permission(Permission.FILES_READ)),
    service: FileService = Depends(get_file_service),
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
    try:
        file = await service.get_file_or_raise(tenant.id, file_id)

        # SECURITY FIX: RFC 5987 content-disposition
        filename_encoded = quote(file.original_name)
        return StreamingResponse(
            service.download_file(tenant.id, file_id),
            media_type=file.mime_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}",
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
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(requires_permission(Permission.FILES_READ)),
    service: FileService = Depends(get_file_service),
) -> FileRead:
    """Get file metadata without downloading.

    Args:
        file_id: File ID.

    Returns:
        FileRead: File metadata.

    Raises:
        404: If file not found.
    """
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
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(requires_permission(Permission.FILES_READ)),
    service: FileService = Depends(get_file_service),
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
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(requires_permission(Permission.FILES_WRITE)),
    service: FileService = Depends(get_file_service),
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
    tenant: Tenant = Depends(get_current_tenant),
    _user: User = Depends(requires_permission(Permission.FILES_DELETE)),
    service: FileService = Depends(get_file_service),
) -> Response:
    """Delete a file.

    Removes the file from storage and database.

    Args:
        file_id: File ID to delete.

    Raises:
        404: If file not found.
    """
    try:
        await service.delete_file(tenant.id, file_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except FileServiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


__all__ = ["router"]
