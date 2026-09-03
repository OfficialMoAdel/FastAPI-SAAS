"""Files API router.

This module defines the API endpoints for file operations.
Endpoints require appropriate FILES_* permissions.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse

from src.app.api.deps import RequestContextDep
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.storage.errors import (
    FileEntityNotFoundError,
    FileTypeNotAllowedError,
    QuotaExceededError,
    StorageBackendError,
    StorageNotConfiguredError,
)
from src.app.modules.storage.schemas import (
    FileList,
    FileRead,
    FileUpdate,
    FileUploadResponse,
    QuotaResponse,
    SignedUrlResponse,
)
from src.app.modules.storage.service import (
    StorageService,
    get_storage,
)

router = APIRouter(prefix="/files", tags=["files"])


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.FILES_WRITE))],
)
async def upload_file(
    ctx: RequestContextDep,
    file: UploadFile = File(..., description="File to upload"),
    service: StorageService = Depends(get_storage),
) -> Any:
    """Upload a file to tenant storage using streaming (file.size probe via seek/tell)."""
    await asyncio.to_thread(file.file.seek, 0, os.SEEK_END)
    size_bytes = await asyncio.to_thread(file.file.tell)
    await asyncio.to_thread(file.file.seek, 0, os.SEEK_SET)

    try:
        result = await service.upload_file(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            file=file.file,
            filename=file.filename or "unnamed",
            mime_type=(
                (mimetypes.guess_type(file.filename)[0] if file.filename else None)
                or file.content_type
                or "application/octet-stream"
            ),
            size_bytes=size_bytes,
        )

        return FileUploadResponse(
            id=result.id,
            filename=result.original_name,
            original_name=result.original_name,
            storage_path=result.storage_path,
            mime_type=result.mime_type,
            size_bytes=result.size_bytes,
            is_public=result.is_public,
            uploaded_at=result.created_at,
            created_at=result.created_at,
        )

    except StorageNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Storage not configured for this tenant.",
        )
    except FileTypeNotAllowedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File type not allowed.",
        )
    except QuotaExceededError:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="Storage quota exceeded.",
        )
    except StorageBackendError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Storage provider error during file upload.",
        )


@router.get(
    "",
    response_model=FileList,
    dependencies=[Depends(requires_permission(Permission.FILES_READ))],
)
async def list_files(
    ctx: RequestContextDep,
    user_id: int | None = Query(None, description="Filter by owner user ID"),
    mime_type: str | None = Query(None, description="Filter by MIME type"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=100, description="Page size"),
    service: StorageService = Depends(get_storage),
) -> Any:
    """List files for the current tenant."""
    offset = (page - 1) * per_page
    files_data = await service.list_files(
        tenant_id=ctx.tenant_id,
        user_id=user_id,
        mime_type=mime_type,
        limit=per_page,
        offset=offset,
    )

    items = files_data.get("items", [])
    total = files_data.get("total", 0)

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/quota", response_model=QuotaResponse, dependencies=[Depends(requires_permission(Permission.FILES_READ))])
async def get_quota(
    ctx: RequestContextDep,
    service: StorageService = Depends(get_storage),
) -> Any:
    """Get active storage quota and usage."""
    try:
        return await service.get_usage(ctx.tenant_id)
    except StorageNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Storage not configured for this tenant.",
        )


@router.get(
    "/{file_id}",
    response_class=StreamingResponse,
    dependencies=[Depends(requires_permission(Permission.FILES_READ))],
)
async def download_file(
    file_id: int,
    ctx: RequestContextDep,
    service: StorageService = Depends(get_storage),
) -> Any:
    """Download a file via streaming response."""
    try:
        download = await service.download(ctx.tenant_id, file_id, user_id=ctx.user_id)
        return download.to_response()
    except FileEntityNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    except StorageNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Storage not configured for this tenant.",
        )


@router.get(
    "/{file_id}/info",
    response_model=FileRead,
    dependencies=[Depends(requires_permission(Permission.FILES_READ))],
)
async def get_file_info(
    file_id: int,
    ctx: RequestContextDep,
    service: StorageService = Depends(get_storage),
) -> Any:
    """Get file metadata without downloading."""
    try:
        file = await service.get_file_or_raise(ctx.tenant_id, file_id, user_id=ctx.user_id)
        return FileRead.model_validate(file, from_attributes=True)
    except FileEntityNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


@router.get(
    "/{file_id}/url",
    response_model=SignedUrlResponse,
    dependencies=[Depends(requires_permission(Permission.FILES_READ))],
)
async def get_signed_url(
    ctx: RequestContextDep,
    file_id: int,
    expires_in: int = Query(3600, ge=60, le=86400, description="Expiration in seconds"),
    service: StorageService = Depends(get_storage),
) -> Any:
    """Get a signed URL for file access."""
    try:
        url = await service.get_presigned_url(ctx.tenant_id, file_id, expires_in, user_id=ctx.user_id)
        from datetime import UTC, datetime, timedelta

        return SignedUrlResponse(
            url=url,
            expires_in=expires_in,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )
    except FileEntityNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


@router.patch(
    "/{file_id}",
    response_model=FileRead,
    dependencies=[Depends(requires_permission(Permission.FILES_WRITE))],
)
async def update_file(
    file_id: int,
    data: FileUpdate,
    ctx: RequestContextDep,
    service: StorageService = Depends(get_storage),
) -> Any:
    """Update file metadata."""
    try:
        file = await service.update_file(ctx.tenant_id, file_id, data, user_id=ctx.user_id)
        return FileRead.model_validate(file, from_attributes=True)
    except FileEntityNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(requires_permission(Permission.FILES_DELETE))],
)
async def delete_file(
    file_id: int,
    ctx: RequestContextDep,
    service: StorageService = Depends(get_storage),
) -> Response:
    """Delete a file."""
    try:
        await service.delete_file(ctx.tenant_id, file_id, user_id=ctx.user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except FileEntityNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    except StorageBackendError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Storage provider error during file deletion.",
        )



__all__ = ["router"]
