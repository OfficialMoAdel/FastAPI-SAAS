from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from src.app.modules.storage.interfaces import StorageResult
from src.app.modules.user_preferences.service import PreferencesService


@pytest.fixture
def mock_storage():
    return AsyncMock()

@pytest.fixture
def service(mock_storage):
    # Patch the storage creation/getting within the service or pass it if possible
    # Since PreferencesService currently doesn't accept storage in __init__,
    # we might need to patch wherever it's used or instantiated.
    # For now, let's assume we'll inject it or patch the class method that returns it.
    svc = PreferencesService()
    # We will likely add a way to set storage or mock the import
    return svc

@pytest.mark.asyncio
async def test_upload_profile_image_success(service, mock_storage):
    user_id = 999
    file = Mock() # Relax spec to avoid attribute issues
    file.filename = "test.png"
    file.content_type = "image/png"
    file.file = Mock()
    file.file.read.return_value = b"fake_image_content"
    # Reset read cursor for size check if needed, though simpler mock might suffice
    file.file.seek.return_value = None
    file.file.tell = Mock(return_value=1024) # 1KB

    mock_storage.upload.return_value = StorageResult(success=True, path="users/999/profile.png")
    mock_storage.get_url.return_value = "https://s3.example.com/users/999/profile.png"

    # We need to patch the storage instance used in the service
    with patch("src.app.modules.user_preferences.service.get_storage", return_value=mock_storage):
        # Patch crud_users
        with patch("src.app.modules.user_preferences.service.crud_users") as mock_crud_users:
            mock_crud_users.get = AsyncMock(return_value=Mock(id=user_id, profile_image_url="old_url"))
            mock_crud_users.update = AsyncMock(return_value=None)

            result = await service.upload_profile_image(
                 db=AsyncMock(),
                 user_id=user_id,
                 file=file
             )

    assert result["image_url"] == "https://s3.example.com/users/999/profile.png"
    mock_storage.upload.assert_called_once()
    mock_crud_users.update.assert_called_once()

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_upload_profile_image_invalid_type(service):
    user_id = 999
    file = Mock()
    file.filename = "test.txt"
    file.content_type = "text/plain"

    with pytest.raises(HTTPException) as exc:
        await service.upload_profile_image(
            db=AsyncMock(),
            user_id=user_id,
            file=file
        )
    assert exc.value.status_code == 400
    assert "Invalid file type" in str(exc.value.detail)

@pytest.mark.asyncio
async def test_upload_profile_image_too_large(service):
    user_id = 999
    file = Mock()
    file.filename = "large.png"
    file.content_type = "image/png"

    # Mock file size check (assuming we read into memory or check sth)
    # If service reads file.file.read(), we mock it to return large bytes
    file.file = Mock()
    file.file.read.return_value = b"a" * (5 * 1024 * 1024 + 1) # 5MB + 1 byte
    file.file.text = None
    file.file.seek.return_value = None
    file.file.tell.return_value = 5 * 1024 * 1024 + 1 # > 5MB

    with pytest.raises(HTTPException) as exc:
        await service.upload_profile_image(
            db=AsyncMock(),
            user_id=user_id,
            file=file
        )
    assert exc.value.status_code == 400
    assert "File too large" in str(exc.value.detail)
