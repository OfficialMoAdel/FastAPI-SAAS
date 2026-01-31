"""CRUD operations for PasskeyCredential.

Task: T071 [US7] Create PasskeyCredential CRUD with max 10 limit (CLR-003)

Provides database operations for:
- Creating passkey credentials
- Listing credentials by user
- Counting credentials (for limit enforcement)
- Deleting credentials
"""

from typing import Any

from fastcrud import FastCRUD
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import PasskeyCredential
from .schemas import (
    PasskeyCredentialCreate,
    PasskeyCredentialRead,
    PasskeyCredentialUpdate,
)


class CRUDPasskeyCredential(
    FastCRUD[
        PasskeyCredential,
        PasskeyCredentialCreate,
        PasskeyCredentialUpdate,
        PasskeyCredentialUpdate,
        PasskeyCredentialUpdate,
        PasskeyCredentialRead,
    ]
):
    """CRUD operations for passkey credentials."""

    MAX_PASSKEYS_PER_USER = 10

    async def get_credential(
        self,
        db: AsyncSession,
        id: int | None = None,
        credential_id: bytes | None = None,
        user_id: int | None = None,
        **kwargs: Any,
    ) -> PasskeyCredential | None:
        """Get passkey credential by ID or credential_id.

        Args:
            db: Database session
            id: Database record ID
            credential_id: WebAuthn credential ID (encrypted)
            user_id: Filter by user ID

        Returns:
            PasskeyCredential or None if not found
        """
        if id is not None:
            # Use parent get with return_as_model for the model object
            query = select(self.model).where(self.model.id == id)
            result = await db.execute(query)
            return result.scalar_one_or_none()

        query = select(self.model)
        if credential_id is not None:
            query = query.where(self.model.credential_id == credential_id)
        if user_id is not None:
            query = query.where(self.model.user_id == user_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_user(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> list[PasskeyCredential]:
        """Get all passkey credentials for a user.

        Args:
            db: Database session
            user_id: User ID to get credentials for

        Returns:
            List of PasskeyCredential objects
        """
        query = select(self.model).where(self.model.user_id == user_id).order_by(self.model.created_at.desc())

        result = await db.execute(query)
        return list(result.scalars().all())

    async def count_by_user(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> int:
        """Count passkey credentials for a user.

        Args:
            db: Database session
            user_id: User ID to count credentials for

        Returns:
            Number of credentials
        """
        query = select(func.count()).select_from(self.model).where(self.model.user_id == user_id)

        result = await db.execute(query)
        return result.scalar() or 0

    async def can_create(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> bool:
        """Check if user can create another passkey (under limit).

        Args:
            db: Database session
            user_id: User ID to check

        Returns:
            True if user can create another passkey
        """
        count = await self.count_by_user(db=db, user_id=user_id)
        return count < self.MAX_PASSKEYS_PER_USER

    async def delete_credential(
        self,
        db: AsyncSession,
        id: int | None = None,
        db_object: PasskeyCredential | None = None,
    ) -> None:
        """Delete a passkey credential.

        Args:
            db: Database session
            id: Database record ID
            db_object: PasskeyCredential object to delete
        """
        if id is not None:
            await super().delete(db=db, id=id)
            return

        if db_object is not None:
            await db.delete(db_object)
            await db.commit()


crud_passkey_credentials = CRUDPasskeyCredential(PasskeyCredential)
