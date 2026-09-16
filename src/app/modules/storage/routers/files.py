"""Files API router.

This module defines the API endpoints for file operations.
Endpoints require appropriate FILES_* permissions.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.storage.schemas import (
    FileList,
    FileRead,
    FileUpdate,
    FileUploadResponse,
    QuotaResponse,
    SignedUrlResponse,
)
from src.app.modules.storage.service import StorageService

router = APIRouter(prefix="/files", tags=["files"])


def get_storage(db: AsyncSession = Depends(async_get_db)) -> StorageService:
    """Dependency injection factory for StorageService.

    Q-M4: factories live in router.py — service files stay Depends-free.
    """
    return StorageService(db)


def get_file_service(db: AsyncSession = Depends(async_get_db)) -> StorageService:
    """Backward-compatible dependency factory for FileService."""
    return StorageService(db)


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
    effective_user_id = user_id
    if user_id is not None:
        # Item 8: ownership via the ONE seam (superuser bypass lives there).
        ctx.require_ownership(user_id)
    if not ctx.is_superuser:
        effective_user_id = ctx.user_id if user_id is None else user_id

    offset = (page - 1) * per_page
    files_data = await service.list_files(
        tenant_id=ctx.tenant_id,
        user_id=effective_user_id,
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
    return await service.get_usage(ctx.tenant_id)


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
    from urllib.parse import quote

    download = await service.download(ctx.tenant_id, file_id, user_id=ctx.user_id)
    encoded_filename = quote(download.file.original_name)
    return StreamingResponse(
        download.iter_bytes(),
        media_type=download.file.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}",
            "Content-Length": str(download.file.size_bytes),
            # R3-1: media_type echoes the stored (claimed) MIME, so forbid
            # sniffing — attachment already blocks inline render; this
            # closes the legacy-sniffer residual.
            "X-Content-Type-Options": "nosniff",
        },
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
    file = await service.get_file_or_raise(ctx.tenant_id, file_id, user_id=ctx.user_id)
    return FileRead.model_validate(file, from_attributes=True)

@router.get(
    "/{file_id}/url",
    response_model=SignedUrlResponse,
    dependencies=[Depends(requires_permission(Permission.FILES_READ))],
)
async def get_signed_url(
    ctx: RequestContextDep,
    file_id: int,
    expires_in: int = Query(3600, ge=60, le=3600, description="Expiration in seconds"),
    service: StorageService = Depends(get_storage),
) -> Any:
    """Get a signed URL for file access."""
    url = await service.get_presigned_url(ctx.tenant_id, file_id, expires_in, user_id=ctx.user_id)
    from datetime import UTC, datetime, timedelta

    return SignedUrlResponse(
        url=url,
        expires_in=expires_in,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
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
    file = await service.update_file(ctx.tenant_id, file_id, data, user_id=ctx.user_id)
    return FileRead.model_validate(file, from_attributes=True)

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
    await service.delete_file(ctx.tenant_id, file_id, user_id=ctx.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
__all__ = [
    "delete_file",
    "download_file",
    "get_file_info",
    "get_quota",
    "get_signed_url",
    "list_files",
    "router",
    "update_file",
    "upload_file",
]

