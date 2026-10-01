"""E2E: an over-long / unicode upload filename must round-trip through storage.

The upload router truncates the client-supplied multipart filename to 255 chars
(``MAX_ORIGINAL_NAME_LENGTH``, matching ``File.original_name = String(255)`` and
the PATCH cap). This exercises the real API path end to end: upload, read back
the metadata, and download the bytes — proving the bound does not break
persistence, quota accounting, or the ``Content-Disposition`` header that
carries the (possibly unicode) name back to the client.
"""

from __future__ import annotations

import io
from urllib.parse import unquote

import pytest
from httpx import AsyncClient

MAX_ORIGINAL_NAME_LENGTH = 255


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "filename"),
    [
        ("unicode", "résumé — 報告書 ✅.txt"),
        ("overlong", "n" * 400 + ".txt"),
        ("unicode_overlong", "🚀" * 300 + ".txt"),
    ],
)
async def test_upload_and_download_with_awkward_filename(
    premium_user: dict, client: AsyncClient, log_step, label: str, filename: str
) -> None:
    """Upload a file whose filename is unicode, over-long, or both, then read
    the metadata and download the content back."""
    headers = {"Authorization": f"Bearer {premium_user['access_token']}"}
    content = b"e2e filename bound payload"

    response = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": (filename, io.BytesIO(content), "text/plain")},
    )
    assert response.status_code == 201, f"upload failed: {response.text}"
    body = response.json()
    file_id = body["id"]
    stored_name = body["original_name"]

    log_step(
        f"uploaded {label}",
        file_id=file_id,
        sent_chars=len(filename),
        stored_chars=len(stored_name),
        storage_path=body["storage_path"],
    )

    # The bound holds, and the value is the SENT value, not an empty/replaced one.
    assert len(stored_name) <= MAX_ORIGINAL_NAME_LENGTH
    assert stored_name == filename[:MAX_ORIGINAL_NAME_LENGTH]
    assert stored_name

    # The storage key is UUID-based regardless of the client name.
    assert body["storage_path"].startswith(f"tenants/{premium_user.get('tenant_id', '')}") or (
        "/files/" in body["storage_path"]
    )
    assert ".." not in body["storage_path"]
    assert body["size_bytes"] == len(content)

    # Metadata read-back agrees with what upload reported.
    info = await client.get(f"/api/v1/files/{file_id}/info", headers=headers)
    assert info.status_code == 200, f"info failed: {info.text}"
    assert info.json()["original_name"] == stored_name

    # The bytes come back intact, and the name survives the Content-Disposition
    # round trip (the router percent-encodes it with quote()).
    download = await client.get(f"/api/v1/files/{file_id}", headers=headers)
    assert download.status_code == 200, f"download failed: {download.text}"
    assert download.content == content

    disposition = download.headers.get("content-disposition", "")
    assert "filename*=utf-8''" in disposition
    encoded = disposition.split("filename*=utf-8''", 1)[1]
    assert unquote(encoded) == stored_name
    log_step(f"downloaded {label}", content_disposition_chars=len(encoded))

    deleted = await client.delete(f"/api/v1/files/{file_id}", headers=headers)
    assert deleted.status_code == 204, f"delete failed: {deleted.text}"
