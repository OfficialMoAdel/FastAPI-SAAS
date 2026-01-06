"""Tenant service layer - encapsulates all tenant business logic."""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .._base.exceptions import DuplicateError, ModelNotFoundError
from .crud import crud_tenants
from .schemas import TenantCreate, TenantResponse, TenantUpdate

logger = logging.getLogger(__name__)


class TenantService:
    """Service class for tenant-related business logic."""

    def __init__(self) -> None:
        self.crud = crud_tenants

    async def create_tenant(self, tenant: TenantCreate, db: AsyncSession) -> dict[str, Any]:
        """Create a new tenant with validation.

        Parameters
        ----------
        tenant : TenantCreate
            Tenant creation data
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Created tenant data

        Raises
        ------
        DuplicateError
            If slug already exists
        """
        slug_exists = await self.crud.exists(db=db, slug=tenant.slug)
        if slug_exists:
            logger.warning(
                "Tenant creation failed: duplicate_slug | slug=%s",
                tenant.slug,
            )
            raise DuplicateError("slug", tenant.slug)

        created_tenant = await self.crud.create(
            db=db,
            object=tenant,
            schema_to_select=TenantResponse,
        )

        logger.info(
            "Tenant created | id=%s | slug=%s | name=%s",
            created_tenant["id"],
            created_tenant["slug"],
            created_tenant["name"],
        )

        return created_tenant

    async def get_tenant(self, tenant_id: int, db: AsyncSession) -> dict[str, Any]:
        """Get a tenant by ID.

        Parameters
        ----------
        tenant_id : int
            Tenant ID
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        tenant = await self.crud.get(db=db, id=tenant_id, schema_to_select=TenantResponse)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)
        return tenant

    async def get_tenant_by_slug(self, slug: str, db: AsyncSession) -> dict[str, Any] | None:
        """Get a tenant by slug.

        Parameters
        ----------
        slug : str
            Tenant slug
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any] | None
            Tenant data or None if not found
        """
        return await self.crud.get(db=db, slug=slug, schema_to_select=TenantResponse)

    async def list_tenants(
        self,
        db: AsyncSession,
        page: int = 1,
        items_per_page: int = 10,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        """List tenants with pagination and optional filtering.

        Parameters
        ----------
        db : AsyncSession
            Database session
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
            "db": db,
            "offset": offset,
            "limit": items_per_page,
            "schema_to_select": TenantResponse,
        }

        if is_active is not None:
            kwargs["is_active"] = is_active

        return await self.crud.get_multi(**kwargs)

    async def update_tenant(
        self,
        tenant_id: int,
        values: TenantUpdate,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Update a tenant.

        Parameters
        ----------
        tenant_id : int
            Tenant ID
        values : TenantUpdate
            Update values
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Updated tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        tenant = await self.crud.get(db=db, id=tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        update_data = values.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)

        await self.crud.update(db=db, object=update_data, id=tenant_id)

        logger.info(
            "Tenant updated | id=%s | fields=%s",
            tenant_id,
            list(update_data.keys()),
        )

        return await self.get_tenant(tenant_id, db)

    async def deactivate_tenant(self, tenant_id: int, db: AsyncSession) -> dict[str, Any]:
        """Deactivate a tenant (soft delete).

        Parameters
        ----------
        tenant_id : int
            Tenant ID
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Deactivated tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        tenant = await self.crud.get(db=db, id=tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        update_data = {
            "is_active": False,
            "updated_at": datetime.now(UTC),
        }

        await self.crud.update(db=db, object=update_data, id=tenant_id)

        logger.info("Tenant deactivated | id=%s | slug=%s", tenant_id, tenant["slug"])

        return await self.get_tenant(tenant_id, db)

    async def reactivate_tenant(self, tenant_id: int, db: AsyncSession) -> dict[str, Any]:
        """Reactivate a deactivated tenant.

        Parameters
        ----------
        tenant_id : int
            Tenant ID
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Reactivated tenant data

        Raises
        ------
        ModelNotFoundError
            If tenant not found
        """
        tenant = await self.crud.get(db=db, id=tenant_id)
        if tenant is None:
            raise ModelNotFoundError("Tenant", tenant_id)

        update_data = {
            "is_active": True,
            "updated_at": datetime.now(UTC),
        }

        await self.crud.update(db=db, object=update_data, id=tenant_id)

        logger.info("Tenant reactivated | id=%s | slug=%s", tenant_id, tenant["slug"])

        return await self.get_tenant(tenant_id, db)


# Singleton instance for import convenience
tenant_service = TenantService()
