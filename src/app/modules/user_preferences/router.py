"""Router for user preferences endpoints.

Task: T091 [US9] Create router with GET/PATCH /user/me/preferences
Task: T097 [US10] Add PATCH /user/me/image endpoint
Task: T098 [US10] Add DELETE /user/me/image endpoint
"""

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.db.database import async_get_db
from src.app.modules.user_preferences.schemas import (
    ImageUploadResponse,
    UserPreferencesResponse,
    UserPreferencesUpdate,
)
from src.app.modules.user_preferences.service import PreferencesService, get_preferences_service

router = APIRouter(prefix="/user/me", tags=["user"])


@router.get("/preferences", response_model=UserPreferencesResponse)
async def get_preferences(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
    service: PreferencesService = Depends(get_preferences_service),
):
    """Get current user's preferences.

    Returns user preferences with defaults for any unset values.
    Creates a preferences record if one doesn't exist.
    """
    result = await service.get_preferences(
        db=db,
        user_id=current_user["id"],
    )
    return UserPreferencesResponse(**result)


@router.patch("/preferences", response_model=UserPreferencesResponse)
async def update_preferences(
    updates: UserPreferencesUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
    service: PreferencesService = Depends(get_preferences_service),
):
    """Update current user's preferences.

    Only provided fields are updated; others remain unchanged.
    """
    result = await service.update_preferences(
        db=db,
        user_id=current_user["id"],
        updates=updates,
    )
    return UserPreferencesResponse(**result)


@router.patch("/image", response_model=ImageUploadResponse)
async def upload_profile_image(
    file: UploadFile = File(...),
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
    service: PreferencesService = Depends(get_preferences_service),
):
    """Upload profile image.

    Uploads image to storage and updates user profile URL.
    Returns the new image URL.
    """
    result = await service.upload_profile_image(
        db=db,
        user_id=current_user["id"],
        file=file,
    )
    return ImageUploadResponse(**result)


@router.delete("/image", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile_image(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
    service: PreferencesService = Depends(get_preferences_service),
):
    """Delete profile image.

    Removes image from storage and clears user profile URL.
    """
    await service.delete_profile_image(
        db=db,
        user_id=current_user["id"],
    )
