"""R2-14 / R2-18: profile-image replacement must be atomic and its key derived
from the DETECTED file type.

The old flow computed ``users/{uid}/profile.{ext}`` from the client filename,
deleted the previous object, and only then uploaded. Two failures followed from
that: an unchanged extension overwrote the only copy (so a failed upload left
the DB naming a file that no longer existed), and the extension was taken from
user input.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from src.app.clients.storage.interfaces import StorageResult
from src.app.modules.user_preferences.service import (
    PreferencesService,
    PreferenceStorageError,
)

# A minimal 1x1 PNG: magic bytes, IHDR, IDAT, IEND.
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

SERVICE = "src.app.modules.user_preferences.service"


def _upload_file(filename: str = "avatar.png") -> UploadFile:
    return UploadFile(
        file=BytesIO(PNG_1X1),
        filename=filename,
        size=len(PNG_1X1),
        headers=Headers({"content-type": "image/png"}),
    )


class _Harness:
    """Wire the service to a fake store and a fake user row."""

    def __init__(self, *, old_url: str | None, upload_ok: bool = True) -> None:
        self.events: list[str] = []
        self.storage = AsyncMock()
        self.storage.upload.return_value = (
            StorageResult(success=True, path="ignored")
            if upload_ok
            else StorageResult(success=False, path="", error="disk full")
        )
        self.storage.get_url.side_effect = self._get_url
        self.storage.exists.return_value = True
        self.storage.delete.side_effect = self._delete

        self.user = MagicMock()
        self.user.profile_image_url = old_url
        self.crud = MagicMock()
        self.crud.get = AsyncMock(return_value=self.user)
        self.crud.update = AsyncMock(side_effect=self._update)

    async def _get_url(self, path: str) -> str:
        self.events.append(f"get_url:{path}")
        return f"https://cdn.example.com/{path}"

    async def _delete(self, path: str) -> StorageResult:
        self.events.append(f"delete:{path}")
        return StorageResult(success=True, path=path)

    async def _update(self, **kwargs: object) -> MagicMock:
        self.events.append("db_update")
        return self.user

    def patches(self):
        tenant = MagicMock()
        tenant.tenant_id = "tenant-uuid"
        return (
            patch(f"{SERVICE}.get_current_tenant", return_value=tenant),
            patch(f"{SERVICE}.crud_users", self.crud),
            patch(f"{SERVICE}.UserCacheInvalidator.invalidate_user_cache", new=AsyncMock()),
        )


@pytest.mark.asyncio
async def test_a_failed_upload_leaves_the_previous_image_untouched() -> None:
    """The old object must still be there when the new write failed."""
    harness = _Harness(old_url="https://cdn.example.com/users/1/profile.jpg.previous", upload_ok=False)

    with harness.patches()[0], harness.patches()[1], harness.patches()[2]:
        with pytest.raises(PreferenceStorageError):
            await PreferencesService().upload_profile_image(
                db=AsyncMock(), user_id=1, file=_upload_file(), image_store=harness.storage
            )

    harness.storage.delete.assert_not_awaited()
    harness.crud.update.assert_not_awaited()
    assert harness.events == [], "nothing may be deleted or referenced before a successful upload"


@pytest.mark.asyncio
async def test_a_failed_db_update_removes_the_new_object_and_keeps_the_old_one() -> None:
    """The DB still names the old object, so the new write is an orphan."""
    harness = _Harness(old_url="https://cdn.example.com/users/1/profile.jpg.previous")
    harness.crud.update.side_effect = RuntimeError("db down")

    with harness.patches()[0], harness.patches()[1], harness.patches()[2]:
        with pytest.raises(RuntimeError, match="db down"):
            await PreferencesService().upload_profile_image(
                db=AsyncMock(), user_id=1, file=_upload_file(), image_store=harness.storage
            )

    deleted = [event for event in harness.events if event.startswith("delete:")]
    assert len(deleted) == 1
    assert "profile.png." in deleted[0], "only the newly written object is cleaned up"
    assert "profile.jpg.previous" not in deleted[0], "the referenced old object must survive"


@pytest.mark.asyncio
async def test_the_previous_image_is_deleted_only_after_the_db_points_at_the_new_one() -> None:
    harness = _Harness(old_url="https://cdn.example.com/users/1/profile.jpg.previous")

    with harness.patches()[0], harness.patches()[1], harness.patches()[2]:
        result = await PreferencesService().upload_profile_image(
            db=AsyncMock(), user_id=1, file=_upload_file(), image_store=harness.storage
        )

    update_at = harness.events.index("db_update")
    old_delete_at = next(
        index for index, event in enumerate(harness.events) if event.startswith("delete:") and "previous" in event
    )
    assert update_at < old_delete_at, "the old object is unreferenced only after the DB update"
    assert result["image_url"].startswith("https://cdn.example.com/users/1/profile.png.")


@pytest.mark.asyncio
async def test_the_extension_comes_from_the_detected_type_not_the_filename() -> None:
    """R2-18: a hostile filename must not reach the storage key."""
    harness = _Harness(old_url=None)

    with harness.patches()[0], harness.patches()[1], harness.patches()[2]:
        await PreferencesService().upload_profile_image(
            db=AsyncMock(),
            user_id=1,
            file=_upload_file(filename="evil.png/../../escape.jpg"),
            image_store=harness.storage,
        )

    uploaded_path = harness.storage.upload.await_args.args[1]
    assert uploaded_path.startswith("users/1/profile.png."), "PNG content must produce a .png key"
    assert uploaded_path.count("/") == 2, f"the key must stay under users/1/: {uploaded_path!r}"
