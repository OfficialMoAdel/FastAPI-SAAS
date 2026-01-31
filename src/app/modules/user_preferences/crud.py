"""CRUD operations for UserPreferences.

Task: T089 [US9] Create UserPreferences CRUD with defaults

Provides database operations for:
- Creating user preferences with defaults
- Getting preferences by user
- Updating preferences (partial updates)
"""

from typing import Any

from fastcrud import FastCRUD
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DEFAULT_PREFERENCES, UserPreferences
from .schemas import (
    UserPreferencesCreate,
    UserPreferencesDeleteInternal,
    UserPreferencesRead,
    UserPreferencesUpdate,
    UserPreferencesUpdateInternal,
)


class CRUDUserPreferences(
    FastCRUD[
        UserPreferences,
        UserPreferencesCreate,
        UserPreferencesUpdate,
        UserPreferencesUpdateInternal,
        UserPreferencesDeleteInternal,
        UserPreferencesRead,
    ]
):
    """CRUD operations for user preferences."""

    async def get_by_user(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> UserPreferences | None:
        """Get preferences for a specific user.

        Args:
            db: Database session
            user_id: User ID to get preferences for

        Returns:
            UserPreferences or None if not found
        """
        query = select(self.model).where(self.model.user_id == user_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> UserPreferences:
        """Get existing preferences or create with defaults.

        This ensures every user has a preferences record.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            UserPreferences (existing or newly created)
        """
        existing = await self.get_by_user(db=db, user_id=user_id)
        if existing:
            return existing

        # Create new preferences with defaults
        new_prefs = UserPreferences(
            user_id=user_id,
            preferences=DEFAULT_PREFERENCES.copy(),
        )
        db.add(new_prefs)
        await db.commit()
        await db.refresh(new_prefs)
        return new_prefs

    async def update_preferences(
        self,
        db: AsyncSession,
        user_id: int,
        updates: dict[str, Any],
    ) -> UserPreferences:
        """Update user preferences (partial update).

        Only provided fields are updated; others remain unchanged.

        Args:
            db: Database session
            user_id: User ID
            updates: Dictionary of fields to update

        Returns:
            Updated UserPreferences
        """
        # Get or create preferences
        prefs = await self.get_or_create(db=db, user_id=user_id)

        # Merge updates into existing preferences
        current_prefs = prefs.preferences.copy() if prefs.preferences else {}
        for key, value in updates.items():
            if value is not None:
                current_prefs[key] = value

        # Update the preferences column
        prefs.preferences = current_prefs
        await db.commit()
        await db.refresh(prefs)
        return prefs


crud_user_preferences = CRUDUserPreferences(UserPreferences)
