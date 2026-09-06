"""SQLAlchemy implementation of UserRepository."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.users.crud import CRUDUser, crud_users
from src.app.modules.users.schemas import UserRead
from src.app.repositories.interfaces.user import UserRepository


class SqlAlchemyUserRepository(UserRepository):
    """SQLAlchemy and FastCRUD backed implementation of UserRepository."""

    def __init__(self, db: AsyncSession, crud: CRUDUser | None = None) -> None:
        self.db = db
        self.crud = crud or crud_users

    async def get(
        self,
        *,
        id: int | None = None,
        username: str | None = None,
        email: str | None = None,
        is_deleted: bool = False,
    ) -> dict[str, Any] | None:
        if id is not None:
            return await self.crud.get(db=self.db, id=id, is_deleted=is_deleted)
        if username is not None:
            return await self.crud.get(db=self.db, username=username, is_deleted=is_deleted)
        if email is not None:
            return await self.crud.get(db=self.db, email=email, is_deleted=is_deleted)
        return None

    async def get_with_tier(
        self,
        *,
        id: int | None = None,
        username: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any] | None:
        return await self.crud.get_user_with_tier_eager(
            db=self.db,
            id=id,
            username=username,
            email=email,
        )

    async def get_multi(
        self,
        *,
        offset: int = 0,
        limit: int = 10,
        is_deleted: bool = False,
    ) -> dict[str, Any]:
        return await self.crud.get_multi(
            db=self.db,
            offset=offset,
            limit=limit,
            is_deleted=is_deleted,
            schema_to_select=UserRead,
        )

    async def create(self, user_data: Any) -> dict[str, Any]:
        result = await self.crud.create(db=self.db, object=user_data, schema_to_select=UserRead)
        return dict(result) if result else {}

    async def update(self, user_id: int, user_data: Any) -> dict[str, Any] | None:
        result = await self.crud.update(
            db=self.db,
            id=user_id,
            object=user_data,
            schema_to_select=UserRead,
        )
        return dict(result) if result else None

    async def delete(self, user_id: int) -> bool:
        await self.crud.db_delete(db=self.db, id=user_id)
        return True


__all__ = ["SqlAlchemyUserRepository"]
