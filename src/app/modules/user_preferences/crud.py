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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.user_preferences.models import DEFAULT_PREFERENCES, UserPreferences
from src.app.modules.user_preferences.schemas import (
    UserPreferencesCreate,
    UserPreferencesDeleteInternal,
    UserPreferencesRead,
    UserPreferencesUpdate,
    UserPreferencesUpdateInternal,
)

# R2-13: the ONLY preference columns whose contract allows an explicit null.
# ``UserPreferencesUpdate`` advertises these as ``str | None``, so a client
# sending ``null`` is asking to CLEAR them — dropping it made a documented
# reset impossible. Every other column keeps skipping ``None``: it is NOT NULL,
# and writing ``None`` there fails at flush instead of clearing.
#
# Derived from the model rather than restated as a literal: a new nullable
# preference column would otherwise be silently MISSING from the set, so a
# client's explicit ``null`` would be skipped (200 returned, old value kept)
# instead of clearing the column. Keys can only reach this set through
# ``UserPreferencesUpdate.model_dump(exclude_unset=True)``, so deriving cannot
# widen what a client is able to clear.
NULLABLE_PREFERENCE_COLUMNS: frozenset[str] = frozenset(
    column.name for column in UserPreferences.__table__.columns if column.nullable
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
        try:
            # R2-20: two concurrent first accesses can both miss the SELECT and
            # both INSERT; the loser hits the unique constraint on user_id. That
            # is not a 500 — the winner's row is the answer.
            #
            # The INSERT is scoped to a SAVEPOINT so the recovery unwinds ONLY
            # this statement. A root ``db.rollback()`` discarded the caller's
            # whole request-scoped unit of work and expired every object it had
            # loaded, even though the successful path would have committed that
            # work — a first-access collision is not a reason to throw away
            # unrelated pending writes. The failed transaction still has to be
            # unwound before the re-read, or the session keeps using it; the
            # savepoint is what does that without reaching past this INSERT.
            async with db.begin_nested():
                db.add(new_prefs)
                await db.flush()
        except IntegrityError:
            winner = await self.get_by_user(db=db, user_id=user_id)
            if winner is None:
                # The winner was deleted between the savepoint rollback and the
                # re-read. Surface the original failure instead of fabricating a
                # detached instance that no row backs.
                raise
            return winner
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
            if hasattr(prefs, key) and key != "preferences":
                if value is None and key not in NULLABLE_PREFERENCE_COLUMNS:
                    continue
                setattr(prefs, key, value)
                continue
            # JSON-bag keys cannot hold a meaningful null: the response merges
            # the bag verbatim, so a stored ``None`` would be echoed back.
            if value is not None:
                current_prefs[key] = value

        # Update the preferences column
        prefs.preferences = current_prefs
        await db.commit()
        await db.refresh(prefs)
        return prefs


crud_user_preferences = CRUDUserPreferences(UserPreferences)
