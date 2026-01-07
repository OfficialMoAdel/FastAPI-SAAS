"""Service for user preferences management.

Task: T090 [US9] Implement PreferencesService
Task: T095 [US10] Implement image validation (MIME type, size)
Task: T096 [US10] Implement S3 upload

Handles:
- Getting user preferences
- Updating user preferences
- Default preference initialization
- Profile image upload and deletion
"""

import logging
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.modules.storage import get_storage
from src.app.modules.user_preferences.crud import crud_user_preferences
from src.app.modules.user_preferences.models import DEFAULT_PREFERENCES
from src.app.modules.user_preferences.schemas import UserPreferencesUpdate
from src.app.modules.users.crud import crud_users

logger = logging.getLogger(__name__)


# Constants for image upload
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


class PreferencesService:
    """Service for handling user preferences operations."""

    async def get_preferences(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> dict[str, Any]:
        """Get user preferences.

        Creates default preferences if none exist.

        Args:
            db: Database session.
            user_id: User ID.

        Returns:
            dict: User preferences with updated_at timestamp.
        """
        prefs = await crud_user_preferences.get_or_create(db=db, user_id=user_id)

        # Build response with defaults for any missing keys
        result = DEFAULT_PREFERENCES.copy()
        if prefs.preferences:
            result.update(prefs.preferences)

        result["updated_at"] = prefs.updated_at

        # We also want to return the profile_image_url if we could,
        # but the schema `UserPreferencesResponse` might not have it.
        # The schema definition in router.py imported from schemas.py checks that.
        # Wait, I need to check `UserPreferencesResponse` schema.
        # T091 created `UserPreferencesResponse`.
        # If it doesn't have image_url, I might not need to return it here,
        # OR I should update `UserPreferencesResponse`.
        # T010 migration says `image_url` on users table.
        # Usually that's returned on `UserRead`.
        # The upload endpoint returns `dict` in my code, so that's fine.
        # But `get_preferences` returns `dict` matching `UserPreferencesResponse`.

        return result

    async def update_preferences(
        self,
        db: AsyncSession,
        user_id: int,
        updates: UserPreferencesUpdate,
    ) -> dict[str, Any]:
        """Update user preferences.

        Only provided fields are updated; others remain unchanged.

        Args:
            db: Database session.
            user_id: User ID.
            updates: Partial update schema.

        Returns:
            dict: Updated user preferences.
        """
        # Convert update schema to dict, excluding None values
        update_dict = updates.model_dump(exclude_unset=True, exclude_none=True)

        # Handle enum conversion
        if "theme" in update_dict and hasattr(update_dict["theme"], "value"):
            update_dict["theme"] = update_dict["theme"].value

        if not update_dict:
            # No updates provided, just return current preferences
            return await self.get_preferences(db=db, user_id=user_id)

        logger.debug(f"Updating preferences for user {user_id}: {update_dict}")

        # Update preferences
        prefs = await crud_user_preferences.update_preferences(
            db=db,
            user_id=user_id,
            updates=update_dict,
        )

        # Build response with defaults for any missing keys
        result = DEFAULT_PREFERENCES.copy()
        if prefs.preferences:
            result.update(prefs.preferences)

        result["updated_at"] = prefs.updated_at

        return result

    async def upload_profile_image(
        self,
        db: AsyncSession,
        user_id: int,
        file: UploadFile,
    ) -> dict[str, str]:
        """Upload profile image.

        Validates image type and size, uploads to storage, and updates user record.

        Args:
            db: Database session.
            user_id: User ID.
            file: Uploaded file.

        Returns:
            dict: {"image_url": "..."}
        """
        # 1. Validate file type
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_IMAGE_TYPES)}",
            )

        # 2. Validate file size
        file.file.seek(0, 2)  # Seek to end
        size = file.file.tell()
        file.file.seek(0)  # Reset to beginning

        if size > MAX_IMAGE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large. Max size: {MAX_IMAGE_SIZE_BYTES / 1024 / 1024} MB",
            )

        # 3. Upload to storage
        storage = get_storage(
            provider=settings.STORAGE_PROVIDER,
            bucket=settings.STORAGE_BUCKET,
            region=settings.STORAGE_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            endpoint_url=settings.AWS_ENDPOINT_URL,
        )

        file_ext = file.filename.split(".")[-1] if "." in file.filename else "png"
        path = f"users/{user_id}/profile.{file_ext}"

        # UploadFile.file IS a binary file-like object
        result = await storage.upload(file.file, path, file.content_type)

        if not result.success:
            logger.error(f"Failed to upload image for user {user_id}: {result.error}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload image",
            )

        # 4. Get URL
        # For this purpose, we assume get_url gives us a usable URL.
        image_url = await storage.get_url(path)

        # 5. Update User record
        user = await crud_users.get(db=db, id=user_id)
        if not user:
            # Should happen rarely if authenticated
            raise HTTPException(status_code=404, detail="User not found")

        # Update user.profile_image_url
        await crud_users.update(db=db, object={"profile_image_url": image_url}, id=user_id)

        return {"image_url": image_url}

    async def delete_profile_image(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> None:
        """Delete profile image.

        Args:
            db: Database session.
            user_id: User ID.
        """
        # 1. Get current user image path?
        # Ideally we know the path. If we used standard path, we can guess it.
        # But file extension might vary.
        # Maybe we should list or just try to delete common extensions?
        # Or better, we just allow overwriting and only support one image per user.
        # If we stored the URL, we might parse it to get the path?

        # For simplicity, we try the standard path logic or just delete what we can.
        # Let's assume we want to delete whatever is there.
        # But S3 delete requires key.

        storage = get_storage(
            provider=settings.STORAGE_PROVIDER,
            bucket=settings.STORAGE_BUCKET,
            region=settings.STORAGE_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            endpoint_url=settings.AWS_ENDPOINT_URL,
        )

        # We need to know the extension to delete the exact file.
        # We can fetch the user to get `profile_image_url` and try to extract key.
        user = await crud_users.get(db=db, id=user_id)
        if not user or not user.profile_image_url:
            return  # Nothing to delete

        # Very rough extraction of key from URL if it's a full URL
        # If it's just a path, we are good.
        # But `storage.get_url` returns full URL.
        # If we can't easily parse it, we might just try deleting all allowed extensions for this user.

        for ext in ["png", "jpg", "jpeg", "gif", "webp"]:
            path = f"users/{user_id}/profile.{ext}"
            if await storage.exists(path):
                await storage.delete(path)
                # We assume one image per user usually
                break

        # Update User record to nullify image_url
        await crud_users.update(
            db=db,
            db_obj=user,
            obj_in={"profile_image_url": None},  # Or default URL? Schema default is "https://www.profileimageurl.com"
        )

        return None


preferences_service = PreferencesService()
