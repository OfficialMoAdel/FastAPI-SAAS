from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.tenant_crud import TenantAwareCRUD

from .models import UserSSOConnection
from .schemas import (
    UserSSOConnectionCreate,
    UserSSOConnectionRead,
    UserSSOConnectionUpdate,
)


class CRUDUserSSOConnection(
    TenantAwareCRUD[
        UserSSOConnection,
        UserSSOConnectionCreate,
        UserSSOConnectionUpdate,
        UserSSOConnectionUpdate,
        UserSSOConnectionUpdate,
        UserSSOConnectionRead,
    ]
):
    async def get(
        self,
        db: AsyncSession,
        id: int | None = None,
        provider: str | None = None,
        provider_user_id: str | None = None,
        user_id: int | None = None,
        tenant_id: int | None = None,
        **kwargs,
    ) -> UserSSOConnection | None:
        """Get SSO connection by various criteria."""
        if id is not None:
            return await super().get(db=db, id=id)

        query = select(self.model)
        if provider is not None:
            query = query.where(self.model.provider == provider)
        if provider_user_id is not None:
            query = query.where(self.model.provider_user_id == provider_user_id)
        if user_id is not None:
            query = query.where(self.model.user_id == user_id)
        if tenant_id is not None:
            query = query.where(self.model.tenant_id == tenant_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def exists(
        self,
        db: AsyncSession,
        user_id: int,
        provider: str,
        tenant_id: int | None = None,
    ) -> bool:
        """Check if SSO connection exists for user and provider."""
        query = select(func.count()).select_from(self.model).where(
            self.model.user_id == user_id,
            self.model.provider == provider,
        )
        if tenant_id is not None:
            query = query.where(self.model.tenant_id == tenant_id)

        result = await db.execute(query)
        return result.scalar() > 0

    async def count(
        self,
        db: AsyncSession,
        user_id: int | None = None,
        tenant_id: int | None = None,
        **kwargs,
    ) -> int:
        """Count SSO connections for a user."""
        if user_id is None and "id" in kwargs:
             return await super().count(db, **kwargs)

        query = select(func.count()).select_from(self.model)
        if user_id is not None:
            query = query.where(self.model.user_id == user_id)

        if tenant_id is not None:
            query = query.where(self.model.tenant_id == tenant_id)

        result = await db.execute(query)
        return result.scalar() or 0

    async def delete(
        self,
        db: AsyncSession,
        id: int | None = None,
        user_id: int | None = None,
        provider: str | None = None,
        tenant_id: int | None = None,
    ) -> None:
        """Delete SSO connection by ID or by user/provider combo."""
        if id is not None:
            await super().delete(db=db, id=id)
            return

        connection = await self.get(
            db=db,
            user_id=user_id,
            provider=provider,
            tenant_id=tenant_id,
        )
        if connection:
            await db.delete(connection)
            await db.commit()


crud_user_sso_connections = CRUDUserSSOConnection(UserSSOConnection)
