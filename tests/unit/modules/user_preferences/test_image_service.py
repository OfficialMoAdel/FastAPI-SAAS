"""Tests for profile image upload service."""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules._base import ValidationError
from src.app.modules.storage.interfaces import StorageResult


class MockUploadFile:
    """Mock UploadFile for testing."""

    def __init__(
        self,
        filename: str,
        content: bytes,
        size: int,
        content_type: str = "application/octet-stream",
    ):
        self.filename = filename
        self._content = content
        self.size = size
        self.content_type = content_type
        self.file = BytesIO(content)
        self._seek_position = 0

    async def seek(self, position: int, whence: int = 0) -> None:
        """Async seek method."""
        if whence == 0:
            self._seek_position = position
        elif whence == 2:
            self._seek_position = len(self._content) + position
        self.file.seek(self._seek_position)

    async def read(self, size: int = -1) -> bytes:
        """Async read method."""
        return self.file.read(size)


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock()


@pytest.fixture
def service():
    """Create preferences service."""
    from src.app.modules.user_preferences.service import PreferencesService

    return PreferencesService()


@pytest.fixture
def valid_image_bytes():
    """Create a minimal valid PNG image (1x1 pixel)."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


@pytest.mark.asyncio
async def test_upload_profile_image_success(service, mock_db, valid_image_bytes):
    """Test successful profile image upload."""
    mock_file = MockUploadFile(
        filename="profile.png",
        content=valid_image_bytes,
        size=len(valid_image_bytes),
        content_type="image/png",
    )

    with patch("src.app.modules.user_preferences.service.get_storage") as mock_get_storage:
        mock_storage = AsyncMock()
        mock_storage.upload.return_value = StorageResult(
            success=True,
            path="users/123/profile.png",
            size=len(valid_image_bytes),
        )
        mock_storage.get_url.return_value = "https://storage.example.com/users/123/profile.png"
        mock_get_storage.return_value = mock_storage

        with patch("src.app.modules.user_preferences.service.crud_users") as mock_crud:
            mock_user = MagicMock()
            mock_user.profile_image_url = None
            mock_crud.get = AsyncMock(return_value=mock_user)
            mock_crud.update = AsyncMock(return_value=mock_user)

            with patch("src.app.modules.user_preferences.service.asyncio.to_thread") as mock_to_thread:
                # Mock to_thread to return the file size
                mock_to_thread.side_effect = [None, len(valid_image_bytes)]

                result = await service.upload_profile_image(
                    db=mock_db,
                    user_id=123,
                    file=mock_file,
                )

    assert result is not None
    assert result["image_url"] == "https://storage.example.com/users/123/profile.png"


@pytest.mark.asyncio
async def test_upload_profile_image_too_large(service, mock_db):
    """Test that large files are rejected."""
    from src.app.modules.user_preferences.service import MAX_IMAGE_SIZE_BYTES

    large_content = b"x" * (MAX_IMAGE_SIZE_BYTES + 1)
    mock_file = MockUploadFile(
        filename="large.png",
        content=large_content,
        size=len(large_content),
        content_type="image/png",
    )

    with pytest.raises(ValidationError, match="File too large"):
        await service.upload_profile_image(
            db=mock_db,
            user_id=123,
            file=mock_file,
        )


@pytest.mark.asyncio
async def test_upload_profile_image_invalid_type(service, mock_db):
    """Test that non-image files are rejected."""
    mock_file = MockUploadFile(
        filename="document.pdf",
        content=b"%PDF-1.4 fake pdf content",
        size=100,
        content_type="application/pdf",
    )

    with pytest.raises(ValidationError, match="Invalid file type"):
        await service.upload_profile_image(
            db=mock_db,
            user_id=123,
            file=mock_file,
        )
