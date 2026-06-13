"""Tenant service layer - encapsulates all tenant business logic."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.utils.cache import client as redis_client
from src.app.modules._base.exceptions import DuplicateError, ModelNotFoundError
from src.app.modules._base.service import BaseService
from src.app.modules.tenants.crud import crud_tenants
from src.app.modules.tenants.models import Tenant
from src.app.modules.tenants.schemas import TenantCreate, TenantResponse, TenantUpdate

logger = get_logger(__name__)

# P2-4: Cache TTL for tenant data (5 minutes)
TENANT_CACHE_TTL = 300


def _tenant_to_dict(tenant: Tenant) -> dict[str, Any]:
    """Convert Tenant ORM object to response dict directly.

    P2-1: Bypasses Pydantic model construction for ~3x faster serialization.
    Fields match TenantResponse schema: id, created_at, updated_at, name, slug, is_active.
    """
    return {
        "id": tenant.id,
        "created_at": tenant.created_at,
        "updated_at": tenant.updated_at,
        "name": tenant.name,
        "slug": tenant.slug,
        "is_active": tenant.is_active,
        "feature_flags": tenant.feature_flags if hasattr(tenant, "feature_flags") and tenant.feature_flags else {},
        "storage_usage_bytes": getattr(tenant, "storage_usage_bytes", 0),
        "file_count": getattr(tenant, "file_count", 0),
    }


def _get_cache_key(tenant_id: UUID | str) -> str:
    """Generate Redis cache key for tenant."""
    return f"tenant:{tenant_id}"


def _get_slug_cache_key(slug: str) -> str:
    """Generate Redis cache key for tenant slug lookup."""
    return f"tenant:slug:{slug.lower().strip()}"


class TenantService(BaseService[Tenant]):
    """Service class for tenant-related business logic."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize TenantService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(Tenant, db)
        self.crud = crud_tenants

    async def _cache_tenant(self, tenant: Tenant) -> None:
        """Cache tenant data in Redis.

        P2-4: Caches both ID and slug lookups for O(1) retrieval.
        Use this for ORM Tenant objects.
        """
        if not redis_client:
            return

        try:
            tenant_data = _tenant_to_dict(tenant)
            cache_key = _get_cache_key(tenant.id)
            await redis_client.set(cache_key, json_dumps(tenant_data, as_str=True), ex=TENANT_CACHE_TTL)

            # Also cache by slug for slug-based lookups
            slug_key = _get_slug_cache_key(tenant.slug)
            await redis_client.set(slug_key, str(tenant.id), ex=TENANT_CACHE_TTL)
        except Exception as e:
            logger.debug("Failed to cache tenant", error=str(e))

    async def _cache_tenant_dict(self, tenant_id: Any, tenant_data: dict[str, Any]) -> None:
        """Cache tenant data dict in Redis.

        P2-4: Used when FastCRUD returns a dict (schema_to_select=TenantResponse).
        Caches both by ID and by slug for O(1) retrieval.
        """
        if not redis_client or not tenant_data:
            return

        try:
            cache_key = _get_cache_key(tenant_id)
            await redis_client.set(cache_key, json_dumps(tenant_data, as_str=True), ex=TENANT_CACHE_TTL)

            # Also cache by slug for slug-based lookups
            slug = tenant_data.get("slug")
            if slug:
                slug_key = _get_slug_cache_key(slug)
                await redis_client.set(slug_key, str(tenant_id), ex=TENANT_CACHE_TTL)
        except Exception as e:
            logger.debug("Failed to cache tenant dict", error=str(e))

    async def _invalidate_tenant_cache(self, tenant_id: UUID, slug: str | None = None) -> None:
        """Invalidate tenant cache in Redis."""
        if not redis_client:
            return

        try:
            await redis_client.delete(_get_cache_key(tenant_id))
            if slug:
                await redis_client.delete(_get_slug_cache_key(slug))
        except Exception as e:
            logger.debug("Failed to invalidate tenant cache", error=str(e))

    async def _get_cached_tenant(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Get tenant from cache if available."""
        if not redis_client:
            return None

        try:
            cached = await redis_client.get(_get_cache_key(tenant_id))
            if cached:
                result: dict[str, Any] = json_loads(cached)
                return result
        except Exception as e:
            logger.error("Failed to get cached tenant", error=str(e))

        return None

    async def _get_cached_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Get tenant by slug from cache if available."""
        if not redis_client:
            return None

        try:
            slug_key = _get_slug_cache_key(slug)
            cached = await redis_client.get(slug_key)

            if cached:
                try:
                    result: dict[str, Any] = json_loads(cached)
                    if isinstance(result, dict) and "id" in result:
                        return result
                except Exception:
                    pass

                # Fallback for old cache entries (UUID string)
                cached_full = await redis_client.get(_get_cache_key(str(cached)))
                if cached_full:
                    result_fallback: dict[str, Any] = json_loads(cached_full)
                    return result_fallback
        except Exception as e:
            logger.debug("Failed to get cached tenant by slug", error=str(e))

        return None

    async def create_tenant(self, tenant: TenantCreate) -> dict[str, Any]:
        """Create a new tenant with validation.

        Parameters
        ----------
        tenant : TenantCreate
            Tenant creation data

        Returns
        -------
        dict[str, Any]
            Created tenant data

        Raises
        ------
        DuplicateError
            If slug already exists
        """
        # Normalize slug to ensure strict database integrity
        if tenant.slug:
            tenant.slug = tenant.slug.lower().strip()

        slug_exists = await self.crud.exists(db=self.db, slug=tenant.slug)
        if slug_exists:
            logger.warning(
                "Tenant creation failed: duplicate_slug | slug=%s",
                tenant.slug,
            )
            raise DuplicateError("slug", tenant.slug)

        try:
            created_tenant = await self.crud.create(
                db=self.db,
                object=tenant,
            )
        except IntegrityError:
            logger.warning(
                "Tenant creation failed: race_condition_duplicate_slug | slug=%s",
                tenant.slug,
            )
            raise DuplicateError("slug", tenant.slug)

        # P2-5: Always use optimized dict construction
        result = _tenant_to_dict(created_tenant)  # type: ignore[arg-type]

        # FIXED: Cache the newly created tenant for immediate lookup
        await self._cache_tenant(created_tenant)

        # Invalidate the negative cache so newly created tenants aren't hidden
        if redis_client and tenant.slug:
            await redis_client.delete(f"tenant:negative:{tenant.slug}")

        logger.info(
            "Tenant created | id=%s | slug=%s | name=%s",
            result["id"],
            result["slug"],
            result["name"],
        )

        return result

    async def get_tenant(self, tenant_id: UUID) -> dict[str, Any]:
        """Get a tenant by ID with Redis caching.

        P2-4: First checks Redis cache for O(1) retrieval, falls back to DB.

        Parameters
        ----------
        tenant_id : UUID
            Tenant unique identifier

        Returns
        -------
        dict[str, Any]
            Tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        # P2-4: Try cache first
        cached = await self._get_cached_tenant(tenant_id)
        if cached:
            return cached

        # Cache miss - fetch from DB
        tenant = await self.crud.get(db=self.db, id=tenant_id, schema_to_select=TenantResponse)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        # Cache for next request
        # NOTE: FastCRUD with schema_to_select returns a dict, not an ORM Tenant object.
        # Cache the dict directly via a lightweight method.
        if tenant:
            await self._cache_tenant_dict(tenant_id, tenant)

        return tenant

    async def get_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Get a tenant by slug with Redis caching.

        P2-4: First checks Redis cache for O(1) retrieval, falls back to DB.

        Parameters
        ----------
        slug : str
            Tenant slug

        Returns
        -------
        dict[str, Any] | None
            Tenant data or None if not found
        """
        normalized_slug = slug.lower().strip()

        # P2-4: Try cache first
        cached = await self._get_cached_tenant_by_slug(normalized_slug)
        if cached:
            return cached

        # Cache miss - fetch from DB
        tenant = await self.crud.get(db=self.db, slug=normalized_slug, schema_to_select=TenantResponse)

        # Cache for next request
        # NOTE: FastCRUD with schema_to_select returns a dict, not an ORM Tenant object.
        if tenant:
            await self._cache_tenant_dict(tenant.get("id"), tenant)

        return tenant

    async def list_tenants(
        self,
        page: int = 1,
        items_per_page: int = 10,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        """List tenants with pagination and optional filtering.

        Parameters
        ----------
        page : int
            Page number (1-indexed)
        items_per_page : int
            Items per page
        is_active : bool | None
            Filter by active status

        Returns
        -------
        dict[str, Any]
            Paginated tenant list
        """
        from fastcrud import compute_offset

        offset = compute_offset(page, items_per_page)

        kwargs: dict[str, Any] = {
            "db": self.db,
            "offset": offset,
            "limit": items_per_page,
            "schema_to_select": TenantResponse,
        }

        if is_active is not None:
            kwargs["is_active"] = is_active

        result: dict[str, Any] = await self.crud.get_multi(**kwargs)
        return result

    async def update_tenant(
        self,
        tenant_id: UUID,
        values: TenantUpdate,
    ) -> dict[str, Any]:
        """Update a tenant.

        Optimized: Uses session refresh instead of second DB roundtrip.

        Parameters
        ----------
        tenant_id : UUID
            Tenant unique identifier
        values : TenantUpdate
            Update values

        Returns
        -------
        dict[str, Any]
            Updated tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        # P1-1: Fetch ORM object directly for in-place update
        tenant = await self.db.get(Tenant, tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        update_data = values.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)

        old_slug = tenant.slug

        # Apply updates to ORM object
        for key, value in update_data.items():
            setattr(tenant, key, value)

        try:
            await self.db.commit()
            await self.db.refresh(tenant)
        except IntegrityError:
            await self.db.rollback()
            logger.warning(
                "Tenant update failed: duplicate_slug | slug=%s",
                tenant.slug,
            )
            raise DuplicateError("slug", tenant.slug)

        # FIXED: Invalidate tenant cache after update to prevent stale data
        await self._invalidate_tenant_cache(tenant_id, tenant.slug)
        if old_slug and old_slug != tenant.slug:
            await self._invalidate_tenant_cache(tenant_id, old_slug)

        # P2-1: Direct dict construction bypasses Pydantic for ~3x speedup
        updated_dict = _tenant_to_dict(tenant)  # type: ignore[arg-type]

        # Write back to cache immediately
        await self._cache_tenant_dict(tenant_id, updated_dict)

        logger.info(
            "Tenant updated | id=%s | fields=%s",
            tenant_id,
            list(update_data.keys()),
        )

        return updated_dict

    async def deactivate_tenant(self, tenant_id: UUID) -> dict[str, Any]:
        """Deactivate a tenant (soft delete).

        Optimized: Uses session refresh instead of second DB roundtrip.

        Parameters
        ----------
        tenant_id : UUID
            Tenant unique identifier

        Returns
        -------
        dict[str, Any]
            Deactivated tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        # P1-1: Fetch ORM object directly for in-place update
        tenant = await self.db.get(Tenant, tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        tenant.is_active = False
        tenant.updated_at = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(tenant)

        # FIXED: Invalidate tenant cache after deactivation to prevent stale data
        await self._invalidate_tenant_cache(tenant_id, tenant.slug)

        logger.info("Tenant deactivated | id=%s | slug=%s", tenant_id, tenant.slug)

        # P2-1: Direct dict construction bypasses Pydantic for ~3x speedup
        updated_dict = _tenant_to_dict(tenant)  # type: ignore[arg-type]
        await self._cache_tenant_dict(tenant_id, updated_dict)
        return updated_dict

    async def reactivate_tenant(self, tenant_id: UUID) -> dict[str, Any]:
        """Reactivate a deactivated tenant.

        Optimized: Uses session refresh instead of second DB roundtrip.

        Parameters
        ----------
        tenant_id : UUID
            Tenant unique identifier

        Returns
        -------
        dict[str, Any]
            Reactivated tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        # P1-1: Fetch ORM object directly for in-place update
        tenant = await self.db.get(Tenant, tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        tenant.is_active = True
        tenant.updated_at = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(tenant)

        # FIXED: Invalidate tenant cache after reactivation to prevent stale data
        await self._invalidate_tenant_cache(tenant_id, tenant.slug)

        logger.info("Tenant reactivated | id=%s | slug=%s", tenant_id, tenant.slug)

        # P2-1: Direct dict construction bypasses Pydantic for ~3x speedup
        updated_dict = _tenant_to_dict(tenant)  # type: ignore[arg-type]
        await self._cache_tenant_dict(tenant_id, updated_dict)
        return updated_dict


def get_tenant_service(db: AsyncSession) -> TenantService:
    """Dependency injection factory for TenantService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    TenantService
        Service instance with database session
    """
    return TenantService(db)
