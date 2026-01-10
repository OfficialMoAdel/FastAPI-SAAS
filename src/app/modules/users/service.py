"""User service layer - encapsulates all user business logic."""

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ForbiddenException
from ...core.security import blacklist_token, get_password_hash
from ...core.tenancy.context import get_tenant_context
from .._base.exceptions import DuplicateError, ModelNotFoundError
from .._base.service import BaseService
from .crud import crud_users
from .models import User
from .schemas import UserCreate, UserCreateInternal, UserTierUpdate, UserUpdate


class UserService(BaseService[User]):
    """Service class for user-related business logic."""

    def __init__(self, db: AsyncSession):
        super().__init__(User, db)
        self.crud = crud_users

    async def create(self, user: UserCreate) -> User:
        """Create a new user with validation and password hashing.

        Parameters
        ----------
        user : UserCreate
            User creation data

        Returns
        -------
        User
            Created user instance

        Raises
        ------
        DuplicateError
            If email or username already exists
        """
        # Optimized: Check for duplicate email or username in single query
        stmt = select(User).where(
            or_(User.email == user.email, User.username == user.username), User.is_deleted.is_(False)
        )
        existing_user = await self.db.scalar(stmt)

        if existing_user:
            if existing_user.email == user.email:
                raise DuplicateError("email", user.email)
            raise DuplicateError("username", user.username)

        # Hash password and create internal user object
        user_dict = user.model_dump()
        user_dict["hashed_password"] = await get_password_hash(password=user_dict["password"])
        del user_dict["password"]

        # Inject tenant_id from context
        tenant = get_tenant_context()
        if tenant is not None:
            user_dict["tenant_id"] = tenant.id

        user_internal = UserCreateInternal(**user_dict)
        return await self.crud.create(db=self.db, object=user_internal)

    async def get_by_username(self, username: str) -> User:
        """Get a user by username.

        Parameters
        ----------
        username : str
            Username to look up

        Returns
        -------
        User
            User instance

        Raises
        ------
        ModelNotFoundError
            If user not found
        """
        db_user = await self.crud.get(db=self.db, username=username, is_deleted=False)
        if db_user is None:
            raise ModelNotFoundError("User", username)
        return db_user

    async def update(
        self,
        username: str,
        values: UserUpdate,
        current_user: dict[str, Any],
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

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user not found
        ForbiddenException
            If current user doesn't own this account
        DuplicateError
            If new email/username already taken
        """
        db_user = await self.crud.get(db=self.db, username=username)
        if db_user is None:
            raise ModelNotFoundError("User", username)

        db_username = db_user.username
        db_email = db_user.email

        # Ownership check
        if db_username != current_user["username"]:
            raise ForbiddenException()

        # Check for duplicate email if changing
        if values.email is not None and values.email != db_email:
            if await self.crud.exists(db=self.db, email=values.email):
                raise DuplicateError("email", values.email)

        # Check for duplicate username if changing
        if values.username is not None and values.username != db_username:
            if await self.crud.exists(db=self.db, username=values.username):
                raise DuplicateError("username", values.username)

        await self.crud.update(db=self.db, object=values, username=username)
        return {"message": "User updated"}

    async def delete(
        self,
        username: str,
        current_user: dict[str, Any],
        token: str,
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

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user not found
        ForbiddenException
            If current user doesn't own this account
        """
        db_user = await self.crud.get(db=self.db, username=username)
        if not db_user:
            raise ModelNotFoundError("User", username)

        if username != current_user["username"]:
            raise ForbiddenException()

        await self.crud.delete(db=self.db, username=username)
        await blacklist_token(token=token, db=self.db)
        return {"message": "User deleted"}

    async def hard_delete(self, username: str, token: str) -> dict[str, str]:
        """Permanently delete a user from database (superuser only).

        Parameters
        ----------
        username : str
            Username of user to delete
        token : str
            Access token to blacklist

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user not found
        """
        db_user = await self.crud.exists(db=self.db, username=username)
        if not db_user:
            raise ModelNotFoundError("User", username)

        await self.crud.db_delete(db=self.db, username=username)
        await blacklist_token(token=token, db=self.db)
        return {"message": "User deleted from the database"}

    async def update_tier(self, username: str, values: UserTierUpdate) -> dict[str, str]:
        """Update user's tier (superuser only).

        Parameters
        ----------
        username : str
            Username of user to update
        values : UserTierUpdate
            Tier update values

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If user or tier not found
        """
        # Use service registry for cross-module decoupling
        from ...core.service_registry import get_service

        db_user = await self.crud.get(db=self.db, username=username)
        if db_user is None:
            raise ModelNotFoundError("User", username)

        # Validate tier exists via registry
        tier_svc = get_service("tier", self.db)
        try:
            await tier_svc.get_tier_by_id(values.tier_id)
        except ModelNotFoundError:
            raise ModelNotFoundError("Tier", values.tier_id)

        await self.crud.update(db=self.db, object=values.model_dump(), username=username)
        return {"message": f"User {db_user.name} Tier updated"}

    async def get_with_rate_limits(self, username: str) -> dict[str, Any]:
        """Get user with their tier's rate limits.

        Parameters
        ----------
        username : str
            Username to look up

        Returns
        -------
        dict[str, Any]
            User data with tier_rate_limits

        Raises
        ------
        ModelNotFoundError
            If user or tier not found
        """
        # Use service registry for cross-module decoupling
        from ...core.service_registry import get_service

        db_user = await self.crud.get(db=self.db, username=username)
        if db_user is None:
            raise ModelNotFoundError("User", username)

        user_dict = {c.name: getattr(db_user, c.name) for c in db_user.__table__.columns}
        if db_user.tier_id is None:
            user_dict["tier_rate_limits"] = []
            return user_dict

        tier_svc = get_service("tier", self.db)
        rate_limit_svc = get_service("rate_limit", self.db)

        db_tier = await tier_svc.get_tier_by_id(db_user.tier_id)
        rate_limits_response = await rate_limit_svc.get_rate_limits_by_tier_id(db_tier["id"])

        user_dict["tier_rate_limits"] = rate_limits_response["data"]
        return user_dict

    async def get_with_tier(self, username: str) -> dict[str, Any] | None:
        """Get user with tier information merged.

        Optimized to use single query via get_user_with_tier_eager.
        """
        user_with_tier = await self.crud.get_user_with_tier_eager(db=self.db, username=username)

        if not user_with_tier:
            raise ModelNotFoundError("User", username)

        if not user_with_tier.get("tier"):
            return None

        # Flatten structure to match expected output format
        user_dict = user_with_tier.copy()
        tier_data = user_dict.pop("tier")

        # Filter out SQLAlchemy internal state
        if "_sa_instance_state" in tier_data:
            del tier_data["_sa_instance_state"]

        for key, value in tier_data.items():
            user_dict[f"tier_{key}"] = value

        return user_dict


def get_user_service(db: AsyncSession) -> UserService:
    """Dependency injection factory for UserService."""
    return UserService(db)
