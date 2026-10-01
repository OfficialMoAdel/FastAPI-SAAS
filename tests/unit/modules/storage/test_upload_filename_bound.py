"""Upload filename is bounded to the same 255 chars the PATCH path enforces.

``File.original_name`` is ``String(255)`` (storage/models.py:184-187) and
``FileUpdate.original_name`` caps updates at 255 (storage/schemas.py:236).
Before this fix the upload router passed the client-controlled multipart
filename straight through, so upload and update disagreed on the same column.

Scope check: ``storage_path`` is built from a fresh ``uuid4()``
(storage/service.py:198-203), so a hostile filename never escapes the tenant
prefix. The truncation bounds storage/response size only — it is NOT a
traversal control. The tests below pin both facts.
"""

from __future__ import annotations

import io
import re
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile

from src.app.api.deps.request_context import RequestContext
from src.app.core.tenancy.context import TenantContext
from src.app.modules.storage.routers.files import (
    MAX_ORIGINAL_NAME_LENGTH,
    upload_file,
)
from src.app.modules.storage.service import StorageService

# tenants/{tenant_uuid}/files/{uuid4()}[.{ext<=10, [A-Za-z0-9] only}]
STORAGE_PATH_RE = re.compile(
    r"^tenants/(?P<tenant>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/files/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"(?:\.(?P<ext>[A-Za-z0-9]{0,10}))?$"
)


def _context() -> RequestContext:
    return RequestContext(
        user=MagicMock(id=4242),
        tenant=TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_slug="acme",
            tenant_name="Acme",
            is_active=True,
        ),
        db=MagicMock(),
    )


async def _drive_router(filename: str) -> str:
    """Run the upload router with ``filename``; return the name it forwarded to
    ``StorageService.upload_file`` — the value that lands in ``original_name``."""
    ctx = _context()

    upload = MagicMock(spec=UploadFile)
    upload.filename = filename
    upload.content_type = "text/plain"
    upload.file = io.BytesIO(b"payload")
    upload.read = AsyncMock()

    result = MagicMock()
    result.id = 7
    result.original_name = filename
    result.storage_path = f"tenants/{ctx.tenant.tenant_id}/files/{uuid.uuid4()}.txt"
    result.mime_type = "text/plain"
    result.size_bytes = 7
    result.is_public = False
    result.created_at = datetime(2024, 1, 1)

    service = AsyncMock()
    service.upload_file.return_value = result

    await upload_file(ctx=ctx, file=upload, service=service)
    forwarded: str = service.upload_file.await_args.kwargs["filename"]
    return forwarded


def _real_service_capturing_key(tenant_id: uuid.UUID) -> tuple[StorageService, list[str]]:
    """A StorageService with only I/O, quota, and DB side effects stubbed, so the
    real storage-key construction (service.py:197-203) runs against the input."""
    svc = StorageService.__new__(StorageService)
    svc.db = AsyncMock()
    svc.db.add = MagicMock()
    svc.db.commit = AsyncMock()
    svc.db.refresh = AsyncMock()

    captured: list[str] = []
    provider = AsyncMock()

    async def _upload(_file: object, path: str, *_a: object, **_kw: object) -> MagicMock:
        captured.append(path)
        return MagicMock(success=True)

    provider.upload = AsyncMock(side_effect=_upload)

    svc.admin = MagicMock()
    svc.admin.resolve = AsyncMock(
        return_value=MagicMock(
            max_file_size_mb=10,
            allowed_file_types=[],
            max_file_count=100,
            storage_quota_bytes=10 * 1024 * 1024,
            provider=provider,
        )
    )
    svc._get_quota_stats = AsyncMock(return_value=(0, 0))
    svc._increment_quota_counters = AsyncMock()
    assert tenant_id is not None
    return svc, captured


@pytest.mark.asyncio
async def test_oversized_upload_filename_is_truncated_to_255() -> None:
    """A >255-char multipart filename is cut to the column bound, not stored raw."""
    hostile = "a" * 300 + ".txt"
    assert len(hostile) == 304

    forwarded = await _drive_router(hostile)

    assert MAX_ORIGINAL_NAME_LENGTH == 255
    assert len(forwarded) == 255
    assert forwarded == hostile[:255]


@pytest.mark.asyncio
async def test_normal_upload_filename_round_trips_unchanged() -> None:
    """COUNTER-TEST: the bound is a cap, not a mangler — a real name survives."""
    forwarded = await _drive_router("quarterly-report-2024.pdf")
    assert forwarded == "quarterly-report-2024.pdf"


@pytest.mark.asyncio
async def test_exactly_255_char_filename_is_untouched() -> None:
    name = "b" * 251 + ".txt"
    assert len(name) == 255
    assert await _drive_router(name) == name


@pytest.mark.asyncio
async def test_empty_filename_falls_back_to_unnamed() -> None:
    assert await _drive_router("") == "unnamed"


@pytest.mark.asyncio
async def test_storage_path_stays_uuid_based_for_hostile_filename() -> None:
    """SCOPE CHECK for the truncation: a name carrying ``../`` and a slash must
    not influence the storage key — the service derives it from ``uuid4()`` only.
    If this ever fails, the finding was understated and the bound is not the
    whole story."""
    tenant_id = uuid.uuid4()
    svc, captured = _real_service_capturing_key(tenant_id)

    with patch("filetype.guess", return_value=None):
        await svc.upload_file(
            tenant_id=tenant_id,
            user_id=4242,
            file=io.BytesIO(b"payload"),
            filename="../../etc/passwd",
            size_bytes=7,
            mime_type="text/plain",
        )

    assert len(captured) == 1
    storage_path = captured[0]
    match = STORAGE_PATH_RE.match(storage_path)
    assert match is not None, f"storage key is not tenant/uuid shaped: {storage_path!r}"
    assert match.group("tenant") == str(tenant_id)
    assert uuid.UUID(match.group("uuid"))
    assert ".." not in storage_path
    # The "extension" is everything after the LAST dot of the client name
    # (service.py:199-200), stripped to [A-Za-z0-9] and capped at 10. For
    # '../../etc/passwd' that tail is './etc/passwd' -> 'etcpasswd': a
    # traversal-free suffix on a UUID key, never a path.
    assert match.group("ext") == "etcpasswd"
    assert "/" not in match.group("ext")


@pytest.mark.asyncio
async def test_hostile_filename_with_dot_extension_still_yields_uuid_key() -> None:
    """Same scope check with a dotted name: only the sanitised tail reaches the
    key, and only as [A-Za-z0-9]{<=10} appended to a fresh uuid4()."""
    tenant_id = uuid.uuid4()
    svc, captured = _real_service_capturing_key(tenant_id)

    with patch("filetype.guess", return_value=None):
        await svc.upload_file(
            tenant_id=tenant_id,
            user_id=4242,
            file=io.BytesIO(b"payload"),
            filename="../../../root/.ssh/id_rsa",
            size_bytes=7,
            mime_type="text/plain",
        )

    assert len(captured) == 1
    match = STORAGE_PATH_RE.match(captured[0])
    assert match is not None, f"storage key is not tenant/uuid shaped: {captured[0]!r}"
    assert ".." not in captured[0]
    assert match.group("ext") == "sshidrsa"
    assert "/" not in match.group("ext")


@pytest.mark.asyncio
async def test_truncated_hostile_filename_still_stores_bounded_name() -> None:
    """Whatever the client sends, the value written to File.original_name fits
    String(255) — including a name that is both hostile and over-long."""
    forwarded = await _drive_router("../" * 200 + "secret.txt")
    assert len(forwarded) == 255
