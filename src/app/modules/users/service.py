"""User service layer - encapsulates all user business logic."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import (
    DuplicateValueException,
    ForbiddenException,
    NotFoundException,
)
from ...core.security import blacklist_token, get_password_hash
from .crud import crud_users
from .schemas import UserCreate, UserCreateInternal, UserRead, UserTierUpdate, UserUpdate


class UserService:
    """Service class for user-related business logic."""

    async def create_user(self, user: UserCreate, db: AsyncSession) -> dict[str, Any]:
        """Create a new user with validation and password hashing.

        Parameters
        ----------
        user : UserCreate
            User creation data
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Created user data

        Raises
        ------
        DuplicateValueException
            If email or username already exists
        NotFoundException
            If user creation fails
        """
        # Check for duplicate email
        email_exists = await crud_users.exists(db=db, email=user.email)
        if email_exists:
            raise DuplicateValueException("Email is already registered")

        # Check for duplicate username
        username_exists = await crud_users.exists(db=db, username=user.username)
        if username_exists:
            raise DuplicateValueException("Username not available")

        # Hash password and create internal user object
        user_dict = user.model_dump()
        user_dict["hashed_password"] = get_password_hash(password=user_dict["password"])
        del user_dict["password"]

        user_internal = UserCreateInternal(**user_dict)
        created_user = await crud_users.create(db=db, object=user_internal, schema_to_select=UserRead)

        if created_user is None:
            raise NotFoundException("Failed to create user")

        return created_user

    async def get_user(self, username: str, db: AsyncSession) -> dict[str, Any]:
        """Get a user by username.

        Parameters
        ----------
        username : str
            Username to look up
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            User data

        Raises
        ------
        NotFoundException
            If user not found
        """
        db_user = await crud_users.get(db=db, username=username, is_deleted=False, schema_to_select=UserRead)
        if db_user is None:
            raise NotFoundException("User not found")
        return db_user

    async def update_user(
        self,
        username: str,
        values: UserUpdate,
        current_user: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, str]:
        """Update a user with ownership validation.

        Parameters
        ----------
        username : str
            Username of user to update
        values : UserUpdate
            Update values
        current_user : dict[str, Any]
            Currently authenticated user
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        NotFoundException
            If user not found
        ForbiddenException
            If current user doesn't own this account
        DuplicateValueException
            If new email/username already taken
        """
        db_user = await crud_users.get(db=db, username=username)
        if db_user is None:
            raise NotFoundException("User not found")

        db_username = db_user["username"]
        db_email = db_user["email"]

        # Ownership check
        if db_username != current_user["username"]:
            raise ForbiddenException()

        # Check for duplicate email if changing
        if values.email is not None and values.email != db_email:
            if await crud_users.exists(db=db, email=values.email):
                raise DuplicateValueException("Email is already registered")

        # Check for duplicate username if changing
        if values.username is not None and values.username != db_username:
            if await crud_users.exists(db=db, username=values.username):
                raise DuplicateValueException("Username not available")

        await crud_users.update(db=db, object=values, username=username)
        return {"message": "User updated"}

    async def delete_user(
        self,
        username: str,
        current_user: dict[str, Any],
        token: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Soft delete a user with ownership validation.

        Parameters
        ----------
        username : str
            Username of user to delete
        current_user : dict[str, Any]
            Currently authenticated user
        token : str
            Access token to blacklist
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        NotFoundException
            If user not found
        ForbiddenException
            If current user doesn't own this account
        """
        db_user = await crud_users.get(db=db, username=username, schema_to_select=UserRead)
        if not db_user:
            raise NotFoundException("User not found")

        if username != current_user["username"]:
            raise ForbiddenException()

        await crud_users.delete(db=db, username=username)
        await blacklist_token(token=token, db=db)
        return {"message": "User deleted"}

    async def hard_delete_user(self, username: str, token: str, db: AsyncSession) -> dict[str, str]:
        """Permanently delete a user from database (superuser only).

        Parameters
        ----------
        username : str
            Username of user to delete
        token : str
            Access token to blacklist
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        NotFoundException
            If user not found
        """
        db_user = await crud_users.exists(db=db, username=username)
        if not db_user:
            raise NotFoundException("User not found")

        await crud_users.db_delete(db=db, username=username)
        await blacklist_token(token=token, db=db)
        return {"message": "User deleted from the database"}

    async def update_user_tier(
        self, username: str, values: UserTierUpdate, db: AsyncSession
    ) -> dict[str, str]:
        """Update user's tier (superuser only).

        Parameters
        ----------
        username : str
            Username of user to update
        values : UserTierUpdate
            Tier update values
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        NotFoundException
            If user or tier not found
        """
        from ...modules.tiers.crud import crud_tiers
        from ...modules.tiers.schemas import TierRead

        db_user = await crud_users.get(db=db, username=username, schema_to_select=UserRead)
        if db_user is None:
            raise NotFoundException("User not found")

        db_tier = await crud_tiers.get(db=db, id=values.tier_id, schema_to_select=TierRead)
        if db_tier is None:
            raise NotFoundException("Tier not found")

        await crud_users.update(db=db, object=values.model_dump(), username=username)
        return {"message": f"User {db_user['name']} Tier updated"}


# Singleton instance for import convenience
user_service = UserService()
