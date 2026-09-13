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

import asyncio
from typing import Any

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules.storage.service import StorageAdminService
from src.app.modules.user_preferences.crud import crud_user_preferences
from src.app.modules.user_preferences.models import DEFAULT_PREFERENCES
from src.app.modules.user_preferences.schemas import UserPreferencesUpdate
from src.app.modules.users import crud_users
from src.app.modules.users.cache import UserCacheInvalidator
from src.app.modules.users.session_invalidation import InvalidationReason
from src.app.shared.exceptions import AppException, ModelNotFoundError, ValidationError

logger = get_logger(__name__)


# Constants for image upload
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


class PreferenceStorageError(AppException):
    """Raised when profile-image storage operations fail.

    Renamed from ``StorageError`` (H4/P1-#4): the old name collided with
    ``src.app.modules.storage.errors.StorageError`` while bypassing the
    storage module's registered tree, so raises fell through to the
    unregistered-exception 500 catch-all instead of a translated row.
    """

    def __init__(self, operation: str, detail: str):
        self.operation = operation
        self.message = f"Storage {operation} failed: {detail}"
        super().__init__(self.message)
        # Keep behavior: ``detail`` stays the raw backend detail, not the
        # composed message (AppException.__init__ mirrors message into detail).
        self.detail = detail


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

        # Merge explicit columns
        result.update(
            {
                "email_notifications_enabled": prefs.email_notifications_enabled,
                "push_notifications_enabled": prefs.push_notifications_enabled,
                "disabled_notification_types": prefs.disabled_notification_types,
                "quiet_hours_enabled": prefs.quiet_hours_enabled,
                "quiet_hours_start": prefs.quiet_hours_start,
                "quiet_hours_end": prefs.quiet_hours_end,
            }
        )

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

        logger.debug("Updating preferences for user :", user_id=user_id, updated_keys=list(update_dict.keys()))

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

        # Merge explicit columns
        result.update(
            {
                "email_notifications_enabled": prefs.email_notifications_enabled,
                "push_notifications_enabled": prefs.push_notifications_enabled,
                "disabled_notification_types": prefs.disabled_notification_types,
                "quiet_hours_enabled": prefs.quiet_hours_enabled,
                "quiet_hours_start": prefs.quiet_hours_start,
                "quiet_hours_end": prefs.quiet_hours_end,
            }
        )

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
        # 1. Validate file type via Content-Type header (fast initial check)
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            raise ValidationError(f"Invalid file type '{file.content_type}'. Allowed: {', '.join(ALLOWED_IMAGE_TYPES)}")

        # 2. Validate file size
        # Use asyncio.to_thread to run blocking I/O on the underlying file object
        # without blocking the event loop.
        await asyncio.to_thread(file.file.seek, 0, 2)  # Seek to end
        size = await asyncio.to_thread(file.file.tell)
        await file.seek(0)  # Reset to beginning

        if size > MAX_IMAGE_SIZE_BYTES:
            raise ValidationError(
                f"File too large ({size / 1024 / 1024:.1f} MB). Max size: {MAX_IMAGE_SIZE_BYTES / 1024 / 1024:.0f} MB"
            )

        # 3. SECURITY: Validate actual file content via magic bytes (prevents Content-Type spoofing).
        # An attacker can set Content-Type: image/jpeg but upload a malicious script.
        # `filetype` reads the file signature bytes to determine true type.
        try:
            import filetype  # type: ignore[import-untyped]

            header_chunk = await file.read(261)  # 261 bytes is enough for filetype detection
            await file.seek(0)
            detected_kind = filetype.guess(header_chunk)
            detected_mime = detected_kind.mime if detected_kind else None
            if detected_mime not in ALLOWED_IMAGE_TYPES:
                raise ValidationError(
                    f"File content does not match an allowed image type. Detected: {detected_mime or 'unknown'}"
                )
        except ImportError:
            logger.warning("filetype library not available; skipping magic-byte validation")

        # 4. Get User record first
        user = await crud_users.get(db=db, id=user_id)
        if not user:
            raise ModelNotFoundError("User", user_id)

        # 4. Prepare storage — tenant-aware provider from storage settings.
        # P2-#10: provider acquisition goes through StorageAdminService, the
        # storage module's admin facade (tenant-aware, cached, credentials
        # decrypted there). Deliberately NOT StorageService.put(): profile
        # images are not File entities — no quota/File-row semantics, and the
        # stable users/{uid}/profile.{ext} path enables old-image cleanup.
        tenant_ctx = get_current_tenant()
        if tenant_ctx is None:
            raise PreferenceStorageError("upload", "Tenant context not available")
        storage = await StorageAdminService(db).get_provider(tenant_ctx.tenant_id)

        filename = file.filename or "image.png"
        file_ext = filename.split(".")[-1] if "." in filename else "png"
        new_path = f"users/{user_id}/profile.{file_ext}"

        # 5. Check for old image and delete if extension changed
        old_url = user.get("profile_image_url") if isinstance(user, dict) else getattr(user, "profile_image_url", None)
        if old_url:
            base_url = old_url.split("?")[0]
            marker = f"users/{user_id}/profile."
            if marker in base_url:
                old_path = base_url[base_url.find(marker) :]
                # If the new path has a different extension, the old image would be orphaned
                if old_path and old_path != new_path:
                    try:
                        if await storage.exists(old_path):
                            await storage.delete(old_path)
                    except Exception as e:
                        logger.warning(
                            "Failed to delete old profile image during upload",
                            user_id=user_id,
                            error=str(e),
                            path=old_path,
                        )

        # UploadFile.file IS a binary file-like object
        result = await storage.upload(file.file, new_path, file.content_type)

        if not result.success:
            logger.error("Failed to upload image for user :", user_id=user_id, error=result.error)
            raise PreferenceStorageError("upload", result.error or "Unknown error")

        # 6. Get URL
        # For this purpose, we assume get_url gives us a usable URL.
        image_url = await storage.get_url(new_path)

        # 7. Update User record
        # Update user.profile_image_url
        await crud_users.update(db=db, object={"profile_image_url": image_url}, id=user_id)

        # Invalidate the profile/auth caches — profile_image_url is embedded
        # in both (user:profile:* and user:auth:*); without this the old URL
        # is served for up to USER_CACHE_TTL / AUTH_INFO_CACHE_TTL.
        await UserCacheInvalidator.invalidate_user_cache(
            user,
            bump_token_version=False,
            reason=InvalidationReason.PROFILE_EDIT,
        )

        return {"image_url": image_url, "message": "Profile image uploaded successfully"}

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

        # Tenant-aware provider from the storage admin facade (P2-#10)
        tenant_ctx = get_current_tenant()
        if tenant_ctx is None:
            raise PreferenceStorageError("delete", "Tenant context not available")
        storage = await StorageAdminService(db).get_provider(tenant_ctx.tenant_id)
        user = await crud_users.get(db=db, id=user_id)
        if not user:
            return  # Nothing to delete

        old_url = user.get("profile_image_url") if isinstance(user, dict) else getattr(user, "profile_image_url", None)
        if not old_url:
            return

        # Extract precise path from URL to accurately find it (avoid guessing extensions)
        base_url = old_url.split("?")[0]
        marker = f"users/{user_id}/profile."

        if marker in base_url:
            old_path = base_url[base_url.find(marker) :]
            try:
                if await storage.exists(old_path):
                    await storage.delete(old_path)
            except Exception as e:
                logger.warning(
                    "Failed to delete profile image",
                    user_id=user_id,
                    error=str(e),
                    path=old_path,
                )
        else:
            # Conservative path: fail soft on unrecognized URL formats to preserve the audit
            # trail and prevent orphaning storage objects without a verified key.
            logger.warning(
                "Unexpected profile image URL format; skipping storage deletion to preserve audit trail",
                user_id=user_id,
                url=old_url,
            )
            return
        # Update User record to nullify image_url
        await crud_users.update(
            db=db,
            object={"profile_image_url": None},
            id=user_id,
        )

        # Invalidate the profile/auth caches (same rationale as upload path).
        await UserCacheInvalidator.invalidate_user_cache(
            user,
            bump_token_version=False,
            reason=InvalidationReason.PROFILE_EDIT,
        )

        return None


def get_preferences_service() -> PreferencesService:
    """Dependency injection factory for PreferencesService.

    Note: This service uses a stateless pattern where db is passed
    to each method rather than the constructor. This allows the same
    service instance to be reused across different database sessions.

    Returns
    -------
    PreferencesService
        Service instance
    """
    return PreferencesService()
