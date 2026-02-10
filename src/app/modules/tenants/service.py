"""Tenant service layer - encapsulates all tenant business logic."""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.feature_flags import FEATURE_TENANT_DICT_OPTIMIZATION

from .._base.exceptions import DuplicateError, ModelNotFoundError
from .._base.service import BaseService
from .crud import crud_tenants
from .models import Tenant
from .schemas import TenantCreate, TenantResponse, TenantUpdate

logger = logging.getLogger(__name__)


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
    }


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
        # Normalize slug (defense in depth, even if schema handles it)
        if tenant.slug:
            tenant.slug = tenant.slug.lower().strip()

        slug_exists = await self.crud.exists(db=self.db, slug=tenant.slug)
        if slug_exists:
            logger.warning(
                "Tenant creation failed: duplicate_slug | slug=%s",
                tenant.slug,
            )
            raise DuplicateError("slug", tenant.slug)

        created_tenant = await self.crud.create(
            db=self.db,
            object=tenant,
        )

        # P2-1: Use optimized dict construction if feature flag is enabled
        if FEATURE_TENANT_DICT_OPTIMIZATION:
            # Type ignore: optimized path assumes ORM object is compatible with _tenant_to_dict
            result = _tenant_to_dict(created_tenant)  # type: ignore[arg-type]
        else:
            result = TenantResponse.model_validate(created_tenant).model_dump()

        logger.info(
            "Tenant created | id=%s | slug=%s | name=%s",
            result["id"],
            result["slug"],
            result["name"],
        )

        return result

    async def get_tenant(self, tenant_id: int) -> dict[str, Any]:
        """Get a tenant by ID.

        Parameters
        ----------
        tenant_id : int
            Tenant ID

        Returns
        -------
        dict[str, Any]
            Tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        tenant = await self.crud.get(db=self.db, id=tenant_id, schema_to_select=TenantResponse)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)
        return tenant

    async def get_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Get a tenant by slug.

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
        return await self.crud.get(db=self.db, slug=normalized_slug, schema_to_select=TenantResponse)

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
        tenant_id: int,
        values: TenantUpdate,
    ) -> dict[str, Any]:
        """Update a tenant.

        Optimized: Uses session refresh instead of second DB roundtrip.

        Parameters
        ----------
        tenant_id : int
            Tenant ID
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

        # Apply updates to ORM object
        for key, value in update_data.items():
            setattr(tenant, key, value)

        await self.db.commit()
        await self.db.refresh(tenant)

        logger.info(
            "Tenant updated | id=%s | fields=%s",
            tenant_id,
            list(update_data.keys()),
        )

        # P2-1: Direct dict construction bypasses Pydantic for ~3x speedup
        return _tenant_to_dict(tenant)  # type: ignore[arg-type]

    async def deactivate_tenant(self, tenant_id: int) -> dict[str, Any]:
        """Deactivate a tenant (soft delete).

        Optimized: Uses session refresh instead of second DB roundtrip.

        Parameters
        ----------
        tenant_id : int
            Tenant ID

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

        logger.info("Tenant deactivated | id=%s | slug=%s", tenant_id, tenant.slug)

        # P2-1: Direct dict construction bypasses Pydantic for ~3x speedup
        return _tenant_to_dict(tenant)  # type: ignore[arg-type]

    async def reactivate_tenant(self, tenant_id: int) -> dict[str, Any]:
        """Reactivate a deactivated tenant.

        Optimized: Uses session refresh instead of second DB roundtrip.

        Parameters
        ----------
        tenant_id : int
            Tenant ID

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

        logger.info("Tenant reactivated | id=%s | slug=%s", tenant_id, tenant.slug)

        # P2-1: Direct dict construction bypasses Pydantic for ~3x speedup
        return _tenant_to_dict(tenant)  # type: ignore[arg-type]


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
