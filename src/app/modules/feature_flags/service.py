
from fastcrud import FastCRUD
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.models import FeatureFlag, FeatureFlagOverride
from .schemas import (
    FeatureFlagCreate,
    FeatureFlagOverrideCreate,
    FeatureFlagUpdate,
)


class FeatureFlagsService:
    def __init__(self):
        self.crud = FastCRUD(FeatureFlag)
        self.override_crud = FastCRUD(FeatureFlagOverride)

    async def get_flag(self, db: AsyncSession, name: str, tenant_id: int) -> FeatureFlag | None:
        return await self.crud.get(db, name=name, tenant_id=tenant_id)

    async def create_flag(self, db: AsyncSession, flag: FeatureFlagCreate, tenant_id: int) -> FeatureFlag:
        flag_data = flag.model_dump()
        flag_data["tenant_id"] = tenant_id
        return await self.crud.create(db, flag_data)

    async def update_flag(
        self,
        db: AsyncSession,
        name: str,
        flag: FeatureFlagUpdate,
        tenant_id: int,
    ) -> FeatureFlag | None:
        return await self.crud.update(
            db,
            name=name,
            tenant_id=tenant_id,
            object=flag.model_dump(exclude_unset=True),
        )

    async def delete_flag(self, db: AsyncSession, name: str, tenant_id: int) -> bool:
        return await self.crud.delete(db, name=name, tenant_id=tenant_id)

    async def list_flags(self, db: AsyncSession, tenant_id: int, skip: int = 0, limit: int = 100):
        return await self.crud.get_multi(db, tenant_id=tenant_id, skip=skip, limit=limit)

    async def get_override(
        self, db: AsyncSession, flag_name: str, user_id: int, tenant_id: int
    ) -> FeatureFlagOverride | None:
        return await self.override_crud.get(db, flag_name=flag_name, user_id=user_id, tenant_id=tenant_id)

    async def create_override(
        self,
        db: AsyncSession,
        override: FeatureFlagOverrideCreate,
        tenant_id: int,
    ) -> FeatureFlagOverride:
        override_data = override.model_dump()
        override_data["tenant_id"] = tenant_id
        return await self.override_crud.create(db, override_data)

    async def delete_override(self, db: AsyncSession, flag_name: str, user_id: int, tenant_id: int) -> bool:
        return await self.override_crud.delete(db, flag_name=flag_name, user_id=user_id, tenant_id=tenant_id)

    async def is_feature_enabled(
        self, db: AsyncSession, flag_name: str, user_id: int | None, tenant_id: int
    ) -> bool:
        flag = await self.get_flag(db, flag_name, tenant_id)
        if not flag:
            return False
        if not flag.is_enabled:
            return False
        if not user_id:
            return True
        user_override = await self.get_override(db, flag_name, user_id, tenant_id)
        if user_override:
            return user_override.is_enabled
        return True


feature_flags_service = FeatureFlagsService()
