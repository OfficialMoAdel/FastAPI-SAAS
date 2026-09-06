"""SQLAlchemy implementation of TenantRepository."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.tenants.crud import crud_tenants
from src.app.modules.tenants.schemas import TenantResponse
from src.app.repositories.interfaces.tenant import TenantRepository


class SqlAlchemyTenantRepository(TenantRepository):
    """SQLAlchemy backed implementation of TenantRepository."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = crud_tenants

    async def get(self, tenant_id: UUID | str) -> dict[str, Any] | None:
        parsed_id = UUID(str(tenant_id)) if isinstance(tenant_id, str) else tenant_id
        result = await self.crud.get(db=self.db, id=parsed_id, schema_to_select=TenantResponse)
        return dict(result) if result else None

    async def get_by_slug(self, slug: str) -> dict[str, Any] | None:
        result = await self.crud.get(db=self.db, slug=slug, schema_to_select=TenantResponse)
        return dict(result) if result else None

    async def get_multi(
        self,
        *,
        offset: int = 0,
        limit: int = 10,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"offset": offset, "limit": limit, "schema_to_select": TenantResponse}
        if is_active is not None:
            kwargs["is_active"] = is_active
        return await self.crud.get_multi(db=self.db, **kwargs)

    async def create(self, tenant_data: Any) -> dict[str, Any]:
        result = await self.crud.create(db=self.db, object=tenant_data, schema_to_select=TenantResponse)
        return dict(result) if result else {}

    async def update(self, tenant_id: UUID | str, tenant_data: Any) -> dict[str, Any] | None:
        parsed_id = UUID(str(tenant_id)) if isinstance(tenant_id, str) else tenant_id
        result = await self.crud.update(
            db=self.db,
            id=parsed_id,
            object=tenant_data,
            schema_to_select=TenantResponse,
        )
        return dict(result) if result else None

    async def delete(self, tenant_id: UUID | str) -> bool:
        parsed_id = UUID(str(tenant_id)) if isinstance(tenant_id, str) else tenant_id
        await self.crud.db_delete(db=self.db, id=parsed_id)
        return True


__all__ = ["SqlAlchemyTenantRepository"]
