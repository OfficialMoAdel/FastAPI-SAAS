"""API Key service layer - business logic for API key management."""

import hashlib
import inspect
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.events import EventPriority, event_bus
from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.modules._base.exceptions import ModelNotFoundError
from src.app.modules._base.service import BaseService
from src.app.modules.api_keys.crud import crud_api_keys
from src.app.modules.api_keys.models import APIKey
from src.app.modules.api_keys.schemas import APIKeyCreate, APIKeyRead, APIKeyResponse

logger = get_logger(__name__)

# Cache configuration for API key validation
API_KEY_CACHE_PREFIX = "api_key"
API_KEY_CACHE_TTL = 300  # 5 minutes


class APIKeyService(BaseService[APIKey]):
    """Service class for API key-related business logic."""

    KEY_PREFIX_LENGTH = 8
    KEY_LENGTH = 32  # 256 bits of entropy

    def __init__(self, db: AsyncSession):
        """Initialize APIKeyService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(APIKey, db)

    def _generate_key(self) -> tuple[str, str, str]:
        """Generate a new API key.

        Returns
        -------
            Tuple of (full_key, prefix, hashed_key)
        """
        # Generate a secure random key
        raw_key = secrets.token_urlsafe(self.KEY_LENGTH)
        prefix = raw_key[: self.KEY_PREFIX_LENGTH]

        # Hash the key for storage (using SHA-256)
        hashed = hashlib.sha256(raw_key.encode()).hexdigest()

        return raw_key, prefix, hashed

    def _hash_key(self, key: str) -> str:
        """Hash an API key for comparison."""
        return hashlib.sha256(key.encode()).hexdigest()

    async def create_api_key(
        self,
        api_key_data: APIKeyCreate,
        user_id: int,
        tenant_id: UUID,
    ) -> APIKeyResponse:
        """Create a new API key for a user.

        Parameters
        ----------
        api_key_data : APIKeyCreate
            API key creation data
        user_id : int
            ID of the user creating the key
        tenant_id : int
            ID of the tenant

        Returns
        -------
        APIKeyResponse
            Created API key with the actual key (only shown once)
        """
        # SECURITY FIX: Prevent duplicate API key names within the same tenant
        from sqlalchemy import select

        stmt = select(APIKey).where(APIKey.tenant_id == tenant_id, APIKey.name == api_key_data.name)
        existing_result = await self.db.execute(stmt)
        if existing_result.scalar_one_or_none():
            from src.app.core.exceptions.http_exceptions import BadRequestException

            raise BadRequestException(f"API key with name '{api_key_data.name}' already exists in this tenant")

        # Generate the key
        full_key, prefix, hashed_key = self._generate_key()

        # Prepare scopes as JSON string if provided
        scopes_json: str | None = None
        if api_key_data.scopes:
            scopes_result = json_dumps(api_key_data.scopes, as_str=True)
            scopes_json = scopes_result if isinstance(scopes_result, str) else scopes_result.decode("utf-8")

        # Create the API key record using SQLAlchemy model directly
        key_record = APIKey(
            name=api_key_data.name,
            key_prefix=prefix,
            hashed_key=hashed_key,
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=scopes_json,
            expires_at=api_key_data.expires_at,
            is_active=True,
        )
        self.db.add(key_record)
        await self.db.commit()
        await self.db.refresh(key_record)

        # Audit log the creation
        from src.app.core.events import event_bus

        await event_bus.publish(
            "audit.log",
            {
                "tenant_id": str(tenant_id),
                "action": "api_key.created",
                "entity_type": "api_key",
                "entity_id": str(key_record.id),
                "user_id": user_id,
                "details": {
                    "key_name": key_record.name,
                    "key_prefix": key_record.key_prefix,
                    "expires_at": key_record.expires_at.isoformat() if key_record.expires_at else None,
                    "scopes": api_key_data.scopes,
                },
            },
        )

        logger.info(
            f"API key created | user={user_id} | tenant={tenant_id} | "
            f"key_id={key_record.id} | key_name={key_record.name}"
        )

        return APIKeyResponse(
            id=key_record.id,
            name=key_record.name,
            key=full_key,  # Only returned on creation!
            key_prefix=prefix,
            expires_at=key_record.expires_at,
            created_at=key_record.created_at,
        )

    async def list_api_keys(
        self,
        user_id: int,
        tenant_id: UUID,
        page: int = 1,
        items_per_page: int = 10,
    ) -> dict[str, Any]:
        """List API keys for a user.

        Parameters
        ----------
        user_id : int
            ID of the user
        tenant_id : int
            ID of the tenant
        page : int
            Page number
        items_per_page : int
            Items per page

        Returns
        -------
        dict
            Paginated list of API keys (without actual keys)
        """
        return await crud_api_keys.get_multi(
            db=self.db,
            user_id=user_id,
            tenant_id=tenant_id,
            offset=(page - 1) * items_per_page,
            limit=items_per_page,
            schema_to_select=APIKeyRead,  # type: ignore[arg-type]
        )

    async def revoke_api_key(
        self,
        key_id: int,
        user_id: int,
        tenant_id: UUID,
    ) -> dict[str, str]:
        """Revoke an API key.

        Parameters
        ----------
        key_id : int
            ID of the API key to revoke
        user_id : int
            ID of the user making the request
        tenant_id : int
            ID of the tenant

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If key not found
        ForbiddenException
            If user doesn't own the key
        """
        from sqlalchemy import select

        # T7 FIX: fetch inside the tenant/user scope so a foreign key id is
        # indistinguishable from a missing one. The previous unscoped lookup +
        # post-hoc ownership check returned 403 for existing foreign keys,
        # giving callers an existence oracle over other tenants' key ids.
        key_stmt = select(APIKey).where(
            APIKey.id == key_id,
            APIKey.tenant_id == tenant_id,
            APIKey.user_id == user_id,
        )
        key_result = await self.db.execute(key_stmt)
        key = key_result.scalar_one_or_none()
        if inspect.isawaitable(key):
            key = await key

        if key is None:
            raise ModelNotFoundError("APIKey", key_id)

        api_key_hashed = key.hashed_key
        api_key_name = key.name
        api_key_prefix = key.key_prefix
        api_key_is_active = key.is_active

        # Invalidate cache before revoking (using hashed_key from DB record)
        redis_client = get_redis()
        if redis_client is not None and api_key_hashed:
            cache_key = self._get_cache_key(api_key_hashed)
            await redis_client.delete(cache_key)

        await crud_api_keys.update(
            db=self.db,
            id=key_id,
            object={"is_active": False},
        )

        # Audit log the revocation
        from src.app.core.events import event_bus

        await event_bus.publish(
            "audit.log",
            {
                "tenant_id": str(tenant_id),
                "action": "api_key.revoked",
                "entity_type": "api_key",
                "entity_id": str(key_id),
                "user_id": user_id,
                "details": {
                    "key_name": api_key_name,
                    "key_prefix": api_key_prefix,
                    "was_active": api_key_is_active,
                },
            },
        )

        logger.info(
            f"API key revoked | user={user_id} | tenant={tenant_id} | key_id={key_id} | key_name={api_key_name}"
        )

        return {"message": "API key revoked successfully"}

    def _get_cache_key(self, api_key_hash: str) -> str:
        """Get Redis cache key for an API key hash.

        Uses full hash to prevent any theoretical collision risk.
        """
        # S7 FIX: Use full hash instead of truncating to 16 chars
        return f"{API_KEY_CACHE_PREFIX}:{api_key_hash}"

    async def _get_cached_tenant_active(self, tenant_id: Any) -> bool | None:
        """Probe the shared L2 tenant cache for liveness (T8).

        Returns True when the cached tenant is active, False when it is
        suspended/deleted, and None when no cache entry exists (or probing
        fails) so the caller falls back to the authoritative DB check.
        """
        redis_client = get_redis()
        if redis_client is None or not tenant_id:
            return None
        try:
            from src.app.modules.tenants.service import _get_cache_key

            raw = await redis_client.get(_get_cache_key(tenant_id))
            if raw is None:
                return None
            data = json_loads(raw)
            if not isinstance(data, dict) or "is_active" not in data:
                return None
            return bool(data["is_active"])
        except Exception as e:  # noqa: BLE001 - probe must never break validation
            logger.debug("tenant_liveness_probe_failed", error=str(e))
            return None

    async def validate_api_key(
        self,
        api_key: str,
    ) -> dict[str, Any] | None:
        """Validate an API key and return the associated user info.

        Security: Verifies tenant is active to prevent suspended tenant access.
        Performance: Uses Redis caching with 5-minute TTL to reduce DB load.

        Parameters
        ----------
        api_key : str
            The API key to validate

        Returns
        -------
        dict | None
            User info if valid, None otherwise
        """
        hashed = self._hash_key(api_key)
        cache_key = self._get_cache_key(hashed)
        redis_client = get_redis()

        # Check cache first (O(1) lookup)
        if redis_client is not None:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                result = json_loads(cached)
                # Check expiration on cached data
                if result.get("expires_at"):
                    expires_at = datetime.fromisoformat(result["expires_at"])
                    if expires_at < datetime.now(UTC):
                        # Expired - delete from cache and return None
                        await redis_client.delete(cache_key)
                        return None
                # BUG FIX: Also check valid_from. Without this, a rotated key that was
                # cached before its valid_from time could be used prematurely (up to 5 min).
                if result.get("valid_from"):
                    valid_from = datetime.fromisoformat(result["valid_from"])
                    if valid_from > datetime.now(UTC):
                        return None
                # T8 FIX: cached key entries previously outlived tenant/key
                # suspension because nothing evicted them. Probe the shared L2
                # tenant cache: False => suspended (reject); missing => the
                # tenant entry was invalidated, so distrust the key cache and
                # take the authoritative DB path below.
                tenant_state = await self._get_cached_tenant_active(result["tenant_id"])
                if tenant_state is False:
                    return None
                if tenant_state is None:
                    pass  # fall through to DB validation
                else:
                    return {
                        "user_id": result["user_id"],
                        "tenant_id": result["tenant_id"],
                        "scopes": result["scopes"],
                        "key_id": result["key_id"],
                    }

        # Cache miss - query database
        result = await self._validate_from_db(hashed)
        if result is None:
            return None

        # Cache the result
        if redis_client is not None:
            cache_data = {
                "user_id": result["user_id"],
                "tenant_id": result["tenant_id"],
                "scopes": result["scopes"],
                "key_id": result["key_id"],
                "expires_at": result["expires_at"].isoformat() if result["expires_at"] else None,
                # BUG FIX: Cache valid_from so it can be enforced on cache hits too.
                "valid_from": result["valid_from"].isoformat() if result.get("valid_from") else None,
            }
            await redis_client.setex(cache_key, API_KEY_CACHE_TTL, json_dumps(cache_data))

        return {
            "user_id": result["user_id"],
            "tenant_id": result["tenant_id"],
            "scopes": result["scopes"],
            "key_id": result["key_id"],
        }

    async def _validate_from_db(self, hashed_key: str) -> dict[str, Any] | None:
        """Validate API key from database.

        Parameters
        ----------
        hashed_key : str
            SHA-256 hash of the API key

        Returns
        -------
        dict | None
            Key info if valid, None otherwise
        """
        from sqlalchemy import select

        from src.app.modules.api_keys.models import APIKey
        from src.app.modules.tenants.models import Tenant

        # JOIN with tenant to check tenant.is_active
        stmt = (
            select(APIKey)
            .join(Tenant, APIKey.tenant_id == Tenant.id)
            .where(
                APIKey.hashed_key == hashed_key,
                APIKey.is_active.is_(True),
                Tenant.is_active.is_(True),  # Prevent suspended tenant access
            )
        )

        result = await self.db.execute(stmt)
        key = result.scalar_one_or_none()

        if not key:
            return None

        # Check valid_from (grace period rotation support)
        # Key is not valid before this timestamp
        if key.valid_from and key.valid_from > datetime.now(UTC):
            return None

        # Check expiration
        if key.expires_at and key.expires_at < datetime.now(UTC):
            return None

        # Update last_used_at without committing — let the request lifecycle handle commit.
        # SECURITY FIX (C4): Previously committed here on the shared request-scoped session,
        # which could flush unrelated pending changes from other parts of the request.
        try:
            from src.app.modules.api_keys.crud import crud_api_keys

            await crud_api_keys.update(
                db=self.db,
                id=key.id,
                object={"last_used_at": datetime.now(UTC)},
                commit=False,
            )
        except Exception as e:
            logger.warning("failed_to_update_api_key_last_used", key_id=key.id, error=str(e))

        return {
            "user_id": key.user_id,
            "tenant_id": key.tenant_id,
            "scopes": json_loads(key.scopes) if key.scopes else [],
            "key_id": key.id,
            "expires_at": key.expires_at,
            # Include valid_from so cache can enforce it on hits
            "valid_from": key.valid_from if hasattr(key, "valid_from") else None,
        }

    async def rotate_api_key(
        self,
        key_id: int,
        user_id: int,
        tenant_id: UUID,
        grace_period_days: int = 7,
    ) -> APIKeyResponse:
        """Rotate an API key with grace period for zero-downtime rotation.

        Flow:
        1. Create new key immediately with valid_from set to future timestamp
        2. Old key remains valid for grace_period_days
        3. New key becomes valid after grace period expires
        4. Both keys work during grace period (seamless transition)

        Security: Revoking the old key automatically invalidates it.
        Users should update their applications within the grace period.

        Parameters
        ----------
        key_id : int
            ID of the API key to rotate
        user_id : int
            ID of the user making the request
        tenant_id : int
            ID of the tenant
        grace_period_days : int
            Days before new key becomes valid (default: 7)

        Returns
        -------
        APIKeyResponse
            New API key (only shown once)

        Raises
        ------
        ModelNotFoundError
            If key not found
        ForbiddenException
            If user doesn't own the key
        """
        from datetime import timedelta

        from sqlalchemy import select

        # Get the existing key
        old_key_stmt = select(APIKey).where(APIKey.id == key_id)
        old_key_result = await self.db.execute(old_key_stmt)
        old_key = old_key_result.scalar_one_or_none()
        if inspect.isawaitable(old_key):
            old_key = await old_key

        if old_key is None:
            raise ModelNotFoundError("APIKey", key_id)

        if old_key.tenant_id != tenant_id or old_key.user_id != user_id:
            raise ForbiddenException("You don't have permission to rotate this key")
        if not old_key.is_active:
            raise ForbiddenException("Cannot rotate a revoked key")

        api_key = old_key
        api_key_name = api_key.name
        api_key_scopes = api_key.scopes
        api_key_expires_at = api_key.expires_at
        api_key_id = api_key.id
        api_key_prefix = api_key.key_prefix

        # BUG FIX: The old logic set valid_from=now+grace on the NEW key, making it unusable
        # during the grace period and never expired the OLD key, leaving it valid indefinitely.
        # Correct behaviour:
        #   - New key: valid immediately (valid_from=None)
        #   - Old key: expires_at = min(now + grace_period, original_expires_at)  (auto-expires after grace window)
        grace_expires_at = datetime.now(UTC) + timedelta(days=grace_period_days)
        if api_key_expires_at and api_key_expires_at < grace_expires_at:
            grace_expires_at = api_key_expires_at

        # Generate the new key
        full_key, prefix, hashed_key = self._generate_key()

        # Create new key record — valid immediately (no valid_from restriction)
        new_key_record = APIKey(
            name=f"{api_key_name} (Rotated)",
            key_prefix=prefix,
            hashed_key=hashed_key,
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=api_key_scopes,  # Copy scopes as JSON string
            expires_at=api_key_expires_at,  # Copy original expiration
            is_active=True,
            valid_from=None,  # Active immediately
        )
        self.db.add(new_key_record)

        # Mark old key to expire after the grace period (atomic update in same transaction)
        await crud_api_keys.update(
            db=self.db,
            id=api_key_id,
            object={"expires_at": grace_expires_at},
            commit=False,
        )

        await self.db.commit()
        await self.db.refresh(new_key_record)

        old_key_hashed = api_key.hashed_key
        redis_client = get_redis()
        if redis_client is not None and old_key_hashed:
            await redis_client.delete(self._get_cache_key(old_key_hashed))

        # Audit log the rotation

        await event_bus.publish(
            "audit.log",
            {
                "action": "api_key.rotated",
                "entity_type": "api_key",
                "entity_id": str(new_key_record.id),
                "tenant_id": str(tenant_id),
                "user_id": str(user_id) if user_id else None,
                "details": {
                    "old_key_id": api_key_id,
                    "old_key_name": api_key_name,
                    "old_key_prefix": api_key_prefix,
                    "new_key_id": new_key_record.id,
                    "new_key_name": new_key_record.name,
                    "new_key_prefix": new_key_record.key_prefix,
                    "grace_period_days": grace_period_days,
                    "old_key_expires_at": grace_expires_at.isoformat(),
                },
            },
            priority=EventPriority.NORMAL,
        )

        logger.info(
            f"API key rotated | user={user_id} | tenant={tenant_id} | "
            f"old_key_id={api_key_id} | new_key_id={new_key_record.id} | "
            f"grace_period={grace_period_days}d | old_key_expires_at={grace_expires_at.isoformat()}"
        )

        return APIKeyResponse(
            id=new_key_record.id,
            name=new_key_record.name,
            key=full_key,  # Only returned on creation!
            key_prefix=prefix,
            expires_at=new_key_record.expires_at,
            created_at=new_key_record.created_at,
        )


def get_api_key_service(db: AsyncSession) -> APIKeyService:
    """Dependency injection factory for APIKeyService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    APIKeyService
        Service instance with database session
    """
    return APIKeyService(db)
