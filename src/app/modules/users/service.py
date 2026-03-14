"""User service layer - encapsulates all user business logic."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.app.core.events import EventPriority, event_bus
from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.security import blacklist_token, get_password_hash
from src.app.core.service_registry import get_service
from src.app.core.tenancy.context import get_current_tenant
from src.app.core.utils import cache
from src.app.modules._base.exceptions import DuplicateError, ModelNotFoundError
from src.app.modules._base.service import BaseService
from src.app.modules.tiers.models import Tier
from src.app.modules.users.crud import CRUDUser, crud_users
from src.app.modules.users.models import User
from src.app.modules.users.schemas import UserCreate, UserCreateInternal, UserRead, UserTierUpdate, UserUpdate

logger = get_logger(__name__)

# Cache configuration
USER_CACHE_TTL = 60  # seconds
USER_CACHE_PREFIX = "user:profile"
AUTH_INFO_CACHE_TTL = 3600  # 1 hour
AUTH_INFO_CACHE_PREFIX = "user:auth"


def _user_cache_key(user_id: UUID | str) -> str:
    """Generate cache key for user profile.

    User ID is globally unique (UUID), so no tenant scoping needed.
    """
    return f"{USER_CACHE_PREFIX}:{user_id}"


def _require_tenant_id() -> str:
    """
    Get current tenant ID or raise HTTPException.

    Tenant context is required for tenant-scoped cache keys to prevent
    cross-tenant cache collisions. Use this for email/username lookups
    which are only unique within a tenant.

    SECURITY FIX: Changed from RuntimeError (causes 500) to HTTPException (500)
    so FastAPI's exception handlers output a proper JSON error response.
    """
    tenant = get_current_tenant()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error: tenant context not available.",
        )
    return str(tenant.id)


def _user_email_cache_key(email: str) -> str:
    """Generate cache key for user by email (tenant-scoped, no collision risk).

    SECURITY FIX: Normalizes email to lowercase to prevent cache misses
    when the same email is provided with different casing.
    """
    t_id = _require_tenant_id()
    # Normalize email to lowercase for consistent cache keys
    normalized_email = email.lower().strip()
    return f"{USER_CACHE_PREFIX}:{t_id}:email:{normalized_email}"


def _user_username_cache_key(username: str) -> str:
    """Generate cache key for user by username (tenant-scoped, no collision risk).

    SECURITY FIX: Normalizes username to lowercase to prevent cache misses
    when the same username is provided with different casing.
    """
    t_id = _require_tenant_id()
    # Normalize username to lowercase for consistent cache keys
    normalized_username = username.lower().strip()
    return f"{USER_CACHE_PREFIX}:{t_id}:username:{normalized_username}"


def _user_auth_cache_key(identifier: str) -> str:
    """Generate cache key for user auth info (tenant-scoped, no collision risk).

    SECURITY FIX: Normalizes identifier to lowercase to prevent cache misses
    when the same email/username is provided with different casing, consistent
    with _user_email_cache_key and _user_username_cache_key.
    """
    t_id = _require_tenant_id()
    normalized = identifier.lower().strip()
    return f"{AUTH_INFO_CACHE_PREFIX}:{t_id}:{normalized}"


class UserService(BaseService[User]):
    """Service class for user-related business logic."""

    crud: "CRUDUser"  # Override type for custom CRUD with get_user_with_tier_eager

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(User, db)
        self.crud = crud_users  # type: ignore[assignment]

    async def list_users(
        self,
        *,
        page: int = 1,
        per_page: int = 10,
        is_deleted: bool = False,
    ) -> dict[str, Any]:
        """Get paginated list of users."""
        offset = (page - 1) * per_page
        return await self.crud.get_multi(
            db=self.db,
            offset=offset,
            limit=per_page,
            is_deleted=is_deleted,
            schema_to_select=UserRead,
        )

    async def _cache_user(self, user: User | dict[str, Any]) -> None:
        """Cache user profile in Redis."""
        if cache.client is None:
            return

        try:
            # Handle both ORM object and dict
            if isinstance(user, dict):
                user_data = user
                user_id = user["id"]
                email = user["email"]
                username = user["username"]
            else:
                # U7: Direct dict construction instead of Pydantic validate+dump
                # ORM data is already validated, skip redundant Pydantic overhead
                user_data = {
                    "id": user.id,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "updated_at": user.updated_at.isoformat() if user.updated_at else None,
                    "name": user.name,
                    "username": user.username,
                    "email": user.email,
                    "profile_image_url": user.profile_image_url,
                    "tier_id": user.tier_id,
                    "tenant_id": user.tenant_id,
                }
                user_id = user.id
                email = user.email
                username = user.username

            serialized = json_dumps(user_data)

            # Cache by all lookup keys using a pipeline for single round-trip
            async with cache.client.pipeline(transaction=False) as pipe:
                pipe.setex(_user_cache_key(user_id), USER_CACHE_TTL, serialized)
                pipe.setex(_user_email_cache_key(email), USER_CACHE_TTL, serialized)
                pipe.setex(_user_username_cache_key(username), USER_CACHE_TTL, serialized)
                await pipe.execute()
        except Exception as e:
            # Log warning but don't fail the request due to cache issues
            user_id_log = user["id"] if isinstance(user, dict) else user.id
            logger.warning("Failed to cache user %s: %s", user_id_log, e)

    async def _invalidate_user_cache(self, user: User | dict[str, Any]) -> None:
        """Invalidate all cache entries for a user."""
        if cache.client is None:
            return

        try:
            # Handle both ORM object and dict
            if isinstance(user, dict):
                user_id = user["id"]
                email = user["email"]
                username = user["username"]
            else:
                user_id = user.id
                email = user.email
                username = user.username

            # Normalize to match cache keys
            email = email.lower().strip()
            username = username.lower().strip()

            await cache.client.delete(
                _user_cache_key(user_id),
                _user_email_cache_key(email),
                _user_username_cache_key(username),
                _user_auth_cache_key(username),
                _user_auth_cache_key(email),  # Also invalidate email-based auth cache
                f"user:{username}:token_version",  # Invalidate token version correctly
                f"user:{email}:token_version",  # Also under email namespace
            )
        except Exception as e:
            user_id_log = user["id"] if isinstance(user, dict) else user.id
            logger.warning("Failed to invalidate cache for user", user_id_log=user_id_log, error=e)

    async def _get_cached_user(self, cache_key: str) -> dict[str, Any] | None:
        """Get cached user data if available."""
        if cache.client is None:
            return None

        try:
            cached = await cache.client.get(cache_key)
            if cached:
                result: dict[str, Any] = json_loads(cached)
                return result
        except Exception as e:
            logger.warning("Failed to get cached user", error=e)
        return None

    async def create(self, user: UserCreate) -> User:  # type: ignore[override]
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
        # SECURITY FIX: Check for duplicates BEFORE expensive password hashing
        # Prevents DoS via forced bcrypt operations
        # OPTIMIZATION FIX: Fetch tenant once and reuse throughout method (#16)
        tenant = get_current_tenant()  # Single fetch, reused throughout

        # Optimized: Check for duplicate email or username in single query
        stmt = select(User).where(
            or_(User.email == user.email, User.username == user.username), User.is_deleted.is_(False)
        )

        # Explicitly filter by tenant_id if available (redundant with RLS but critical for SQLite tests)
        # Uses cached tenant from single fetch above
        if tenant:
            stmt = stmt.where(User.tenant_id == tenant.id)

        existing_user = await self.db.scalar(stmt)

        if existing_user:
            # SECURITY FIX: Use a generic error message regardless of which field exists.
            # Different messages for email vs username would allow enumeration of registered users.
            raise DuplicateError("email or username", user.email)

        # Hash password and create internal user object
        user_dict = user.model_dump()
        user_dict["hashed_password"] = await get_password_hash(password=user_dict["password"])
        del user_dict["password"]

        # Inject tenant_id from context - uses same cached tenant
        if tenant is not None:
            user_dict["tenant_id"] = tenant.id

        user_internal = UserCreateInternal(**user_dict)

        try:
            result = await self.crud.create(db=self.db, object=user_internal, commit=False)

            # SECURITY FIX: Ensure event is published ONLY after successful commit
            # Capture result data before commit since it may expire after session flush
            event_payload = {
                "user_id": result.id,
                "email": result.email,
                "username": result.username,
                "tenant_id": str(result.tenant_id),
            }

            # Now explicitly commit the transaction so the event fires
            await self.db.commit()

            import asyncio
            # Fire-and-forget task to publish the event safely from async context
            asyncio.create_task(
                event_bus.publish(
                    "user.created",
                    event_payload,
                    priority=EventPriority.NORMAL,
                )
            )

        except IntegrityError as e:
            # Handle race condition: duplicate was created between our check and insert
            await self.db.rollback()
            if "uq_user_email_tenant" in str(e):
                raise DuplicateError("email", user.email) from e
            elif "uq_user_username_tenant" in str(e):
                raise DuplicateError("username", user.username) from e
            raise

        return result  # type: ignore[return-value]

    async def get_user_auth_info(
        self, *, username: str | None = None, email: str | None = None
    ) -> dict[str, Any] | None:
        """Get user with tier info for authentication (cached).

        Parameters
        ----------
        username : str | None
            Username to look up (mutually exclusive with email)
        email : str | None
            Email to look up (mutually exclusive with username)

        Returns
        -------
        dict[str, Any] | None
            User dict with tier info, or None if not found
        """
        identifier = email if email else username
        if not identifier:
            return None

        cache_key = _user_auth_cache_key(identifier)

        # Try cache
        cached = await self._get_cached_user(cache_key)
        if cached:
            return cached

        # Fetch from DB (using eager loading)
        user_with_tier = await self.crud.get_user_with_tier_eager(db=self.db, username=username, email=email)
        if not user_with_tier:
            return None

        # Cache it
        if cache.client:
            try:
                serialized = json_dumps(user_with_tier)
                await cache.client.setex(cache_key, AUTH_INFO_CACHE_TTL, serialized)
            except Exception as e:
                logger.warning("Failed to cache auth info for :", identifier=identifier, error=e)

        # FIXED: Ensure return type is dict as expected by callers and hinted in signature.
        # This resolves the type mismatch between ORM objects and dictionaries.
        if hasattr(user_with_tier, "__dict__") and not isinstance(user_with_tier, dict):
            # Convert ORM object to dict
            return {
                "id": user_with_tier.id,
                "username": user_with_tier.username,
                "email": user_with_tier.email,
                "hashed_password": user_with_tier.hashed_password,
                "tenant_id": user_with_tier.tenant_id,
                "is_superuser": user_with_tier.is_superuser,
                "is_deleted": user_with_tier.is_deleted,
                "is_active": user_with_tier.is_active,
                "token_version": user_with_tier.token_version,
                "tier": {
                    "id": user_with_tier.tier.id,
                    "name": user_with_tier.tier.name,
                }
                if user_with_tier.tier
                else None,
            }

        return user_with_tier

    async def get_by_username(self, username: str) -> User | dict[str, Any]:
        """Get a user by username with Redis caching.

        Parameters
        ----------
        username : str
            Username to look up

        Returns
        -------
        User | dict[str, Any]
            User instance or cached dict

        Raises
        ------
        ModelNotFoundError
            If user not found
        """
        # Try cache first - return cached data directly to avoid DB query
        cached = await self._get_cached_user(_user_username_cache_key(username))
        if cached:
            logger.debug("Cache hit for username", username=username)
            return cached

        db_user = await self.crud.get(db=self.db, username=username, is_deleted=False)
        if db_user is None:
            raise ModelNotFoundError("User", username)

        await self._cache_user(db_user)
        return db_user

    async def get(self, user_id: Any) -> User | dict[str, Any] | None:  # type: ignore[override]
        """Get a user by ID with Redis caching.

        Parameters
        ----------
        user_id : Any
            User ID (UUID)

        Returns
        -------
        User | dict[str, Any] | None
            User instance, cached dict, or None if not found
        """
        # Try cache first - return cached data directly to avoid DB query
        cached = await self._get_cached_user(_user_cache_key(user_id))
        if cached:
            logger.debug("Cache hit for user", user_id=user_id)
            return cached

        user = await self.crud.get(db=self.db, id=user_id, is_deleted=False)
        if user:
            await self._cache_user(user)
        return user

    async def get_by_email(self, email: str) -> User | dict[str, Any] | None:
        """Get a user by email address with Redis caching.

        Parameters
        ----------
        email : str
            Email address to look up

        Returns
        -------
        User | dict[str, Any] | None
            User instance, cached dict, or None if not found
        """
        # Try cache first - return cached data directly to avoid DB query
        cached = await self._get_cached_user(_user_email_cache_key(email))
        if cached:
            logger.debug("Cache hit for user email", email=email)
            return cached

        user = await self.crud.get(db=self.db, email=email, is_deleted=False)
        if user:
            await self._cache_user(user)
        return user

    async def update(  # type: ignore[override]
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
        # SECURITY FIX: Prevent updating soft-deleted users
        db_user = await self.crud.get(db=self.db, username=username, is_deleted=False)
        if db_user is None:
            raise ModelNotFoundError("User", username)

        if isinstance(db_user, dict):
            db_username = db_user["username"]
            db_email = db_user["email"]
        else:
            db_username = db_user.username
            db_email = db_user.email

        # Ownership check (superusers bypass)
        if db_username != current_user["username"] and not current_user.get("is_superuser"):
            raise ForbiddenException()

        # Check for duplicate email if changing
        if values.email is not None and values.email != db_email:
            tenant_id_val = db_user.get("tenant_id") if isinstance(db_user, dict) else db_user.tenant_id
            if await self.crud.exists(db=self.db, email=values.email, tenant_id=tenant_id_val):
                raise DuplicateError("email", values.email)

        # Check for duplicate username if changing
        if values.username is not None and values.username != db_username:
            tenant_id_val = db_user.get("tenant_id") if isinstance(db_user, dict) else db_user.tenant_id
            if await self.crud.exists(db=self.db, username=values.username, tenant_id=tenant_id_val):
                raise DuplicateError("username", values.username)

        await self.crud.update(db=self.db, object=values, username=username)

        # Invalidate cache after update
        await self._invalidate_user_cache(db_user)

        return {"message": "User updated"}

    async def delete(  # type: ignore[override]
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
        db_user = await self.crud.get(db=self.db, username=username, is_deleted=False)
        if not db_user:
            raise ModelNotFoundError("User", username)

        if username != current_user["username"] and not current_user.get("is_superuser"):
            raise ForbiddenException()

        # BUG FIX: Previously used two separate auto-committing calls:
        #   1. crud.update(token_version + 1)  <- auto-commit
        #   2. crud.delete(username)            <- auto-commit (soft delete)
        # A crash between them left token_version incremented but is_deleted=False:
        # old sessions were invalidated but the user could still log in.
        # Fix: combine both into a single atomic UPDATE, commit once.
        from datetime import UTC, datetime

        from sqlalchemy import update as sa_update

        user_id_val = db_user["id"] if isinstance(db_user, dict) else db_user.id
        await self.db.execute(
            sa_update(User)
            .where(User.id == user_id_val)
            .values(
                token_version=User.token_version + 1,
                is_deleted=True,
                updated_at=datetime.now(UTC),
            )
        )
        await self.db.commit()

        await self._invalidate_user_cache(db_user)

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
        # SECURITY FIX: Single query with soft-delete filter (prevents double query)
        db_user = await self.crud.get(db=self.db, username=username, is_deleted=False)
        if not db_user:
            raise ModelNotFoundError("User", username)

        # SECURITY FIX: Delete user BEFORE invalidating cache
        # Prevents stale cache if DB operation fails
        await self.crud.db_delete(db=self.db, username=username)
        await self._invalidate_user_cache(db_user)

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
        db_user = await self.crud.get(db=self.db, username=username, is_deleted=False)
        if db_user is None:
            raise ModelNotFoundError("User", username)

        # Validate tier exists via registry
        tier_svc = get_service("tier", self.db)
        try:
            await tier_svc.get_tier_by_id(values.tier_id)
        except ModelNotFoundError:
            raise ModelNotFoundError("Tier", values.tier_id)

        await self.crud.update(db=self.db, object=values.model_dump(), username=username)

        # Invalidate cache
        await self._invalidate_user_cache(db_user)

        # A3 FIX: Handle both ORM objects and dict responses
        user_name = db_user["name"] if isinstance(db_user, dict) else db_user.name
        return {"message": f"User {user_name} Tier updated"}

    async def get_with_rate_limits(self, username: str) -> dict[str, Any]:
        """Get user with their tier's rate limits.

        Optimized to use eager loading - fetches User + Tier in single query.

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
            If user not found
        """
        # Eager load User + Tier + RateLimits in single query
        # joinedload(User.tier).joinedload(Tier.rate_limits) ensures we fetch everything at once
        stmt = (
            select(User)
            .options(joinedload(User.tier).joinedload(Tier.rate_limits))
            .where(User.username == username, User.is_deleted.is_(False))
        )
        # SECURITY FIX: Filter by tenant to prevent cross-tenant data exposure.
        # Without this, a superuser could query any user across all tenants by username.
        tenant = get_current_tenant()
        if tenant:
            stmt = stmt.where(User.tenant_id == tenant.id)

        result = await self.db.execute(stmt)
        db_user = result.unique().scalar_one_or_none()

        if db_user is None:
            raise ModelNotFoundError("User", username)

        # U7: Direct dict construction instead of Pydantic validate+dump
        # ORM data is already validated, skip redundant Pydantic overhead
        user_dict = {
            "id": db_user.id,
            "created_at": db_user.created_at,
            "updated_at": db_user.updated_at,
            "name": db_user.name,
            "username": db_user.username,
            "email": db_user.email,
            "profile_image_url": db_user.profile_image_url,
            "tier_id": db_user.tier_id,
        }

        if db_user.tier_id is None or db_user.tier is None:
            user_dict["tier_rate_limits"] = []
            return user_dict

        # Rate limits are already loaded via joinedload
        # U7: Direct dict construction for rate limits
        user_dict["tier_rate_limits"] = [
            {
                "id": rl.id,
                "tier_id": rl.tier_id,
                "name": rl.name,
                "path": rl.path,
                "limit": rl.limit,
                "period": rl.period,
            }
            for rl in db_user.tier.rate_limits
        ]

        return user_dict

    async def get_with_tier(self, username: str) -> dict[str, Any] | None:
        """Get user with tier information merged.

        Optimized to use single query via get_user_with_tier_eager.
        """
        user_with_tier = await self.crud.get_user_with_tier_eager(db=self.db, username=username)

        if not user_with_tier:
            raise ModelNotFoundError("User", username)

        # BUG FIX: Previously returned None if user has no tier, which is misleading
        # because the user DOES exist. Callers would confuse None with "user not found".
        # Now returns the user dict without tier_* keys when no tier is assigned.
        if not user_with_tier.get("tier"):
            return user_with_tier

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
