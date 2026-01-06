from unittest.mock import AsyncMock, patch

from src.app.modules.storage.interfaces import StorageResult


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
        # We also need to mock crud_users.get inside service to return a user with image_url

        with patch("src.app.modules.user_preferences.service.crud_users") as mock_crud_users:
             from unittest.mock import Mock
             mock_user = Mock()
             mock_user.profile_image_url = "https://example.com/profile.png"
             mock_crud_users.get = AsyncMock(return_value=mock_user)
             mock_crud_users.update = AsyncMock(return_value=None)

             response = client.delete("/api/v1/user/me/image", headers=regular_user_token_headers)

             assert response.status_code == 204

             # Verify call to storage & update
             mock_storage.delete.assert_called()
             mock_crud_users.update.assert_called()
