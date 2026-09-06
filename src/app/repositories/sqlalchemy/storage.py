"""SQLAlchemy implementation of StorageRepository."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.tenant_crud import tenant_scope
from src.app.modules.storage.models import File, StorageSettings
from src.app.repositories.interfaces.storage import StorageRepository


class SqlAlchemyStorageRepository(StorageRepository):
    """SQLAlchemy backed implementation of StorageRepository."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_file(self, tenant_id: UUID, file_id: int) -> Any | None:
        stmt = select(File).where(File.id == file_id, tenant_scope(File, tenant_id))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_settings(self, tenant_id: UUID) -> Any | None:
        stmt = select(StorageSettings).where(tenant_scope(StorageSettings, tenant_id))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def save_file(self, file_data: Any) -> Any:
        self.db.add(file_data)
        await self.db.commit()
        await self.db.refresh(file_data)
        return file_data

    async def update_settings(self, tenant_id: UUID, settings_data: Any) -> Any:
        self.db.add(settings_data)
        await self.db.commit()
        await self.db.refresh(settings_data)
        return settings_data

    async def delete_file(self, tenant_id: UUID, file_id: int) -> bool:
        stmt = delete(File).where(File.id == file_id, tenant_scope(File, tenant_id))
        result = await self.db.execute(stmt)
        await self.db.commit()
        return bool(getattr(result, "rowcount", 0) > 0)

    async def get_quota_usage(self, tenant_id: UUID) -> dict[str, int]:
        stmt = select(
            func.coalesce(func.sum(File.size_bytes), 0),
            func.count(File.id),
        ).where(tenant_scope(File, tenant_id))
        result = await self.db.execute(stmt)
        row = result.first()
        if row:
            return {"used_bytes": int(row[0]), "file_count": int(row[1])}
        return {"used_bytes": 0, "file_count": 0}


__all__ = ["SqlAlchemyStorageRepository"]
