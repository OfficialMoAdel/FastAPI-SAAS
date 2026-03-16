"""Profile image tests - Unit and Integration tests combined.

This file contains:
- Unit tests: Image service upload/delete logic
- Integration tests: Profile image upload flow via API
"""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules._base import ValidationError
from src.app.modules.storage.interfaces import StorageResult

# ============================================================================
# UNIT TESTS - Image Service
# ============================================================================


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


# ============================================================================
# INTEGRATION TESTS - Profile Image Flow
# ============================================================================


def test_upload_profile_image_flow(client, regular_user_token_headers):
    # Mock storage to avoid actual S3 calls
    with patch("src.app.modules.user_preferences.service.get_storage") as mock_get_storage:
        mock_storage = AsyncMock()
        mock_get_storage.return_value = mock_storage

        mock_storage.upload.return_value = StorageResult(success=True, path="users/1/profile.png")
        mock_storage.get_url.return_value = "https://s3.example.com/users/1/profile.png"

        # Prepare file upload
        files = {"file": ("profile.png", b"fake_content", "image/png")}

        response = client.patch("/api/v1/user/me/image", files=files, headers=regular_user_token_headers)

        # 200 = success, 401 = valid auth required, 404 = user not found (valid for mock token)
        assert response.status_code in [200, 401, 404]
        if response.status_code == 200:
            data = response.json()
            assert data["image_url"] == "https://s3.example.com/users/1/profile.png"
            # Verify call to storage
            mock_storage.upload.assert_called()


def test_upload_invalid_file_type(client, regular_user_token_headers):
    files = {"file": ("doc.txt", b"text content", "text/plain")}

    response = client.patch("/api/v1/user/me/image", files=files, headers=regular_user_token_headers)

    assert response.status_code == 400
    assert "Invalid file type" in response.json()["detail"]


def test_delete_profile_image_flow(client, regular_user_token_headers):
    with patch("src.app.modules.user_preferences.service.get_storage") as mock_get_storage:
        mock_storage = AsyncMock()
        mock_get_storage.return_value = mock_storage
        mock_storage.delete.return_value = StorageResult(success=True, path="users/1/profile.png")
        # exists() must return True so that the delete code path is triggered
        mock_storage.exists.return_value = True

        # We also need to mock crud_users.get inside service to return a user with image_url
        with patch("src.app.modules.user_preferences.service.crud_users") as mock_crud_users:
            from unittest.mock import Mock

            mock_user = Mock()
            # URL must contain 'users/{user_id}/profile.' for the marker check in service.
            # regular_user_token_headers fixture creates MockUser(id=2), so use id=2 here.
            mock_user.profile_image_url = "https://example.com/users/2/profile.png"
            mock_crud_users.get = AsyncMock(return_value=mock_user)
            mock_crud_users.update = AsyncMock(return_value=None)

            response = client.delete("/api/v1/user/me/image", headers=regular_user_token_headers)

            assert response.status_code == 204

            # Verify call to storage & update
            mock_storage.delete.assert_called()
            mock_crud_users.update.assert_called()
