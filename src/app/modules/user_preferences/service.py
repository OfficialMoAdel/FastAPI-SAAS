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
from typing import Any, cast
from uuid import UUID, uuid4

import filetype  # type: ignore[import-untyped]
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules.user_preferences.crud import crud_user_preferences
from src.app.modules.user_preferences.models import DEFAULT_PREFERENCES
from src.app.modules.user_preferences.protocols import ProfileImageStore
from src.app.modules.user_preferences.schemas import UserPreferencesUpdate
from src.app.modules.users import InvalidationReason, UserCacheInvalidator, crud_users
from src.app.shared.error_codes import ErrorCode
from src.app.shared.exceptions import AppException, ModelNotFoundError, ValidationError

logger = get_logger(__name__)


# Constants for image upload
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

#: Marker every profile image key starts with. The replacement cleanup and
#: ``delete_profile_image`` both recover the storage key from the stored URL
#: through this prefix, so the key layout and the cleanup must agree.
_PROFILE_KEY_MARKER = "users/{user_id}/profile."


def _profile_key_from_url(url: str | None, user_id: int) -> str | None:
    """Recover this user's profile-image storage key from its public URL.

    Returns ``None`` for an unrecognised URL so the caller can fail soft
    instead of deleting a key it did not verify.
    """
    if not url:
        return None
    base_url = url.split("?")[0]
    marker = _PROFILE_KEY_MARKER.format(user_id=user_id)
    if marker not in base_url:
        return None
    return base_url[base_url.find(marker) :]


async def _delete_object_quietly(storage: ProfileImageStore, path: str, *, user_id: int) -> None:
    """Delete a storage object, logging instead of raising.

    Cleanup runs after the request has already succeeded, so it must never turn
    a good upload into an error: an orphaned object is bounded (one per upload),
    a broken ``profile_image_url`` is not.
    """
    try:
        if await storage.exists(path):
            await storage.delete(path)
    except Exception as e:  # noqa: BLE001 - best-effort cleanup by contract
        logger.warning(
            "Failed to delete profile image object",
            user_id=user_id,
            error=str(e),
            path=path,
        )


def _build_preferences_response(prefs: Any) -> dict[str, Any]:
    """Merge defaults, stored JSON preferences, and explicit columns (I14).

    Single owner of the response shape for get_preferences and
    update_preferences — the two bodies were copy-paste identical.
    """
    result = DEFAULT_PREFERENCES.copy()
    if prefs.preferences:
        result.update(prefs.preferences)

    # Merge explicit columns. These WIN over the JSON bag: the columns are the
    # authoritative storage for every setting that also exists as a column, so
    # a stale bag key can never shadow the value the API actually enforces.
    result.update(
        {
            # R2-26: `email_notifications` is the public JSON field; the column
            # `email_notifications_enabled` is what delivery reads. They were two
            # settings where only the column gated anything, so the JSON field was
            # a no-op the API still accepted and echoed. It is now an ALIAS — this
            # line is what makes the public field tell the truth.
            "email_notifications": prefs.email_notifications_enabled,
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


async def _resolve_image_store(
    db: AsyncSession,
    tenant_id: UUID,
    image_store: ProfileImageStore | None,
) -> ProfileImageStore:
    """Return the injected store, else acquire via the storage facade (I8).

    The lazy facade-root import keeps user_preferences free of any direct
    ``storage.service`` dependency; tests may inject a ``ProfileImageStore``
    instead.
    """
    if image_store is not None:
        return image_store
    from src.app.modules.storage import StorageAdminService  # facade root (lazy: avoids import cycle)

    # get_provider is untyped downstream; the provider satisfies the
    # ProfileImageStore protocol structurally (upload/delete/exists/get_url).
    return cast(ProfileImageStore, await StorageAdminService(db).get_provider(tenant_id))


class PreferenceStorageError(AppException):
    """Raised when profile-image storage operations fail.

    Renamed from ``StorageError`` (H4/P1-#4): the old name collided with
    ``src.app.modules.storage.errors.StorageError`` while bypassing the
    storage module's registered tree, so raises fell through to the
    unregistered-exception 500 catch-all instead of a translated row.

    Candidate 3, pass 5: it now declares its own 400 mapping, which is what
    finally lets ``core.errors`` stop importing this module — the last
    ``core -> modules`` edge out of the exception-mapping hub.
    """

    http_status = 400
    response_code = ErrorCode.INVALID_INPUT
    log_message = "Profile image storage operation failed"
    # A backend upload/delete failing is an operational incident.
    log_error = True

    def __init__(self, operation: str, detail: str):
        self.operation = operation
        self.message = f"Storage {operation} failed: {detail}"
        super().__init__(self.message)
        # Keep behavior: ``detail`` stays the raw backend detail, not the
        # composed message (AppException.__init__ mirrors message into detail).
        self.detail = detail

    def http_details(self) -> dict[str, Any]:
        """Envelope ``details``: the operation, and the RAW backend reason.

        ``reason`` reads ``self.detail`` — the raw backend string, deliberately
        not the composed ``message``, so a client sees the provider's own words
        rather than our wrapper sentence.
        """
        return {
            "operation": getattr(self, "operation", ""),
            "reason": getattr(self, "detail", str(self)),
        }


class PreferencesService:
    """Service for handling user preferences operations.

    Note
    ----
    ``PreferencesService`` deliberately does **not** subclass
    ``BaseService``. Its read/update/delete contracts return preference dicts
    rather than ORM entities or a configured read schema, so inheriting would
    give the base a ``select_schema`` it can never honour and would type the
    CRUD surface as something this service cannot produce. It composes the
    same primitives (``model``, ``db``, ``crud``) directly instead.

    This mirrors ``users.UserService``, which documents the same choice in its
    own docstring, and is recorded here as well as in
    ``user_preferences/AGENTS.md`` so the reason is visible from the class,
    not only from the module guide. Migration is a separate piece of work.
    """

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

        return _build_preferences_response(prefs)

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
        # R2-13: keep explicit nulls. `exclude_none` made every nullable field
        # (quiet_hours_start/end) impossible to clear, even though the schema
        # advertises `str | None` — so a null is a request, not an absence. The
        # CRUD layer decides which columns may actually take it.
        update_dict = updates.model_dump(exclude_unset=True)

        # R2-26: `email_notifications` (JSON bag) and `email_notifications_enabled`
        # (column) were duplicate settings where only the column gated delivery.
        # The public JSON field is now an alias of the column, so a client that
        # sends it changes delivery instead of writing an inert bag key.
        if "email_notifications" in update_dict:
            legacy_value = update_dict.pop("email_notifications")
            if "email_notifications_enabled" in update_dict:
                if update_dict["email_notifications_enabled"] != legacy_value:
                    logger.warning(
                        "conflicting_notification_preference_fields",
                        user_id=user_id,
                        used="email_notifications_enabled",
                    )
            else:
                update_dict["email_notifications_enabled"] = legacy_value

        # Handle enum conversion
        if "theme" in update_dict and hasattr(update_dict["theme"], "value"):
            update_dict["theme"] = update_dict["theme"].value

        if not update_dict:
            # No updates provided, just return current preferences
            return await self.get_preferences(db=db, user_id=user_id)

        logger.debug("Updating preferences for user", user_id=user_id, updated_keys=list(update_dict.keys()))

        # Update preferences

        prefs = await crud_user_preferences.update_preferences(
            db=db,
            user_id=user_id,
            updates=update_dict,
        )

        return _build_preferences_response(prefs)

    async def upload_profile_image(
        self,
        db: AsyncSession,
        user_id: int,
        file: UploadFile,
        image_store: ProfileImageStore | None = None,
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

        # 3. SECURITY: Validate actual file content via magic bytes (prevents
        # Content-Type spoofing). An attacker can set Content-Type: image/jpeg but
        # upload a malicious script; `filetype` reads the file signature instead.
        #
        # R2-19: `filetype` is a declared dependency, so it is imported at module
        # scope. The old in-function import caught ImportError and CARRIED ON with
        # magic-byte validation silently disabled — a missing dependency must fail
        # loudly, not quietly downgrade a security check.
        header_chunk = await file.read(261)  # 261 bytes is enough for filetype detection
        await file.seek(0)
        detected_kind = filetype.guess(header_chunk)
        if detected_kind is None or detected_kind.mime not in ALLOWED_IMAGE_TYPES:
            detected_mime = detected_kind.mime if detected_kind else None
            raise ValidationError(
                f"File content does not match an allowed image type. Detected: {detected_mime or 'unknown'}"
            )

        # 4. Get User record first
        user = await crud_users.get(db=db, id=user_id)
        if not user:
            raise ModelNotFoundError("User", user_id)

        old_url = user.get("profile_image_url") if isinstance(user, dict) else getattr(user, "profile_image_url", None)

        # 5. Prepare storage — tenant-aware provider from storage settings.
        # P2-#10: provider acquisition goes through the storage admin facade
        # (tenant-aware, cached, credentials decrypted there). Deliberately
        # NOT StorageService.put(): profile images are not File entities — no
        # quota/File-row semantics.
        tenant_ctx = get_current_tenant()
        if tenant_ctx is None:
            raise PreferenceStorageError("upload", "Tenant context not available")
        storage = await _resolve_image_store(db, tenant_ctx.tenant_id, image_store)

        # R2-18: the extension comes from the DETECTED type, never from the
        # client-supplied filename. `filename.split(".")[-1]` cannot produce a
        # ".." segment, but it CAN contain "/" and corrupt the
        # `users/{uid}/profile.` cleanup marker that both the replacement cleanup
        # below and delete_profile_image rely on.
        file_ext = str(detected_kind.extension)
        # Defence in depth: the extension becomes a path segment, so a future
        # ALLOWED_IMAGE_TYPES entry must not be able to smuggle a separator in.
        if not file_ext.isalnum() or len(file_ext) > 5:
            raise ValidationError("Unsupported image extension")

        # R2-14: versioned key + upload-first.
        #
        # A fixed `profile.{ext}` key made an atomic replace impossible: with an
        # unchanged extension the upload OVERWROTE the only copy (nothing to roll
        # back to), and with a changed extension the old object was deleted BEFORE
        # the new upload — so a failed upload left `profile_image_url` pointing at
        # a deleted object. A per-upload suffix makes the swap atomic: the DB
        # reference moves to a fully-written object, and the previous object is
        # dropped only once nothing points at it.
        new_path = f"users/{user_id}/profile.{file_ext}.{uuid4().hex}"

        # UploadFile.file IS a binary file-like object
        result = await storage.upload(file.file, new_path, file.content_type)
        if not result.success:
            logger.error("Failed to upload image for user", user_id=user_id, error=result.error)
            raise PreferenceStorageError("upload", result.error or "Unknown error")

        image_url = await storage.get_url(new_path)

        # 6. Point the user at the new object FIRST. Until this commits, the old
        #    object is still the referenced one and must stay untouched.
        try:
            await crud_users.update(db=db, object={"profile_image_url": image_url}, id=user_id)
        except Exception:
            # The DB still names the old object, so the object we just wrote is
            # unreferenced. Drop it and re-raise the real failure.
            await _delete_object_quietly(storage, new_path, user_id=user_id)
            raise

        # Invalidate the profile/auth caches — profile_image_url is embedded
        # in both (user:profile:* and user:auth:*); without this the old URL
        # is served for up to USER_CACHE_TTL / AUTH_INFO_CACHE_TTL.
        await UserCacheInvalidator.invalidate_user_cache(
            user,
            bump_token_version=False,
            reason=InvalidationReason.PROFILE_EDIT,
        )

        # 7. Only now is the previous object unreferenced. Best-effort: a failed
        #    cleanup leaves an orphan, never a broken image.
        previous_path = _profile_key_from_url(old_url, user_id)
        if previous_path is not None and previous_path != new_path:
            await _delete_object_quietly(storage, previous_path, user_id=user_id)

        return {"image_url": image_url, "message": "Profile image uploaded successfully"}

    async def delete_profile_image(
        self,
        db: AsyncSession,
        user_id: int,
        image_store: ProfileImageStore | None = None,
    ) -> None:
        """Delete profile image.

        Args:
            db: Database session.
            user_id: User ID.
            image_store: Optional injected store (defaults to the storage
                facade provider).
        """
        # The storage key is recovered from the stored URL (the extension
        # varies per upload); unrecognized URL formats fail soft below.

        # Tenant-aware provider from the storage admin facade (P2-#10)
        tenant_ctx = get_current_tenant()
        if tenant_ctx is None:
            raise PreferenceStorageError("delete", "Tenant context not available")
        storage = await _resolve_image_store(db, tenant_ctx.tenant_id, image_store)
        user = await crud_users.get(db=db, id=user_id)
        if not user:
            return  # Nothing to delete

        old_url = user.get("profile_image_url") if isinstance(user, dict) else getattr(user, "profile_image_url", None)
        if not old_url:
            return

        # Extract precise path from URL to accurately find it (avoid guessing extensions)
        old_path = _profile_key_from_url(old_url, user_id)
        if old_path is None:
            # Conservative path: fail soft on unrecognized URL formats to preserve the audit
            # trail and prevent orphaning storage objects without a verified key.
            logger.warning(
                "Unexpected profile image URL format; skipping storage deletion to preserve audit trail",
                user_id=user_id,
                url=old_url,
            )
            return
        await _delete_object_quietly(storage, old_path, user_id=user_id)
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
